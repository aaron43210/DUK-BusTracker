"""
test/run_test.py — Convenient test launcher from the root project directory.
"""
import os
import sys

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, backend_dir)

if __name__ == "__main__":
    import asyncio
    from tests.test_gps_simulator import run_tests
    asyncio.run(run_tests())
