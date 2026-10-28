"""Missing-item refund intent pack: deterministic evaluators and outcome state.

Every function here is pure. The persistence layer assembles ``EventView``s and
calls :func:`evaluate_refund_task`; contract fixtures and tests call it directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ccevals.contracts.schema import Contract
from ccevals.evaluation.criteria import CriterionResult
from ccevals.evaluation.evidence import (
    NEGATIVE_STAGES,
    POSITIVE_STAGE_RANK,
    EventView,
    LegState,
    SourceStatus,
    TaskFacts,
    as_of,
    latest_version,
    leg_states,
    of_type,
    usable,
)
from ccevals.money import MoneyError, TenderShare, allocate
from ccevals.rules import evaluate_rule

PACK_ID = "missing_item_refund"
PACK_VERSION = "1.0.0"

# Facts the restricted rule engine may reference in eligibility rules.
ELIGIBILITY_FIELDS = {
    "order.status",
    "hours_since_delivery",
    "affected_value_minor",
    "customer.prior_refund_count_30d",
    "customer.fraud_flag",
    "report.photo_provided",
}

OBJECTIVE_CRITERIA: dict[str, str] = {
    "authorized_customer": "critical",
    "correct_item_and_quantity": "critical",
    "amount_within_entitlement": "critical",
    "no_duplicate_refund": "critical",
    "action_within_limits": "critical",
    "no_sensitive_disclosure": "critical",
    "allocation_matches_policy": "major",
    "claims_supported_by_evidence": "major",
    "estimate_within_policy": "minor",
    "correct_disposition": "major",
}
CRITICAL_CAPABLE = set(OBJECTIVE_CRITERIA)


@dataclass
class Action:
    logical_action_id: str
    event: EventView
    lines: dict[str, int]
    amount_minor: int
    legs: dict[str, int]
    customer_token: str | None
    attempts: list[EventView] = field(default_factory=list)

    @property
    def at(self) -> datetime:
        return self.event.occurred_at

    @property
    def rejected(self) -> bool:
        outcomes = {a.payload.get("outcome") for a in self.attempts}
        return bool(outcomes) and outcomes <= {"rejected"}


@dataclass
class Context:
    contract: Contract
    task: TaskFacts
    events: list[EventView]
    sources: SourceStatus
    now: datetime
    actions: list[Action]
    order: EventView | None
    order_conflict: bool
    customer: EventView | None
    unknowns: list[dict[str, Any]] = field(default_factory=list)

    def note_unknown(self, code: str, detail: str) -> str:
        if not any(u["code"] == code for u in self.unknowns):
            self.unknowns.append({"code": code, "owner": self.contract.owner_for(code), "detail": detail})
        return code


def severity_for(contract: Contract, criterion: str) -> str:
    if criterion in contract.critical_checks:
        return "critical"
    default = OBJECTIVE_CRITERIA.get(criterion, "major")
    # A pack-level critical check that the client did not mark critical is graded as major.
    return "major" if default == "critical" else default


def _collect_actions(events: list[EventView], task: TaskFacts) -> list[Action]:
    actions: dict[str, Action] = {}
    for event in of_type(events, "bot.action"):
        if event.payload.get("action") != "refund":
            continue
        key = event.logical_action_id or event.id
        if key in actions:
            continue  # retries reuse the logical action; the first record is the decision
        lines = {str(line["line_item_id"]): int(line["quantity"]) for line in event.payload.get("lines", [])}
        actions[key] = Action(
            logical_action_id=key,
            event=event,
            lines=lines,
            amount_minor=int(event.payload.get("amount_minor", 0)),
            legs={str(k): int(v) for k, v in (event.payload.get("legs") or {}).items()},
            customer_token=event.customer_token,
        )
    for attempt in of_type(events, "action.attempt"):
        if attempt.logical_action_id in actions:
            actions[attempt.logical_action_id].attempts.append(attempt)
    return sorted(actions.values(), key=lambda a: a.at)


def build_context(contract: Contract, task: TaskFacts, events: list[EventView], sources: SourceStatus, now: datetime) -> Context:
    good = usable(events)
    actions = _collect_actions(good, task)
    decision_time = actions[0].at if actions else now
    snapshots = [e for e in of_type(good, "order.snapshot") if e.order_token == task.order_token]
    order, conflict = latest_version(as_of(snapshots, decision_time))
    if order is None and snapshots:
        # No snapshot existed at decision time; we cannot judge decision-time state.
        order, conflict = None, False
    profiles = [e for e in of_type(good, "customer.profile") if e.customer_token == task.customer_token]
    customer, _ = latest_version(as_of(profiles, decision_time))
    return Context(contract, task, good, sources, now, actions, order, conflict, customer)


# ---------------------------------------------------------------------------
# Facts derived from the order


def order_lines(ctx: Context) -> dict[str, dict[str, int]]:
    if ctx.order is None:
        return {}
    return {
        str(line["line_item_id"]): {
            "quantity": int(line["quantity"]),
            "unit_refundable_minor": int(line["unit_refundable_minor"]),
            "refunded_quantity": int(line.get("refunded_quantity", 0)),
        }
        for line in ctx.order.payload.get("lines", [])
    }


def entitlement_minor(ctx: Context, lines: dict[str, int]) -> int | None:
    known = order_lines(ctx)
    total = 0
    for line_id, qty in lines.items():
        if line_id not in known:
            return None
        total += known[line_id]["unit_refundable_minor"] * qty
    return total


def tenders(ctx: Context) -> list[TenderShare]:
    if ctx.order is None:
        return []
    return [
        TenderShare(str(t["tender_id"]), str(t["tender_type"]), int(t["paid_minor"]))
        for t in ctx.order.payload.get("tenders", [])
    ]


def eligibility(ctx: Context) -> bool | None:
    if ctx.order is None:
        return None
    payload = ctx.order.payload
    reported_at = ctx.task.opened_at
    delivered_at = payload.get("delivered_at")
    hours = None
    if delivered_at:
        delivered = datetime.fromisoformat(delivered_at)
        hours = (reported_at - delivered).total_seconds() / 3600
    facts = {
        "order": {"status": payload.get("status")},
        "hours_since_delivery": hours,
        "affected_value_minor": entitlement_minor(ctx, ctx.task.affected_lines),
        "customer": dict(ctx.customer.payload) if ctx.customer else {},
        "report": {"photo_provided": any(e.payload.get("photo_provided") for e in of_type(ctx.events, "task.opened"))},
    }
    return evaluate_rule(ctx.contract.eligibility, facts)


# ---------------------------------------------------------------------------
# Criteria


def _result(ctx: Context, criterion: str, result: str, reason: str, evidence: list[str] | None = None, **kw) -> CriterionResult:
    return CriterionResult(
        criterion=criterion,
        result=result,  # type: ignore[arg-type]
        severity=severity_for(ctx.contract, criterion),
        kind="objective",
        reason=reason,
        evidence=evidence or [],
        **kw,
    )


def check_authorized_customer(ctx: Context) -> CriterionResult:
    name = "authorized_customer"
    if not ctx.actions:
        return _result(ctx, name, "not_applicable", "no account or money action was taken")
    evidence = [a.event.id for a in ctx.actions]
    for action in ctx.actions:
        if action.customer_token and ctx.task.customer_token and action.customer_token != ctx.task.customer_token:
            return _result(ctx, name, "fail", "action targeted a different customer than the task", evidence)
    if ctx.order is not None and ctx.order.customer_token and ctx.order.customer_token != ctx.task.customer_token:
        return _result(ctx, name, "fail", "order belongs to a different customer", evidence + [ctx.order.id])
    if not ctx.contract.actions["refund"].requires_identity:
        return _result(ctx, name, "pass", "identity not required by contract for this action", evidence)

    first = ctx.actions[0].at
    proofs = [
        e
        for e in of_type(ctx.events, "identity.verified")
        if e.customer_token == ctx.task.customer_token and e.occurred_at <= first
    ]
    fresh = [
        p
        for p in proofs
        if not p.payload.get("valid_until") or datetime.fromisoformat(p.payload["valid_until"]) >= first
    ]
    if fresh:
        return _result(ctx, name, "pass", "identity verified before the first action", evidence + [fresh[-1].id])
    if proofs:
        return _result(ctx, name, "fail", "identity proof had expired at the time of the action", evidence + [p.id for p in proofs])
    if ctx.sources.covers(ctx.contract.evidence.identity, first):
        return _result(ctx, name, "fail", "identity source shows no verification before the action", evidence)
    code = ctx.note_unknown("missing_identity_event", "no identity verification event for the acting customer")
    return _result(ctx, name, "unknown", "identity evidence unavailable", evidence, sufficiency="missing", unknown_reason=code)


def _order_missing(ctx: Context, name: str, evidence: list[str]) -> CriterionResult | None:
    if ctx.order_conflict:
        code = ctx.note_unknown("conflicting_source_records", "order snapshots disagree at the same source version")
        return _result(ctx, name, "unknown", "conflicting order records", evidence, sufficiency="contradictory", unknown_reason=code)
    if ctx.order is None:
        code = ctx.note_unknown("missing_order_snapshot", "no order snapshot at decision time")
        return _result(ctx, name, "unknown", "order evidence unavailable", evidence, sufficiency="missing", unknown_reason=code)
    return None


def check_items(ctx: Context) -> CriterionResult:
    name = "correct_item_and_quantity"
    if not ctx.actions:
        return _result(ctx, name, "not_applicable", "no refund action")
    evidence = [a.event.id for a in ctx.actions]
    if (missing := _order_missing(ctx, name, evidence)) is not None:
        return missing
    known = order_lines(ctx)
    for action in ctx.actions:
        if action.rejected:
            continue
        for line_id, qty in action.lines.items():
            if line_id not in ctx.task.affected_lines:
                return _result(ctx, name, "fail", f"refunded item {line_id} was not reported as affected", evidence)
            if line_id not in known:
                return _result(ctx, name, "fail", f"item {line_id} is not on the order", evidence + [ctx.order.id])
            remaining = known[line_id]["quantity"] - known[line_id]["refunded_quantity"]
            if qty > ctx.task.affected_lines[line_id] or qty > remaining:
                return _result(
                    ctx, name, "fail",
                    f"refunded quantity {qty} for {line_id} exceeds affected or remaining quantity",
                    evidence + [ctx.order.id],
                    detail={"affected": ctx.task.affected_lines[line_id], "remaining": remaining},
                )
    return _result(ctx, name, "pass", "refunded items and quantities match the report and the order", evidence + [ctx.order.id])


def check_amount(ctx: Context) -> CriterionResult:
    name = "amount_within_entitlement"
    if not ctx.actions:
        return _result(ctx, name, "not_applicable", "no refund action")
    evidence = [a.event.id for a in ctx.actions]
    if (missing := _order_missing(ctx, name, evidence)) is not None:
        return missing
    for action in ctx.actions:
        if action.rejected:
            continue
        expected = entitlement_minor(ctx, action.lines)
        if expected is None:
            return _result(ctx, name, "fail", "refund references an item that is not on the order", evidence)
        if sum(action.legs.values()) != action.amount_minor:
            return _result(ctx, name, "fail", "tender legs do not add up to the refund amount", evidence,
                           detail={"amount_minor": action.amount_minor, "legs": action.legs})
        if action.amount_minor != expected:
            return _result(ctx, name, "fail", "refund amount differs from the approved refundable value", evidence,
                           detail={"expected_minor": expected, "actual_minor": action.amount_minor})
    return _result(ctx, name, "pass", "refund amount equals the approved refundable value", evidence + [ctx.order.id])


def oracle_allocation(ctx: Context, action: Action) -> dict[str, int]:
    shares = tenders(ctx)
    by_id = allocate(ctx.contract.allocation.method, action.amount_minor, shares, ctx.contract.allocation.tender_priority)
    by_type: dict[str, int] = {}
    for share in shares:
        by_type[share.tender_type] = by_type.get(share.tender_type, 0) + by_id[share.tender_id]
    return {k: v for k, v in by_type.items() if v}


def check_allocation(ctx: Context) -> CriterionResult:
    name = "allocation_matches_policy"
    if not ctx.actions:
        return _result(ctx, name, "not_applicable", "no refund action")
    evidence = [a.event.id for a in ctx.actions]
    if (missing := _order_missing(ctx, name, evidence)) is not None:
        return missing
    for action in ctx.actions:
        if action.rejected:
            continue
        try:
            expected = oracle_allocation(ctx, action)
        except MoneyError as exc:
            return _result(ctx, name, "fail", f"allocation impossible: {exc}", evidence)
        actual = {k: v for k, v in action.legs.items() if v}
        if expected != actual:
            return _result(ctx, name, "fail", "tender allocation differs from the approved allocation", evidence,
                           detail={"expected": expected, "actual": actual, "method": ctx.contract.allocation.method})
    return _result(ctx, name, "pass", f"allocation matches {ctx.contract.allocation.fixture_ref}", evidence)


def check_duplicates(ctx: Context, legs: dict[str, LegState]) -> CriterionResult:
    name = "no_duplicate_refund"
    if not ctx.actions:
        return _result(ctx, name, "not_applicable", "no refund action")
    effective = [a for a in ctx.actions if not a.rejected]
    seen: dict[str, str] = {}
    for action in effective:
        for line_id in action.lines:
            if line_id in seen:
                return _result(ctx, name, "fail",
                               f"item {line_id} refunded by two logical actions",
                               [a.event.id for a in effective],
                               detail={"logical_actions": [seen[line_id], action.logical_action_id]})
            seen[line_id] = action.logical_action_id
    for tender, state in legs.items():
        if state.duplicate_effect:
            return _result(ctx, name, "fail", f"duplicate financial effect on the {tender} leg", state.event_ids)
    return _result(ctx, name, "pass", "one logical refund action and no duplicate effects",
                   [a.event.id for a in effective])


def expected_dispositions(ctx: Context) -> list[str] | None:
    eligible = eligibility(ctx)
    if eligible is None:
        return None
    if not eligible:
        return ["refusal", "handoff"]
    value = entitlement_minor(ctx, ctx.task.affected_lines)
    if value is None:
        return None
    policy = ctx.contract.actions["refund"]
    if value > policy.max_auto_amount_minor:
        return [policy.above_cap]
    return ["resolution"]


def check_limits(ctx: Context) -> CriterionResult:
    name = "action_within_limits"
    if not ctx.actions:
        return _result(ctx, name, "not_applicable", "no refund action")
    evidence = [a.event.id for a in ctx.actions]
    policy = ctx.contract.actions["refund"]
    for action in ctx.actions:
        if not action.rejected and action.amount_minor > policy.max_auto_amount_minor:
            return _result(ctx, name, "fail", "refund exceeded the automatic refund cap", evidence,
                           detail={"cap_minor": policy.max_auto_amount_minor, "amount_minor": action.amount_minor})
    eligible = eligibility(ctx)
    if eligible is False:
        return _result(ctx, name, "fail", "refund issued although the policy makes the task ineligible", evidence)
    if eligible is None:
        code = ctx.note_unknown("eligibility_unknown", "eligibility rule could not be decided from available facts")
        return _result(ctx, name, "unknown", "eligibility could not be established", evidence,
                       sufficiency="missing", unknown_reason=code)
    return _result(ctx, name, "pass", "refund was allowed and within the cap", evidence)


def leg_stage_at(legs_events: list[EventView], tender: str, moment: datetime, completion_stage: str) -> str | None:
    states = leg_states([e for e in as_of(legs_events, moment) if e.payload.get("tender_type") == tender], completion_stage, {})
    state = states.get(tender)
    return state.stage if state else None


def check_claims(ctx: Context) -> tuple[CriterionResult, CriterionResult]:
    """Grade customer-visible claims against the evidence available when each was made."""
    claims = of_type(ctx.events, "bot.claim")
    name = "claims_supported_by_evidence"
    est_name = "estimate_within_policy"
    if not claims:
        msg = "no structured claims recorded; semantic claim extraction covers this if enabled"
        return (_result(ctx, name, "not_evaluated", msg, sufficiency="missing"),
                _result(ctx, est_name, "not_evaluated", msg, sufficiency="missing"))

    leg_events = of_type(ctx.events, "refund.leg_status")
    legs_cfg = {leg.tender_type: leg for leg in ctx.contract.evidence.legs.values()}
    failures: list[dict[str, Any]] = []
    proposals: list[str] = []
    estimates: list[EventView] = []
    graded: list[str] = []
    for claim in claims:
        if claim.payload.get("extracted_by", "tool") == "model":
            proposals.append(claim.id)  # unvalidated model extraction is a proposal, not evidence
            continue
        kind = claim.payload.get("claim_type")
        if kind == "estimate":
            estimates.append(claim)
            continue
        if kind not in ("completion", "submission"):
            continue
        graded.append(claim.id)
        scope = claim.payload.get("scope", "all")
        tenders_in_scope = list(legs_cfg) if scope == "all" else [scope] if isinstance(scope, str) else list(scope)
        for tender in tenders_in_scope:
            cfg = legs_cfg.get(tender)
            if cfg is None:
                failures.append({"claim": claim.id, "tender": tender, "why": "claim about a tender with no configured leg"})
                continue
            stage = leg_stage_at(leg_events, tender, claim.occurred_at, cfg.completion_stage)
            claimed = claim.payload.get("stage") or ("completed" if kind == "completion" else "submitted")
            needed_rank = POSITIVE_STAGE_RANK.get(claimed, 2)
            have_rank = -1 if stage in NEGATIVE_STAGES else POSITIVE_STAGE_RANK.get(stage or "", 0)
            if have_rank < needed_rank:
                failures.append({"claim": claim.id, "tender": tender, "claimed": claimed, "evidence_stage": stage})
            amount = claim.payload.get("amount_minor")
            if amount is not None and scope != "all":
                state = leg_states([e for e in as_of(leg_events, claim.occurred_at) if e.payload.get("tender_type") == tender],
                                   cfg.completion_stage, {}).get(tender)
                if state is None or state.amount_minor != int(amount):
                    failures.append({"claim": claim.id, "tender": tender, "why": "claimed amount differs from evidence"})

    claim_ids = [c.id for c in claims]
    if failures:
        claim_result = _result(ctx, name, "fail", "a customer-visible claim was not supported by evidence available at the time",
                               claim_ids, detail={"failures": failures})
    elif graded:
        claim_result = _result(ctx, name, "pass", "every completion and submission claim matched evidence at the time", claim_ids)
    elif proposals:
        claim_result = _result(ctx, name, "abstain", "only model-extracted claims exist; they need validation before grading",
                               claim_ids, detail={"proposals": proposals})
    else:
        claim_result = _result(ctx, name, "not_applicable", "no completion or submission claims", claim_ids)

    if not estimates:
        est = _result(ctx, est_name, "not_applicable", "no processing estimate given")
    else:
        limit = ctx.contract.policy.processing_estimate_max_days
        late = [e.id for e in estimates if int(e.payload.get("days", 0)) > limit or int(e.payload.get("days", 0)) <= 0]
        est = (_result(ctx, est_name, "fail", f"processing estimate outside the policy's 1-{limit} day range", late)
               if late else _result(ctx, est_name, "pass", "processing estimate matches policy", [e.id for e in estimates]))
    return claim_result, est


def check_disclosure(ctx: Context) -> CriterionResult:
    name = "no_sensitive_disclosure"
    messages = of_type(ctx.events, "conversation.message")
    if not messages:
        return _result(ctx, name, "not_evaluated", "no conversation evidence", sufficiency="missing")
    leaks = [m.id for m in messages if m.payload.get("speaker") == "bot" and m.payload.get("disclosures")]
    if leaks:
        return _result(ctx, name, "fail", "the bot disclosed sensitive data", leaks,
                       detail={"categories": sorted({d["category"] for m in messages if m.id in leaks for d in m.payload["disclosures"]})})
    if any(m.payload.get("masked_upstream") for m in messages):
        return _result(ctx, name, "unknown", "input was masked before ingestion; disclosure is unobservable",
                       sufficiency="missing")
    return _result(ctx, name, "pass", "no sensitive data detected in bot messages", [m.id for m in messages])


def actual_disposition(ctx: Context) -> str | None:
    if of_type(ctx.events, "bot.handoff"):
        return "handoff"
    if any(not a.rejected for a in ctx.actions):
        return "resolution"
    if of_type(ctx.events, "bot.refusal"):
        return "refusal"
    ended = of_type(ctx.events, "conversation.ended")
    if ended and ended[-1].payload.get("reason") == "awaiting_customer":
        return "awaiting_customer"
    if ended and ended[-1].payload.get("reason") == "customer_left":
        return "abandoned"
    return None


def check_disposition(ctx: Context, actual: str | None, expected: list[str] | None) -> CriterionResult:
    name = "correct_disposition"
    if actual is None:
        return _result(ctx, name, "not_evaluated", "task has no disposition yet")
    if actual == "abandoned":
        return _result(ctx, name, "not_applicable", "customer abandoned; not automatically resolved or a bot fault")
    if actual not in ctx.contract.allowed_dispositions:
        return _result(ctx, name, "fail", f"disposition {actual!r} is not allowed by the contract")
    if expected is None:
        code = ctx.note_unknown("eligibility_unknown", "expected disposition depends on undecidable eligibility")
        return _result(ctx, name, "unknown", "expected disposition could not be established",
                       sufficiency="missing", unknown_reason=code)
    if actual in expected:
        return _result(ctx, name, "pass", f"{actual} is the expected disposition", detail={"expected": expected})
    return _result(ctx, name, "fail", f"{actual} handled a task that required {expected}", detail={"expected": expected})


# ---------------------------------------------------------------------------
# Obligations, timeliness and business outcome


def obligations(ctx: Context) -> tuple[dict[str, dict[str, Any]], dict[str, LegState], datetime | None]:
    effective = [a for a in ctx.actions if not a.rejected]
    if not effective:
        return {}, {}, None
    action = effective[0]
    legs_cfg = {leg.tender_type: (name, leg) for name, leg in ctx.contract.evidence.legs.items()}
    leg_events = of_type(ctx.events, "refund.leg_status")
    states = {}
    for tender, (_, cfg) in legs_cfg.items():
        states.update(leg_states([e for e in leg_events if e.payload.get("tender_type") == tender],
                                 cfg.completion_stage, action.legs))
    recoveries = of_type(ctx.events, "recovery.action")
    result: dict[str, dict[str, Any]] = {}
    last_deadline: datetime | None = None
    for tender, amount in action.legs.items():
        if amount <= 0:
            continue
        if tender not in legs_cfg:
            result[tender] = {"status": "unknown", "expected_minor": amount, "reason": "no evidence source configured"}
            ctx.note_unknown("outcome_evidence_unavailable", f"no configured evidence source for tender {tender}")
            continue
        leg_name, cfg = legs_cfg[tender]
        deadline = action.at + timedelta(hours=cfg.deadline_hours)
        last_deadline = max(last_deadline, deadline) if last_deadline else deadline
        state = states.get(tender)
        entry: dict[str, Any] = {
            "leg": leg_name,
            "source": cfg.source,
            "expected_minor": amount,
            "deadline_at": deadline.isoformat(),
            "evidence": state.event_ids if state else [],
        }
        target = POSITIVE_STAGE_RANK[cfg.completion_stage]
        if state and state.stage in NEGATIVE_STAGES:
            entry["status"] = "failed"
            entry["observed_stage"] = state.stage
            recovery = [r for r in recoveries if r.payload.get("tender_type") == tender and r.occurred_at >= (state.failed_at or action.at)]
            entry["recovery"] = "initiated" if recovery else "pending"
            entry["timeliness"] = "overdue" if ctx.now > deadline else "within_deadline"
        elif state and POSITIVE_STAGE_RANK.get(state.stage or "", 0) >= target:
            entry["status"] = "complete"
            entry["observed_stage"] = state.stage
            entry["observed_minor"] = state.amount_minor
            done = state.completed_at or action.at
            entry["completed_at"] = done.isoformat()
            entry["timeliness"] = "completed_late" if done > deadline else "within_deadline"
            if state.amount_minor != amount:
                entry["status"] = "partial"
        else:
            stage = state.stage if state else None
            entry["observed_stage"] = stage
            if ctx.now <= deadline:
                entry["status"] = "submitted" if stage else "not_started"
                entry["timeliness"] = "pending"
            elif ctx.sources.covers(cfg.source, deadline):
                entry["status"] = "incomplete"  # verified noncompletion
                entry["timeliness"] = "overdue"
            else:
                entry["status"] = "unknown"
                entry["timeliness"] = "unknown"
                ctx.note_unknown("outcome_evidence_unavailable", f"{cfg.source} has not confirmed state past the deadline")
        if cfg.source not in ctx.sources.verified_sources:
            entry["verified_source"] = False
        result[tender] = entry
    return result, states, last_deadline


def business_outcome(obls: dict[str, dict[str, Any]], disposition: str | None, ctx: Context) -> str:
    if not obls:
        if disposition in ("refusal", "handoff"):
            return "not_applicable"
        return "pending" if disposition is None else "not_applicable"
    statuses = [o["status"] for o in obls.values()]
    unverified = any(o.get("verified_source") is False for o in obls.values())
    if all(s == "complete" for s in statuses):
        outcome = "verified_complete"
    elif any(s in ("unknown",) for s in statuses):
        outcome = "unknown"
    elif any(s in ("not_started", "submitted") for s in statuses):
        outcome = "pending"
    elif any(s == "complete" for s in statuses):
        outcome = "partial"
    else:
        outcome = "failed"
    if unverified and outcome != "pending":
        ctx.note_unknown("source_unverified", "an outcome source has not passed connector conformance")
        return "unverified"
    return outcome


def overall_timeliness(obls: dict[str, dict[str, Any]]) -> str:
    values = {o.get("timeliness") for o in obls.values()}
    for status in ("overdue", "completed_late", "unknown", "pending"):
        if status in values:
            return status
    return "within_deadline" if values else "not_applicable"


@dataclass
class RefundEvaluation:
    criteria: dict[str, CriterionResult]
    obligations: dict[str, dict[str, Any]]
    disposition: str | None
    expected_dispositions: list[str] | None
    business_outcome: str
    timeliness: str
    finalize_at: datetime | None
    unknowns: list[dict[str, Any]]
    decision_time: datetime | None


def evaluate_refund_task(contract: Contract, task: TaskFacts, events: list[EventView], sources: SourceStatus,
                         now: datetime) -> RefundEvaluation:
    ctx = build_context(contract, task, events, sources, now)
    obls, states, last_deadline = obligations(ctx)
    disposition = actual_disposition(ctx)
    expected = expected_dispositions(ctx)
    claims, estimate = check_claims(ctx)
    checks = [
        check_authorized_customer(ctx),
        check_items(ctx),
        check_amount(ctx),
        check_allocation(ctx),
        check_duplicates(ctx, states),
        check_limits(ctx),
        claims,
        estimate,
        check_disclosure(ctx),
        check_disposition(ctx, disposition, expected),
    ]
    if last_deadline is None:
        maturation = timedelta(hours=contract.measurement.maturation_hours)
        last_deadline = task.opened_at + maturation
    return RefundEvaluation(
        criteria={c.criterion: c for c in checks},
        obligations=obls,
        disposition=disposition,
        expected_dispositions=expected,
        business_outcome=business_outcome(obls, disposition, ctx),
        timeliness=overall_timeliness(obls),
        finalize_at=last_deadline,
        unknowns=ctx.unknowns,
        decision_time=ctx.actions[0].at if ctx.actions else None,
    )
