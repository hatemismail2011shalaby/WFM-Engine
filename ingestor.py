# core/ingestor.py
# ─────────────────────────────────────────────────────────────────────────────
# METACOGNITIVE WFM ENGINE — Real-Time 15-Minute Interval Pipeline
# Responsibilities:
#   1. Ingest raw interval feed (CSV file, API payload, or WebSocket frame)
#   2. Route through PII Scrubber
#   3. Build ErlangCInput (with TMK pre-load from memory store)
#   4. Fire Erlang C Engine
#   5. Evaluate SLA and Occupancy thresholds
#   6. Trigger Skills-Based Router if thresholds breached
#   7. Record IntervalRecord for downstream Metacognitive Reflector
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import csv
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Callable, List, Optional

from core.models import (
    DeviationClass,
    ErlangCInput,
    IntervalRecord,
    IntervalStatus,
    RawIntervalFeed,
    RoutingAction,
    ScrubbedAgentLog,
)
from core.erlang import compute_interval_capacity
from core.models import TMKEntry

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: THRESHOLD CONSTANTS
# Centralised here — never hardcoded downstream.
# ─────────────────────────────────────────────────────────────────────────────

SLA_BREACH_THRESHOLD        : float = 0.80   # Trigger reroute if SLA < 80%
OCCUPANCY_BREACH_THRESHOLD  : float = 0.85   # Trigger reroute if Occ > 85%
CAPACITY_DELTA_HITL_PCT     : float = 0.20   # Trigger HITL if gap > 20% of scheduled


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: RAW FEED PARSERS
# Three ingestion modes: CSV file, JSON dict, raw dict list.
# All converge to List[RawIntervalFeed].
# ─────────────────────────────────────────────────────────────────────────────

def parse_csv_feed(file_path: str | Path) -> List[RawIntervalFeed]:
    """
    Parse a CSV file of interval rows into RawIntervalFeed objects.

    Expected CSV columns:
        timestamp, call_volume, aht_seconds, shrinkage_pct,
        agents_scheduled, agents_available

    Timestamps must be ISO-8601 format: 2025-01-07T09:00:00
    """
    feeds: List[RawIntervalFeed] = []
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Feed CSV not found: {path}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            try:
                feed = RawIntervalFeed(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    call_volume=int(row["call_volume"]),
                    aht_seconds=float(row["aht_seconds"]),
                    shrinkage_pct=float(row["shrinkage_pct"]),
                    agents_scheduled=int(row["agents_scheduled"]),
                    agents_available=int(row["agents_available"]),
                )
                feeds.append(feed)
            except (KeyError, ValueError) as e:
                logger.error("CSV row %d parse error: %s | row=%s", i, e, row)
                continue

    logger.info("Parsed %d interval rows from CSV: %s", len(feeds), path)
    return feeds


def parse_json_feed(payload: dict) -> RawIntervalFeed:
    """
    Parse a single JSON dict (e.g., from REST API or WebSocket) into
    a RawIntervalFeed. Used for real-time streaming ingestion.
    """
    try:
        return RawIntervalFeed(**payload)
    except Exception as e:
        raise ValueError(f"Invalid interval JSON payload: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: THRESHOLD EVALUATOR
# Pure function — evaluates SLA and Occupancy against breach thresholds.
# Returns the routing action that should be taken.
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_thresholds(
    predicted_sla_pct: float,
    predicted_occupancy: float,
    agents_required_net: int,
    agents_scheduled: int,
) -> tuple[RoutingAction, bool]:
    """
    Evaluate whether SLA or Occupancy thresholds have been breached.
    Also checks if the staffing gap exceeds the HITL capacity delta.

    Returns:
        Tuple of (RoutingAction, hitl_required: bool)
    """
    hitl_required   = False
    routing_action  = RoutingAction.NORMAL

    # ── Check SLA breach ──────────────────────────────────────────────────
    if predicted_sla_pct < SLA_BREACH_THRESHOLD:
        routing_action = RoutingAction.SKILL_REROUTE
        logger.warning(
            "SLA BREACH: %.1f%% < threshold %.1f%%. Triggering SKILL_REROUTE.",
            predicted_sla_pct * 100, SLA_BREACH_THRESHOLD * 100,
        )

    # ── Check Occupancy breach ────────────────────────────────────────────
    if predicted_occupancy > OCCUPANCY_BREACH_THRESHOLD:
        routing_action = RoutingAction.SKILL_REROUTE
        logger.warning(
            "OCCUPANCY BREACH: %.1f%% > threshold %.1f%%. Triggering SKILL_REROUTE.",
            predicted_occupancy * 100, OCCUPANCY_BREACH_THRESHOLD * 100,
        )

    # ── Check capacity delta for HITL ─────────────────────────────────────
    if agents_scheduled > 0:
        delta_pct = abs(agents_required_net - agents_scheduled) / agents_scheduled
        if delta_pct > CAPACITY_DELTA_HITL_PCT:
            hitl_required  = True
            routing_action = RoutingAction.ESCALATE
            logger.error(
                "CAPACITY DELTA EXCEEDED: required=%d vs scheduled=%d "
                "(delta=%.1f%% > %.1f%% threshold). HITL required.",
                agents_required_net, agents_scheduled,
                delta_pct * 100, CAPACITY_DELTA_HITL_PCT * 100,
            )

    return routing_action, hitl_required


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: INTERVAL PROCESSOR
# Core function: processes one RawIntervalFeed through the full System 1 pipeline.
# ─────────────────────────────────────────────────────────────────────────────

def process_interval(
    raw_feed: RawIntervalFeed,
    scrub_fn: Callable,
    router_fn: Callable,
    hitl_fn: Callable,
    tmk_entries: List[TMKEntry],
    target_sla_pct: float = 0.80,
    target_answer_sec: int = 20,
) -> IntervalRecord:
    """
    Full System 1 pipeline for one 15-minute interval.

    Args:
        raw_feed:         Validated RawIntervalFeed for this interval.
        scrub_fn:         PII Scrubber function: (List[RawAgentLog]) → List[ScrubbedAgentLog]
        router_fn:        Skills-Based Router function: (RoutingAction, ErlangCOutput) → None
        hitl_fn:          HITL escalation function: (HITLTriggerReason, IntervalRecord) → None
        tmk_entries:      Active TMK heuristics pre-loaded for this interval.
        target_sla_pct:   SLA target (default 80%).
        target_answer_sec: Answer speed target in seconds (default 20s).

    Returns:
        IntervalRecord with System 1 outputs fully populated.
    """
    interval_id = str(uuid.uuid4())
    record = IntervalRecord(
        record_id=interval_id,
        timestamp=raw_feed.timestamp,
        status=IntervalStatus.PROCESSING,
    )

    # ── Step 1: Scrub PII from agent logs ─────────────────────────────────
    scrubbed_logs: List[ScrubbedAgentLog] = []
    if raw_feed.raw_agent_logs:
        try:
            scrubbed_logs = scrub_fn(raw_feed.raw_agent_logs)
            logger.info(
                "Interval [%s]: scrubbed %d agent logs.",
                interval_id[:8], len(scrubbed_logs),
            )
        except Exception as e:
            logger.error("PII scrub failed for interval [%s]: %s", interval_id[:8], e)

    record.scrubbed_logs = scrubbed_logs

    # ── Step 2: Build ErlangCInput ────────────────────────────────────────
    erlang_input = ErlangCInput(
        interval_id=interval_id,
        timestamp=raw_feed.timestamp,
        call_volume=raw_feed.call_volume,
        aht_seconds=raw_feed.aht_seconds,
        target_sla_pct=target_sla_pct,
        target_answer_sec=target_answer_sec,
        shrinkage_pct=raw_feed.shrinkage_pct,
    )

    # ── Step 3: Compute capacity via Erlang C ─────────────────────────────
    try:
        erlang_output = compute_interval_capacity(
            erlang_input=erlang_input,
            active_tmk_entries=tmk_entries,
        )
        record.erlang_output = erlang_output
        logger.info(
            "Interval [%s]: Erlang C → net=%d agents | SLA=%.1f%% | Occ=%.1f%%",
            interval_id[:8],
            erlang_output.agents_required_net,
            erlang_output.predicted_sla_pct * 100,
            erlang_output.predicted_occupancy * 100,
        )
    except Exception as e:
        logger.error("Erlang C computation failed: %s", e)
        record.status = IntervalStatus.ESCALATED
        return record

    # ── Step 4: Evaluate thresholds ───────────────────────────────────────
    routing_action, hitl_required = evaluate_thresholds(
        predicted_sla_pct=erlang_output.predicted_sla_pct,
        predicted_occupancy=erlang_output.predicted_occupancy,
        agents_required_net=erlang_output.agents_required_net,
        agents_scheduled=raw_feed.agents_scheduled,
    )
    record.routing_action = routing_action

    # ── Step 5: Fire Skills-Based Router if needed ────────────────────────
    if routing_action != RoutingAction.NORMAL:
        try:
            router_fn(routing_action, erlang_output)
        except Exception as e:
            logger.error("Router trigger failed: %s", e)

    # ── Step 6: Fire HITL if capacity delta exceeded ──────────────────────
    if hitl_required:
        record.status = IntervalStatus.ESCALATED
        try:
            from core.models import HITLTriggerReason
            hitl_fn(
                HITLTriggerReason.CAPACITY_DELTA_EXCEED,
                record,
                context_summary=(
                    f"Capacity delta exceeded at {raw_feed.timestamp.isoformat()}. "
                    f"Required: {erlang_output.agents_required_net} agents, "
                    f"Scheduled: {raw_feed.agents_scheduled} agents."
                ),
            )
        except Exception as e:
            logger.error("HITL trigger failed: %s", e)
    else:
        record.status = IntervalStatus.COMPLETE

    return record


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: BATCH INGESTOR
# Processes a full list of intervals sequentially.
# Used for CSV replay, backtesting, and warm-start from historical data.
# ─────────────────────────────────────────────────────────────────────────────

def run_batch_ingestor(
    feeds: List[RawIntervalFeed],
    scrub_fn: Callable,
    router_fn: Callable,
    hitl_fn: Callable,
    tmk_loader_fn: Callable,
    target_sla_pct: float = 0.80,
    target_answer_sec: int = 20,
) -> List[IntervalRecord]:
    """
    Process a list of RawIntervalFeed objects sequentially.
    For each interval, loads the latest TMK heuristics before processing.

    Args:
        feeds:             List of parsed intervals (from CSV or API).
        scrub_fn:          PII scrubber callable.
        router_fn:         Skills-Based Router callable.
        hitl_fn:           HITL escalation callable.
        tmk_loader_fn:     Function to load active TMK entries for a timestamp.
        target_sla_pct:    SLA target.
        target_answer_sec: Answer speed target.

    Returns:
        List of completed IntervalRecords — passed to Metacognitive Reflector.
    """
    records: List[IntervalRecord] = []

    for i, feed in enumerate(feeds):
        logger.info(
            "─── Processing interval %d/%d | %s ───",
            i + 1, len(feeds), feed.timestamp.isoformat(),
        )

        # Pre-load TMK heuristics for this interval's timestamp
        tmk_entries = tmk_loader_fn(feed.timestamp)

        record = process_interval(
            raw_feed=feed,
            scrub_fn=scrub_fn,
            router_fn=router_fn,
            hitl_fn=hitl_fn,
            tmk_entries=tmk_entries,
            target_sla_pct=target_sla_pct,
            target_answer_sec=target_answer_sec,
        )
        records.append(record)

    completed  = sum(1 for r in records if r.status == IntervalStatus.COMPLETE)
    escalated  = sum(1 for r in records if r.status == IntervalStatus.ESCALATED)
    rerouted   = sum(1 for r in records if r.routing_action == RoutingAction.SKILL_REROUTE)

    logger.info(
        "Batch complete: %d intervals | %d completed | %d escalated | %d rerouted",
        len(records), completed, escalated, rerouted,
    )
    return records


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: ASYNC STREAMING INGESTOR
# For live WebSocket or API push feeds — processes intervals as they arrive.
# ─────────────────────────────────────────────────────────────────────────────

async def stream_ingestor(
    feed_generator: AsyncGenerator[dict, None],
    scrub_fn: Callable,
    router_fn: Callable,
    hitl_fn: Callable,
    tmk_loader_fn: Callable,
    target_sla_pct: float = 0.80,
    target_answer_sec: int = 20,
) -> AsyncGenerator[IntervalRecord, None]:
    """
    Async generator: yields IntervalRecords as each raw feed dict arrives.
    Designed for WebSocket or SSE streaming ingestion.

    Usage:
        async for record in stream_ingestor(ws_feed, ...):
            await reflector.process(record)
    """
    async for payload in feed_generator:
        try:
            raw_feed   = parse_json_feed(payload)
            tmk_entries = tmk_loader_fn(raw_feed.timestamp)

            record = process_interval(
                raw_feed=raw_feed,
                scrub_fn=scrub_fn,
                router_fn=router_fn,
                hitl_fn=hitl_fn,
                tmk_entries=tmk_entries,
                target_sla_pct=target_sla_pct,
                target_answer_sec=target_answer_sec,
            )
            yield record

        except Exception as e:
            logger.error("Stream ingestor error on payload: %s | error: %s", payload, e)
            continue