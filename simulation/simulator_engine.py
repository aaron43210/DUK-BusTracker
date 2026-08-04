"""
simulation/simulator_engine.py — Dedicated GPS Simulator Engine.
Replays recorded GPS trajectories from gps_tracker.db into the live bus tracking backend.
"""
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

# Add backend directory to sys.path to connect with database/models/services
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import AsyncSessionLocal
from models.gps import GpsLog
from services.trip_lifecycle import (
    handle_gps_update,
    handle_power_on,
    handle_power_off,
)

logger = logging.getLogger(__name__)

# Default path to recorded sqlite database
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gps_tracker.db"))


class StandaloneGpsSimulator:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.is_running: bool = False
        self.is_paused: bool = False
        self.current_index: int = 0
        self.points: List[Dict[str, Any]] = []
        self.speed_multiplier: float = 5.0
        self.selected_date: str = "2026-06-12"
        self.selected_phase: str = "all"
        self.loop: bool = False
        self.last_dispatched: Optional[Dict[str, Any]] = None
        self.points_dispatched_count: int = 0

        self._task: Optional[asyncio.Task] = None
        self._pause_event: asyncio.Event = asyncio.Event()
        self._pause_event.set()

    def get_available_dates(self) -> List[Dict[str, Any]]:
        """Queries recorded dates and metadata from gps_tracker.db."""
        if not os.path.exists(self.db_path):
            return []

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT 
                    substr(server_time, 1, 10) as dt,
                    COUNT(*) as total_pings,
                    MIN(server_time) as first_ping,
                    MAX(server_time) as last_ping,
                    SUM(CASE WHEN strftime('%H', server_time) BETWEEN '06' AND '09' THEN 1 ELSE 0 END) as morning_pings,
                    SUM(CASE WHEN strftime('%H', server_time) BETWEEN '10' AND '16' THEN 1 ELSE 0 END) as midday_unscheduled_pings,
                    SUM(CASE WHEN strftime('%H', server_time) >= '17' THEN 1 ELSE 0 END) as evening_pings
                FROM gps_logs
                WHERE server_time IS NOT NULL AND lat IS NOT NULL
                GROUP BY dt
                ORDER BY dt DESC
            """)
            rows = cursor.fetchall()
            dates = []
            for r in rows:
                dates.append({
                    "date": r[0],
                    "total_pings": r[1],
                    "first_ping": r[2],
                    "last_ping": r[3],
                    "morning_pings": r[4],
                    "midday_unscheduled_pings": r[5],
                    "evening_pings": r[6],
                    "has_full_day": r[4] > 20 and r[6] > 20,
                })
            return dates
        finally:
            conn.close()

    def load_points(self, date_str: str = "2026-06-12", phase: str = "all") -> int:
        """Loads recorded points from SQLite database for given date and phase."""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found at {self.db_path}")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT id, server_time, gps_time, lat, lon, event
                FROM gps_logs
                WHERE server_time LIKE ? AND lat IS NOT NULL AND lon IS NOT NULL
                ORDER BY id ASC
            """, (f"{date_str}%",))
            rows = cursor.fetchall()

            all_points = []
            for r in rows:
                st_str = r[1]
                gt_str = r[2]
                lat = float(r[3])
                lon = float(r[4])
                event = r[5]

                try:
                    dt = datetime.fromisoformat(st_str)
                    hour = dt.hour
                except Exception:
                    hour = 8

                all_points.append({
                    "id": r[0],
                    "server_time_orig": st_str,
                    "gps_time_orig": gt_str,
                    "hour": hour,
                    "lat": lat,
                    "lon": lon,
                    "event": event,
                })

            filtered = []
            for p in all_points:
                h = p["hour"]
                if phase == "all":
                    filtered.append(p)
                elif phase == "morning" and (6 <= h <= 9):
                    filtered.append(p)
                elif phase == "unscheduled_midday" and (9 <= h <= 12):
                    filtered.append(p)
                elif phase == "midday_idle" and (12 <= h <= 15):
                    filtered.append(p)
                elif phase == "evening" and (h >= 16):
                    filtered.append(p)

            # Compute estimated speed between consecutive points
            for i in range(len(filtered)):
                if i > 0:
                    prev = filtered[i - 1]
                    curr = filtered[i]
                    d_lat = curr["lat"] - prev["lat"]
                    d_lon = curr["lon"] - prev["lon"]
                    dist_km = ((d_lat * 111.0) ** 2 + (d_lon * 111.0 * 0.98) ** 2) ** 0.5
                    curr["speed_kmh"] = min(80.0, max(0.0, dist_km * 360.0))
                else:
                    filtered[i]["speed_kmh"] = 0.0

            self.points = filtered
            self.current_index = 0
            self.selected_date = date_str
            self.selected_phase = phase
            logger.info(
                "[SIMULATOR] Loaded %d points for date=%s, phase=%s",
                len(self.points), date_str, phase
            )
            return len(self.points)
        finally:
            conn.close()

    def get_status(self) -> Dict[str, Any]:
        """Returns current status summary of simulation."""
        progress_pct = 0.0
        if self.points:
            progress_pct = round((self.current_index / len(self.points)) * 100, 1)

        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "selected_date": self.selected_date,
            "selected_phase": self.selected_phase,
            "speed_multiplier": self.speed_multiplier,
            "current_index": self.current_index,
            "total_points": len(self.points),
            "progress_percent": progress_pct,
            "points_dispatched": self.points_dispatched_count,
            "last_dispatched": self.last_dispatched,
        }

    async def start(
        self,
        date_str: str = "2026-06-12",
        phase: str = "all",
        speed_multiplier: float = 5.0,
        loop: bool = False,
    ):
        """Starts real-time GPS simulation."""
        await self.stop()

        self.speed_multiplier = max(0.1, min(speed_multiplier, 100.0))
        self.loop = loop
        self.load_points(date_str, phase)

        if not self.points:
            raise ValueError(f"No GPS points found for date '{date_str}'")

        self.is_running = True
        self.is_paused = False
        self._pause_event.set()
        self._task = asyncio.create_task(self._playback_loop())
        logger.info(
            "[SIMULATOR] Started playback task with %d points at %.1fx speed",
            len(self.points), self.speed_multiplier
        )

    async def pause(self):
        """Pauses simulation."""
        if self.is_running and not self.is_paused:
            self.is_paused = True
            self._pause_event.clear()
            logger.info("[SIMULATOR] Paused at index %d", self.current_index)

    async def resume(self):
        """Resumes paused simulation."""
        if self.is_running and self.is_paused:
            self.is_paused = False
            self._pause_event.set()
            logger.info("[SIMULATOR] Resumed at index %d", self.current_index)

    async def stop(self):
        """Stops simulation."""
        self.is_running = False
        self.is_paused = False
        self._pause_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("[SIMULATOR] Stopped")

    async def step_forward(self) -> Optional[Dict[str, Any]]:
        """Manually dispatches the next GPS point (single-step mode)."""
        if not self.points:
            self.load_points(self.selected_date, self.selected_phase)

        if self.current_index >= len(self.points):
            if self.loop:
                self.current_index = 0
            else:
                return None

        point = self.points[self.current_index]
        res = await self._dispatch_point(point)
        self.current_index += 1
        return res

    async def jump_to(self, index: int = 0, percentage: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Jumps playback to a specific index or percentage."""
        if not self.points:
            self.load_points(self.selected_date, self.selected_phase)

        if not self.points:
            return None

        if percentage is not None:
            pct = max(0.0, min(percentage, 100.0))
            self.current_index = int((pct / 100.0) * (len(self.points) - 1))
        else:
            self.current_index = max(0, min(index, len(self.points) - 1))

        point = self.points[self.current_index]
        return await self._dispatch_point(point)

    async def reset_day(self):
        """Resets today's test trips and stops simulation."""
        await self.stop()
        self.current_index = 0
        self.points_dispatched_count = 0
        self.last_dispatched = None

    async def _dispatch_point(self, point: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches a single GPS point into the live backend on port 5004 via HTTP ingestion."""
        import httpx
        from config import get_settings
        settings = get_settings()

        now_utc = datetime.now(timezone.utc)
        lat = point["lat"]
        lon = point["lon"]
        speed = point.get("speed_kmh", 0.0)
        event = point.get("event")

        # Format hardware raw string
        if event in ["POWER_LOST", "POWER_OFF"]:
            raw_payload = "POWER_OFF"
        elif event in ["POWER_RESTORE", "POWER_ON"]:
            raw_payload = "POWER_ON"
        else:
            time_str = now_utc.strftime("%H%M%S.0")
            date_str = now_utc.strftime("%d%m%y")
            speed_knots = (speed / 1.852) if speed else 0.0
            raw_payload = f"+QGPSLOC: {time_str},{lat:.6f},{lon:.6f},1.0,50.0,3,0.0,{speed_knots:.2f},0.0,{date_str},12"

        backend_url = os.environ.get("BACKEND_URL", "http://localhost:5004")
        
        # Send raw GPS payload to backend process on port 5004
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.post(
                    f"{backend_url}/api/v1/gps",
                    content=raw_payload.encode("utf-8"),
                    headers={
                        "X-API-Key": settings.GPS_API_KEY,
                        "Content-Type": "text/plain",
                        "Host": "localhost",
                    },
                )
                if resp.status_code != 200:
                    logger.warning("[SIMULATOR] Backend returned status %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("[SIMULATOR] Failed to deliver GPS to backend on %s: %s", backend_url, e)

        broadcast_data = {
            "type": "gps",
            "lat": lat,
            "lon": lon,
            "speed_kmh": speed,
            "server_time": now_utc.isoformat(),
            "simulated": True,
            "sim_index": self.current_index,
            "sim_total": len(self.points),
            "phase": self.selected_phase,
        }

        self.last_dispatched = broadcast_data
        self.points_dispatched_count += 1
        return broadcast_data


    async def _playback_loop(self):
        """Internal background task that feeds points according to calculated timestamps."""
        logger.info("[SIMULATOR] Playback loop active")
        try:
            while self.is_running and self.current_index < len(self.points):
                await self._pause_event.wait()
                if not self.is_running:
                    break

                curr_pt = self.points[self.current_index]
                await self._dispatch_point(curr_pt)
                self.current_index += 1

                delay = 10.0 / self.speed_multiplier
                await asyncio.sleep(max(0.05, delay))

            if self.loop and self.is_running:
                self.current_index = 0
                self._task = asyncio.create_task(self._playback_loop())
            else:
                self.is_running = False
                logger.info("[SIMULATOR] Completed playback.")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("[SIMULATOR] Loop crashed: %s", e)
            self.is_running = False



# Global singleton instance
standalone_simulator = StandaloneGpsSimulator()
