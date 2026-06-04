# Inside core/reflector.py, at the end of a reflection cycle, add:
from core.tmk_store import store_heuristic

_store_snapshot(
    capacity_delta=delta,
    estimated_savings=savings,
    deviation_class=deviation_class,
    heuristic_applied=True,
)

# Persist the adjustment in the TMK store
store_heuristic(
    deviation_class=deviation_class,
    capacity_delta=delta,
    estimated_savings=savings,
    heuristic_details={"rule": "increase_staffing_by_1", "confidence": 0.92},
    source_interval=actual.interval_ts.isoformat() if hasattr(actual, 'interval_ts') else None,
)