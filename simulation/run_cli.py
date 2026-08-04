"""
simulation/run_cli.py — Command-line GPS simulator replay tool.

Usage:
    python3 run_cli.py --date 2026-06-12 --speed 10 --phase morning
"""
import argparse
import asyncio
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.abspath(os.path.join(CURRENT_DIR, "..", "backend")))

from dotenv import load_dotenv
load_dotenv(os.path.abspath(os.path.join(CURRENT_DIR, "..", "backend", ".env")))

from simulator_engine import standalone_simulator

async def main():
    parser = argparse.ArgumentParser(description="DUK Bus GPS Simulator CLI Replayer")
    parser.add_argument("--date", default="2026-06-12", help="Date in YYYY-MM-DD format")
    parser.add_argument("--speed", type=float, default=5.0, help="Playback speed multiplier (e.g. 5.0)")
    parser.add_argument("--phase", default="all", choices=["all", "morning", "unscheduled_midday", "midday_idle", "evening"], help="Trip phase to replay")
    parser.add_argument("--loop", action="store_true", help="Loop playback continuously")
    parser.add_argument("--list-dates", action="store_true", help="List all available dates in sqlite DB")

    args = parser.parse_args()

    if args.list_dates:
        dates = standalone_simulator.get_available_dates()
        print("\n📅 Available recorded dates in gps_tracker.db:")
        print("------------------------------------------------------------")
        for d in dates:
            print(f"  • {d['date']}: {d['total_pings']} pings (Morning: {d['morning_pings']}, Midday: {d['midday_unscheduled_pings']}, Evening: {d['evening_pings']})")
        print("------------------------------------------------------------\n")
        return

    print(f"🚀 Starting GPS Simulation for date {args.date} [{args.phase}] at {args.speed}x speed...")
    await standalone_simulator.start(
        date_str=args.date,
        phase=args.phase,
        speed_multiplier=args.speed,
        loop=args.loop,
    )

    try:
        while standalone_simulator.is_running:
            st = standalone_simulator.get_status()
            last = st.get("last_dispatched") or {}
            print(
                f"\r[Point {st['current_index']}/{st['total_points']} ({st['progress_percent']}%) | "
                f"Speed: {last.get('speed_kmh', 0):.1f} km/h | "
                f"Lat: {last.get('lat', 0):.5f}, Lon: {last.get('lon', 0):.5f}]",
                end="",
                flush=True,
            )
            await asyncio.sleep(0.5)
        print("\n✅ Simulation completed successfully.")
    except KeyboardInterrupt:
        print("\n🛑 Stopping simulation...")
        await standalone_simulator.stop()

if __name__ == "__main__":
    asyncio.run(main())
