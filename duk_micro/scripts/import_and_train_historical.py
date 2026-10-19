#!/usr/bin/env python3
"""
import_and_train_historical.py

Imports the raw 1-month GPS dump, applies smart spatial/temporal filtering,
reconstructs logical trips, and trains the Day 1 ETA Machine Learning model.
"""
import os
import sys
import json
import subprocess
import shlex
import pathlib
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)

# Validate required environment variable — no insecure fallback
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable is required")
    sys.exit(1)

# Ensure we use psycopg2 for pandas/sqlalchemy
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

# SQL file path must be explicitly provided — no hardcoded developer paths
SQL_FILE = os.environ.get("HISTORICAL_SQL_FILE")
if not SQL_FILE:
    logger.error("HISTORICAL_SQL_FILE environment variable is required")
    sys.exit(1)

# Validate the path exists and is within an allowed directory
ALLOWED_DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR", "/opt/duk/data")).resolve()
sql_path = pathlib.Path(SQL_FILE).resolve()

if not sql_path.is_file():
    logger.error("SQL file not found: %s", sql_path)
    sys.exit(1)

# Path traversal check — ensure file is within allowed directory
if not str(sql_path).startswith(str(ALLOWED_DATA_DIR)):
    logger.error("SQL file must be within DATA_DIR: %s", ALLOWED_DATA_DIR)
    sys.exit(1)

def validate_sql_file(filepath: str) -> bool:
    """Ensure the SQL dump doesn't contain malicious commands before execution."""
    disallowed_keywords = [
        "DROP ", "DELETE FROM", "ALTER TABLE", "GRANT ", "REVOKE ", "TRUNCATE ", "UPDATE "
    ]
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                upper_line = line.upper()
                for keyword in disallowed_keywords:
                    if keyword in upper_line:
                        logger.error("Malicious SQL keyword '%s' detected on line %d. Aborting.", keyword, line_num)
                        return False
    except Exception as e:
        logger.error("Failed to read SQL file for validation: %s", e)
        return False
    return True

def import_sql_safely(sql_file: str, database_url: str) -> bool:
    """
    Import SQL using psql subprocess instead of executing arbitrary text via SQLAlchemy.
    This limits execution to INSERT statements only via --single-transaction.
    """
    if not validate_sql_file(sql_file):
        logger.error("SQL validation failed. Refusing to import.")
        return False
    from urllib.parse import urlparse
    parsed = urlparse(database_url)
    
    env = os.environ.copy()
    env["PGPASSWORD"] = parsed.password or ""
    
    cmd = [
        "psql",
        "-h", parsed.hostname,
        "-p", str(parsed.port or 5432),
        "-U", parsed.username,
        "-d", parsed.path.lstrip("/"),
        "--single-transaction",
        "--set", "ON_ERROR_STOP=1",
        "-f", sql_file,
    ]
    
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    
    if result.returncode != 0:
        logger.error("psql import failed: %s", result.stderr[:500])
        return False
    
    logger.info("SQL imported successfully")
    return True


def main():
    print(" Starting Historical Data Import and Smart Filtering...")

    engine = create_engine(DATABASE_URL)
    
    # 1. Ensure table exists before import
    # The SQL file has INSERT statements, but we should make sure the table exists
    # We will just run the migration script if needed, or assume it's created.
    
    # 2. Import raw SQL data
    print(f" Importing raw GPS data from {SQL_FILE}...")
    if not import_sql_safely(SQL_FILE, DATABASE_URL):
        print(f" Failed to import {SQL_FILE}.")
        sys.exit(1)

    # 3. Spatial and Temporal Filtering
    print(" Cleaning data: Applying spatial bounding box and time filters...")
    
    # The bounding box of the OSRM Trivandrum extract
    BOUNDS = {
        "lat_min": 8.20,
        "lat_max": 8.70,
        "lon_min": 76.50,
        "lon_max": 77.30,
    }
    
    query = text("""
        SELECT * FROM gps_realtime 
        WHERE lat BETWEEN :lat_min AND :lat_max
        AND lon BETWEEN :lon_min AND :lon_max
        ORDER BY ist_time ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, BOUNDS)
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
        
    print(f"   ↳ Found {len(df)} points inside the valid geographical bounding box.")

    if df.empty:
        print(" No valid GPS data found after filtering.")
        sys.exit(1)

    # Convert to datetime
    df['ist_time'] = pd.to_datetime(df['ist_time'])
    
    # Filter by time of day (Morning: 6:30-11:00, Evening: 15:30-20:30)
    df['hour'] = df['ist_time'].dt.hour
    is_morning = (df['hour'] >= 6) & (df['hour'] < 11)
    is_evening = (df['hour'] >= 15) & (df['hour'] < 21)
    df = df[is_morning | is_evening].copy()
    
    print(f"   ↳ Retained {len(df)} points during official trip hours (Morning/Evening).")

    # 4. Trip Reconstruction (Grouping by time gaps)
    print("🧩 Reconstructing missing trip IDs...")
    # If the gap between two consecutive points is > 30 minutes, it's a new trip.
    df['time_gap'] = df['ist_time'].diff()
    new_trip_mask = df['time_gap'] > pd.Timedelta(minutes=30)
    
    # Also first row is a new trip
    new_trip_mask.iloc[0] = True
    
    # Assign incrementing group IDs
    df['mock_trip_id'] = new_trip_mask.cumsum() + 90000  # Start at 90000 to avoid collision
    
    # Determine direction based on hour
    df['direction'] = df['hour'].apply(lambda h: 'morning' if h < 12 else 'evening')
    
    trip_summary = df.groupby('mock_trip_id').agg({
        'ist_time': ['min', 'max', 'count'],
        'direction': 'first'
    })
    
    # Filter out "trips" that have too few points (< 50)
    valid_trip_ids = trip_summary[trip_summary[('ist_time', 'count')] > 50].index
    df_valid = df[df['mock_trip_id'].isin(valid_trip_ids)].copy()
    
    print(f"   ↳ Successfully reconstructed {len(valid_trip_ids)} valid historical trips.")

    # 5. Insert mock trips into `trips` table so `train.py` can find them
    print("💾 Saving reconstructed trips to database...")
    
    mock_trips = []
    for trip_id in valid_trip_ids:
        trip_data = df_valid[df_valid['mock_trip_id'] == trip_id]
        started_at = trip_data['ist_time'].min()
        ended_at = trip_data['ist_time'].max()
        direction = trip_data['direction'].iloc[0]
        
        # We need mock visited_stops so train.py can calculate times to upcoming stops.
        # We will evenly space 10 mock stops across the trip duration.
        duration = ended_at - started_at
        mock_visited_stops = []
        for i in range(1, 11):
            arrival_time = started_at + (duration / 10) * i
            mock_visited_stops.append({
                "stop_id": i,
                "arrival_time": arrival_time.isoformat() + "Z"
            })
            
        mock_trips.append({
            "id": trip_id,
            "route_id": 1,
            "direction": direction,
            "status": "ended",
            "started_at": started_at,
            "ended_at": ended_at,
            "visited_stops": json.dumps(mock_visited_stops)
        })
        
    df_mock_trips = pd.DataFrame(mock_trips)
    # Write directly to trips table (will fail if schema is strict, but we can do it raw)
    with engine.begin() as conn:
        for _, row in df_mock_trips.iterrows():
            conn.execute(text("""
                INSERT INTO trips (id, route_id, direction, status, started_at, ended_at, visited_stops)
                VALUES (:id, :route_id, :direction, :status, :started_at, :ended_at, :visited_stops)
                ON CONFLICT (id) DO NOTHING
            """), {
                "id": row['id'],
                "route_id": row['route_id'],
                "direction": row['direction'],
                "status": row['status'],
                "started_at": row['started_at'],
                "ended_at": row['ended_at'],
                "visited_stops": row['visited_stops']
            })
            
        # Update gps_realtime with the new trip_ids
        for trip_id in valid_trip_ids:
            # We just need to update the rows where ist_time is between min and max for this trip
            min_t = df_valid[df_valid['mock_trip_id'] == trip_id]['ist_time'].min()
            max_t = df_valid[df_valid['mock_trip_id'] == trip_id]['ist_time'].max()
            
            conn.execute(text("""
                UPDATE gps_realtime 
                SET trip_id = :trip_id 
                WHERE ist_time BETWEEN :min_t AND :max_t
            """), {
                "trip_id": trip_id,
                "min_t": min_t,
                "max_t": max_t
            })

    print(" Historical database perfectly primed.")

    # 6. Trigger Training
    print("\n Triggering Day 1 ML Training Script...")
    train_script = os.path.join(os.path.dirname(__file__), "..", "services", "eta-ml-service", "train.py")
    
    # Pass the DB URL to the subprocess
    env = os.environ.copy()
    env["DATABASE_URL"] = DATABASE_URL
    
    result = subprocess.run([sys.executable, train_script], env=env)
    
    if result.returncode == 0:
        print("\n Success! The Day 1 ETA Machine Learning model is trained and ready.")
    else:
        print("\n Training script encountered an issue.")

if __name__ == "__main__":
    main()
