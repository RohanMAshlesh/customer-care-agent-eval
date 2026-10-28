"""Evidence ingestion, task evaluations, review queue and adjudication."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccevals.api.deps import any_role, get_session, require
from ccevals.audit import audit
from ccevals.config import get_settings
from ccevals.ingestion import ingest_batch
from ccevals.models import Adjudication, Evaluation, ReviewItem, Task, utcnow
from ccevals.pipeline import evaluate_task, process_pending
from ccevals.schemas import BatchIn, BatchReceipt, EventResult
from ccevals.security import Principal

router = APIRouter(prefix="/v1")


@router.post("/events:batch", response_model=BatchReceipt, tags=["evidence"])
def post_events(body: BatchIn, request: Request, principal: Principal = Depends(require("ingest")),
                session: Session = Depends(get_session)) -> BatchReceipt:
    settings = get_settings()
    if len(body.events) > settings.max_batch_events:
        raise HTTPException(413, f"batches are limited to {settings.max_batch_events} events")
    allowed, blocked = [], []
    for index, raw in enumerate(body.events):
        env = raw.get("environment", "production") if isinstance(raw, dict) else "production"
        (allowed if principal.may_ingest(env) else blocked).append((index, raw))
    receipt = ingest_batch(session, principal.tenant_id, settings.region, [raw for _, raw in allowed])
    # Restore original indexes and add scope rejections.
    results = [r.model_copy(update={"index": allowed[r.index][0]}) for r in receipt.results]
    for index, raw in blocked:
        results.append(EventResult(index=index, event_id=raw.get("event_id"), status="rejected",
                                   code="environment_not_permitted",
                                   message="this key may not write evidence to that environment"))
    receipt.results = sorted(results, key=lambda r: r.index)
    receipt.rejected += len(blocked)
    session.commit()
    if settings.inline_evaluation:
        process_pending(session, request.app.state.judge, tenant_id=principal.tenant_id)
        session.commit()
    return receipt


def _task(session: Session, tenant_id: str, task_key: str) -> Task:
    task = session.scalar(select(Task).where(Task.tenant_id == tenant_id, Task.task_key == task_key))
    if task is None:
        raise HTTPException(404, "task not found")
    return task


def _evaluation_out(ev: Evaluation) -> dict[str, Any]:
    return {"revision": ev.revision, "decision_id": ev.decision_id, "created_at": ev.created_at.isoformat(),
            "reason": ev.reason, "provisional": ev.provisional, "contract_ref": ev.contract_ref,
            "evaluator_bundle": ev.evaluator_bundle,
            "evidence_cutoff": ev.evidence_cutoff.isoformat() if ev.evidence_cutoff else None,
            "result": ev.result}


@router.get("/tasks/{task_key}/evaluations", tags=["evaluations"])
def get_evaluations(task_key: str, principal: Principal = Depends(any_role),
                    session: Session = Depends(get_session)) -> dict[str, Any]:
    task = _task(session, principal.tenant_id, task_key)
    revisions = list(session.scalars(select(Evaluation).where(Evaluation.task_id == task.id)
                                     .order_by(Evaluation.revision)))
    return {
        "task_key": task.task_key, "intent": task.intent, "environment": task.environment,
        "finalized": task.finalized, "finalize_at": task.deadline_at.isoformat() if task.deadline_at else None,
        "current_revision": task.current_revision,
        "current": _evaluation_out(revisions[-1]) if revisions else None,
        "history": [{k: v for k, v in _evaluation_out(r).items() if k != "result"} for r in revisions],
    }


@router.get("/tasks/{task_key}/evaluations/{revision}", tags=["evaluations"])
def get_evaluation_revision(task_key: str, revision: int, principal: Principal = Depends(any_role),
                            session: Session = Depends(get_session)) -> dict[str, Any]:
    task = _task(session, principal.tenant_id, task_key)
    ev = session.scalar(select(Evaluation).where(Evaluation.task_id == task.id, Evaluation.revision == revision))
    if ev is None:
        raise HTTPException(404, "revision not found")
    return _evaluation_out(ev)


@router.get("/review-items", tags=["review"])
def list_review_items(status: str = "open", owner_role: str | None = None, kind: str | None = None, limit: int = 100,
                      principal: Principal = Depends(any_role), session: Session = Depends(get_session)) -> dict[str, Any]:
    query = select(ReviewItem, Task.task_key).join(Task, Task.id == ReviewItem.task_id).where(
        ReviewItem.tenant_id == principal.tenant_id, ReviewItem.status == status)
    if owner_role:
        query = query.where(ReviewItem.owner_role == owner_role)
    if kind:
        query = query.where(ReviewItem.kind == kind)
    now = utcnow()
    rows = session.execute(query.order_by(ReviewItem.kind, ReviewItem.created_at).limit(min(limit, 500))).all()
    return {"items": [{
        "id": item.id, "task_key": key, "kind": item.kind, "reason_code": item.reason_code, "criterion": item.criterion,
        "owner_role": item.owner_role, "age_hours": round((now - item.created_at).total_seconds() / 3600, 1),
        "detail": item.detail, "status": item.status} for item, key in rows]}


class AdjudicationIn(BaseModel):
    criterion: str
    label: Literal["pass", "fail", "not_applicable"]
    note: str = Field(min_length=5, max_length=4000)
    evidence_event_ids: list[str] = []


@router.post("/tasks/{task_key}/adjudications", tags=["review"])
def adjudicate(task_key: str, body: AdjudicationIn, request: Request,
               principal: Principal = Depends(require("reviewer")),
               session: Session = Depends(get_session)) -> dict[str, Any]:
    """Contest or confirm a verdict. History is never edited: a new result revision is appended."""
    task = _task(session, principal.tenant_id, task_key)
    current = session.scalar(select(Evaluation).where(Evaluation.task_id == task.id)
                             .order_by(Evaluation.revision.desc()).limit(1))
    if current is None or body.criterion not in current.result.get("axes", {}).get("bot_behaviour", {}):
        raise HTTPException(422, f"criterion {body.criterion!r} is not part of this task's result")
    session.add(Adjudication(tenant_id=principal.tenant_id, task_id=task.id, criterion=body.criterion, label=body.label,
                             note=body.note, evidence_event_ids=body.evidence_event_ids, reviewer=principal.actor))
    session.flush()
    for item in session.scalars(select(ReviewItem).where(ReviewItem.task_id == task.id, ReviewItem.status == "open",
                                                         ReviewItem.criterion == body.criterion)):
        item.status, item.resolved_at, item.resolved_by = "resolved", utcnow(), principal.actor
    audit(session, principal.tenant_id, principal.actor, "verdict.adjudicated", task_key,
          criterion=body.criterion, label=body.label)
    revision = evaluate_task(session, task, request.app.state.judge, reason="adjudication")
    return {"task_key": task_key, "new_revision": revision.revision if revision else task.current_revision}
