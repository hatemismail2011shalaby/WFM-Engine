#!/usr/bin/env python3
"""
demo_telemetry.py — Pilot Simulation Script
Sends a continuous stream of interval telemetry to the LinePilot API,
proving the live closed‑loop engine for the pilot demo.
"""

import os
import time
import random
import requests
from datetime import datetime, timezone

API_BASE = os.environ.get("LINEPILOT_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("LINEPILOT_API_KEY", "pilot-demo-key-2025")
POLL_INTERVAL = int(os.environ.get("LINEPILOT_POLL_INTERVAL", "5"))

# Simulated interval profile – calls, agents, AHT, service level target
SCENARIOS = [
    {"calls": 42, "agents": 12, "aht": 180, "sl_target": 0.80},
    {"calls": 55, "agents": 12, "aht": 195, "sl_target": 0.80},
    {"calls": 38, "agents": 13, "aht": 170, "sl_target": 0.85},
    {"calls": 70, "agents": 11, "aht": 210, "sl_target": 0.75},
    {"calls": 48, "agents": 12, "aht": 185, "sl_target": 0.80},
]

def send_interval(scenario: dict):
    """Send one interval ingestion to the API."""
    payload = {
        "source": "pilot_sim",
        "payload": {
            "interval": datetime.now(timezone.utc).isoformat(),
            "calls": scenario["calls"],
            "agents": scenario["agents"],
            "avg_handle_time": scenario["aht"],
            "service_level_target": scenario["sl_target"],
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(
            f"{API_BASE}/ingest",
            json=payload,
            headers={"X-API-Key": API_KEY},
            timeout=2,
        )
        if resp.status_code == 200:
            print(f"✓ Ingested interval | calls={scenario['calls']} agents={scenario['agents']}")
        else:
            print(f"✗ API error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"✗ Connection failed: {e}")

def main():
    print("🚀 LinePilot Pilot Demo — Live Telemetry Stream")
    print(f"   API: {API_BASE} | Poll interval: {POLL_INTERVAL}s")
    print("   Press Ctrl+C to stop.\n")
    idx = 0
    while True:
        scenario = SCENARIOS[idx % len(SCENARIOS)]
        send_interval(scenario)
        idx += 1
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()