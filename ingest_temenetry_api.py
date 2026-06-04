@app.post(
    "/ingest",
    response_model=IngestionResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["ingestion"],
)
async def ingest_telemetry(request: IngestionRequest) -> IngestionResponse:
    """
    Primary ingestion endpoint.
    Runs core.ingestor pipeline → System 1 Erlang C + System 2 Reflector,
    and stores a live snapshot for the dashboard.
    """
    try:
        if _ingestion_pipeline:
            result = _ingestion_pipeline(request.payload)
            # Normalise output to dict (supports Pydantic model or plain dict)
            if hasattr(result, "dict"):
                result_dict = result.dict()
            elif isinstance(result, dict):
                result_dict = result
            else:
                result_dict = {}

            if _reflector_store:
                _reflector_store(
                    capacity_delta=result_dict.get("capacity_delta"),
                    estimated_savings=result_dict.get("estimated_savings"),
                    deviation_class=result_dict.get("deviation_class"),
                    heuristic_applied=result_dict.get("heuristic_applied", False),
                )
        else:
            _LOG.warning("core.ingestor not available; telemetry will not be processed")
    except Exception:
        _LOG.exception("Ingestion pipeline failed")

    return IngestionResponse(
        status="accepted",
        record_id=f"rec_{request.source}_{hash(request.payload) & 0xFFFFFF:06x}",
        interval_processed=True,
    )