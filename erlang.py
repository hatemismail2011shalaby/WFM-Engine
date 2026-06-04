# core/erlang.py
# ─────────────────────────────────────────────────────────────────────────────
# METACOGNITIVE WFM ENGINE — Erlang C Capacity Mathematics Engine
# Program-Aided Language (PAL) execution: all math is code, no approximations.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import math
import logging
from datetime import datetime
from typing import Tuple

from core.models import (
    ErlangCInput,
    ErlangCOutput,
    TMKEntry,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: PURE MATHEMATICAL PRIMITIVES
# These functions are stateless and side-effect free.
# They operate on raw floats only — no Pydantic models at this layer.
# ─────────────────────────────────────────────────────────────────────────────

def compute_traffic_intensity(
    call_volume: int,
    aht_seconds: float,
    interval_seconds: int = 900,
) -> float:
    """
    Compute Erlang traffic intensity (A).

    Formula:
        A = λ × h
        where λ = call_volume / interval_seconds  (arrival rate per second)
              h = aht_seconds                      (average service time)

    Returns dimensionless Erlang value.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")
    arrival_rate = call_volume / interval_seconds
    A = arrival_rate * aht_seconds
    logger.debug("Traffic intensity A=%.4f (volume=%d, aht=%.1fs)", A, call_volume, aht_seconds)
    return A


def erlang_c_probability(A: float, N: int) -> float:
    """
    Compute the Erlang C probability — the probability that an arriving
    call will be queued (i.e., all N agents are busy).

    Formula:
        C(N, A) = [ (A^N / N!) * (N / (N - A)) ]
                  ─────────────────────────────────────────────────────
                  [ (A^N / N!) * (N / (N - A)) ] + [ Σ_{k=0}^{N-1} A^k/k! ]

    Numerically stable via log-space computation to handle large N.

    Args:
        A: Traffic intensity in Erlangs.
        N: Number of agents (must be > A to ensure stable queue).

    Returns:
        Float in [0.0, 1.0]: probability a call waits.
    """
    if N <= 0:
        return 1.0
    if A <= 0:
        return 0.0
    if N <= A:
        # System is overloaded — queue grows without bound; return 1.0
        logger.warning("Overloaded: N=%d <= A=%.4f. Returning C=1.0", N, A)
        return 1.0

    # ── Log-space computation for numerical stability ──────────────────────
    # log of A^N / N!
    log_AN_over_Nfact = N * math.log(A) - math.lgamma(N + 1)

    # log of N / (N - A)
    log_intensity_factor = math.log(N) - math.log(N - A)

    # log of numerator term
    log_numerator = log_AN_over_Nfact + log_intensity_factor

    # Σ_{k=0}^{N-1} A^k / k!  — computed in log space, then summed in linear
    log_sum_terms = []
    for k in range(N):
        if k == 0:
            log_sum_terms.append(0.0)          # A^0 / 0! = 1
        else:
            log_sum_terms.append(k * math.log(A) - math.lgamma(k + 1))

    # Numerically stable log-sum-exp
    max_log = max(log_sum_terms)
    sum_linear = math.exp(max_log) * sum(
        math.exp(lt - max_log) for lt in log_sum_terms
    )

    numerator   = math.exp(log_numerator)
    denominator = numerator + sum_linear

    if denominator == 0:
        return 0.0

    C = numerator / denominator
    return min(max(C, 0.0), 1.0)   # clamp to [0, 1]


def compute_service_level(
    A: float,
    N: int,
    target_answer_sec: int,
    aht_seconds: float,
) -> float:
    """
    Compute the predicted Service Level for a given staffing level.

    Formula:
        SL(t) = 1 - C(N, A) × exp(-(N - A) × (t / h))

        where t = target_answer_sec
              h = aht_seconds
              C = Erlang C probability

    Returns:
        Float in [0.0, 1.0]: fraction of calls answered within target_answer_sec.
    """
    if N <= A:
        return 0.0

    C = erlang_c_probability(A, N)
    exponent = -(N - A) * (target_answer_sec / aht_seconds)
    SL = 1.0 - C * math.exp(exponent)
    return min(max(SL, 0.0), 1.0)


def compute_occupancy(A: float, N: int) -> float:
    """
    Compute agent occupancy (utilization).

    Formula:
        ρ = A / N

    Returns:
        Float in [0.0, 1.0].
    """
    if N <= 0:
        return 1.0
    return min(A / N, 1.0)


def apply_shrinkage_buffer(agents_raw: int, shrinkage_pct: float) -> int:
    """
    Gross-up raw agent requirement to account for shrinkage.

    Formula:
        agents_net = ceil(agents_raw / (1 - shrinkage_pct))

    Args:
        agents_raw:    Minimum agents needed to meet SLA.
        shrinkage_pct: Shrinkage as decimal (e.g., 0.30 = 30%).

    Returns:
        Integer: scheduled headcount requirement.
    """
    if shrinkage_pct >= 1.0:
        raise ValueError("shrinkage_pct must be < 1.0")
    net = math.ceil(agents_raw / (1.0 - shrinkage_pct))
    logger.debug(
        "Shrinkage buffer: raw=%d → net=%d (shrinkage=%.1f%%)",
        agents_raw, net, shrinkage_pct * 100,
    )
    return net


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: STAFFING OPTIMIZER
# Binary search over agent count N to find minimum N that meets SLA target.
# ─────────────────────────────────────────────────────────────────────────────

def find_minimum_agents(
    A: float,
    target_sla_pct: float,
    target_answer_sec: int,
    aht_seconds: float,
    max_agents: int = 500,
) -> Tuple[int, float]:
    """
    Binary search to find the minimum number of agents N such that
    compute_service_level(A, N, ...) >= target_sla_pct.

    Args:
        A:                 Traffic intensity (Erlangs).
        target_sla_pct:    e.g. 0.80 for 80%.
        target_answer_sec: e.g. 20 seconds.
        aht_seconds:       Average handle time.
        max_agents:        Search ceiling (safety cap).

    Returns:
        Tuple of (min_agents: int, achieved_sla: float).
    """
    # Lower bound: must be at least ceil(A) + 1 to keep queue stable
    lo = max(1, math.ceil(A) + 1)
    hi = max_agents

    # Edge case: even max_agents cannot meet SLA
    if compute_service_level(A, hi, target_answer_sec, aht_seconds) < target_sla_pct:
        logger.error(
            "Cannot meet SLA=%.1f%% even with %d agents. A=%.2f",
            target_sla_pct * 100, max_agents, A,
        )
        achieved = compute_service_level(A, hi, target_answer_sec, aht_seconds)
        return hi, achieved

    # Binary search
    while lo < hi:
        mid = (lo + hi) // 2
        sl  = compute_service_level(A, mid, target_answer_sec, aht_seconds)
        if sl >= target_sla_pct:
            hi = mid
        else:
            lo = mid + 1

    achieved_sla = compute_service_level(A, lo, target_answer_sec, aht_seconds)
    logger.info(
        "Minimum agents found: N=%d → SLA=%.2f%% (target=%.2f%%)",
        lo, achieved_sla * 100, target_sla_pct * 100,
    )
    return lo, achieved_sla


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: TMK HEURISTIC APPLICATION
# Before computing, apply any active TMK memory adjustments.
# ─────────────────────────────────────────────────────────────────────────────

def apply_tmk_adjustments(
    erlang_input: ErlangCInput,
    active_tmk_entries: list[TMKEntry],
) -> ErlangCInput:
    """
    Apply curated TMK heuristics to adjust input parameters before
    Erlang C computation. Modifies shrinkage, AHT, and volume.

    Returns a new ErlangCInput with adjustments applied.
    """
    adjusted_shrinkage = erlang_input.shrinkage_pct
    adjusted_aht       = erlang_input.aht_seconds
    adjusted_volume    = float(erlang_input.call_volume)

    current_hour = erlang_input.timestamp.hour
    current_dow  = erlang_input.timestamp.weekday()

    for entry in active_tmk_entries:
        # Check scope match
        hour_match = (entry.hour_of_day is None) or (entry.hour_of_day == current_hour)
        dow_match  = (entry.day_of_week is None) or (entry.day_of_week == current_dow)

        if not (hour_match and dow_match):
            continue

        # Apply adjustments
        adjusted_shrinkage = min(
            adjusted_shrinkage + entry.shrinkage_adjustment, 0.95
        )
        adjusted_aht *= (1.0 + entry.aht_adjustment_pct)
        adjusted_volume *= (1.0 + entry.volume_adjustment_pct)

        logger.info(
            "TMK [%s] applied: shrinkage+%.3f | AHT×%.3f | vol×%.3f",
            entry.entry_id[:8],
            entry.shrinkage_adjustment,
            1.0 + entry.aht_adjustment_pct,
            1.0 + entry.volume_adjustment_pct,
        )

    # Return a new model copy with adjusted values
    return erlang_input.model_copy(update={
        "shrinkage_pct": round(adjusted_shrinkage, 4),
        "aht_seconds":   round(adjusted_aht, 2),
        "call_volume":   max(1, int(round(adjusted_volume))),
    })


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: MASTER COMPUTE FUNCTION
# Single entry point called by the Orchestrator every 15-minute interval.
# ─────────────────────────────────────────────────────────────────────────────

def compute_interval_capacity(
    erlang_input: ErlangCInput,
    active_tmk_entries: list[TMKEntry] | None = None,
) -> ErlangCOutput:
    """
    Full capacity computation pipeline for one 15-minute interval.

    Steps:
        1. Apply TMK heuristic adjustments to inputs.
        2. Compute traffic intensity A.
        3. Binary-search for minimum agents_required_raw.
        4. Apply shrinkage buffer → agents_required_net.
        5. Compute predicted SLA and occupancy at agents_required_net.
        6. Return fully populated ErlangCOutput.

    Args:
        erlang_input:        Validated ErlangCInput for this interval.
        active_tmk_entries:  TMK heuristics loaded for this interval (may be empty).

    Returns:
        ErlangCOutput with all computed fields populated.
    """
    tmk_entries = active_tmk_entries or []

    # ── Step 1: Apply TMK adjustments ─────────────────────────────────────
    adjusted_input = apply_tmk_adjustments(erlang_input, tmk_entries)

    # ── Step 2: Traffic intensity ──────────────────────────────────────────
    A = compute_traffic_intensity(
        call_volume=adjusted_input.call_volume,
        aht_seconds=adjusted_input.aht_seconds,
        interval_seconds=900,
    )

    # ── Step 3: Find minimum raw agents ───────────────────────────────────
    agents_raw, predicted_sla = find_minimum_agents(
        A=A,
        target_sla_pct=adjusted_input.target_sla_pct,
        target_answer_sec=adjusted_input.target_answer_sec,
        aht_seconds=adjusted_input.aht_seconds,
    )

    # ── Step 4: Apply shrinkage ────────────────────────────────────────────
    agents_net = apply_shrinkage_buffer(
        agents_raw=agents_raw,
        shrinkage_pct=adjusted_input.shrinkage_pct,
    )

    # ── Step 5: Occupancy at net staffing ──────────────────────────────────
    predicted_occupancy = compute_occupancy(A, agents_net)

    logger.info(
        "Interval [%s] | A=%.3f | raw=%d | net=%d | SLA=%.1f%% | Occ=%.1f%%",
        erlang_input.interval_id,
        A,
        agents_raw,
        agents_net,
        predicted_sla * 100,
        predicted_occupancy * 100,
    )

    # ── Step 6: Return output ──────────────────────────────────────────────
    return ErlangCOutput(
        interval_id=erlang_input.interval_id,
        timestamp=erlang_input.timestamp,
        traffic_intensity_A=round(A, 4),
        agents_required_raw=agents_raw,
        agents_required_net=agents_net,
        predicted_sla_pct=round(predicted_sla, 4),
        predicted_occupancy=round(predicted_occupancy, 4),
        interval_seconds=900,
    )