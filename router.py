# core/router.py
# ─────────────────────────────────────────────────────────────────────────────
# METACOGNITIVE WFM ENGINE — Skills-Based Routing Trigger Engine
# Responsibilities:
#   1. Receive routing action signals from the Ingestor threshold evaluator
#   2. Maintain a registry of skill queues with agent capacity metadata
#   3. Execute rerouting decisions: redistribute load across available queues
#   4. Log all routing decisions with full audit trail
#   5. Expose queue health metrics for dashboard consumption
#   6. Enforce routing cooldown to prevent thrashing on threshold boundaries
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from core.models import ErlangCOutput, RoutingAction

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

ROUTING_COOLDOWN_SECONDS : int   = 300    # 5 minutes between reroute triggers
MAX_QUEUE_OCCUPANCY      : float = 0.90   # Never route to a queue above 90% occupancy
MIN_AVAILABLE_AGENTS     : int   = 2      # Queue must have >= 2 free agents to accept load


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: SKILL QUEUE DATA MODEL
# Represents a single ACD/IVR skill queue with live capacity state.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SkillQueue:
    """
    Represents one skills-based routing queue in the ACD system.

    Attributes:
        queue_id:          Unique identifier (e.g., "Q_BILLING", "Q_TECH_TIER2").
        display_name:      Human-readable queue label.
        skill_tags:        Set of skill labels this queue handles.
        agents_capacity:   Total agents assigned to this queue.
        agents_available:  Currently available (not on call) agents.
        active_calls:      Calls currently in progress on this queue.
        queued_calls:      Calls waiting in this queue's buffer.
        priority:          Routing priority (lower number = preferred).
                           Used to rank queues when multiple are eligible.
        is_active:         Whether this queue is currently accepting traffic.
        last_routed_at:    Timestamp of last routing action to this queue.
    """
    queue_id          : str
    display_name      : str
    skill_tags        : List[str]
    agents_capacity   : int
    agents_available  : int
    active_calls      : int       = 0
    queued_calls      : int       = 0
    priority          : int       = 10
    is_active         : bool      = True
    last_routed_at    : Optional[datetime] = None

    @property
    def occupancy(self) -> float:
        """Current occupancy ratio: active_calls / agents_capacity."""
        if self.agents_capacity <= 0:
            return 1.0
        return min(self.active_calls / self.agents_capacity, 1.0)

    @property
    def free_agents(self) -> int:
        """Agents not currently handling a call."""
        return max(self.agents_available - self.active_calls, 0)

    @property
    def is_eligible_for_overflow(self) -> bool:
        """
        True if this queue can accept overflow traffic.
        Conditions:
            - Queue is active
            - Occupancy below MAX_QUEUE_OCCUPANCY
            - At least MIN_AVAILABLE_AGENTS free
        """
        return (
            self.is_active
            and self.occupancy < MAX_QUEUE_OCCUPANCY
            and self.free_agents >= MIN_AVAILABLE_AGENTS
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: ROUTING DECISION RECORD
# Immutable audit record of every routing action taken.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RoutingDecision:
    """
    Immutable audit record of a single routing action.
    Written to the routing log on every trigger.
    """
    decision_id       : str
    decided_at        : datetime
    trigger_action    : RoutingAction
    source_interval_id: str
    predicted_sla_pct : float
    predicted_occupancy: float
    agents_required   : int
    queues_rerouted_to: List[str]
    calls_redistributed: int
    rationale         : str


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: SKILL QUEUE REGISTRY
# Central in-memory registry of all queues, with thread-safe state mutation.
# In production, replace in-memory state with ACD API calls
# (e.g., Genesys Cloud, NICE CXone, Amazon Connect).
# ─────────────────────────────────────────────────────────────────────────────

class SkillQueueRegistry:
    """
    Thread-safe registry of all SkillQueue objects.

    Provides:
        - Queue registration and deregistration
        - Eligibility filtering for overflow routing
        - State updates after routing decisions
        - Snapshot export for dashboard rendering
    """

    def __init__(self):
        self._lock    : threading.RLock           = threading.RLock()
        self._queues  : Dict[str, SkillQueue]     = {}
        self._log     : List[RoutingDecision]     = []
        self._last_reroute_at: Optional[datetime] = None

    # ── Registration ──────────────────────────────────────────────────────

    def register_queue(self, queue: SkillQueue) -> None:
        """Add or replace a queue in the registry."""
        with self._lock:
            self._queues[queue.queue_id] = queue
            logger.info(
                "Queue registered: [%s] '%s' capacity=%d priority=%d",
                queue.queue_id, queue.display_name,
                queue.agents_capacity, queue.priority,
            )

    def deregister_queue(self, queue_id: str) -> None:
        """Remove a queue from the registry."""
        with self._lock:
            if queue_id in self._queues:
                del self._queues[queue_id]
                logger.info("Queue deregistered: [%s]", queue_id)

    def update_queue_state(
        self,
        queue_id: str,
        agents_available: Optional[int] = None,
        active_calls: Optional[int] = None,
        queued_calls: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> None:
        """
        Update live state fields on a registered queue.
        Called by ACD integration layer every 15-minute interval
        or on real-time event push from the telephony platform.
        """
        with self._lock:
            if queue_id not in self._queues:
                logger.warning("update_queue_state: unknown queue_id '%s'", queue_id)
                return
            q = self._queues[queue_id]
            if agents_available is not None:
                q.agents_available = agents_available
            if active_calls is not None:
                q.active_calls = active_calls
            if queued_calls is not None:
                q.queued_calls = queued_calls
            if is_active is not None:
                q.is_active = is_active

    # ── Query ─────────────────────────────────────────────────────────────

    def get_all_queues(self) -> List[SkillQueue]:
        """Return all registered queues sorted by priority ascending."""
        with self._lock:
            return sorted(self._queues.values(), key=lambda q: q.priority)

    def get_eligible_queues(
        self,
        required_skills: Optional[List[str]] = None,
    ) -> List[SkillQueue]:
        """
        Return queues eligible for overflow, filtered by skill tags.

        Args:
            required_skills: If provided, queue must have ALL required skills.
                             If None, all active queues are considered.

        Returns:
            List of eligible SkillQueue objects sorted by priority ASC,
            then free_agents DESC.
        """
        with self._lock:
            eligible = [
                q for q in self._queues.values()
                if q.is_eligible_for_overflow
                and (
                    required_skills is None
                    or all(s in q.skill_tags for s in required_skills)
                )
            ]
        return sorted(
            eligible,
            key=lambda q: (q.priority, -q.free_agents),
        )

    def cooldown_active(self) -> bool:
        """
        True if a reroute occurred within the last ROUTING_COOLDOWN_SECONDS.
        Prevents thrashing when SLA/Occ hover around threshold boundaries.
        """
        if self._last_reroute_at is None:
            return False
        elapsed = (datetime.utcnow() - self._last_reroute_at).total_seconds()
        return elapsed < ROUTING_COOLDOWN_SECONDS

    # ── Log ───────────────────────────────────────────────────────────────

    def append_decision(self, decision: RoutingDecision) -> None:
        """Append a RoutingDecision to the in-memory audit log."""
        with self._lock:
            self._log.append(decision)
            self._last_reroute_at = decision.decided_at

    def get_decision_log(self, limit: int = 50) -> List[RoutingDecision]:
        """Return most recent routing decisions, newest first."""
        with self._lock:
            return list(reversed(self._log[-limit:]))

    def snapshot(self) -> List[dict]:
        """
        Export a JSON-serializable snapshot of all queue states.
        Used by the dashboard renderer.
        """
        with self._lock:
            return [
                {
                    "queue_id":         q.queue_id,
                    "display_name":     q.display_name,
                    "skill_tags":       q.skill_tags,
                    "agents_capacity":  q.agents_capacity,
                    "agents_available": q.agents_available,
                    "active_calls":     q.active_calls,
                    "queued_calls":     q.queued_calls,
                    "occupancy_pct":    round(q.occupancy * 100, 1),
                    "free_agents":      q.free_agents,
                    "is_active":        q.is_active,
                    "priority":         q.priority,
                    "eligible":         q.is_eligible_for_overflow,
                }
                for q in sorted(self._queues.values(), key=lambda x: x.priority)
            ]


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: ROUTING STRATEGY IMPLEMENTATIONS
# Each strategy takes the registry + erlang output and returns
# a list of (queue_id, calls_to_route) tuples.
# ─────────────────────────────────────────────────────────────────────────────

def _strategy_priority_overflow(
    registry: SkillQueueRegistry,
    overflow_calls: int,
    required_skills: Optional[List[str]] = None,
) -> List[Tuple[str, int]]:
    """
    STRATEGY: Priority Overflow
    Route overflow calls to eligible queues in priority order.
    Fill the highest-priority queue first, then spill to the next.

    Returns:
        List of (queue_id, calls_assigned) tuples.
    """
    eligible   = registry.get_eligible_queues(required_skills)
    assignments: List[Tuple[str, int]] = []
    remaining  = overflow_calls

    for queue in eligible:
        if remaining <= 0:
            break
        capacity    = queue.free_agents
        assignable  = min(remaining, capacity)
        if assignable > 0:
            assignments.append((queue.queue_id, assignable))
            remaining -= assignable

    if remaining > 0:
        logger.warning(
            "Priority overflow: %d calls could not be assigned to any queue.",
            remaining,
        )

    return assignments


def _strategy_round_robin(
    registry: SkillQueueRegistry,
    overflow_calls: int,
    required_skills: Optional[List[str]] = None,
) -> List[Tuple[str, int]]:
    """
    STRATEGY: Round Robin
    Distribute overflow calls evenly across all eligible queues.

    Returns:
        List of (queue_id, calls_assigned) tuples.
    """
    eligible = registry.get_eligible_queues(required_skills)
    if not eligible:
        logger.warning("Round robin: no eligible queues available.")
        return []

    base_share  = overflow_calls // len(eligible)
    remainder   = overflow_calls % len(eligible)
    assignments : List[Tuple[str, int]] = []

    for i, queue in enumerate(eligible):
        share = base_share + (1 if i < remainder else 0)
        share = min(share, queue.free_agents)
        if share > 0:
            assignments.append((queue.queue_id, share))

    return assignments


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: MASTER ROUTING TRIGGER
# Single public entry point called by the Ingestor on threshold breach.
# ─────────────────────────────────────────────────────────────────────────────

def trigger_routing_action(
    action: RoutingAction,
    erlang_output: ErlangCOutput,
    registry: SkillQueueRegistry,
    strategy: str = "priority_overflow",
    required_skills: Optional[List[str]] = None,
    overflow_calls: Optional[int] = None,
) -> Optional[RoutingDecision]:
    """
    Execute a routing action in response to an SLA or occupancy breach.

    Args:
        action:          RoutingAction signal from threshold evaluator.
        erlang_output:   ErlangCOutput for the breaching interval.
        registry:        Live SkillQueueRegistry instance.
        strategy:        Routing strategy: 'priority_overflow' | 'round_robin'.
        required_skills: Optional skill filter for eligible queues.
        overflow_calls:  Number of calls to redistribute.
                         Defaults to queued_calls across all queues if None.

    Returns:
        RoutingDecision audit record, or None if action was NORMAL/blocked.
    """

    # ── Guard: NORMAL action — nothing to do ──────────────────────────────
    if action == RoutingAction.NORMAL:
        logger.debug("Routing action NORMAL — no reroute required.")
        return None

    # ── Guard: ESCALATE — routing halted, HITL owns this ─────────────────
    if action == RoutingAction.ESCALATE:
        logger.error(
            "Routing action ESCALATE received — routing engine defers to HITL queue."
        )
        return None

    # ── Guard: Cooldown active ────────────────────────────────────────────
    if registry.cooldown_active():
        logger.info(
            "Routing cooldown active (%ds window) — skipping reroute.",
            ROUTING_COOLDOWN_SECONDS,
        )
        return None

    # ── Determine overflow call count ─────────────────────────────────────
    if overflow_calls is None:
        overflow_calls = sum(
            q.queued_calls for q in registry.get_all_queues()
        )
        overflow_calls = max(overflow_calls, 1)   # minimum 1 to log intent

    logger.warning(
        "ROUTING TRIGGER: action=%s | SLA=%.1f%% | Occ=%.1f%% | overflow=%d",
        action.value,
        erlang_output.predicted_sla_pct * 100,
        erlang_output.predicted_occupancy * 100,
        overflow_calls,
    )

    # ── Execute strategy ──────────────────────────────────────────────────
    if strategy == "round_robin":
        assignments = _strategy_round_robin(registry, overflow_calls, required_skills)
    else:
        assignments = _strategy_priority_overflow(registry, overflow_calls, required_skills)

    if not assignments:
        logger.error(
            "Routing trigger fired but NO eligible queues found. "
            "All queues may be at capacity."
        )
        rationale = (
            f"SKILL_REROUTE attempted but no eligible queues available. "
            f"SLA={erlang_output.predicted_sla_pct*100:.1f}% "
            f"Occ={erlang_output.predicted_occupancy*100:.1f}%."
        )
        decision = RoutingDecision(
            decision_id=str(uuid.uuid4()),
            decided_at=datetime.utcnow(),
            trigger_action=action,
            source_interval_id=erlang_output.interval_id,
            predicted_sla_pct=erlang_output.predicted_sla_pct,
            predicted_occupancy=erlang_output.predicted_occupancy,
            agents_required=erlang_output.agents_required_net,
            queues_rerouted_to=[],
            calls_redistributed=0,
            rationale=rationale,
        )
        registry.append_decision(decision)
        return decision

    # ── Apply assignments to registry state ───────────────────────────────
    total_redistributed = 0
    queues_used: List[str] = []

    for queue_id, calls_assigned in assignments:
        registry.update_queue_state(
            queue_id=queue_id,
            active_calls=None,   # ACD owns active_calls; we only log intent
        )
        queues_used.append(queue_id)
        total_redistributed += calls_assigned
        logger.info(
            "  → Routed %d call(s) to queue [%s]",
            calls_assigned, queue_id,
        )

    # ── Build rationale string ────────────────────────────────────────────
    rationale = (
        f"SKILL_REROUTE ({strategy}): "
        f"SLA={erlang_output.predicted_sla_pct*100:.1f}% below threshold. "
        f"Occ={erlang_output.predicted_occupancy*100:.1f}%. "
        f"Redistributed {total_redistributed} call(s) across "
        f"{len(queues_used)} queue(s): {', '.join(queues_used)}."
    )

    decision = RoutingDecision(
        decision_id=str(uuid.uuid4()),
        decided_at=datetime.utcnow(),
        trigger_action=action,
        source_interval_id=erlang_output.interval_id,
        predicted_sla_pct=erlang_output.predicted_sla_pct,
        predicted_occupancy=erlang_output.predicted_occupancy,
        agents_required=erlang_output.agents_required_net,
        queues_rerouted_to=queues_used,
        calls_redistributed=total_redistributed,
        rationale=rationale,
    )

    registry.append_decision(decision)
    logger.info("Routing decision recorded [%s]: %s", decision.decision_id[:8], rationale)

    return decision


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: DEFAULT REGISTRY FACTORY
# Bootstraps a production-representative queue registry.
# Swap these entries for your actual ACD queue IDs in production.
# ─────────────────────────────────────────────────────────────────────────────

def build_default_registry() -> SkillQueueRegistry:
    """
    Build and return a SkillQueueRegistry pre-populated with
    representative skill queues for a general-purpose call center.

    In production: replace with ACD API call to fetch live queue config.
    """
    registry = SkillQueueRegistry()

    default_queues = [
        SkillQueue(
            queue_id="Q_GENERAL",
            display_name="General Inquiries",
            skill_tags=["general", "billing", "account"],
            agents_capacity=30,
            agents_available=22,
            active_calls=18,
            queued_calls=4,
            priority=1,
        ),
        SkillQueue(
            queue_id="Q_TECH_TIER1",
            display_name="Technical Support Tier 1",
            skill_tags=["technical", "troubleshooting", "connectivity"],
            agents_capacity=20,
            agents_available=15,
            active_calls=12,
            queued_calls=2,
            priority=2,
        ),
        SkillQueue(
            queue_id="Q_TECH_TIER2",
            display_name="Technical Support Tier 2",
            skill_tags=["technical", "escalation", "advanced"],
            agents_capacity=10,
            agents_available=8,
            active_calls=4,
            queued_calls=0,
            priority=3,
        ),
        SkillQueue(
            queue_id="Q_BILLING",
            display_name="Billing & Payments",
            skill_tags=["billing", "payments", "disputes"],
            agents_capacity=15,
            agents_available=10,
            active_calls=8,
            queued_calls=1,
            priority=2,
        ),
        SkillQueue(
            queue_id="Q_RETENTION",
            display_name="Retention & Cancellations",
            skill_tags=["retention", "cancellation", "offers"],
            agents_capacity=8,
            agents_available=6,
            active_calls=3,
            queued_calls=0,
            priority=4,
        ),
    ]

    for q in default_queues:
        registry.register_queue(q)

    logger.info(
        "Default registry built: %d queues registered.",
        len(default_queues),
    )
    return registry