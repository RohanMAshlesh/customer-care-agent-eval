"""Evaluation orchestration: assemble evidence, evaluate, append revisions, finalise.

Workers call :func:`process_pending`. Rows are claimed with ``FOR UPDATE SKIP
LOCKED`` on PostgreSQL so several workers can run safely; result writes are
idempotent because a revision is appended only when the material content changes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ccevals.audit import enqueue_callback
from ccevals.contracts import registry
from ccevals.db import is_postgres
from ccevals.evaluation.engine import AdjudicationView, errored_result, evaluate
from ccevals.evaluation.evidence import SourceStatus, TaskFacts
from ccevals.evaluation.semantic import Judge
from ccevals.ingestion import CUSTOMER_SCOPED, ORDER_SCOPED, view
from ccevals.models import (
    Adjudication,
    ConformanceRun,
    Evaluation,
    Event,
    ReleaseDecision,
    ReviewItem,
    Run,
    Task,
    new_id,
    utcnow,
)

log = logging.getLogger(__name__)


def source_status(session: Session, tenant_id: str, environment: str) -> SourceStatus:
    latest_runs: dict[str, bool] = {}
    for run in session.scalars(select(ConformanceRun).where(ConformanceRun.tenant_id == tenant_id)
                               .order_by(ConformanceRun.created_at)):
        latest_runs[run.source] = run.passed
    verified = {source for source, ok in latest_runs.items() if ok}
    watermarks: dict[str, datetime] = {}
    for event in session.scalars(select(Event).where(
        Event.tenant_id == tenant_id, Event.event_type == "source.heartbeat", Event.quarantined.is_(False),
        Event.environment == environment,
    )):
        mark = datetime.fromisoformat(event.payload["watermark"])
        if event.source not in watermarks or mark > watermarks[event.source]:
            watermarks[event.source] = mark
    if environment != "production":
        # Sandbox and conformance evidence comes from isolated fixtures whose sources are the scenario itself.
        verified |= {e for e in session.scalars(select(Event.source).where(
            Event.tenant_id == tenant_id, Event.environment == environment).distinct())}
    return SourceStatus(verified_sources=frozenset(verified), watermarks=watermarks)


def task_events(session: Session, task: Task) -> list[Event]:
    action_ids = select(Event.logical_action_id).where(
        Event.tenant_id == task.tenant_id, Event.task_key == task.task_key, Event.logical_action_id.is_not(None))
    conditions = [Event.task_key == task.task_key, Event.logical_action_id.in_(action_ids)]
    if task.order_token:
        conditions.append(and_(Event.order_token == task.order_token, Event.event_type.in_(ORDER_SCOPED)))
    if task.customer_token:
        conditions.append(and_(Event.customer_token == task.customer_token, Event.event_type.in_(CUSTOMER_SCOPED)))
    query = select(Event).where(Event.tenant_id == task.tenant_id, Event.environment == task.environment, or_(*conditions))
    if task.run_id:
        query = query.where(or_(Event.run_id == task.run_id, Event.run_id.is_(None)))
    return list(session.scalars(query.order_by(Event.occurred_at, Event.id)))


def task_facts(task: Task, events: list[Event]) -> TaskFacts | None:
    opened = [e for e in events if e.event_type == "task.opened" and not e.quarantined]
    if not opened:
        return None
    first = opened[0]
    return TaskFacts(
        task_key=task.task_key,
        intent=task.intent,
        opened_at=first.occurred_at,
        customer_token=task.customer_token,
        order_token=task.order_token,
        affected_lines={line["line_item_id"]: int(line["quantity"]) for line in first.payload["affected_lines"]},
        environment=task.environment,
        market=task.market,
    )


def _business_time(events: list[Event], task: Task) -> datetime:
    for event in events:
        if event.event_type == "order.snapshot" and event.payload.get("placed_at"):
            return datetime.fromisoformat(event.payload["placed_at"])
    return task.opened_at


def _sync_review_items(session: Session, task: Task, result: dict[str, Any]) -> None:
    open_items = {(i.kind, i.reason_code, i.criterion): i for i in session.scalars(select(ReviewItem).where(
        ReviewItem.tenant_id == task.tenant_id, ReviewItem.task_id == task.id, ReviewItem.status == "open",
        ReviewItem.kind.in_(["unknown", "critical"])))}
    wanted: dict[tuple, dict[str, Any]] = {}
    for unknown in result.get("unknown_reasons", []):
        wanted[("unknown", unknown["code"], None)] = {"owner": unknown["owner"], "detail": {"detail": unknown["detail"]}}
    for name, crit in result.get("axes", {}).get("bot_behaviour", {}).items():
        if crit["result"] == "fail" and crit["severity"] == "critical" and crit.get("source") != "adjudication":
            wanted[("critical", "critical_violation", name)] = {
                "owner": result.get("review_owner", "operations_reviewer"),
                "detail": {"reason": crit["reason"], "evidence": crit["evidence"]}}
    now = utcnow()
    for key, item in open_items.items():
        if key not in wanted:
            item.status = "resolved"
            item.resolved_at = now
            item.resolved_by = "system:evidence_changed"
    for key, spec in wanted.items():
        if key not in open_items:
            session.add(ReviewItem(tenant_id=task.tenant_id, task_id=task.id, kind=key[0], reason_code=key[1],
                                   criterion=key[2], owner_role=spec["owner"], detail=spec["detail"]))


def _supersede_decisions(session: Session, task: Task, reason: str) -> None:
    if not task.run_id:
        return
    for decision in session.scalars(select(ReleaseDecision).where(
        ReleaseDecision.tenant_id == task.tenant_id, ReleaseDecision.status == "active",
        or_(ReleaseDecision.candidate_run_id == task.run_id, ReleaseDecision.baseline_run_id == task.run_id))):
        decision.status = "review_required"
        decision.superseded_reason = reason
        enqueue_callback(session, task.tenant_id, "release_decision.superseded",
                         {"decision_id": decision.id, "reason": reason, "task_key": task.task_key})


def evaluate_task(session: Session, task: Task, judge: Judge, now: datetime | None = None,
                  reason: str | None = None) -> Evaluation | None:
    now = now or utcnow()
    rows = task_events(session, task)
    facts = task_facts(task, rows)
    pinned = None
    if task.run_id:
        run = session.get(Run, task.run_id)
        pinned = run.contract_ref if run and run.tenant_id == task.tenant_id else None
    resolved = registry.resolve_for_task(session, task.tenant_id, task.intent, _business_time(rows, task), pinned)
    previous = session.scalar(select(Evaluation).where(Evaluation.task_id == task.id)
                              .order_by(Evaluation.revision.desc()).limit(1))
    task.dirty = False

    if facts is None:
        # Census-only so far. The task still matures so it stays in the metric denominator as unknown.
        if resolved is not None:
            maturation = timedelta(hours=resolved[1].measurement.maturation_hours)
            task.deadline_at = task.opened_at + maturation
            task.finalized = now >= task.deadline_at
        return None
    if resolved is None:
        result = errored_result(None, f"no approved contract for intent {task.intent!r}", now)
        result["axes"]["evaluation_execution"] = "invalid"
        content_hash, cutoff, provisional, finalize_at = "no-contract", None, True, None
        bundle, contract_ref = "none", None
    else:
        row, contract = resolved
        adjudications = [
            AdjudicationView(a.criterion, a.label, a.reviewer, a.note, tuple(a.evidence_event_ids), a.created_at)
            for a in session.scalars(select(Adjudication).where(Adjudication.task_id == task.id))
        ]
        try:
            outcome = evaluate(contract, task.tenant_id, facts, [view(e) for e in rows],
                               source_status(session, task.tenant_id, task.environment), now, judge, adjudications)
        except Exception as exc:
            log.exception("evaluation failed for task %s", task.id)
            result = errored_result(contract.ref, repr(exc), now)
            content_hash, cutoff, provisional, finalize_at = f"error:{type(exc).__name__}", None, True, None
        else:
            result = outcome.result
            result["review_owner"] = contract.review_owner
            content_hash, cutoff = outcome.content_hash, outcome.evidence_cutoff
            provisional, finalize_at = outcome.provisional, outcome.finalize_at
        bundle, contract_ref = result.get("evaluator_bundle", "error"), row.ref

    task.deadline_at = finalize_at
    if not provisional:
        task.finalized = True

    if previous is not None and previous.content_hash == content_hash:
        return None
    if previous is None:
        why = "initial_evidence"
    elif reason:
        why = reason
    elif previous.provisional and not provisional:
        why = "finalized_at_deadline"
    elif not previous.provisional:
        why = "late_event"
    else:
        why = "new_evidence"
    revision = Evaluation(
        tenant_id=task.tenant_id, task_id=task.id, revision=(previous.revision + 1) if previous else 1,
        decision_id="dec_" + new_id(), evidence_cutoff=cutoff, contract_ref=contract_ref, evaluator_bundle=bundle,
        provisional=provisional, reason=why, content_hash=content_hash, result=result,
        supersedes_id=previous.id if previous else None,
    )
    session.add(revision)
    task.current_revision = revision.revision
    session.flush()
    _sync_review_items(session, task, result)
    payload = {"decision_id": revision.decision_id, "task_key": task.task_key, "revision": revision.revision,
               "reason": why, "provisional": provisional, "business_outcome": result.get("business_outcome"),
               "critical_violation": result.get("critical_violation")}
    if previous is not None:
        payload["supersedes_decision_id"] = previous.decision_id
        enqueue_callback(session, task.tenant_id, "verdict.superseded", payload)
        if why in ("late_event", "adjudication", "policy_correction"):
            _supersede_decisions(session, task, f"task {task.task_key} revised: {why}")
    else:
        enqueue_callback(session, task.tenant_id, "verdict.created", payload)
    return revision


def _claim(session: Session, query):
    if is_postgres(session):
        query = query.with_for_update(skip_locked=True)
    return list(session.scalars(query))


def process_pending(session: Session, judge: Judge, now: datetime | None = None, limit: int = 200,
                    tenant_id: str | None = None) -> int:
    """Evaluate dirty tasks and finalise tasks whose deadline has passed. Returns tasks processed."""
    now = now or utcnow()
    query = select(Task).where(or_(Task.dirty.is_(True),
                                   and_(Task.finalized.is_(False), Task.deadline_at.is_not(None), Task.deadline_at <= now)))
    if tenant_id:
        query = query.where(Task.tenant_id == tenant_id)
    tasks = _claim(session, query.order_by(Task.deadline_at.is_(None), Task.deadline_at).limit(limit))
    for task in tasks:
        evaluate_task(session, task, judge, now)
    session.flush()
    return len(tasks)


def latest_evaluation(session: Session, task: Task) -> Evaluation | None:
    return session.scalar(select(Evaluation).where(Evaluation.task_id == task.id)
                          .order_by(Evaluation.revision.desc()).limit(1))


def latest_results_for_run(session: Session, tenant_id: str, run_id: str) -> list[tuple[Task, Evaluation | None]]:
    tasks = list(session.scalars(select(Task).where(Task.tenant_id == tenant_id, Task.run_id == run_id)))
    sub = (select(Evaluation.task_id, func.max(Evaluation.revision).label("rev"))
           .where(Evaluation.tenant_id == tenant_id).group_by(Evaluation.task_id).subquery())
    evals = {e.task_id: e for e in session.scalars(
        select(Evaluation).join(sub, and_(Evaluation.task_id == sub.c.task_id, Evaluation.revision == sub.c.rev))
        .where(Evaluation.task_id.in_([t.id for t in tasks])))} if tasks else {}
    return [(t, evals.get(t.id)) for t in tasks]
