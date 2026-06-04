# core/models.py
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# METACOGNITIVE WFM ENGINE â€” Tier-1 Typed Data Models
# All inter-module contracts are defined here. No module may pass raw dicts.
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECTION 1: ENUMERATIONS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class RoutingAction(str, Enum):
    """Possible routing states the Skills-Based Router may emit."""
    NORMAL       = "NORMAL"
    SKILL_REROUTE = "SKILL_REROUTE"
    ESCALATE     = "ESCALATE"


class DeviationClass(str, Enum):
    """Failure categories the Metacognitive Reflector may classify."""
    VOLUME_SPIKE       = "VOLUME_SPIKE"
    AHT_DRIFT          = "AHT_DRIFT"
    SHRINKAGE_ANOMALY  = "SHRINKAGE_ANOMALY"
    MULTI_FACTOR       = "MULTI_FACTOR"
    UNCORRECTABLE_LOOP = "UNCORRECTABLE_LOOP"
    NONE               = "NONE"


class HITLTriggerReason(str, Enum):
    """Why a Human-in-the-Loop event was raised."""
    UNCORRECTABLE_LOOP    = "UNCORRECTABLE_LOOP"
    CAPACITY_DELTA_EXCEED = "CAPACITY_DELTA_EXCEED"
    MANUAL_ESCALATION     = "MANUAL_ESCALATION"


class IntervalStatus(str, Enum):
    """Processing state of a 15-minute interval."""
    PENDING    = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETE   = "COMPLETE"
    ESCALATED  = "ESCALATED"


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECTION 2: RAW INPUT SCHEMAS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class RawAgentLog(BaseModel):
    """
    Unstructured log entry from an individual agent session.
    PII fields are present here â€” this model is PRE-scrub only.
    Must never be passed beyond the PII Scrubber module.
    """
    log_id:     str       = Field(default_factory=lambda: str(uuid.uuid4()))
    raw_text:   str
    agent_id:   str       # WILL BE SCRUBBED â†’ person_N placeholder
    timestamp:  datetime


class ScrubbedAgentLog(BaseModel):
    """
    Post-PII-scrub log. Safe for downstream analytics.
    agent_id is replaced with a context-aware placeholder.
    """
    log_id:        str
    scrubbed_text: str
    agent_placeholder: str   # e.g., "person_1"
    timestamp:     datetime


class RawIntervalFeed(BaseModel):
    """
    One 15-minute interval of call center telemetry as received
    from the upstream data source (CSV row, API payload, WebSocket frame).
    """
    feed_id:           str      = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:         datetime
    call_volume:       int      = Field(..., ge=0, description="Calls offered in interval")
    aht_seconds:       float    = Field(..., gt=0, description="Average Handle Time in seconds")
    shrinkage_pct:     float    = Field(..., ge=0.0, le=1.0, description="Shrinkage as decimal, e.g. 0.30")
    agents_scheduled:  int      = Field(..., ge=0)
    agents_available:  int      = Field(..., ge=0)
    raw_agent_logs:    List[RawAgentLog] = Field(default_factory=list)

    @field_validator("agents_available")
    @classmethod
    def available_cannot_exceed_scheduled(cls, v: int, info) -> int:
        scheduled = info.data.get("agents_scheduled", v)
        if v > scheduled:
            raise ValueError(
                f"agents_available ({v}) cannot exceed agents_scheduled ({scheduled})"
            )
        return v


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECTION 3: ERLANG C & CAPACITY SCHEMAS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ErlangCInput(BaseModel):
    """
    Sanitized inputs fed into the Erlang C computation engine.
    Derived from RawIntervalFeed after PII scrub and shrinkage adjustment.
    """
    interval_id:       str
    timestamp:         datetime
    call_volume:       int
    aht_seconds:       float
    target_sla_pct:    float  = Field(0.80, ge=0.0, le=1.0)
    target_answer_sec: int    = Field(20,   ge=1,   description="Answer within N seconds")
    shrinkage_pct:     float  = Field(...,  ge=0.0, le=1.0)


class ErlangCOutput(BaseModel):
    """
    Result of Erlang C computation for one interval.
    """
    interval_id:         str
    timestamp:           datetime
    traffic_intensity_A: float   # Erlangs: call_volume * (aht / interval_seconds)
    agents_required_raw: int     # Minimum agents to meet SLA ignoring shrinkage
    agents_required_net: int     # After shrinkage buffer applied
    predicted_sla_pct:   float
    predicted_occupancy: float
    interval_seconds:    int     = 900   # 15 min = 900 sec


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECTION 4: INTERVAL PROCESSING RECORD
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class IntervalRecord(BaseModel):
    """
    Master record for a single 15-minute interval lifecycle.
    Written after System 1 execution; updated after System 2 reflection.
    Stored in TMK store for historical retrieval.
    """
    record_id:            str      = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:            datetime
    status:               IntervalStatus = IntervalStatus.PENDING

    # System 1 outputs
    erlang_output:        Optional[ErlangCOutput]  = None
    routing_action:       RoutingAction             = RoutingAction.NORMAL
    actual_sla_pct:       Optional[float]           = None
    actual_occupancy:     Optional[float]           = None
    scrubbed_logs: List[ScrubbedAgentLog] = Field(default_factory=list)

    # System 2 outputs
    forecast_deviation_pct: Optional[float]         = None
    deviation_class:        DeviationClass           = DeviationClass.NONE
    tmk_update_generated:   bool                     = False
    tmk_update_text:        Optional[str]            = None

    class Config:
        # Allow mutation so orchestrator can update record in stages
        frozen = False


# Fix forward reference typo-safe alias
IntervalRecord.model_rebuild()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECTION 5: TMK MEMORY SCHEMA
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TMKEntry(BaseModel):
    """
    A single curated heuristic stored in the Task-Method-Knowledge database.
    Written by the Metacognitive Reflector; read by the Ingestor pre-loop.
    """
    entry_id:       str      = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at:     datetime = Field(default_factory=datetime.utcnow)
    last_applied:   Optional[datetime] = None
    apply_count:    int      = 0

    # Scope: when does this heuristic activate?
    day_of_week:    Optional[int]   = None   # 0=Mon â€¦ 6=Sun; None = always
    hour_of_day:    Optional[int]   = None   # 0â€“23; None = always
    deviation_class: DeviationClass = DeviationClass.NONE

    # The heuristic itself
    heuristic_text:       str
    shrinkage_adjustment: float = Field(0.0, description="Delta to add to shrinkage_pct")
    aht_adjustment_pct:   float = Field(0.0, description="Multiplier delta for AHT forecast")
    volume_adjustment_pct: float = Field(0.0, description="Multiplier delta for volume forecast")

    # Confidence tracking
    confidence_score: float = Field(1.0, ge=0.0, le=1.0)
    times_validated:  int   = 0
    times_failed:     int   = 0


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECTION 6: HITL EVENT SCHEMA
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class HITLEvent(BaseModel):
    """
    Escalation record written when automated execution must halt
    and a human reviewer must intervene.
    """
    event_id:        str      = Field(default_factory=lambda: str(uuid.uuid4()))
    triggered_at:    datetime = Field(default_factory=datetime.utcnow)
    resolved_at:     Optional[datetime] = None
    resolved_by:     Optional[str]      = None   # Reviewer ID (not PII â€” internal staff ref)

    trigger_reason:  HITLTriggerReason
    interval_record: IntervalRecord
    context_summary: str      # Human-readable explanation of why this escalated

    recommended_action: Optional[str] = None
    resolution_notes:   Optional[str] = None
    is_resolved:        bool           = False


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECTION 7: DASHBOARD PAYLOAD SCHEMA
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class DashboardPayload(BaseModel):
    """
    Serialized snapshot emitted at end of each interval cycle.
    Consumed by the Streamlit dashboard for real-time rendering.
    """
    snapshot_at:          datetime
    current_sla_pct:      float
    current_occupancy:    float
    agents_required_net:  int
    agents_available:     int
    staffing_gap:         int        # agents_required_net - agents_available
    routing_action:       RoutingAction
    active_hitl_events:   int
    last_tmk_update:      Optional[str]
    deviation_class:      DeviationClass
    forecast_deviation_pct: Optional[float]
    interval_history:     List[dict] = Field(default_factory=list)