"""
core/hitl_queue.py — Human-in-the-Loop Escalation Layer
Tracks consecutive metacognitive failures and fires Slack webhooks.
Suspends automation when threshold is breached.
"""
import os
import threading
import requests
from datetime import datetime, timezone
from typing import Optional

SLACK_WEBHOOK_URL = os.environ.get("LINEPILOT_SLACK_WEBHOOK")
FAILURE_THRESHOLD = 3  # consecutive failures before escalation

_lock = threading.Lock()
_consecutive_counts: dict = {}  # {deviation_class: count}
_automation_suspended: bool = False

def _send_slack_message(text: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    try:
        requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": text},
            timeout=5,
        )
    except Exception:
        pass  # do not crash the engine on notification failure

def record_reflection(deviation_class: str, heuristic_applied: bool) -> None:
    """
    Call after every reflector pass.
    - Resets consecutive count for the deviation class on success (heuristic applied).
    - Increments on failure (no heuristic applied).
    - Escalates when threshold reached.
    """
    global _automation_suspended
    with _lock:
        if not deviation_class or deviation_class == "none":
            return

        if heuristic_applied:
            # Success resets failure count for this class
            _consecutive_counts.pop(deviation_class, None)
            return

        # Failure: increment count
        count = _consecutive_counts.get(deviation_class, 0) + 1
        _consecutive_counts[deviation_class] = count

        if count >= FAILURE_THRESHOLD and not _automation_suspended:
            _automation_suspended = True
            msg = (
                f"🚨 *LinePilot HITL Escalation*\n"
                f"Automation suspended after {count} consecutive failures.\n"
                f"Deviation class: `{deviation_class}`\n"
                f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
                f"Manual intervention required."
            )
            _send_slack_message(msg)

def is_automation_suspended() -> bool:
    """Check if automation is currently suspended (for dashboard/injestor gating)."""
    with _lock:
        return _automation_suspended

def reset_suspension() -> None:
    """Admin reset (exposed via API or dashboard)."""
    global _automation_suspended
    with _lock:
        _automation_suspended = False
        _consecutive_counts.clear()

def get_status() -> dict:
    """Return current HITL status for dashboard polling."""
    with _lock:
        return {
            "automation_suspended": _automation_suspended,
            "consecutive_failures": dict(_consecutive_counts),
            "threshold": FAILURE_THRESHOLD,
        }