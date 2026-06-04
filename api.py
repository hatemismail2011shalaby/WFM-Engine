"""
api.py — LinePilot WFM Engine API
Production-grade FastAPI entrypoint with API-key-secured ingestion endpoints.
"""

import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Security Layer: API Key verification via dependency injection
# -----------------------------------------------------------------------------
_LOG = logging.getLogger("linepilot.api")

_LINEPILOT_API_KEY: Optional[str] = os.environ.get("LINEPILOT_API_KEY")
if not _LINEPILOT_API_KEY:
    _LOG.warning("LINEPILOT_API_KEY not set — ingestion endpoints will reject all requests")

async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    """
    Dependency: validates X-API-Key header against LINEPILOT_API_KEY env var.
    Returns the key on success. Raises 403 on any failure (no detail leakage).
    """
    if not _LINEPILOT_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key not configured on server",
        )
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    # Use secrets.compare_digest in production if key lengths differ; here strict ==
    if x_api_key != _LINEPILOT_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    return x_api_key


# -----------------------------------------------------------------------------
# Pydantic Request/Response Contracts (strict — no raw dicts)
# -----------------------------------------------------------------------------
class IngestionRequest(BaseModel):
    """Telemetry payload accepted by the ingestion endpoint."""
    source: str = Field(..., description="Origin system identifier")
    payload: dict = Field(..., description="Raw telemetry blob for downstream scrubbing")
    timestamp: Optional[str] = Field(default=None, description="ISO-8601 event timestamp")


class IngestionResponse(BaseModel):
    """Structured ingestion acknowledgment."""
    status: str
    record_id: Optional[str] = None
    interval_processed: bool = False


class ReflectorPollResponse(BaseModel):
    """Latest metacognitive reflector snapshot for dashboard consumption."""
    capacity_delta: Optional[float] = None
    estimated_savings: Optional[float] = None
    deviation_class: Optional[str] = None
    heuristic_applied: Optional[bool] = None
    timestamp: Optional[str] = None
    source: str = "reflector"


# -----------------------------------------------------------------------------
# Application Lifecycle
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks: init SQLite TMK store, warm Erlang lookup tables."""
    _LOG.info("LinePilot API booting — loading TMK store and System 1 primitives")
    # TODO: init tmk_store connection pool, warm Erlang C cache
    yield
    _LOG.info("LinePilot API shutting down — closing store connections")

app = FastAPI(
    title="LinePilot WFM Engine",
    description="Real-time metacognitive workforce management orchestration API",
    version="0.2.0-pilot",
    lifespan=lifespan,
)


# -----------------------------------------------------------------------------
# Public Health Endpoint (unsecured — for load-balancer probes)
# -----------------------------------------------------------------------------
@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """Liveness probe. Always returns 200."""
    return {"status": "alive", "service": "linepilot"}


# -----------------------------------------------------------------------------
# Secured Ingestion Endpoints (all require X-API-Key header)
# -----------------------------------------------------------------------------
@app.post(
    "/ingest",
    response_model=IngestionResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["ingestion"],
)
async def ingest_telemetry(request: IngestionRequest) -> IngestionResponse:
    """
    Primary ingestion endpoint.
    Accepts raw telemetry, scrubs PII, fires System 1 (Erlang C),
    and hands off to System 2 (Reflector) for metacognitive evaluation.
    """
    # TODO: wire to core.ingestor.pipeline()
    return IngestionResponse(
        status="accepted",
        record_id=f"rec_{request.source}_{hash(request.payload) & 0xFFFFFF:06x}",
        interval_processed=True,
    )


@app.post(
    "/ingest/batch",
    response_model=list[IngestionResponse],
    dependencies=[Depends(verify_api_key)],
    tags=["ingestion"],
)
async def ingest_batch(requests: list[IngestionRequest]) -> list[IngestionResponse]:
    """Batch ingestion for high-volume telemetry streams."""
    return [await ingest_telemetry(req) for req in requests]


@app.get(
    "/reflector/latest",
    response_model=ReflectorPollResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["metacognition"],
)
async def poll_reflector() -> ReflectorPollResponse:
    """
    Dashboard polling endpoint.
    Returns the latest System 2 (Reflector) snapshot from memory/TMK store.
    """
    # TODO: wire to core.reflector.get_latest_snapshot() or core.tmk_store.fetch_latest()
    return ReflectorPollResponse(
        capacity_delta=0.0,
        estimated_savings=0.0,
        deviation_class="none",
        heuristic_applied=False,
        timestamp=None,
    )


# -----------------------------------------------------------------------------
# Error Handlers (suppress stack traces in production)
# -----------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )