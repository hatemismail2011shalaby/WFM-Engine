"""
core/reflector.py — System 2 Metacognition (with live snapshot for dashboard)
"""

import threading
from typing import Any, Dict, Optional

# -----------------------------------------------------------------------------
# Snapshot Storage (module-level, thread‑safe)
# -----------------------------------------------------------------------------
_snapshot_lock = threading.Lock()
_latest_snapshot: Optional[Dict[str, Any]] = None


def _store_snapshot(
    capacity_delta: Optional[float] = None,
    estimated_savings: Optional[float] = None,
    deviation_class: Optional[str] = None,
    heuristic_applied: bool = False,
) -> None:
    """
    Update the module‑level snapshot from the latest metacognitive pass.
    Call this immediately after the reflector finishes its analysis.
    """
    global _latest_snapshot
    from datetime import datetime, timezone

    snapshot = {
        "capacity_delta": capacity_delta,
        "estimated_savings": estimated_savings,
        "deviation_class": deviation_class or "none",
        "heuristic_applied": heuristic_applied,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "reflector",
    }
    with _snapshot_lock:
        _latest_snapshot = snapshot


def get_latest_snapshot() -> Optional[Dict[str, Any]]:
    """
    Thread‑safe accessor for the most recent reflector output.
    Returns a dict matching the ReflectorPollResponse schema, or None if no cycle has run.
    """
    with _snapshot_lock:
        return _latest_snapshot.copy() if _latest_snapshot else None


# -----------------------------------------------------------------------------
# EXISTING REFLECTOR LOGIC — place your current implementation below.
# At the end of each reflection pass, call _store_snapshot(...) with the results.
# -----------------------------------------------------------------------------

# TODO: INSERT YOUR EXISTING REFLECTOR CODE HERE
# Example integration:
#
# def run_reflection(actual: IntervalRecord, forecast: float) -> None:
#     # your analysis ...
#     delta = actual.calls - forecast
#     savings = calculate_savings(delta)
#     deviation = classify_deviation(delta)
#     heuristic = apply_heuristic(deviation)
#     _store_snapshot(
#         capacity_delta=delta,
#         estimated_savings=savings,
#         deviation_class=deviation,
#         heuristic_applied=heuristic is not None,
#     )