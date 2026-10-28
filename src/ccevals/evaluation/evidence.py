"""Pure evidence views used by evaluators.

Evaluators work on immutable ``EventView`` records so the same code grades
stored production evidence, sandbox runs and in-memory contract fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Stage ordering for refund legs. Negative stages are terminal failures.
POSITIVE_STAGE_RANK = {"submitted": 1, "credited": 2, "completed": 2}
NEGATIVE_STAGES = {"failed", "reversed"}


@dataclass(frozen=True)
class EventView:
    id: str
    event_type: str
    source: str
    occurred_at: datetime
    observed_at: datetime
    payload: dict[str, Any]
    entity_refs: dict[str, Any] = field(default_factory=dict)
    logical_action_id: str | None = None
    source_object_version: int | None = None
    evidence_authority: str = ""
    quarantined: bool = False
    customer_token: str | None = None
    order_token: str | None = None
    conversation_id: str | None = None

    def sort_key(self) -> tuple:
        version = self.source_object_version if self.source_object_version is not None else -1
        return (version, self.occurred_at, self.observed_at, self.id)


@dataclass(frozen=True)
class SourceStatus:
    """What the platform knows about each evidence source for this tenant."""

    verified_sources: frozenset[str] = frozenset()
    # Latest heartbeat watermark per source: the source asserts it has delivered
    # every event that occurred before this time.
    watermarks: dict[str, datetime] = field(default_factory=dict)

    def covers(self, source: str, moment: datetime) -> bool:
        watermark = self.watermarks.get(source)
        return watermark is not None and watermark >= moment


@dataclass
class TaskFacts:
    task_key: str
    intent: str
    opened_at: datetime
    customer_token: str | None
    order_token: str | None
    affected_lines: dict[str, int]
    environment: str = "production"
    market: str | None = None


def usable(events: list[EventView]) -> list[EventView]:
    """Quarantined evidence stays visible but cannot satisfy a check."""
    return [e for e in events if not e.quarantined]


def of_type(events: list[EventView], *types: str) -> list[EventView]:
    return sorted((e for e in events if e.event_type in types), key=lambda e: (e.occurred_at, e.id))


def latest_version(events: list[EventView]) -> tuple[EventView | None, bool]:
    """Return the authoritative latest record and whether sources conflict.

    A late-arriving older version never replaces a newer one. Two different
    payloads that claim the same version are a conflict, not a tie-break.
    """
    if not events:
        return None, False
    ordered = sorted(events, key=EventView.sort_key)
    top = ordered[-1]
    conflict = any(
        e.source_object_version is not None
        and e.source_object_version == top.source_object_version
        and e.payload != top.payload
        for e in ordered[:-1]
    )
    return top, conflict


def as_of(events: list[EventView], moment: datetime) -> list[EventView]:
    return [e for e in events if e.occurred_at <= moment]


@dataclass
class LegState:
    tender_type: str
    stage: str | None = None
    amount_minor: int = 0
    provider_refs: set[str] = field(default_factory=set)
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    event_ids: list[str] = field(default_factory=list)
    duplicate_effect: bool = False


def leg_states(leg_events: list[EventView], completion_stage: str, expected_minor: dict[str, int]) -> dict[str, LegState]:
    """Reconcile leg status events into one state per tender type.

    Events are grouped by provider reference; each group is resolved by source
    version then occurrence time, so out-of-order delivery cannot regress state.
    Two provider references that both reached a positive stage for one leg are a
    duplicate financial effect.
    """
    by_leg: dict[str, dict[str, list[EventView]]] = {}
    for event in leg_events:
        tender = event.payload.get("tender_type")
        if not tender:
            continue
        ref = str(event.payload.get("provider_ref") or event.logical_action_id or "default")
        by_leg.setdefault(tender, {}).setdefault(ref, []).append(event)

    target_rank = POSITIVE_STAGE_RANK.get(completion_stage, 2)
    states: dict[str, LegState] = {}
    for tender, groups in by_leg.items():
        state = LegState(tender_type=tender)
        positive_refs = 0
        total_effect = 0
        best: tuple[int, str, int] | None = None
        for ref, group in groups.items():
            latest, _ = latest_version(group)
            assert latest is not None
            stage = latest.payload.get("stage")
            amount = int(latest.payload.get("amount_minor", 0))
            state.event_ids.extend(e.id for e in group)
            state.provider_refs.add(ref)
            if stage in POSITIVE_STAGE_RANK:
                positive_refs += 1
                total_effect += amount
            rank = -1 if stage in NEGATIVE_STAGES else POSITIVE_STAGE_RANK.get(stage, 0)
            if best is None or rank > best[0]:
                best = (rank, stage, amount)
            for e in sorted(group, key=EventView.sort_key):
                stage_e = e.payload.get("stage")
                if POSITIVE_STAGE_RANK.get(stage_e, 0) >= target_rank and state.completed_at is None:
                    state.completed_at = e.occurred_at
                if stage_e in NEGATIVE_STAGES:
                    state.failed_at = e.occurred_at
        assert best is not None
        state.stage = best[1]
        state.amount_minor = best[2]
        if state.stage in NEGATIVE_STAGES:
            state.completed_at = None
        expected = expected_minor.get(tender)
        if positive_refs > 1 or (expected is not None and total_effect > expected):
            state.duplicate_effect = True
        states[tender] = state
    return states
