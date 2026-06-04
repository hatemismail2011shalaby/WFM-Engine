# core/tmk_store.py
# ─────────────────────────────────────────────────────────────────────────────
# METACOGNITIVE WFM ENGINE — Task-Method-Knowledge (TMK) Memory Store
# Responsibilities:
#   1. Persist curated heuristic entries to SQLite (zero external dependencies)
#   2. Load active TMK entries scoped to a given timestamp (dow + hour)
#   3. Update confidence scores after each interval validation
#   4. Merge duplicate heuristics to avoid compounding adjustments
#   5. Expose a full audit log of all heuristic applications
#   6. Provide clean read/write interface consumed by Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional

from core.models import DeviationClass, TMKEntry

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: CONSTANTS & SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_DB_PATH: str = "data/tmk_memory.db"

# SQLite DDL — executed once on first connection
TMK_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS tmk_entries (
    entry_id              TEXT PRIMARY KEY,
    created_at            TEXT NOT NULL,
    last_applied          TEXT,
    apply_count           INTEGER DEFAULT 0,
    day_of_week           INTEGER,          -- NULL = applies every day
    hour_of_day           INTEGER,          -- NULL = applies every hour
    deviation_class       TEXT NOT NULL,
    heuristic_text        TEXT NOT NULL,
    shrinkage_adjustment  REAL DEFAULT 0.0,
    aht_adjustment_pct    REAL DEFAULT 0.0,
    volume_adjustment_pct REAL DEFAULT 0.0,
    confidence_score      REAL DEFAULT 1.0,
    times_validated       INTEGER DEFAULT 0,
    times_failed          INTEGER DEFAULT 0
);
"""

TMK_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS tmk_audit_log (
    audit_id      TEXT PRIMARY KEY,
    entry_id      TEXT NOT NULL,
    interval_id   TEXT NOT NULL,
    applied_at    TEXT NOT NULL,
    outcome       TEXT NOT NULL,       -- 'VALIDATED' | 'FAILED' | 'APPLIED'
    notes         TEXT
);
"""


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: CONNECTION MANAGER
# Thread-safe context manager for SQLite connections.
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def get_connection(db_path: str = DEFAULT_DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager for SQLite connections.
    Ensures tables exist on first use.
    Commits on clean exit, rolls back on exception.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")   # Write-Ahead Logging for concurrency

    try:
        conn.execute(TMK_TABLE_DDL)
        conn.execute(TMK_AUDIT_DDL)
        conn.commit()
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("TMK DB transaction rolled back: %s", e)
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: SERIALIZATION HELPERS
# Convert between TMKEntry Pydantic models and SQLite row dicts.
# ─────────────────────────────────────────────────────────────────────────────

def _entry_to_row(entry: TMKEntry) -> dict:
    """Serialize a TMKEntry to a flat dict for SQLite insertion."""
    return {
        "entry_id":              entry.entry_id,
        "created_at":            entry.created_at.isoformat(),
        "last_applied":          entry.last_applied.isoformat() if entry.last_applied else None,
        "apply_count":           entry.apply_count,
        "day_of_week":           entry.day_of_week,
        "hour_of_day":           entry.hour_of_day,
        "deviation_class":       entry.deviation_class.value,
        "heuristic_text":        entry.heuristic_text,
        "shrinkage_adjustment":  entry.shrinkage_adjustment,
        "aht_adjustment_pct":    entry.aht_adjustment_pct,
        "volume_adjustment_pct": entry.volume_adjustment_pct,
        "confidence_score":      entry.confidence_score,
        "times_validated":       entry.times_validated,
        "times_failed":          entry.times_failed,
    }


def _row_to_entry(row: sqlite3.Row) -> TMKEntry:
    """Deserialize a SQLite row into a TMKEntry Pydantic model."""
    return TMKEntry(
        entry_id=row["entry_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_applied=(
            datetime.fromisoformat(row["last_applied"])
            if row["last_applied"] else None
        ),
        apply_count=row["apply_count"],
        day_of_week=row["day_of_week"],
        hour_of_day=row["hour_of_day"],
        deviation_class=DeviationClass(row["deviation_class"]),
        heuristic_text=row["heuristic_text"],
        shrinkage_adjustment=row["shrinkage_adjustment"],
        aht_adjustment_pct=row["aht_adjustment_pct"],
        volume_adjustment_pct=row["volume_adjustment_pct"],
        confidence_score=row["confidence_score"],
        times_validated=row["times_validated"],
        times_failed=row["times_failed"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: WRITE OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

def write_tmk_entry(
    entry: TMKEntry,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """
    Persist a new TMKEntry to the store.

    Merge logic:
        If an entry with the same (day_of_week, hour_of_day, deviation_class)
        already exists, MERGE adjustments by weighted averaging rather than
        inserting a duplicate. This prevents compounding overcorrections.

    Args:
        entry:   TMKEntry to persist.
        db_path: SQLite database path.
    """
    with get_connection(db_path) as conn:
        # ── Check for existing entry with same scope ───────────────────────
        existing_row = conn.execute(
            """
            SELECT * FROM tmk_entries
            WHERE deviation_class = ?
              AND (day_of_week IS ? OR (day_of_week IS NULL AND ? IS NULL))
              AND (hour_of_day  IS ? OR (hour_of_day  IS NULL AND ? IS NULL))
            LIMIT 1
            """,
            (
                entry.deviation_class.value,
                entry.day_of_week, entry.day_of_week,
                entry.hour_of_day, entry.hour_of_day,
            ),
        ).fetchone()

        if existing_row:
            existing = _row_to_entry(existing_row)
            # Weighted average merge — new entry weight = 0.4, existing = 0.6
            w_new, w_old = 0.4, 0.6
            merged_shrinkage = round(
                w_old * existing.shrinkage_adjustment + w_new * entry.shrinkage_adjustment, 4
            )
            merged_aht = round(
                w_old * existing.aht_adjustment_pct + w_new * entry.aht_adjustment_pct, 4
            )
            merged_volume = round(
                w_old * existing.volume_adjustment_pct + w_new * entry.volume_adjustment_pct, 4
            )
            merged_confidence = round(
                w_old * existing.confidence_score + w_new * entry.confidence_score, 4
            )

            conn.execute(
                """
                UPDATE tmk_entries SET
                    shrinkage_adjustment  = ?,
                    aht_adjustment_pct    = ?,
                    volume_adjustment_pct = ?,
                    confidence_score      = ?,
                    heuristic_text        = ?,
                    last_applied          = ?
                WHERE entry_id = ?
                """,
                (
                    merged_shrinkage,
                    merged_aht,
                    merged_volume,
                    merged_confidence,
                    entry.heuristic_text,
                    datetime.utcnow().isoformat(),
                    existing.entry_id,
                ),
            )
            logger.info(
                "TMK MERGED [%s]: shrinkage=%.3f | aht=%.3f | vol=%.3f",
                existing.entry_id[:8],
                merged_shrinkage,
                merged_aht,
                merged_volume,
            )
        else:
            # Fresh insert
            row = _entry_to_row(entry)
            conn.execute(
                """
                INSERT INTO tmk_entries (
                    entry_id, created_at, last_applied, apply_count,
                    day_of_week, hour_of_day, deviation_class, heuristic_text,
                    shrinkage_adjustment, aht_adjustment_pct, volume_adjustment_pct,
                    confidence_score, times_validated, times_failed
                ) VALUES (
                    :entry_id, :created_at, :last_applied, :apply_count,
                    :day_of_week, :hour_of_day, :deviation_class, :heuristic_text,
                    :shrinkage_adjustment, :aht_adjustment_pct, :volume_adjustment_pct,
                    :confidence_score, :times_validated, :times_failed
                )
                """,
                row,
            )
            logger.info(
                "TMK INSERTED [%s]: %s",
                entry.entry_id[:8], entry.heuristic_text,
            )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: READ OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

def load_active_tmk_entries(
    timestamp: datetime,
    db_path: str = DEFAULT_DB_PATH,
    min_confidence: float = 0.3,
) -> List[TMKEntry]:
    """
    Load all TMK entries active for the given timestamp.

    Scope matching rules:
        - day_of_week IS NULL  → applies every day
        - hour_of_day IS NULL  → applies every hour
        - Both set             → applies only at that day + hour
        - confidence_score ≥ min_confidence (default 0.3)

    Args:
        timestamp:      Interval timestamp to match against.
        db_path:        SQLite database path.
        min_confidence: Minimum confidence score to include entry.

    Returns:
        List of matching TMKEntry objects, ordered by confidence DESC.
    """
    dow  = timestamp.weekday()
    hour = timestamp.hour

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM tmk_entries
            WHERE confidence_score >= ?
              AND (day_of_week IS NULL OR day_of_week = ?)
              AND (hour_of_day  IS NULL OR hour_of_day  = ?)
            ORDER BY confidence_score DESC
            """,
            (min_confidence, dow, hour),
        ).fetchall()

    entries = [_row_to_entry(row) for row in rows]
    logger.debug(
        "Loaded %d TMK entries for %s (dow=%d, hour=%d)",
        len(entries), timestamp.isoformat(), dow, hour,
    )
    return entries


def get_all_tmk_entries(
    db_path: str = DEFAULT_DB_PATH,
) -> List[TMKEntry]:
    """Return all TMK entries in the store. Used by dashboard and audit."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tmk_entries ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_entry(row) for row in rows]


def get_tmk_entry_by_id(
    entry_id: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[TMKEntry]:
    """Fetch a single TMK entry by its UUID."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM tmk_entries WHERE entry_id = ?", (entry_id,)
        ).fetchone()
    return _row_to_entry(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: CONFIDENCE UPDATER
# Called by Orchestrator after each interval to validate or penalize entries.
# ─────────────────────────────────────────────────────────────────────────────

def update_tmk_confidence(
    entry_id: str,
    validated: bool,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """
    Update the confidence score of a TMK entry after it was applied.

    Validation logic:
        - If the interval that used this heuristic had deviation < 5%
          (i.e., heuristic worked): increment times_validated, boost confidence.
        - If deviation still > 5% after applying heuristic:
          increment times_failed, decay confidence.

    Confidence update formula:
        new_confidence = old_confidence × 1.05   if validated
        new_confidence = old_confidence × 0.85   if failed
        Clamped to [0.0, 1.0].

    Args:
        entry_id:  UUID of the TMK entry to update.
        validated: True if heuristic improved outcome; False if failed.
        db_path:   SQLite database path.
    """
    boost  = 1.05 if validated else 0.85
    col_v  = "times_validated" if validated else "times_failed"

    with get_connection(db_path) as conn:
        conn.execute(
            f"""
            UPDATE tmk_entries SET
                confidence_score = MIN(1.0, MAX(0.0, confidence_score * ?)),
                {col_v}          = {col_v} + 1,
                last_applied     = ?
            WHERE entry_id = ?
            """,
            (boost, datetime.utcnow().isoformat(), entry_id),
        )
    logger.info(
        "TMK confidence updated [%s]: validated=%s boost=×%.2f",
        entry_id[:8], validated, boost,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: AUDIT LOG WRITER
# ─────────────────────────────────────────────────────────────────────────────

def write_tmk_audit(
    entry_id: str,
    interval_id: str,
    outcome: str,
    notes: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """
    Write an audit log record for a TMK entry application.

    Args:
        entry_id:    TMK entry UUID.
        interval_id: Interval record UUID.
        outcome:     One of 'APPLIED', 'VALIDATED', 'FAILED'.
        notes:       Optional free-text notes.
        db_path:     SQLite database path.
    """
    import uuid as _uuid
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO tmk_audit_log (audit_id, entry_id, interval_id, applied_at, outcome, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(_uuid.uuid4()),
                entry_id,
                interval_id,
                datetime.utcnow().isoformat(),
                outcome,
                notes,
            ),
        )
    logger.debug("TMK audit written: entry=%s outcome=%s", entry_id[:8], outcome)


def get_audit_log(
    entry_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
    limit: int = 200,
) -> List[dict]:
    """
    Retrieve audit log records.
    If entry_id provided, filters to that entry only.
    Returns list of plain dicts for dashboard rendering.
    """
    with get_connection(db_path) as conn:
        if entry_id:
            rows = conn.execute(
                """
                SELECT * FROM tmk_audit_log
                WHERE entry_id = ?
                ORDER BY applied_at DESC LIMIT ?
                """,
                (entry_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tmk_audit_log ORDER BY applied_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    return [dict(row) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8: STORE RESET (TEST / HITL USE ONLY)
# ─────────────────────────────────────────────────────────────────────────────

def reset_tmk_store(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Wipe all TMK entries and audit logs.
    USE ONLY: in test environments or after HITL-authorized full reset.
    Logs a warning — this action is irreversible.
    """
    logger.warning("TMK STORE RESET INITIATED. All heuristics will be deleted.")
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM tmk_entries;")
        conn.execute("DELETE FROM tmk_audit_log;")
    logger.warning("TMK STORE RESET COMPLETE.")