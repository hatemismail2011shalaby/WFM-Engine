# -----------------------------------------------------------------------------
# At the top of api.py, add defensive import of reflector
# -----------------------------------------------------------------------------
try:
    from core.reflector import get_latest_snapshot as _get_reflector_snapshot
except Exception:
    _get_reflector_snapshot = None

# -----------------------------------------------------------------------------
# Replace the existing poll_reflector endpoint body with this:
# -----------------------------------------------------------------------------
@app.get(
    "/reflector/latest",
    response_model=ReflectorPollResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["metacognition"],
)
async def poll_reflector() -> ReflectorPollResponse:
    """
    Dashboard polling endpoint.
    Returns the latest System 2 (Reflector) snapshot from in‑memory cache.
    Falls back to a zeroed response if no snapshot exists yet.
    """
    if _get_reflector_snapshot:
        try:
            snap = _get_reflector_snapshot()
            if snap:
                return ReflectorPollResponse(**snap)
        except Exception:
            _LOG.exception("Failed to read reflector snapshot")

    # No snapshot available yet
    return ReflectorPollResponse(
        source="reflector",
        deviation_class="none",
    )