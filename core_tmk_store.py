"""
core/tmk_store.py — System 3 Memory (SQLite-backed Task-Method-Knowledge)
Persistent store for learned adjustments and retrieval for dashboard history.
"""
import sqlite3
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_PATH = "linepilot_memory.db"
_lock = threading.Lock()

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    """Ensure the heuristics table exists. Call once at startup."""
    with _lock:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS heuristics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                deviation_class TEXT,
                capacity_delta REAL,
                estimated_savings REAL,
                heuristic_details TEXT,
                source_interval TEXT
            )
        """)
        conn.commit()
        conn.close()

def store_heuristic(
    *,
    deviation_class: str,
    capacity_delta: float,
    estimated_savings: float,
    heuristic_details: Optional[Dict[str, Any]] = None,
    source_interval: Optional[str] = None,
) -> None:
    """
    Persist a learned heuristic adjustment.
    Call this from the reflector after a successful heuristic application.
    """
    init_db()  # idempotent
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO heuristics
                (timestamp, deviation_class, capacity_delta, estimated_savings, heuristic_details, source_interval)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                deviation_class,
                capacity_delta,
                estimated_savings,
                json.dumps(heuristic_details) if heuristic_details else None,
                source_interval,
            ),
        )
        conn.commit()
        conn.close()

def fetch_recent_heuristics(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Return the most recent N heuristic entries for the dashboard history panel.
    """
    init_db()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "SELECT timestamp, deviation_class, capacity_delta, estimated_savings, heuristic_details, source_interval "
            "FROM heuristics ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {
                "timestamp": r[0],
                "deviation_class": r[1],
                "capacity_delta": r[2],
                "estimated_savings": r[3],
                "heuristic_details": json.loads(r[4]) if r[4] else None,
                "source_interval": r[5],
            }
            for r in rows
        ]