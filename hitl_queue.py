# core/hitl_queue.py
# ─────────────────────────────────────────────────────────────────────────────
# METACOGNITIVE WFM ENGINE — Human-in-the-Loop (HITL) Review Queue
# Responsibilities:
#   1. Persist HITL escalation events to SQLite with full context
#   2. Provide a structured queue interface for human reviewers
#   3. Accept resolution inputs and write resolution records
#   4. Expose queue depth and SLA breach metrics for dashboard
#   5. Enforce halt of automated execution on active unresolved events
#   6. Notify via configurable webhook (Slack / Teams / email stub)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional
from contextlib import contextmanager
import sqlite3

from core.models import (
    HITLEvent,
    HITLTriggerReason,
    IntervalRecord,
    IntervalStatus,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_HITL_DB_PATH    : str   = "data/hitl_queue.db"
HITL_SLA_HOURS          : int   = 2      # Unresolved events older than N hours = SLA breach
MAX_UNRESOLVED_BEFORE_HALT: int = 3      # Halt automation if N+ unresolved events exist


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: DATABASE SCHEMA & CONNECTION
# ─────────────────────────────────────────────────────────────────────────────

HITL_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS hitl_events (
    event_id            TEXT PRIMARY KEY,
    triggered_at        TEXT NOT NULL,
    resolved_at         TEXT,
    resolved_by         TEXT,
    trigger_reason      TEXT NOT NULL,
    interval_record_json TEXT NOT NULL,
    context_summary     TEXT NOT NULL,
    recommended_action  TEXT,
    resolution_notes    TEXT,
    is_resolved         INTEGER DEFAULT 0,
    webhook_sent        INTEGER DEFAULT 0
);
"""

HITL_COMMENTS_DDL = """
CREATE TABLE IF NOT EXISTS hitl_comments (
    comment_id   TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL,
    authored_by  TEXT NOT NULL,
    authored_at  TEXT NOT NULL,
    body         TEXT NOT NULL
);
"""


@contextmanager
def _get_connection(
    db_path: str = DEFAULT_HITL_DB_PATH,
):
    """Thread-safe SQLite context manager with WAL mode."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    try:
        conn.execute(HITL_TABLE_DDL)
        conn.execute(HITL_COMMENTS_DDL)
        conn.commit()
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("HITL DB transaction rolled back: %s", e)
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: SERIALIZATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_interval_record(record: IntervalRecord) -> str:
    """
    Serialize an IntervalRecord to a JSON string for SQLite storage.
    Handles nested Pydantic models and datetime objects.
    """
    return record.model_dump_json()


def _deserialize_interval_record(raw_json: str) -> IntervalRecord:
    """Reconstruct an IntervalRecord from its stored JSON string."""
    return IntervalRecord.model_validate_json(raw_json)


def _row_to_hitl_event(row: sqlite3.Row) -> HITLEvent:
    """Deserialize a SQLite row into a HITLEvent Pydantic model."""
    return HITLEvent(
        event_id=row["event_id"],
        triggered_at=datetime.fromisoformat(row["triggered_at"]),
        resolved_at=(
            datetime.fromisoformat(row["resolved_at"])
            if row["resolved_at"] else None
        ),
        resolved_by=row["resolved_by"],
        trigger_reason=HITLTriggerReason(row["trigger_reason"]),
        interval_record=_deserialize_interval_record(row["interval_record_json"]),
        context_summary=row["context_summary"],
        recommended_action=row["recommended_action"],
        resolution_notes=row["resolution_notes"],
        is_resolved=bool(row["is_resolved"]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: WEBHOOK NOTIFIER
# Sends structured alert payloads to an external notification endpoint.
# Supports Slack-compatible JSON payloads. Swap body for Teams/PagerDuty.
# ─────────────────────────────────────────────────────────────────────────────

def _send_webhook_notification(
    event: HITLEvent,
    webhook_url: Optional[str] = None,
) -> bool:
    """
    POST a structured HITL alert to a webhook URL.

    Payload format is Slack Block Kit compatible.
    If webhook_url is None, logs the payload and returns False (dry-run mode).

    Args:
        event:       The HITLEvent to notify about.
        webhook_url: Target webhook URL (Slack / Teams / custom).

    Returns:
        True if webhook delivered successfully, False otherwise.
    """
    payload = {
        "text": f":rotating_light: *HITL ESCALATION* | {event.trigger_reason.value}",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚨 WFM HITL Alert: {event.trigger_reason.value}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Event ID:*\n`{event.event_id[:8]}`"},
                    {"type": "mrkdwn", "text": f"*Triggered At:*\n{event.triggered_at.isoformat()}"},
                    {"type": "mrkdwn", "text": f"*Reason:*\n{event.trigger_reason.value}"},
                    {"type": "mrkdwn", "text": f"*Interval:*\n{event.interval_record.timestamp.isoformat()}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Context:*\n{event.context_summary}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Recommended Action:*\n"
                        f"{event.recommended_action or '_No recommendation generated._'}"
                    ),
                },
            },
        ],
    }

    if not webhook_url:
        logger.warning(
            "HITL webhook URL not configured. Dry-run payload:\n%s",
            json.dumps(payload, indent=2),
        )
        return False

    try:
        import urllib.request
        data    = json.dumps(payload).encode("utf-8")
        req     = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            if status == 200:
                logger.info("HITL webhook delivered: event=%s", event.event_id[:8])
                return True
            else:
                logger.error("Webhook returned HTTP %d for event=%s", status, event.event_id[:8])
                return False
    except Exception as e:
        logger.error("Webhook delivery failed for event=%s: %s", event.event_id[:8], e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: RECOMMENDED ACTION GENERATOR
# Generates a human-readable recommended action string from trigger context.
# ─────────────────────────────────────────────────────────────────────────────

def _generate_recommended_action(
    trigger_reason: HITLTriggerReason,
    record: IntervalRecord,
) -> str:
    """
    Generate a context-aware recommended action string for the reviewer.

    Args:
        trigger_reason: Why HITL was triggered.
        record:         The IntervalRecord at time of escalation.

    Returns:
        Human-readable string with specific guidance for the reviewer.
    """
    ts   = record.timestamp.strftime("%H:%M on %A %d %b %Y")
    gap  = (
        record.erlang_output.agents_required_net - record.erlang_output.agents_required_raw
        if record.erlang_output else 0
    )
    net  = record.erlang_output.agents_required_net if record.erlang_output else "N/A"
    sla  = (
        f"{record.erlang_output.predicted_sla_pct * 100:.1f}%"
        if record.erlang_output else "N/A"
    )

    if trigger_reason == HITLTriggerReason.UNCORRECTABLE_LOOP:
        return (
            f"UNCORRECTABLE LOOP at {ts}. "
            f"Automated corrections failed 3+ consecutive intervals. "
            f"Actions required: (1) Review TMK heuristics for corrupted entries. "
            f"(2) Manually inspect call volume feed for data pipeline errors. "
            f"(3) Authorize reset of LoopDetector via /api/hitl/resolve endpoint. "
            f"Predicted SLA at time of halt: {sla}."
        )

    elif trigger_reason == HITLTriggerReason.CAPACITY_DELTA_EXCEED:
        return (
            f"CAPACITY DELTA >20% at {ts}. "
            f"Engine requires {net} net agents — exceeds safe auto-adjustment threshold. "
            f"Actions required: (1) Confirm staffing availability with WFM team. "
            f"(2) Authorize emergency overtime or contractor deployment if needed. "
            f"(3) Manually approve or override the staffing recommendation in the dashboard. "
            f"Shrinkage-adjusted gap: {gap} agents above raw requirement."
        )

    elif trigger_reason == HITLTriggerReason.MANUAL_ESCALATION:
        return (
            f"MANUAL ESCALATION at {ts}. "
            f"A supervisor or automated rule triggered this review. "
            f"Actions required: (1) Review attached interval record. "
            f"(2) Determine if forecast model requires retraining. "
            f"(3) Clear this event and document resolution notes."
        )

    return "Review interval record and clear this event when resolved."


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: CORE QUEUE OPERATIONS — WRITE
# ─────────────────────────────────────────────────────────────────────────────

def enqueue_hitl_event(
    trigger_reason: HITLTriggerReason,
    record: IntervalRecord,
    context_summary: str,
    webhook_url: Optional[str] = None,
    db_path: str = DEFAULT_HITL_DB_PATH,
) -> HITLEvent:
    """
    Create and persist a new HITL escalation event.

    Steps:
        1. Build HITLEvent with generated recommended action.
        2. Persist to SQLite hitl_events table.
        3. Attempt webhook notification.
        4. Return the persisted HITLEvent.

    Args:
        trigger_reason:  Why automation is halting.
        record:          IntervalRecord at time of escalation.
        context_summary: Human-readable explanation of the trigger.
        webhook_url:     Optional Slack/Teams webhook for notification.
        db_path:         SQLite database path.

    Returns:
        Persisted HITLEvent.
    """
    recommended = _generate_recommended_action(trigger_reason, record)

    event = HITLEvent(
        trigger_reason=trigger_reason,
        interval_record=record,
        context_summary=context_summary,
        recommended_action=recommended,
    )

    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO hitl_events (
                event_id, triggered_at, resolved_at, resolved_by,
                trigger_reason, interval_record_json, context_summary,
                recommended_action, resolution_notes, is_resolved, webhook_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.triggered_at.isoformat(),
                None,
                None,
                event.trigger_reason.value,
                _serialize_interval_record(record),
                context_summary,
                recommended,
                None,
                0,
                0,
            ),
        )

    logger.error(
        "HITL EVENT ENQUEUED [%s]: reason=%s | interval=%s",
        event.event_id[:8],
        trigger_reason.value,
        record.timestamp.isoformat(),
    )

    # Attempt webhook delivery
    delivered = _send_webhook_notification(event, webhook_url)
    if delivered:
        with _get_connection(db_path) as conn:
            conn.execute(
                "UPDATE hitl_events SET webhook_sent = 1 WHERE event_id = ?",
                (event.event_id,),
            )

    return event


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: CORE QUEUE OPERATIONS — READ
# ─────────────────────────────────────────────────────────────────────────────

def get_unresolved_events(
    db_path: str = DEFAULT_HITL_DB_PATH,
) -> List[HITLEvent]:
    """
    Return all unresolved HITL events, ordered oldest-first.
    Used by the Orchestrator automation-halt check and the dashboard.
    """
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM hitl_events
            WHERE is_resolved = 0
            ORDER BY triggered_at ASC
            """,
        ).fetchall()
    events = [_row_to_hitl_event(row) for row in rows]
    logger.debug("Unresolved HITL events: %d", len(events))
    return events


def get_all_events(
    db_path: str = DEFAULT_HITL_DB_PATH,
    limit: int = 100,
) -> List[HITLEvent]:
    """Return all HITL events (resolved and unresolved) for audit/dashboard."""
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM hitl_events ORDER BY triggered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_hitl_event(row) for row in rows]


def get_event_by_id(
    event_id: str,
    db_path: str = DEFAULT_HITL_DB_PATH,
) -> Optional[HITLEvent]:
    """Fetch a single HITL event by UUID."""
    with _get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM hitl_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    return _row_to_hitl_event(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8: RESOLUTION HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def resolve_hitl_event(
    event_id: str,
    resolved_by: str,
    resolution_notes: str,
    loop_detector_reset_fn: Optional[Callable] = None,
    db_path: str = DEFAULT_HITL_DB_PATH,
) -> HITLEvent:
    """
    Mark a HITL event as resolved.

    Steps:
        1. Validate event exists and is currently unresolved.
        2. Write resolution fields to database.
        3. If trigger_reason was UNCORRECTABLE_LOOP, reset LoopDetector.
        4. Return the updated HITLEvent.

    Args:
        event_id:               UUID of the event to resolve.
        resolved_by:            Reviewer identifier (internal staff ref — not PII).
        resolution_notes:       Free-text notes from the reviewer.
        loop_detector_reset_fn: Optional callable to reset the LoopDetector
                                (required when resolving UNCORRECTABLE_LOOP events).
        db_path:                SQLite database path.

    Returns:
        Updated HITLEvent with is_resolved=True.

    Raises:
        ValueError: If event not found or already resolved.
    """
    event = get_event_by_id(event_id, db_path)

    if event is None:
        raise ValueError(f"HITL event not found: {event_id}")
    if event.is_resolved:
        raise ValueError(f"HITL event already resolved: {event_id}")

    resolved_at = datetime.utcnow()

    with _get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE hitl_events SET
                is_resolved      = 1,
                resolved_at      = ?,
                resolved_by      = ?,
                resolution_notes = ?
            WHERE event_id = ?
            """,
            (
                resolved_at.isoformat(),
                resolved_by,
                resolution_notes,
                event_id,
            ),
        )

    logger.info(
        "HITL EVENT RESOLVED [%s]: by=%s | notes=%s",
        event_id[:8], resolved_by, resolution_notes[:80],
    )

    # If loop event, reset the LoopDetector so automation can resume
    if (
        event.trigger_reason == HITLTriggerReason.UNCORRECTABLE_LOOP
        and loop_detector_reset_fn is not None
    ):
        loop_detector_reset_fn()
        logger.info("LoopDetector reset authorized by HITL resolution [%s].", event_id[:8])

    # Return refreshed event
    return get_event_by_id(event_id, db_path)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9: AUTOMATION HALT GUARD
# Called by Orchestrator at the start of every interval cycle.
# If unresolved events >= MAX_UNRESOLVED_BEFORE_HALT, execution is blocked.
# ─────────────────────────────────────────────────────────────────────────────

def automation_is_halted(
    db_path: str = DEFAULT_HITL_DB_PATH,
) -> bool:
    """
    Check whether automated execution should be halted.

    Returns True (halt) if:
        - Any unresolved HITL events exist with reason UNCORRECTABLE_LOOP, OR
        - Total unresolved event count >= MAX_UNRESOLVED_BEFORE_HALT

    Returns False (clear to run) otherwise.

    This function is called at the TOP of every Orchestrator cycle.
    """
    unresolved = get_unresolved_events(db_path)

    if not unresolved:
        return False

    # Hard halt on any uncorrectable loop event
    loop_events = [
        e for e in unresolved
        if e.trigger_reason == HITLTriggerReason.UNCORRECTABLE_LOOP
    ]
    if loop_events:
        logger.error(
            "AUTOMATION HALTED: %d unresolved UNCORRECTABLE_LOOP event(s). "
            "Resolve via HITL queue before resuming.",
            len(loop_events),
        )
        return True

    # Soft halt on too many unresolved events
    if len(unresolved) >= MAX_UNRESOLVED_BEFORE_HALT:
        logger.error(
            "AUTOMATION HALTED: %d unresolved HITL events >= threshold of %d.",
            len(unresolved), MAX_UNRESOLVED_BEFORE_HALT,
        )
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10: SLA BREACH DETECTOR
# Identifies events that have exceeded the HITL_SLA_HOURS response window.
# ─────────────────────────────────────────────────────────────────────────────

def get_sla_breached_events(
    db_path: str = DEFAULT_HITL_DB_PATH,
) -> List[HITLEvent]:
    """
    Return unresolved HITL events that have exceeded the SLA response window.

    SLA breach = triggered_at is more than HITL_SLA_HOURS hours ago
                 and event is still unresolved.

    Used by the dashboard to surface critical overdue items.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=HITL_SLA_HOURS)).isoformat()

    with _get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM hitl_events
            WHERE is_resolved = 0
              AND triggered_at <= ?
            ORDER BY triggered_at ASC
            """,
            (cutoff,),
        ).fetchall()

    events = [_row_to_hitl_event(row) for row in rows]
    if events:
        logger.warning(
            "%d HITL event(s) have breached the %dh SLA window.",
            len(events), HITL_SLA_HOURS,
        )
    return events


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11: COMMENT THREAD
# Allows reviewers to annotate events during investigation.
# ─────────────────────────────────────────────────────────────────────────────

def add_hitl_comment(
    event_id: str,
    authored_by: str,
    body: str,
    db_path: str = DEFAULT_HITL_DB_PATH,
) -> str:
    """
    Add a comment to a HITL event thread.

    Args:
        event_id:    Target HITL event UUID.
        authored_by: Reviewer identifier.
        body:        Comment text.
        db_path:     SQLite database path.

    Returns:
        comment_id of the newly created comment.
    """
    comment_id = str(uuid.uuid4())
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO hitl_comments (comment_id, event_id, authored_by, authored_at, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                comment_id,
                event_id,
                authored_by,
                datetime.utcnow().isoformat(),
                body,
            ),
        )
    logger.info("HITL comment added [%s] by %s.", event_id[:8], authored_by)
    return comment_id


def get_hitl_comments(
    event_id: str,
    db_path: str = DEFAULT_HITL_DB_PATH,
) -> List[dict]:
    """Retrieve all comments for a HITL event, ordered chronologically."""
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM hitl_comments
            WHERE event_id = ?
            ORDER BY authored_at ASC
            """,
            (event_id,),
        ).fetchall()
    return [dict(row) for row in rows]