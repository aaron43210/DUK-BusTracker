"""
scripts/fetch_live_gps.py — Fetch and watch live GPS points directly from Supabase.
"""
import time
import requests


import os

SUPABASE_URL = "https://mtkdzcdzxtfjwpgnpujc.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "your_supabase_key_here")
TABLE_NAME = "gps_realtime"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def fetch_latest_points(limit=10):
    url = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=*&order=id.desc&limit={limit}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"❌ Error fetching data: {response.status_code} - {response.text}")
        return []

def main():
    print("=" * 70)
    print(f"🛰️  Fetching live GPS stream from Supabase ({TABLE_NAME})...")
    print("=" * 70)

    last_seen_id = None

    while True:
        rows = fetch_latest_points(limit=5)
        if rows:
            for row in reversed(rows):
                row_id = row.get("id")
                if last_seen_id is None or row_id > last_seen_id:
                    created_at = row.get("created_at", "N/A")
                    event = row.get("event", "N/A")
                    lat = row.get("lat")
                    lon = row.get("lon")
                    speed = row.get("speed")

                    if lat is not None and lon is not None:
                        print(f"[{created_at}] ID #{row_id:<4} | EVENT: {event:<8} | 📍 LAT: {lat:.6f} | LON: {lon:.6f} | ⚡ SPEED: {speed} km/h")
                    else:
                        print(f"[{created_at}] ID #{row_id:<4} | EVENT: {event:<8} | (No GPS Fix)")
                    
                    last_seen_id = row_id

        time.sleep(1.5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
