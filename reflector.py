# core/reflector.py
# ─────────────────────────────────────────────────────────────────────────────
# METACOGNITIVE WFM ENGINE — System 2: Metacognitive Reflection Layer
# Responsibilities:
#   1. Compare Erlang C forecast against actual interval outcomes
#   2. Compute forecast deviation percentage
#   3. Classify the root cause of deviation
#   4. Detect uncorrectable error loops (3+ consecutive failures)
#   5. Generate curated TMK heuristic update text
#   6. Trigger HITL if uncorrectable loop detected
#   7. Return enriched IntervalRecord with all reflection outputs
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional, Tuple

from core.models import (
    DeviationClass,
    HITLTriggerReason,
    IntervalRecord,
    IntervalStatus,
    RoutingAction,
    TMKEntry,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: REFLECTION CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

DEVIATION_THRESHOLD_PCT     : float = 0.05   # 5% — triggers reflection
UNCORRECTABLE_LOOP_WINDOW   : int   = 3      # 3 consecutive failures = loop
VOLUME_SPIKE_THRESHOLD_PCT  : float = 0.15   # 15% actual vs predicted volume delta
AHT_DRIFT_THRESHOLD_PCT     : float = 0.10   # 10% AHT delta
SHRINKAGE_ANOMALY_THRESHOLD : float = 0.08   # 8 percentage point shrinkage delta


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: DEVIATION CALCULATOR
# Pure function — computes deviation between predicted and actual SLA.
# ─────────────────────────────────────────────────────────────────────────────

def compute_forecast_deviation(
    predicted_sla_pct: float,
    actual_sla_pct: float,
) -> float:
    """
    Compute absolute percentage deviation between forecast and actual SLA.

    Formula:
        deviation = |predicted - actual| / predicted

    Returns:
        Float: deviation as a proportion (e.g., 0.08 = 8% deviation).
    """
    if predicted_sla_pct <= 0:
        logger.warning("predicted_sla_pct is zero — cannot compute deviation.")
        return 1.0

    deviation = abs(predicted_sla_pct - actual_sla_pct) / predicted_sla_pct
    logger.debug(
        "Deviation: predicted=%.2f%% | actual=%.2f%% | delta=%.2f%%",
        predicted_sla_pct * 100,
        actual_sla_pct * 100,
        deviation * 100,
    )
    return round(deviation, 4)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: DEVIATION CLASSIFIER
# Classifies WHY the forecast deviated using multi-factor analysis.
# ─────────────────────────────────────────────────────────────────────────────

def classify_deviation(
    record: IntervalRecord,
    actual_call_volume: int,
    actual_aht_seconds: float,
    actual_shrinkage_pct: float,
) -> DeviationClass:
    """
    Classify the root cause of a forecast deviation.

    Compares predicted inputs (from ErlangCOutput) against actuals.
    Returns a DeviationClass enum indicating the primary failure mode.

    Classification priority:
        1. UNCORRECTABLE_LOOP — handled by caller, not here
        2. MULTI_FACTOR       — if 2+ factors deviated
        3. VOLUME_SPIKE       — if volume deviated > threshold
        4. AHT_DRIFT          — if AHT deviated > threshold
        5. SHRINKAGE_ANOMALY  — if shrinkage deviated > threshold
        6. NONE               — deviation < 5%, no action
    """
    if record.erlang_output is None:
        logger.warning("No Erlang output on record — cannot classify deviation.")
        return DeviationClass.NONE

    predicted_volume     = record.erlang_output.traffic_intensity_A  # proportional proxy
    predicted_aht        = record.erlang_output.interval_seconds      # used as reference
    factors_triggered: List[DeviationClass] = []

    # ── Volume spike check ────────────────────────────────────────────────
    # Re-derive predicted volume from traffic intensity A and AHT
    predicted_call_volume = (
        record.erlang_output.traffic_intensity_A
        * record.erlang_output.interval_seconds
        / actual_aht_seconds
        if actual_aht_seconds > 0 else 0
    )

    if predicted_call_volume > 0:
        volume_delta = abs(actual_call_volume - predicted_call_volume) / predicted_call_volume
        if volume_delta > VOLUME_SPIKE_THRESHOLD_PCT:
            factors_triggered.append(DeviationClass.VOLUME_SPIKE)
            logger.info(
                "Volume spike detected: predicted≈%.0f | actual=%d | delta=%.1f%%",
                predicted_call_volume, actual_call_volume, volume_delta * 100,
            )

    # ── AHT drift check ───────────────────────────────────────────────────
    # Derive predicted AHT from A and volume
    if actual_call_volume > 0:
        predicted_aht_seconds = (
            record.erlang_output.traffic_intensity_A
            * record.erlang_output.interval_seconds
            / actual_call_volume
        )
        aht_delta = abs(actual_aht_seconds - predicted_aht_seconds) / predicted_aht_seconds
        if aht_delta > AHT_DRIFT_THRESHOLD_PCT:
            factors_triggered.append(DeviationClass.AHT_DRIFT)
            logger.info(
                "AHT drift detected: predicted=%.1fs | actual=%.1fs | delta=%.1f%%",
                predicted_aht_seconds, actual_aht_seconds, aht_delta * 100,
            )

    # ── Shrinkage anomaly check ───────────────────────────────────────────
    # Compare shrinkage implied by net vs raw agent requirement
    if record.erlang_output.agents_required_raw > 0:
        implied_shrinkage = 1.0 - (
            record.erlang_output.agents_required_raw
            / record.erlang_output.agents_required_net
        )
        shrinkage_delta = abs(actual_shrinkage_pct - implied_shrinkage)
        if shrinkage_delta > SHRINKAGE_ANOMALY_THRESHOLD:
            factors_triggered.append(DeviationClass.SHRINKAGE_ANOMALY)
            logger.info(
                "Shrinkage anomaly: implied=%.1f%% | actual=%.1f%% | delta=%.1f%%",
                implied_shrinkage * 100,
                actual_shrinkage_pct * 100,
                shrinkage_delta * 100,
            )

    # ── Final classification ──────────────────────────────────────────────
    if len(factors_triggered) >= 2:
        return DeviationClass.MULTI_FACTOR
    elif len(factors_triggered) == 1:
        return factors_triggered[0]
    else:
        return DeviationClass.NONE


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: HEURISTIC GENERATOR
# Generates a curated TMK memory entry from a classified deviation.
# ─────────────────────────────────────────────────────────────────────────────

def generate_tmk_heuristic(
    record: IntervalRecord,
    deviation_class: DeviationClass,
    actual_call_volume: int,
    actual_aht_seconds: float,
    actual_shrinkage_pct: float,
    deviation_pct: float,
) -> Optional[TMKEntry]:
    """
    Generate a curated TMK heuristic entry from a classified deviation.

    Returns None if deviation class is NONE or UNCORRECTABLE_LOOP
    (loop is handled by HITL, not by heuristic update).
    """
    if deviation_class in (DeviationClass.NONE, DeviationClass.UNCORRECTABLE_LOOP):
        return None

    ts          = record.timestamp
    dow         = ts.weekday()
    hour        = ts.hour
    dow_names   = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

    shrinkage_adj : float = 0.0
    aht_adj_pct   : float = 0.0
    volume_adj_pct: float = 0.0
    heuristic_text: str   = ""

    if deviation_class == DeviationClass.VOLUME_SPIKE:
        volume_adj_pct  = round(deviation_pct * 0.75, 4)   # conservative 75% correction
        heuristic_text  = (
            f"Increase volume forecast by {volume_adj_pct*100:.1f}% on "
            f"{dow_names[dow]}s at {hour:02d}:00 due to recurring volume spike "
            f"(deviation={deviation_pct*100:.1f}%)."
        )

    elif deviation_class == DeviationClass.AHT_DRIFT:
        aht_adj_pct    = round(deviation_pct * 0.5, 4)    # 50% correction to avoid overshoot
        heuristic_text = (
            f"Increase AHT forecast by {aht_adj_pct*100:.1f}% on "
            f"{dow_names[dow]}s at {hour:02d}:00 due to recurring AHT drift "
            f"(deviation={deviation_pct*100:.1f}%)."
        )

    elif deviation_class == DeviationClass.SHRINKAGE_ANOMALY:
        # Derive delta between actual and implied shrinkage
        implied = (
            1.0 - record.erlang_output.agents_required_raw
            / record.erlang_output.agents_required_net
            if record.erlang_output and record.erlang_output.agents_required_net > 0
            else actual_shrinkage_pct
        )
        shrinkage_adj  = round(actual_shrinkage_pct - implied, 4)
        heuristic_text = (
            f"Increase shrinkage buffer by {shrinkage_adj*100:.1f}% on "
            f"{dow_names[dow]}s at {hour:02d}:00 due to recurring shrinkage anomaly "
            f"(actual={actual_shrinkage_pct*100:.1f}% vs implied={implied*100:.1f}%)."
        )

    elif deviation_class == DeviationClass.MULTI_FACTOR:
        shrinkage_adj   = 0.03
        aht_adj_pct     = 0.05
        volume_adj_pct  = 0.05
        heuristic_text  = (
            f"Multi-factor anomaly on {dow_names[dow]}s at {hour:02d}:00. "
            f"Apply conservative buffers: shrinkage+3%, AHT+5%, volume+5%. "
            f"Deviation={deviation_pct*100:.1f}%."
        )

    logger.info("TMK heuristic generated: %s", heuristic_text)

    return TMKEntry(
        day_of_week=dow,
        hour_of_day=hour,
        deviation_class=deviation_class,
        heuristic_text=heuristic_text,
        shrinkage_adjustment=shrinkage_adj,
        aht_adjustment_pct=aht_adj_pct,
        volume_adjustment_pct=volume_adj_pct,
        confidence_score=max(0.5, 1.0 - deviation_pct),
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: LOOP DETECTOR
# Stateful sliding-window detector for uncorrectable error loops.
# ─────────────────────────────────────────────────────────────────────────────

class LoopDetector:
    """
    Maintains a sliding window of recent deviation results.
    If UNCORRECTABLE_LOOP_WINDOW consecutive intervals all deviate > threshold,
    declares an uncorrectable loop and requires HITL intervention.
    """

    def __init__(self, window: int = UNCORRECTABLE_LOOP_WINDOW):
        self.window     : int                        = window
        self.history    : Deque[bool]                = deque(maxlen=window)
        self.loop_active: bool                       = False

    def record(self, deviated: bool) -> bool:
        """
        Record whether the current interval deviated.
        Returns True if an uncorrectable loop is now detected.
        """
        self.history.append(deviated)

        if len(self.history) == self.window and all(self.history):
            if not self.loop_active:
                logger.error(
                    "UNCORRECTABLE LOOP DETECTED: %d consecutive deviations > %.0f%%.",
                    self.window, DEVIATION_THRESHOLD_PCT * 100,
                )
                self.loop_active = True
            return True

        if not all(self.history):
            self.loop_active = False

        return False

    def reset(self):
        """Manually reset loop state after HITL resolution."""
        self.history.clear()
        self.loop_active = False
        logger.info("LoopDetector reset by HITL resolution.")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: MASTER REFLECT FUNCTION
# Single entry point called by Orchestrator after each interval completes.
# ─────────────────────────────────────────────────────────────────────────────

def reflect_on_interval(
    record: IntervalRecord,
    actual_sla_pct: float,
    actual_occupancy: float,
    actual_call_volume: int,
    actual_aht_seconds: float,
    actual_shrinkage_pct: float,
    loop_detector: LoopDetector,
    tmk_write_fn: Callable[[TMKEntry], None],
    hitl_fn: Callable,
) -> IntervalRecord:
    """
    Full System 2 metacognitive reflection pipeline for one interval.

    Steps:
        1. Inject actual outcomes into the IntervalRecord.
        2. Compute forecast deviation.
        3. If deviation > 5%, classify root cause.
        4. Check LoopDetector for uncorrectable loop.
        5. If loop → fire HITL and mark record ESCALATED.
        6. If classifiable → generate TMK heuristic and write to store.
        7. Return enriched IntervalRecord.

    Args:
        record:               IntervalRecord from System 1 (must be COMPLETE).
        actual_sla_pct:       Measured SLA for this interval.
        actual_occupancy:     Measured occupancy for this interval.
        actual_call_volume:   Actual calls received.
        actual_aht_seconds:   Actual average handle time.
        actual_shrinkage_pct: Actual shrinkage observed.
        loop_detector:        Shared LoopDetector instance (stateful).
        tmk_write_fn:         Callable to persist TMKEntry to TMK store.
        hitl_fn:              HITL escalation callable.

    Returns:
        Fully enriched IntervalRecord with System 2 outputs.
    """
    if record.erlang_output is None:
        logger.warning(
            "Skipping reflection for [%s] — no Erlang output present.",
            record.record_id[:8],
        )
        return record

    # ── Step 1: Inject actuals ────────────────────────────────────────────
    record.actual_sla_pct   = actual_sla_pct
    record.actual_occupancy = actual_occupancy

    # ── Step 2: Compute deviation ─────────────────────────────────────────
    deviation_pct = compute_forecast_deviation(
        predicted_sla_pct=record.erlang_output.predicted_sla_pct,
        actual_sla_pct=actual_sla_pct,
    )
    record.forecast_deviation_pct = deviation_pct

    logger.info(
        "Reflection [%s]: deviation=%.2f%% (threshold=%.0f%%)",
        record.record_id[:8],
        deviation_pct * 100,
        DEVIATION_THRESHOLD_PCT * 100,
    )

    # ── Step 3: Check if deviation exceeds threshold ──────────────────────
    significant_deviation = deviation_pct > DEVIATION_THRESHOLD_PCT
    loop_triggered = loop_detector.record(significant_deviation)

    # ── Step 4: Uncorrectable loop check ─────────────────────────────────
    if loop_triggered:
        record.deviation_class = DeviationClass.UNCORRECTABLE_LOOP
        record.status          = IntervalStatus.ESCALATED
        record.tmk_update_text = (
            f"LOOP ALERT: {UNCORRECTABLE_LOOP_WINDOW} consecutive deviations "
            f">{DEVIATION_THRESHOLD_PCT*100:.0f}%. Halting automation. HITL required."
        )
        try:
            hitl_fn(
                HITLTriggerReason.UNCORRECTABLE_LOOP,
                record,
                context_summary=record.tmk_update_text,
            )
        except Exception as e:
            logger.error("HITL trigger failed in reflector: %s", e)
        return record

    # ── Step 5: Classify deviation if significant ─────────────────────────
    if significant_deviation:
        deviation_class = classify_deviation(
            record=record,
            actual_call_volume=actual_call_volume,
            actual_aht_seconds=actual_aht_seconds,
            actual_shrinkage_pct=actual_shrinkage_pct,
        )
        record.deviation_class = deviation_class

        # ── Step 6: Generate and write TMK heuristic ──────────────────────
        tmk_entry = generate_tmk_heuristic(
            record=record,
            deviation_class=deviation_class,
            actual_call_volume=actual_call_volume,
            actual_aht_seconds=actual_aht_seconds,
            actual_shrinkage_pct=actual_shrinkage_pct,
            deviation_pct=deviation_pct,
        )

        if tmk_entry:
            try:
                tmk_write_fn(tmk_entry)
                record.tmk_update_generated = True
                record.tmk_update_text      = tmk_entry.heuristic_text
                logger.info(
                    "TMK written for [%s]: %s",
                    record.record_id[:8], tmk_entry.heuristic_text,
                )
            except Exception as e:
                logger.error("TMK write failed: %s", e)
    else:
        record.deviation_class = DeviationClass.NONE
        logger.info(
            "Reflection [%s]: deviation within threshold — no TMK update required.",
            record.record_id[:8],
        )

    return record


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: BATCH REFLECTOR
# Processes a list of completed IntervalRecords through System 2.
# Used after batch ingestion for historical replay and model validation.
# ─────────────────────────────────────────────────────────────────────────────

def run_batch_reflector(
    records: List[IntervalRecord],
    actuals: List[Dict],
    loop_detector: LoopDetector,
    tmk_write_fn: Callable[[TMKEntry], None],
    hitl_fn: Callable,
) -> List[IntervalRecord]:
    """
    Run metacognitive reflection over a batch of completed IntervalRecords.

    Args:
        records:       List of System 1 IntervalRecords.
        actuals:       Parallel list of dicts with keys:
                           actual_sla_pct, actual_occupancy,
                           actual_call_volume, actual_aht_seconds,
                           actual_shrinkage_pct
        loop_detector: Shared LoopDetector instance.
        tmk_write_fn:  TMK store write callable.
        hitl_fn:       HITL escalation callable.

    Returns:
        List of enriched IntervalRecords with System 2 outputs applied.
    """
    if len(records) != len(actuals):
        raise ValueError(
            f"records ({len(records)}) and actuals ({len(actuals)}) must be same length."
        )

    enriched: List[IntervalRecord] = []

    for i, (record, actual) in enumerate(zip(records, actuals)):
        logger.info(
            "─── Reflecting interval %d/%d | %s ───",
            i + 1, len(records), record.timestamp.isoformat(),
        )
        enriched_record = reflect_on_interval(
            record=record,
            actual_sla_pct=actual["actual_sla_pct"],
            actual_occupancy=actual["actual_occupancy"],
            actual_call_volume=actual["actual_call_volume"],
            actual_aht_seconds=actual["actual_aht_seconds"],
            actual_shrinkage_pct=actual["actual_shrinkage_pct"],
            loop_detector=loop_detector,
            tmk_write_fn=tmk_write_fn,
            hitl_fn=hitl_fn,
        )
        enriched.append(enriched_record)

    tmk_updates = sum(1 for r in enriched if r.tmk_update_generated)
    escalations = sum(1 for r in enriched if r.status == IntervalStatus.ESCALATED)

    logger.info(
        "Batch reflection complete: %d intervals | %d TMK updates | %d escalations",
        len(enriched), tmk_updates, escalations,
    )
    return enriched