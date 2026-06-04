from core.hitl_queue import record_reflection, is_automation_suspended

# At the end of reflection, even before storing snapshot:
if not is_automation_suspended():
    # apply heuristic, etc.
    record_reflection(deviation_class=deviation_class, heuristic_applied=heuristic_applied)