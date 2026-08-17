# 🚌 DUK Bus Tracker — Live Dashboard (Map + Route View)

A clean-room, simplified rebuild of the DUK Bus Tracker dashboard based on the
reference PDFs (`backend.pdf`, `pwafrontend.pdf`) and the screenshot colour
template. Built with **React + MapLibre GL** on the frontend and **FastAPI +
async SQLAlchemy** on the backend.

**Core behaviour (as requested):**

- The **Map view is always full-screen** — route polyline, stops and bus position.
- The **Route view (slide-in panel) opens ONLY while a trip is `active`**
  (`GET /trip_state` → `status === "active"`). Otherwise the map stays full with a
  small "Not in Service / Next trip …" chip.
- Backend contracts mirror the original: `/trip_state`, `/stops`, `/latest`,
  `/route_geometry`, `/current_trace`, `/eta`, `/gps`, `/ws/bus`, auth + admin.
- **DEMO_MODE** (default `true`): a GPS simulator drives the bus along the real
  Central Polytechnic ↔ DUK route (21 stops, seeded from the original `seed.py`),
  so the dashboard is fully alive with zero hardware.

---

## 🎨 Colour template (extracted from the reference screenshots)

| Token | Hex | Use |
|---|---|---|
| Cream canvas | `#FBF8F4` | app background |
| White | `#FFFFFF` | cards / panel |
| Periwinkle (primary) | `#5F88D4` / `#507DD0` | route line, next stop, buttons |
| Soft teal | `#3F887F` | visited stops, live status |
| Sky | `#57A5BF` | map water, live trail |
| Ink | `#1E2738` | headings |
| Muted | `#677489` | secondary text |
| Border | `#E3E8EF` | hairlines |
| Danger / Amber | `#DC2626` / `#D97706` | delays, connecting |

---

## 🗺️ Map (MapLibre GL)

- Basemap: **OpenFreeMap `positron`** (free, no API key), re-themed at runtime to
  the cream/sky palette above (background `#F6F0E4`, water `#BFD9E7`, parks sage).
- Layers: route casing + periwinkle route line, dashed sky live-trail, stop dots
  (teal = visited, periwinkle = next, white/ring = upcoming), stop labels.
- Live bus = DOM marker with rotating SVG + pulsing ring; pulsing marker on the
  **next stop**; auto-follow with a "Recentre" FAB when the user pans away.

## 🧭 Route view (only when trip active)

Slide-in panel (right on desktop, bottom-sheet on mobile):
- Trip header (`Morning Trip → Digital University Kerala`) + live status pill + delay chip
- Stats: **ETA** to destination · **speed km/h** · **stops done/total**
- Trip progress bar with km remaining
- Stop timeline: scheduled time, visited/next/upcoming states, START/END badges
- Footer: "Live · x s ago" + Recentre

---

## 🏗️ Architecture

```
┌───────────────────────────── Browser ─────────────────────────────┐
│  React (Vite) + MapLibre GL                                       │
│  App.jsx ── useTripState (10s) ──┐                                │
│          ── useBusPosition (2s) ─┤  → api/client.js (fetch)       │
│          ── useStops / useEta    ┘        │                        │
│  MapView (always on)  ·  RoutePanel (only when active)            │
└────────────────────────────────────────────┼──────────────────────┘
                                             │ same-origin (Vite proxy)
┌─────────────────────────────── FastAPI :8000 ─────────────────────┐
│  routers/                                                         │
│   tracking.py   trip_state · stops · latest · route_geometry     │
│                 current_trace · eta · history                     │
│   gps.py        POST /gps (API-key) · WS /ws/bus (push)           │
│   auth.py       register(OTP) · verify(JWT) · login · preferences │
│   admin.py      trip create / cancel / cancel-advance / revoke    │
│  services/                                                         │
│   bus_simulator.py  GPS simulator loop (demo) + device override   │
│   trip_logic.py     trip-state resolver (same rules as original)  │
│   ws_manager.py     WebSocket broadcast                           │
│  models.py · database.py · seed.py · constants.py · config.py     │
│  SQLite (aiosqlite) — swap DATABASE_URL for Postgres in prod      │
└────────────────────────────────────────────────────────────────────┘
```

---

## 🔌 API contracts

| Method | Path | Description |
|---|---|---|
| GET | `/trip_state` | `{trip, status, trip_id, late_by_minutes, cancellation_reason, next_trip_time, is_deviated, demo_mode}` — status ∈ `active, connecting, waiting, weekend, offline, completed, cancelled` |
| GET | `/stops` | ordered stops with morning/evening origin-destination role flags |
| GET | `/latest` | `{lat, lon, speed_kmh, bearing, traveled_km, remaining_km, progress_pct, visited_stops, total_stops, ist_time, …}` |
| GET | `/route_geometry` | GeoJSON FeatureCollection (LineString + stop points) |
| GET | `/current_trace` | recent trail `[[lon,lat],…]` |
| GET | `/eta?target_stop_id=` | `{stop_name, km, minutes, speed_kmh}` (default: direction destination) |
| GET | `/history/{YYYY-MM-DD}` | GPS rows for an IST date |
| POST | `/gps` | device upload (header `X-Api-Key` or body `api_key`) |
| WS | `/ws/bus` | live position push every 2 s |
| POST | `/register` `/verify` | OTP auth (JWT on verify) |
| POST | `/login` | admin login (from `.env`) |
| POST | `/trip/create` `/trip/cancel` `/trip/cancel-advance` `/trip/revoke-cancel` | admin trip management (`X-Admin-Token`) |

Swagger UI: `http://localhost:8000/docs`

---

## 🚀 Run it

```bash
# 1) backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # or edit .env
uvicorn main:app --host 0.0.0.0 --port 8000

# 2) frontend (new terminal)
cd frontend
npm install
npm run dev                     # http://localhost:5173 (proxies API → :8000)
```

### Mode switches (`backend/.env`)

- `DEMO_MODE=true` → always-active Morning trip + simulated bus (instant demo).
- `DEMO_MODE=false` → real time-window logic from `constants.py`
  (07:00–11:00 morning, 17:30–20:30 evening IST) and simulator at 30 km/h;
  a real device `POST /gps` (within 5 min) pauses the simulator automatically.
- `SIM_SPEED_KMH`, `SIM_TIME_SCALE` (demo acceleration) tune the simulator.

## 📁 Layout

```
duk-dashboard/
├── backend/            FastAPI (same structure as the reference backend)
│   ├── config.py  constants.py  database.py  models.py  seed.py  main.py
│   ├── routers/   tracking.py  gps.py  auth.py  admin.py
│   └── services/  bus_simulator.py  trip_logic.py  ws_manager.py
└── frontend/           React + Vite + MapLibre
    └── src/
        ├── App.jsx                    (master switch: map always, route panel when active)
        ├── api/client.js              (all backend calls)
        ├── hooks/                     (useTripState · useBusPosition · useStops · usePolling)
        ├── components/                (MapView · RoutePanel · HeaderCard · IdleChip · Legend)
        └── utils/                     (geo · format)
```
