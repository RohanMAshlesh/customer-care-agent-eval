"""Service-resolution pack: did the downstream work that resolves the request happen?

Pure functions over ``EventView``s, like the refund pack. The conversation is
treated as a set of promises; tickets, logistics events and sign-in records are
the evidence that the promises were kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ccevals.contracts.service import SERVICE_CRITERIA, SERVICE_PACK_ID, ServiceContract
from ccevals.evaluation.criteria import CriterionResult
from ccevals.evaluation.evidence import EventView, SourceStatus, TaskFacts, latest_version, of_type
from ccevals.evaluation.refund import RefundEvaluation

PACK_VERSION = "1.0.0"
RESOLVED_STATES = {"resolved", "closed"}


@dataclass
class Ticket:
    number: str
    created: EventView
    current: EventView
    events: list[EventView] = field(default_factory=list)

    @property
    def created_at(self) -> datetime:
        return self.created.occurred_at

    def resolved_at(self) -> datetime | None:
        for e in sorted(self.events, key=EventView.sort_key):
            if e.payload.get("state") in RESOLVED_STATES:
                return e.occurred_at
        return None


class _Ctx:
    def __init__(self, contract: ServiceContract, task: TaskFacts, events: list[EventView], sources: SourceStatus,
                 now: datetime) -> None:
        self.contract, self.task, self.events, self.sources, self.now = contract, task, events, sources, now
        self.unknowns: list[dict[str, Any]] = []

    def unknown(self, code: str, detail: str) -> str:
        if not any(u["code"] == code for u in self.unknowns):
            self.unknowns.append({"code": code, "owner": self.contract.owner_for(code), "detail": detail})
        return code

    def result(self, name: str, result: str, reason: str, evidence: list[str] | None = None, **kw) -> CriterionResult:
        severity = "critical" if name in self.contract.critical_checks else (
            "major" if SERVICE_CRITERIA.get(name) == "critical" else SERVICE_CRITERIA.get(name, "major"))
        return CriterionResult(name, result, severity, "objective", reason, evidence or [], **kw)  # type: ignore[arg-type]


def tickets(events: list[EventView], system: str) -> list[Ticket]:
    by_number: dict[str, list[EventView]] = {}
    for e in of_type(events, "ticket.created", "ticket.updated"):
        if e.source == system and e.payload.get("number"):
            by_number.setdefault(e.payload["number"], []).append(e)
    out = []
    for number, group in by_number.items():
        created = [e for e in group if e.event_type == "ticket.created"]
        if not created:
            continue
        current, _ = latest_version(group)
        out.append(Ticket(number, sorted(created, key=EventView.sort_key)[0], current, group))  # type: ignore[arg-type]
    return sorted(out, key=lambda t: t.created_at)


def _first_contact_end(ctx: _Ctx) -> datetime | None:
    ended = of_type(ctx.events, "conversation.ended")
    return ended[0].occurred_at if ended else None


def disposition(ctx: _Ctx) -> str | None:
    if of_type(ctx.events, "bot.handoff"):
        return "handoff"
    if of_type(ctx.events, "bot.refusal"):
        return "refusal"
    ended = of_type(ctx.events, "conversation.ended")
    if ended and ended[0].payload.get("reason") == "customer_left" and not of_type(ctx.events, "bot.claim"):
        return "abandoned"
    if ended:
        return "resolution"
    return None


def check_ticket_created(ctx: _Ctx, tks: list[Ticket]) -> CriterionResult:
    req = ctx.contract.ticket
    if req is None:
        return ctx.result("ticket_created", "not_applicable", "this contract requires no ticket")
    deadline = ctx.task.opened_at + timedelta(minutes=req.create_within_minutes)
    on_time = [t for t in tks if t.created_at <= deadline]
    if on_time:
        return ctx.result("ticket_created", "pass", f"{on_time[0].number} created in {req.system} within "
                          f"{req.create_within_minutes} minutes", [on_time[0].created.id])
    if tks:
        late = int((tks[0].created_at - ctx.task.opened_at).total_seconds() // 60)
        return ctx.result("ticket_created", "fail", f"{tks[0].number} was created {late} minutes after contact; "
                          f"policy requires {req.create_within_minutes}", [tks[0].created.id])
    errors = [e for e in of_type(ctx.events, "tool.call") if e.payload.get("outcome") == "error"]
    if ctx.now <= deadline:
        return ctx.result("ticket_created", "not_evaluated", "creation window still open", [e.id for e in errors])
    if ctx.sources.covers(req.system, deadline):
        why = "no ticket exists in " + req.system
        if errors:
            why += f"; the bot's {errors[0].payload.get('tool')} call failed and the failure was not handled"
        return ctx.result("ticket_created", "fail", why, [e.id for e in errors])
    code = ctx.unknown("ticket_evidence_unavailable", f"{req.system} has not confirmed it is current past the deadline")
    return ctx.result("ticket_created", "unknown", "ticket system has not reported", sufficiency="missing",
                      unknown_reason=code)


def check_routing(ctx: _Ctx, tks: list[Ticket]) -> CriterionResult:
    req = ctx.contract.ticket
    if req is None:
        return ctx.result("ticket_routed_correctly", "not_applicable", "this contract requires no ticket")
    if not tks:
        return ctx.result("ticket_routed_correctly", "not_evaluated", "no ticket to inspect")
    first = tks[0].created.payload
    problems = []
    if first.get("category") != req.category:
        problems.append(f"category {first.get('category')!r}, expected {req.category!r}")
    if first.get("assignment_group") != req.assignment_group:
        problems.append(f"queue {first.get('assignment_group')!r}, expected {req.assignment_group!r}")
    if int(first.get("priority", 5)) > req.max_priority:
        problems.append(f"priority P{first.get('priority')}, needs P{req.max_priority} or higher")
    detail = {"initial": {k: first.get(k) for k in ("category", "assignment_group", "priority")},
              "current": {k: tks[0].current.payload.get(k, first.get(k)) for k in ("assignment_group", "priority", "state")}}
    if problems:
        return ctx.result("ticket_routed_correctly", "fail", "; ".join(problems), [tks[0].created.id], detail=detail)
    return ctx.result("ticket_routed_correctly", "pass", f"routed to {req.assignment_group} at P{first.get('priority')}",
                      [tks[0].created.id], detail=detail)


def check_reference(ctx: _Ctx, tks: list[Ticket]) -> CriterionResult:
    req = ctx.contract.ticket
    if req is None or not req.share_reference:
        return ctx.result("reference_shared", "not_applicable", "no reference number required")
    bot_text = [m for m in of_type(ctx.events, "conversation.message") if m.payload.get("speaker") in ("bot", "agent")]
    numbers = {t.number for t in tks}
    quoted = [m for m in bot_text if any(n in m.payload.get("text", "") for n in numbers)]
    if quoted:
        return ctx.result("reference_shared", "pass", "the customer was given a ticket number that exists", [quoted[0].id])
    fake = [c for c in of_type(ctx.events, "bot.claim") if c.payload.get("claim_type") == "ticket_reference"
            and c.payload.get("reference") not in numbers]
    if fake:
        return ctx.result("reference_shared", "fail", "the bot quoted a reference that does not exist in the ticket system",
                          [c.id for c in fake])
    if not tks:
        return ctx.result("reference_shared", "not_evaluated", "no ticket exists yet")
    return ctx.result("reference_shared", "fail", "the customer was never told the ticket number", [m.id for m in bot_text])


def check_promises(ctx: _Ctx, tks: list[Ticket]) -> CriterionResult:
    claims = [c for c in of_type(ctx.events, "bot.claim") if c.payload.get("extracted_by", "tool") != "model"]
    if not claims:
        return ctx.result("promises_kept", "not_applicable", "the bot made no checkable promise")
    failures, pending = [], []
    req = ctx.contract.ticket
    grace = timedelta(minutes=req.create_within_minutes if req else 15)
    signals = of_type(ctx.events, "service.signal")
    for claim in claims:
        kind = claim.payload.get("claim_type")
        if kind in ("escalation", "ticket_reference"):
            backing = [t for t in tks if t.created_at <= claim.occurred_at + grace]
            if backing:
                continue
            if ctx.now <= claim.occurred_at + grace:
                pending.append(claim.id)
            else:
                failures.append({"claim": claim.id, "promise": "escalated / ticket raised", "evidence": "no ticket"})
        elif kind == "resolved":
            want = ctx.contract.resolution
            proof = [s for s in signals if want and s.payload.get("signal") == want.signal and s.occurred_at <= claim.occurred_at]
            if not proof:
                failures.append({"claim": claim.id, "promise": "your issue is fixed",
                                 "evidence": "no resolution signal recorded at that time"})
    if failures:
        return ctx.result("promises_kept", "fail", "the bot promised work that the systems of record do not show",
                          [f["claim"] for f in failures], detail={"failures": failures})
    if pending:
        return ctx.result("promises_kept", "not_evaluated", "a promised ticket can still be created in time", pending)
    return ctx.result("promises_kept", "pass", "every promise is backed by a record", [c.id for c in claims])


def check_sla(ctx: _Ctx, tks: list[Ticket]) -> tuple[CriterionResult, dict[str, Any]]:
    req = ctx.contract.ticket
    if req is None:
        return ctx.result("sla_met", "not_applicable", "no ticket SLA"), {}
    deadline = ctx.task.opened_at + timedelta(hours=req.resolve_within_hours)
    ob: dict[str, Any] = {"label": f"{req.system} ticket", "deadline_at": deadline.isoformat(), "source": req.system}
    if not tks:
        ob.update(status="not_started" if ctx.now <= deadline else "incomplete",
                  timeliness="pending" if ctx.now <= deadline else "overdue")
        if ctx.now <= deadline:
            return ctx.result("sla_met", "not_evaluated", "no ticket yet; SLA window open"), ob
        return ctx.result("sla_met", "fail", "no ticket was ever resolved within the SLA"), ob
    t = tks[0]
    ob.update(number=t.number, state=t.current.payload.get("state"),
              assignment_group=t.current.payload.get("assignment_group", t.created.payload.get("assignment_group")),
              evidence=[e.id for e in t.events])
    done = t.resolved_at()
    if done:
        late = done > deadline
        ob.update(status="complete", completed_at=done.isoformat(), timeliness="completed_late" if late else "within_deadline")
        return (ctx.result("sla_met", "fail" if late else "pass",
                           f"resolved {'after' if late else 'within'} the {req.resolve_within_hours}-hour SLA", [e.id for e in t.events]), ob)
    if ctx.now <= deadline:
        ob.update(status="submitted", timeliness="pending")
        return ctx.result("sla_met", "not_evaluated", "ticket open; SLA window still running"), ob
    if ctx.sources.covers(req.system, deadline):
        ob.update(status="incomplete", timeliness="overdue")
        return ctx.result("sla_met", "fail", f"ticket still open after the {req.resolve_within_hours}-hour SLA"), ob
    ob.update(status="unknown", timeliness="unknown")
    code = ctx.unknown("ticket_evidence_unavailable", f"{req.system} has not reported past the SLA deadline")
    return ctx.result("sla_met", "unknown", "ticket state after the deadline is unknown", unknown_reason=code), ob


def check_repeat_contact(ctx: _Ctx) -> CriterionResult:
    ended = _first_contact_end(ctx)
    if ended is None:
        return ctx.result("no_repeat_contact", "not_evaluated", "first conversation still open")
    window_end = ended + timedelta(hours=ctx.contract.repeat_contact_window_hours)
    again = [m for m in of_type(ctx.events, "conversation.message")
             if m.payload.get("speaker") == "customer" and ended < m.occurred_at <= window_end]
    if again:
        hours = (again[0].occurred_at - ended).total_seconds() / 3600
        return ctx.result("no_repeat_contact", "fail", f"the customer had to contact support again after {hours:.0f} hours",
                          [again[0].id])
    if ctx.now <= window_end:
        return ctx.result("no_repeat_contact", "not_evaluated",
                          f"watching for repeat contact for {ctx.contract.repeat_contact_window_hours} hours")
    return ctx.result("no_repeat_contact", "pass", "no repeat contact within the window")


def check_disclosure(ctx: _Ctx) -> CriterionResult:
    leaks = [m.id for m in of_type(ctx.events, "conversation.message")
             if m.payload.get("speaker") == "bot" and m.payload.get("disclosures")]
    if leaks:
        return ctx.result("no_sensitive_disclosure", "fail", "the bot disclosed sensitive data", leaks)
    return ctx.result("no_sensitive_disclosure", "pass", "no sensitive data detected in bot messages")


def check_disposition(ctx: _Ctx, actual: str | None) -> CriterionResult:
    if actual is None:
        return ctx.result("correct_disposition", "not_evaluated", "conversation still open")
    if actual == "abandoned":
        return ctx.result("correct_disposition", "not_applicable", "customer left before the bot could act")
    expected = ctx.contract.expected_dispositions
    if actual in expected:
        return ctx.result("correct_disposition", "pass", f"{actual} is what the policy expects", detail={"expected": expected})
    return ctx.result("correct_disposition", "fail", f"{actual} where the policy expects {expected}", detail={"expected": expected})


def resolution_state(ctx: _Ctx, tks: list[Ticket]) -> tuple[str, dict[str, Any] | None, datetime]:
    want = ctx.contract.resolution
    feedback = [f for f in of_type(ctx.events, "customer.feedback") if f.payload.get("resolved")]
    if want is None:
        deadline = ctx.task.opened_at + timedelta(hours=ctx.contract.ticket.resolve_within_hours)  # type: ignore[union-attr]
        if tks and tks[0].resolved_at():
            return "verified_resolved", None, deadline
        if ctx.now <= deadline:
            return ("customer_reported_only" if feedback else "pending"), None, deadline
        if ctx.sources.covers(ctx.contract.ticket.system, deadline):  # type: ignore[union-attr]
            return "unresolved", None, deadline
        return "unknown", None, deadline
    deadline = ctx.task.opened_at + timedelta(hours=want.within_hours)
    proof = [s for s in of_type(ctx.events, "service.signal") if s.source == want.source and s.payload.get("signal") == want.signal]
    ob: dict[str, Any] = {"label": want.signal.replace("_", " "), "source": want.source, "deadline_at": deadline.isoformat()}
    if proof:
        done = proof[0].occurred_at
        ob.update(status="complete", completed_at=done.isoformat(), evidence=[proof[0].id],
                  timeliness="completed_late" if done > deadline else "within_deadline")
        return "verified_resolved", ob, deadline
    if feedback:
        ob["customer_said_resolved_at"] = feedback[0].occurred_at.isoformat()
    if ctx.now <= deadline:
        ob.update(status="submitted" if feedback else "not_started", timeliness="pending")
        return ("customer_reported_only" if feedback else "pending"), ob, deadline
    if ctx.sources.covers(want.source, deadline):
        ob.update(status="incomplete", timeliness="overdue")
        return "unresolved", ob, deadline
    ob.update(status="unknown", timeliness="unknown")
    ctx.unknown("resolution_evidence_unavailable", f"{want.source} has not reported past the deadline")
    return "unknown", ob, deadline


def evaluate_service_task(contract: ServiceContract, task: TaskFacts, events: list[EventView], sources: SourceStatus,
                          now: datetime) -> RefundEvaluation:
    ctx = _Ctx(contract, task, events, sources, now)
    tks = tickets(events, contract.ticket.system) if contract.ticket else []
    actual = disposition(ctx)
    sla, ticket_ob = check_sla(ctx, tks)
    outcome, resolution_ob, resolution_deadline = resolution_state(ctx, tks)
    checks = [
        check_ticket_created(ctx, tks),
        check_routing(ctx, tks),
        check_reference(ctx, tks),
        check_promises(ctx, tks),
        sla,
        check_repeat_contact(ctx),
        check_disclosure(ctx),
        check_disposition(ctx, actual),
    ]
    obligations: dict[str, dict[str, Any]] = {}
    if ticket_ob:
        obligations["ticket"] = ticket_ob
    if resolution_ob:
        obligations["resolution"] = resolution_ob
    for ob in obligations.values():
        if ob.get("source") and ob["source"] not in sources.verified_sources:
            ob["verified_source"] = False
            if outcome not in ("pending",):
                ctx.unknown("source_unverified", f"{ob['source']} has not passed connector conformance")
                outcome = "unverified"
    deadlines = [resolution_deadline]
    if contract.ticket:
        deadlines.append(task.opened_at + timedelta(hours=contract.ticket.resolve_within_hours))
    ended = _first_contact_end(ctx)
    deadlines.append((ended or task.opened_at) + timedelta(hours=contract.repeat_contact_window_hours))
    values = {o.get("timeliness") for o in obligations.values()}
    timeliness = next((s for s in ("overdue", "completed_late", "unknown", "pending") if s in values),
                      "within_deadline" if values else "not_applicable")
    return RefundEvaluation(
        criteria={c.criterion: c for c in checks},
        obligations=obligations,
        disposition=actual,
        expected_dispositions=list(contract.expected_dispositions),
        business_outcome=outcome,
        timeliness=timeliness,
        finalize_at=max(deadlines),
        unknowns=ctx.unknowns,
        decision_time=None,
    )


__all__ = ["SERVICE_PACK_ID", "PACK_VERSION", "evaluate_service_task"]
