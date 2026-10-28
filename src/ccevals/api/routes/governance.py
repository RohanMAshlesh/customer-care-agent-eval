"""Contracts, sandbox runs and release decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccevals.api.deps import any_role, get_session, require
from ccevals.audit import audit
from ccevals.contracts import registry
from ccevals.contracts.registry import ContractError
from ccevals.models import Contract as ContractRow
from ccevals.models import ReleaseDecision, ReleaseException, Run, new_id
from ccevals.release import add_exception, decide, effective_status
from ccevals.security import Principal

router = APIRouter(prefix="/v1")


class ValidateIn(BaseModel):
    contract: dict[str, Any]
    fixtures: list[dict[str, Any]] = []


@router.get("/contracts/starter", tags=["contracts"])
def starter(principal: Principal = Depends(any_role)) -> dict[str, Any]:
    return registry.starter_contract()


@router.post("/contracts:validate", tags=["contracts"])
def validate(body: ValidateIn, principal: Principal = Depends(require("policy_author", "policy_owner"))) -> dict[str, Any]:
    return registry.validate_contract(body.contract, body.fixtures)


def _contract_out(row: ContractRow) -> dict[str, Any]:
    return {"ref": row.ref, "name": row.name, "version": row.version, "intent": row.intent, "status": row.status,
            "effective_from": row.effective_from.isoformat(), "created_by": row.created_by,
            "approved_by": row.approved_by, "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "validation": row.validation, "body": row.body}


@router.post("/contracts", status_code=201, tags=["contracts"])
def create_contract(body: ValidateIn, principal: Principal = Depends(require("policy_author")),
                    session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        row = registry.create_draft(session, principal.tenant_id, body.contract, principal.actor)
    except ContractError as exc:
        raise HTTPException(422, {"problems": exc.problems}) from exc
    audit(session, principal.tenant_id, principal.actor, "contract.draft_saved", row.ref)
    return _contract_out(row)


class ApproveIn(BaseModel):
    reviewed_fixtures: list[str] = Field(description="Built-in examples the approver inspected")


@router.post("/contracts/{name}/versions/{version}:approve", tags=["contracts"])
def approve_contract(name: str, version: int, body: ApproveIn, principal: Principal = Depends(require("policy_owner")),
                     session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        row = registry.approve(session, principal.tenant_id, name, version, principal.actor, body.reviewed_fixtures)
    except ContractError as exc:
        raise HTTPException(422, {"problems": exc.problems}) from exc
    audit(session, principal.tenant_id, principal.actor, "contract.approved", row.ref)
    return _contract_out(row)


@router.get("/contracts", tags=["contracts"])
def list_contracts(principal: Principal = Depends(any_role), session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.scalars(select(ContractRow).where(ContractRow.tenant_id == principal.tenant_id)
                           .order_by(ContractRow.name, ContractRow.version))
    return {"contracts": [{k: v for k, v in _contract_out(r).items() if k != "body"} for r in rows]}


class RunIn(BaseModel):
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    bot_deployment_ref: str
    contract_ref: str
    dataset_ref: str
    families: int = Field(ge=1)
    trials_per_family: int = Field(ge=1)
    notes: str = ""


@router.post("/runs", status_code=201, tags=["runs"])
def create_run(body: RunIn, principal: Principal = Depends(require("evaluation_engineer", "release_owner")),
               session: Session = Depends(get_session)) -> dict[str, Any]:
    """Register a version-pinned sandbox run. Its episodes arrive as sandbox events tagged with the run id."""
    name, _, version = body.contract_ref.partition("@v")
    contract = session.scalar(select(ContractRow).where(
        ContractRow.tenant_id == principal.tenant_id, ContractRow.name == name,
        ContractRow.version == (int(version) if version.isdigit() else -1), ContractRow.status == "approved"))
    if contract is None:
        raise HTTPException(422, f"{body.contract_ref} is not an approved contract")
    run_id = body.id or "run_" + new_id()
    if session.get(Run, run_id) is not None:
        raise HTTPException(409, "run id already exists")
    run = Run(id=run_id, tenant_id=principal.tenant_id, bot_deployment_ref=body.bot_deployment_ref,
              contract_ref=contract.ref, evaluator_bundle="pinned-at-evaluation", dataset_ref=body.dataset_ref,
              manifest={"families": body.families, "trials_per_family": body.trials_per_family, "notes": body.notes},
              created_by=principal.actor)
    session.add(run)
    audit(session, principal.tenant_id, principal.actor, "run.created", run_id, contract_ref=contract.ref)
    return {"id": run_id, "contract_ref": contract.ref, "manifest": run.manifest,
            "ingest_with": {"environment": "sandbox", "run_id": run_id}}


class DecisionIn(BaseModel):
    candidate_run_id: str
    baseline_run_id: str
    required_slices: list[dict[str, str]] = []
    latency_tradeoff_approved: bool = False


def _decision_out(session: Session, d: ReleaseDecision) -> dict[str, Any]:
    exceptions = session.scalars(select(ReleaseException).where(ReleaseException.decision_id == d.id))
    return {"id": d.id, "decision": d.decision, "status": effective_status(d), "reasons": d.reasons,
            "slices": d.slices, "binding": d.binding, "statistics": d.statistics,
            "accountable_owner": d.accountable_owner, "created_at": d.created_at.isoformat(),
            "valid_until": d.valid_until.isoformat(), "superseded_reason": d.superseded_reason,
            "exceptions": [{"id": e.id, "reason": e.reason, "scope": e.scope, "compensating_review": e.compensating_review,
                            "expires_at": e.expires_at.isoformat(), "granted_by": e.granted_by} for e in exceptions]}


@router.post("/release-decisions", status_code=201, tags=["releases"])
def create_decision(body: DecisionIn, principal: Principal = Depends(require("release_owner")),
                    session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        decision = decide(session, principal.tenant_id, body.candidate_run_id, body.baseline_run_id, principal.actor,
                          body.required_slices, body.latency_tradeoff_approved)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    session.flush()
    return _decision_out(session, decision)


@router.get("/release-decisions/{decision_id}", tags=["releases"])
def get_decision(decision_id: str, principal: Principal = Depends(any_role),
                 session: Session = Depends(get_session)) -> dict[str, Any]:
    d = session.get(ReleaseDecision, decision_id)
    if d is None or d.tenant_id != principal.tenant_id:
        raise HTTPException(404, "decision not found")
    return _decision_out(session, d)


class ExceptionIn(BaseModel):
    reason: str = Field(min_length=10)
    scope: str = Field(min_length=3)
    compensating_review: str = Field(min_length=10)
    expires_at: datetime


@router.post("/release-decisions/{decision_id}/exceptions", status_code=201, tags=["releases"])
def create_exception(decision_id: str, body: ExceptionIn, principal: Principal = Depends(require("release_owner")),
                     session: Session = Depends(get_session)) -> dict[str, Any]:
    d = session.get(ReleaseDecision, decision_id)
    if d is None or d.tenant_id != principal.tenant_id:
        raise HTTPException(404, "decision not found")
    if body.expires_at.tzinfo is None:
        raise HTTPException(422, "expires_at must include a time zone")
    try:
        add_exception(session, principal.tenant_id, d, principal.actor, body.reason, body.scope,
                      body.compensating_review, body.expires_at)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.flush()
    return _decision_out(session, d)
