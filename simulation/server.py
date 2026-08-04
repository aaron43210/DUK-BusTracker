"""
simulation/server.py — Dedicated Standalone GPS Simulator Control Center.
Runs on Port 8050.

Usage:
    cd /Users/aaronr/Desktop/BUS_TRACKER/simulation
    source /Users/aaronr/py311/bin/activate
    python3 server.py
"""
import os
import sys
import asyncio
import logging
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Ensure simulation and backend paths are available
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.abspath(os.path.join(CURRENT_DIR, "..", "backend")))

from dotenv import load_dotenv
load_dotenv(os.path.abspath(os.path.join(CURRENT_DIR, "..", "backend", ".env")))

from simulator_engine import standalone_simulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("simulation_server")

app = FastAPI(title="DUK Bus GPS Simulator Control Center", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_websockets: List[WebSocket] = []

class SimStartRequest(BaseModel):
    date: str = "2026-06-12"
    phase: str = "all"
    speed: float = 5.0
    loop: bool = False

class SimJumpRequest(BaseModel):
    index: Optional[int] = None
    percentage: Optional[float] = None

class SimSpeedRequest(BaseModel):
    speed: float


@app.get("/api/dates")
async def get_dates():
    return standalone_simulator.get_available_dates()


@app.get("/api/status")
async def get_status():
    return standalone_simulator.get_status()


@app.post("/api/start")
async def start_sim(req: SimStartRequest):
    try:
        await standalone_simulator.start(
            date_str=req.date,
            phase=req.phase,
            speed_multiplier=req.speed,
            loop=req.loop,
        )
        return {"status": "started", "details": standalone_simulator.get_status()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pause")
async def pause_sim():
    await standalone_simulator.pause()
    return {"status": "paused", "details": standalone_simulator.get_status()}


@app.post("/api/resume")
async def resume_sim():
    await standalone_simulator.resume()
    return {"status": "resumed", "details": standalone_simulator.get_status()}


@app.post("/api/stop")
async def stop_sim():
    await standalone_simulator.stop()
    return {"status": "stopped", "details": standalone_simulator.get_status()}


@app.post("/api/step")
async def step_sim():
    res = await standalone_simulator.step_forward()
    return {"status": "stepped", "point": res, "details": standalone_simulator.get_status()}


@app.post("/api/jump")
async def jump_sim(req: SimJumpRequest):
    res = await standalone_simulator.jump_to(index=req.index or 0, percentage=req.percentage)
    return {"status": "jumped", "point": res, "details": standalone_simulator.get_status()}


@app.post("/api/speed")
async def set_speed(req: SimSpeedRequest):
    standalone_simulator.speed_multiplier = max(0.1, min(req.speed, 100.0))
    return {"status": "updated", "speed": standalone_simulator.speed_multiplier}


@app.post("/api/reset_day")
async def reset_day():
    await standalone_simulator.reset_day()
    return {"status": "reset", "details": standalone_simulator.get_status()}


@app.websocket("/ws")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.append(websocket)
    try:
        while True:
            status = standalone_simulator.get_status()
            await websocket.send_json(status)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if websocket in connected_websockets:
            connected_websockets.remove(websocket)


HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DUK Bus GPS Simulator Control Center</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(18, 26, 44, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --accent-blue: #3b82f6;
      --accent-cyan: #06b6d4;
      --accent-green: #10b981;
      --accent-amber: #f59e0b;
      --accent-rose: #f43f5e;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --panel-glow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', sans-serif;
      background: radial-gradient(circle at top, #131d33 0%, var(--bg) 100%);
      color: var(--text-main);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }

    header {
      padding: 16px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--card-border);
      background: rgba(9, 13, 22, 0.8);
      backdrop-filter: blur(12px);
      position: sticky;
      top: 0;
      z-index: 1000;
    }

    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-icon {
      width: 38px; height: 38px;
      background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      font-size: 20px;
      box-shadow: 0 0 15px rgba(6, 182, 212, 0.4);
    }
    .brand-title { font-size: 18px; font-weight: 700; letter-spacing: -0.5px; }
    .brand-subtitle { font-size: 11px; color: var(--accent-cyan); font-family: 'JetBrains Mono', monospace; }

    .header-links { display: flex; align-items: center; gap: 16px; }
    .link-btn {
      padding: 8px 14px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      color: var(--text-main);
      text-decoration: none;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.2s;
    }
    .link-btn:hover { background: rgba(255, 255, 255, 0.12); border-color: var(--accent-cyan); }

    .container {
      max-width: 1440px; margin: 0 auto; padding: 24px;
      width: 100%; display: grid; grid-template-columns: 380px 1fr;
      gap: 24px; flex: 1;
    }
    @media (max-width: 1024px) { .container { grid-template-columns: 1fr; } }

    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px;
      backdrop-filter: blur(16px);
      box-shadow: var(--panel-glow);
      display: flex; flex-direction: column; gap: 16px;
    }

    .card-title {
      font-size: 15px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.8px;
      color: var(--text-muted);
      display: flex; align-items: center; justify-content: space-between;
    }

    .control-group { display: flex; flex-direction: column; gap: 8px; }
    .control-label {
      font-size: 12px; font-weight: 600;
      color: var(--text-muted);
      display: flex; justify-content: space-between;
    }

    select, input[type="text"], input[type="number"] {
      width: 100%;
      background: rgba(10, 15, 29, 0.8);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 10px 14px;
      color: var(--text-main);
      font-size: 14px;
      outline: none;
      transition: border-color 0.2s;
    }
    select:focus, input:focus { border-color: var(--accent-cyan); }

    .btn-row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .btn-row-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }

    button {
      cursor: pointer; padding: 12px 16px; border-radius: 10px;
      font-size: 13px; font-weight: 600; border: none;
      display: flex; align-items: center; justify-content: center; gap: 8px;
      transition: all 0.2s;
    }
    button:active { transform: scale(0.98); }

    .btn-primary {
      background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
      color: #fff;
      box-shadow: 0 4px 14px rgba(6, 182, 212, 0.35);
    }
    .btn-pause {
      background: rgba(245, 158, 11, 0.15);
      border: 1px solid rgba(245, 158, 11, 0.4);
      color: #fbbf24;
    }
    .btn-danger {
      background: rgba(244, 63, 94, 0.15);
      border: 1px solid rgba(244, 63, 94, 0.4);
      color: #fb7185;
    }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid var(--card-border);
      color: var(--text-main);
    }

    .telemetry-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
    .telemetry-box {
      background: rgba(10, 15, 29, 0.6);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 12px;
    }
    .tel-label { font-size: 11px; font-weight: 500; color: var(--text-muted); }
    .tel-val {
      font-size: 18px; font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
      color: var(--accent-cyan);
      margin-top: 4px;
    }

    input[type=range] { -webkit-appearance: none; width: 100%; background: transparent; }
    input[type=range]:focus { outline: none; }
    input[type=range]::-webkit-slider-runnable-track {
      width: 100%; height: 8px; cursor: pointer;
      background: rgba(255, 255, 255, 0.1);
      border-radius: 4px;
    }
    input[type=range]::-webkit-slider-thumb {
      height: 18px; width: 18px; border-radius: 50%;
      background: var(--accent-cyan); cursor: pointer;
      -webkit-appearance: none; margin-top: -5px;
      box-shadow: 0 0 10px rgba(6, 182, 212, 0.8);
    }

    .visualizer-column { display: flex; flex-direction: column; gap: 24px; }
    #sim-map {
      width: 100%; height: 480px;
      border-radius: 16px;
      border: 1px solid var(--card-border);
    }

    .log-box {
      background: #060911;
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 14px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: #94a3b8;
      height: 160px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .log-line { display: flex; gap: 10px; }
    .log-time { color: var(--accent-cyan); }

    .status-pill {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 4px 10px; border-radius: 20px;
      font-size: 12px; font-weight: 600; text-transform: uppercase;
    }
    .status-running { background: rgba(16, 185, 129, 0.15); color: #34d399; }
    .status-paused { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
    .status-idle { background: rgba(148, 163, 184, 0.15); color: #94a3b8; }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <div class="brand-icon">⚡</div>
      <div>
        <div class="brand-title">DUK Bus GPS Simulator</div>
        <div class="brand-subtitle">PORT 8050 • ISOLATED TEST ENGINE</div>
      </div>
    </div>
    <div class="header-links">
      <a href="http://localhost:5173" target="_blank" class="link-btn">Open Admin Dashboard (5173) ↗</a>
      <a href="http://localhost:5004/api/docs" target="_blank" class="link-btn">Backend API Docs (5004) ↗</a>
    </div>
  </header>

  <div class="container">
    
    <!-- LEFT COLUMN: CONTROLS & PARAMS -->
    <div style="display: flex; flex-direction: column; gap: 20px;">
      
      <div class="card">
        <div class="card-title">
          <span>Simulation Control</span>
          <span id="statusPill" class="status-pill status-idle"><span class="status-dot"></span><span id="statusText">IDLE</span></span>
        </div>

        <div class="control-group">
          <label class="control-label">Recorded Database Date</label>
          <select id="dateSelect">
            <option value="2026-06-12">2026-06-12 (Full Day + IIITMK Trip)</option>
          </select>
        </div>

        <div class="control-group">
          <label class="control-label">Trip Phase to Simulate</label>
          <select id="phaseSelect">
            <option value="all">🚀 All Day (Continuous Replay)</option>
            <option value="morning">🌅 Morning Trip (Central Poly → DUK)</option>
            <option value="unscheduled_midday">🔄 Unscheduled Midday Trips (DUK ↔ IIITMK)</option>
            <option value="midday_idle">🅿️ Midday Idle (Parked at DUK)</option>
            <option value="evening">🌇 Evening Return Trip (DUK → Central Poly)</option>
          </select>
        </div>

        <div class="control-group">
          <div class="control-label">
            <span>Playback Speed</span>
            <span id="speedVal" style="color:var(--accent-cyan); font-weight:700;">5.0x</span>
          </div>
          <input type="range" id="speedSlider" min="0.5" max="50" step="0.5" value="5" oninput="onSpeedChange(this.value)" />
        </div>

        <div class="btn-row">
          <button id="btnPlay" class="btn-primary" onclick="startSim()">▶ Start Simulation</button>
          <button id="btnPause" class="btn-pause" onclick="togglePause()">⏸ Pause</button>
        </div>

        <div class="btn-row-3">
          <button class="btn-secondary" onclick="stepSim()">⏭ Step 1x</button>
          <button class="btn-secondary" onclick="resetDay()">🔄 Reset Day</button>
          <button class="btn-danger" onclick="stopSim()">⏹ Stop</button>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Timeline Scrubber</div>
        <div class="control-group">
          <div class="control-label">
            <span id="pointProgress">Point 0 / 0</span>
            <span id="pctProgress">0%</span>
          </div>
          <input type="range" id="timelineSlider" min="0" max="100" step="0.1" value="0" oninput="onJumpTimeline(this.value)" />
        </div>
      </div>

      <div class="card">
        <div class="card-title">Live Telemetry</div>
        <div class="telemetry-grid">
          <div class="telemetry-box">
            <div class="tel-label">SPEED (KM/H)</div>
            <div id="telSpeed" class="tel-val">0.0</div>
          </div>
          <div class="telemetry-box">
            <div class="tel-label">PHASE</div>
            <div id="telPhase" class="tel-val" style="font-size:13px;">—</div>
          </div>
          <div class="telemetry-box">
            <div class="tel-label">LATITUDE</div>
            <div id="telLat" class="tel-val" style="font-size:14px;">8.53500</div>
          </div>
          <div class="telemetry-box">
            <div class="tel-label">LONGITUDE</div>
            <div id="telLon" class="tel-val" style="font-size:14px;">76.99080</div>
          </div>
        </div>
      </div>

    </div>

    <!-- RIGHT COLUMN: MAP & LOGS -->
    <div class="visualizer-column">
      <div class="card" style="flex:1;">
        <div class="card-title">
          <span>Live Replay Map Visualizer</span>
          <span style="font-size:12px; color:var(--accent-cyan);" id="pointCountBadge">0 pings recorded</span>
        </div>
        <div id="sim-map"></div>
      </div>

      <div class="card">
        <div class="card-title">Live Simulator Dispatch Logs</div>
        <div id="logBox" class="log-box">
          <div class="log-line"><span class="log-time">[00:00:00]</span> <span>Simulator initialized on port 8050. Ready.</span></div>
        </div>
      </div>
    </div>

  </div>

  <script>
    let map, busMarker, polyline;
    let trailCoords = [];
    let isPaused = false;
    let isRunning = false;

    function initMap() {
      map = L.map('sim-map', { zoomControl: false }).setView([8.5350, 76.9908], 13);
      L.control.zoom({ position: 'topright' }).addTo(map);

      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19
      }).addTo(map);

      polyline = L.polyline([], { color: '#3b82f6', weight: 5, opacity: 0.9 }).addTo(map);

      const busIcon = L.divIcon({
        className: 'custom-bus-pin',
        html: `<div style="background:#10b981; width:28px; height:28px; border-radius:50%; border:3px solid #ffffff; box-shadow:0 0 14px #10b981; display:flex; align-items:center; justify-content:center; color:#fff; font-size:14px;">🚌</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14]
      });

      busMarker = L.marker([8.5350, 76.9908], { icon: busIcon }).addTo(map);
    }

    async function loadDates() {
      try {
        const res = await fetch('/api/dates');
        const dates = await res.json();
        const sel = document.getElementById('dateSelect');
        if (dates.length > 0) {
          sel.innerHTML = dates.map(d => `<option value="${d.date}">${d.date} (${d.total_pings} pings • ${d.has_full_day ? 'Full Day' : 'Partial'})</option>`).join('');
        }
      } catch (e) {
        console.error('Error loading dates', e);
      }
    }

    async function startSim() {
      const date = document.getElementById('dateSelect').value;
      const phase = document.getElementById('phaseSelect').value;
      const speed = parseFloat(document.getElementById('speedSlider').value);

      trailCoords = [];
      polyline.setLatLngs([]);
      addLog(`Starting simulation for date ${date} [Phase: ${phase}] at ${speed}x speed...`);

      const res = await fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date, phase, speed, loop: false })
      });
      const data = await res.json();
      updateUI(data.details);
    }

    async function togglePause() {
      if (isPaused) {
        await fetch('/api/resume', { method: 'POST' });
        addLog('Simulation resumed.');
      } else {
        await fetch('/api/pause', { method: 'POST' });
        addLog('Simulation paused.');
      }
    }

    async function stopSim() {
      await fetch('/api/stop', { method: 'POST' });
      addLog('Simulation stopped.');
    }

    async function stepSim() {
      const res = await fetch('/api/step', { method: 'POST' });
      const data = await res.json();
      if (data.point) {
        addLog(`Stepped to point index ${data.details.current_index}: Lat ${data.point.lat}, Lon ${data.point.lon}`);
      }
      updateUI(data.details);
    }

    async function onSpeedChange(val) {
      document.getElementById('speedVal').innerText = `${val}x`;
      await fetch('/api/speed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed: parseFloat(val) })
      });
    }

    async function onJumpTimeline(pct) {
      const res = await fetch('/api/jump', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ percentage: parseFloat(pct) })
      });
      const data = await res.json();
      updateUI(data.details);
    }

    async function resetDay() {
      await fetch('/api/reset_day', { method: 'POST' });
      trailCoords = [];
      polyline.setLatLngs([]);
      addLog('Reset simulation state to start of day.');
    }

    function addLog(msg) {
      const box = document.getElementById('logBox');
      const time = new Date().toLocaleTimeString();
      const line = document.createElement('div');
      line.className = 'log-line';
      line.innerHTML = `<span class="log-time">[${time}]</span> <span>${msg}</span>`;
      box.appendChild(line);
      box.scrollTop = box.scrollHeight;
    }

    function updateUI(status) {
      if (!status) return;
      isRunning = status.is_running;
      isPaused = status.is_paused;

      const pill = document.getElementById('statusPill');
      const stText = document.getElementById('statusText');
      if (isRunning && !isPaused) {
        pill.className = 'status-pill status-running';
        stText.innerText = 'RUNNING';
      } else if (isPaused) {
        pill.className = 'status-pill status-paused';
        stText.innerText = 'PAUSED';
      } else {
        pill.className = 'status-pill status-idle';
        stText.innerText = 'IDLE';
      }

      document.getElementById('btnPause').innerText = isPaused ? '▶ Resume' : '⏸ Pause';
      document.getElementById('pointProgress').innerText = `Point ${status.current_index} / ${status.total_points}`;
      
      const pct = status.total_points > 0 ? ((status.current_index / status.total_points) * 100).toFixed(1) : 0;
      document.getElementById('pctProgress').innerText = `${pct}%`;
      document.getElementById('timelineSlider').value = pct;
      document.getElementById('pointCountBadge').innerText = `${status.total_points} pings loaded`;

      if (status.last_dispatched) {
        const pt = status.last_dispatched;
        document.getElementById('telSpeed').innerText = (pt.speed_kmh || 0).toFixed(1);
        document.getElementById('telLat').innerText = (pt.lat || 0).toFixed(5);
        document.getElementById('telLon').innerText = (pt.lon || 0).toFixed(5);
        document.getElementById('telPhase').innerText = status.selected_phase || 'all';

        if (pt.lat && pt.lon) {
          busMarker.setLatLng([pt.lat, pt.lon]);
          trailCoords.push([pt.lat, pt.lon]);
          polyline.setLatLngs(trailCoords);
          if (!map.getBounds().contains([pt.lat, pt.lon])) {
            map.panTo([pt.lat, pt.lon]);
          }
        }
      }
    }

    function connectWS() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      ws.onmessage = (e) => {
        try {
          const status = JSON.parse(e.data);
          updateUI(status);
        } catch (_) {}
      };
      ws.onclose = () => setTimeout(connectWS, 2000);
    }

    window.onload = () => {
      initMap();
      loadDates();
      connectWS();
      fetch('/api/status').then(r => r.json()).then(updateUI);
    };
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTML_CONTENT


if __name__ == "__main__":
    port = 8050
    print(f"============================================================")
    print(f"⚡ DUK GPS Simulator Control Center starting on Port {port}")
    print(f"👉 Open in browser: http://localhost:{port}")
    print(f"👉 Admin Dashboard:  http://localhost:5173")
    print(f"============================================================")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
