# 🚌 GPS Simulator & Test Suite Guide

This folder contains test scripts and runners to simulate real-time GPS movement and test the entire bus tracking lifecycle.

---

## 1. Web Control Dashboard (Easiest & Most Visual)
With your backend running on port `5004`, open your browser at:
👉 **[http://localhost:5004/api/v1/simulator/ui](http://localhost:5004/api/v1/simulator/ui)**

From this UI, you can:
- Select the **Date** (e.g. `2026-06-12` or `2026-06-22`).
- Select the **Trip Phase**:
  - `Morning Scheduled Trip` (07:00 – 09:30 to DUK)
  - `Midday Unscheduled Shuttle` (09:30 – 12:00 IIITMK / Technocity)
  - `Campus Idle / Parked` (12:00 – 15:00 Not In Service)
  - `Evening Scheduled Trip` (17:00 – 19:30 Return to City)
  - `Full Day Trajectory`
- Change speed multiplier (`1x`, `2x`, `5x`, `10x`, `20x`, `50x`).
- Step single points manually or scrub the progress bar.

---

## 2. Automated Test Suite
To verify the simulator engine and database queries:

```bash
cd /Users/aaronr/Desktop/BUS_TRACKER/backend
source /Users/aaronr/py311/bin/activate
python3 -m tests.test_gps_simulator
```

---

## 3. Terminal CLI Runner
To run a real-time live simulation directly from your terminal:

```bash
cd /Users/aaronr/Desktop/BUS_TRACKER/backend
source /Users/aaronr/py311/bin/activate

# Run morning trip at 5x speed:
python3 -m tests.run_simulation_cli --phase morning --speed 5

# Run midday unscheduled trip to IIITMK at 10x speed:
python3 -m tests.run_simulation_cli --phase unscheduled_midday --speed 10

# Run evening return trip at 5x speed:
python3 -m tests.run_simulation_cli --phase evening --speed 5

# Run complete day at 20x speed:
python3 -m tests.run_simulation_cli --phase all --speed 20
```

---

## 4. REST API Endpoints
You can also trigger simulations from any HTTP client (cURL, Postman, Frontend):

- `GET /api/v1/simulator/status`
- `GET /api/v1/simulator/dates`
- `POST /api/v1/simulator/start` (Body: `{"date": "2026-06-12", "phase": "morning", "speed_multiplier": 5.0}`)
- `POST /api/v1/simulator/pause`
- `POST /api/v1/simulator/resume`
- `POST /api/v1/simulator/step`
- `POST /api/v1/simulator/jump` (Body: `{"percentage": 50.0}`)
- `POST /api/v1/simulator/stop`
