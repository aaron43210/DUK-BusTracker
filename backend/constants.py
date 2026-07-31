"""
constants.py — DUK Bus Tracker shared schedule constants.
Edit these values here when the trip schedule changes.
All IST offset and time-window logic imports from this file.
"""
from datetime import timedelta

# IST timezone offset (UTC+5:30)
IST_OFFSET = timedelta(hours=5, minutes=30)

# Trip time windows in minutes-since-midnight (IST)
MORNING_START_MINS = 420   # 07:00 AM — morning trip begins
MORNING_END_MINS   = 660   # 11:00 AM — morning trip expires
EVENING_START_MINS = 1050  # 05:30 PM — evening trip begins
EVENING_END_MINS   = 1230  # 08:30 PM — evening trip expires

# Waiting window — 1 hour before each scheduled trip
MORNING_WAIT_START = 360   # 06:00 AM
EVENING_WAIT_START = 990   # 04:30 PM
