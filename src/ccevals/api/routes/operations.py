"""Client journey, readiness, connectors, calibration, metrics, audit and key management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccevals.api.deps import any_role, get_session, require
from ccevals.audit import audit
from ccevals.contracts.registry import load_contract
from ccevals.contracts.schema import Contract
from ccevals.metrics import cohort_metrics
from ccevals.models import Attestation, AuditRecord, CalibrationReport, ConformanceRun, utcnow
from ccevals.models import Contract as ContractRow
from ccevals.onboarding import (
    ATTESTED_STAGES,
    calibration_report,
    conformance_kit,
    onboarding_stages,
    readiness,
    run_conformance,
)
from ccevals.security import ROLES, Principal, issue_key

router = APIRouter(prefix="/v1")


def _current_contract(session: Session, tenant_id: str) -> Contract:
    row = session.scalar(select(ContractRow).where(ContractRow.tenant_id == tenant_id, ContractRow.status == "approved")
                         .order_by(ContractRow.effective_from.desc(), ContractRow.version.desc()).limit(1))
    if row is None:
        raise HTTPException(409, "approve a contract first; it names the evidence sources to test")
    return load_contract(row.body)


@router.get("/readiness", tags=["journey"])
def get_readiness(request: Request, principal: Principal = Depends(any_role),
                  session: Session = Depends(get_session)) -> dict[str, Any]:
    return readiness(session, principal.tenant_id, utcnow(), request.app.state.judge.bundle_id)


@router.get("/onboarding", tags=["journey"])
def get_onboarding(request: Request, principal: Principal = Depends(any_role),
                   session: Session = Depends(get_session)) -> dict[str, Any]:
    return onboarding_stages(session, principal.tenant_id, utcnow(), request.app.state.judge.bundle_id)


class AttestationIn(BaseModel):
    stage: Literal["qualify_pilot", "establish_trust", "activate_release_gates", "expand_coverage"]
    evidence_ref: str = Field(min_length=3, max_length=500)
    note: str = ""


STAGE_ROLES = {"qualify_pilot": "admin", "establish_trust": "admin",
               "activate_release_gates": "release_owner", "expand_coverage": "admin"}


@router.post("/onboarding/attestations", status_code=201, tags=["journey"])
def attest(body: AttestationIn, principal: Principal = Depends(any_role),
           session: Session = Depends(get_session)) -> dict[str, Any]:
    if not principal.has(STAGE_ROLES[body.stage]):
        raise HTTPException(403, f"{body.stage} must be attested by {STAGE_ROLES[body.stage]}")
    session.add(Attestation(tenant_id=principal.tenant_id, stage=body.stage, evidence_ref=body.evidence_ref,
                            note=body.note, attested_by=principal.actor))
    audit(session, principal.tenant_id, principal.actor, "onboarding.attested", body.stage, evidence_ref=body.evidence_ref)
    return {"stage": body.stage, "exit_evidence": ATTESTED_STAGES[body.stage], "recorded": True}


@router.get("/connectors/{source}/conformance-kit", tags=["connectors"])
def get_kit(source: str, principal: Principal = Depends(any_role), session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        return conformance_kit(_current_contract(session, principal.tenant_id), source)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


class ConformanceIn(BaseModel):
    fixtures: dict[str, Any]


@router.post("/connectors/{source}/conformance", status_code=201, tags=["connectors"])
def post_conformance(source: str, body: ConformanceIn,
                     principal: Principal = Depends(require("evaluation_engineer", "admin")),
                     session: Session = Depends(get_session)) -> dict[str, Any]:
    contract = _current_contract(session, principal.tenant_id)
    try:
        report = run_conformance(contract, source, body.fixtures)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    session.add(ConformanceRun(tenant_id=principal.tenant_id, source=source, passed=report["passed"], results=report,
                               created_by=principal.actor))
    audit(session, principal.tenant_id, principal.actor, "connector.conformance", source, passed=report["passed"])
    return report


class CalibrationItem(BaseModel):
    task_ref: str
    reviewer_a: Literal["pass", "fail"]
    reviewer_b: Literal["pass", "fail"]
    adjudicated: Literal["pass", "fail"]
    evaluator: Literal["pass", "fail", "abstain"]
    severity: Literal["critical", "major", "minor"] = "major"


class CalibrationIn(BaseModel):
    criterion: str
    language: str
    split: Literal["calibration", "validation"]
    evaluator_bundle: str | None = None
    items: list[CalibrationItem] = Field(min_length=1)


@router.post("/calibration", status_code=201, tags=["evaluators"])
def post_calibration(body: CalibrationIn, request: Request,
                     principal: Principal = Depends(require("evaluation_engineer")),
                     session: Session = Depends(get_session)) -> dict[str, Any]:
    bundle = body.evaluator_bundle or request.app.state.judge.bundle_id
    report = calibration_report([i.model_dump() for i in body.items], body.split)
    session.add(CalibrationReport(tenant_id=principal.tenant_id, criterion=body.criterion, language=body.language,
                                  evaluator_bundle=bundle, passed=report["passed"], metrics=report["metrics"],
                                  created_by=principal.actor))
    audit(session, principal.tenant_id, principal.actor, "evaluator.calibrated", f"{body.criterion}/{body.language}",
          passed=report["passed"], bundle=bundle)
    return {"criterion": body.criterion, "language": body.language, "evaluator_bundle": bundle, **report}


@router.get("/metrics", tags=["metrics"])
def get_metrics(intent: str = "missing_item_refund", opened_from: datetime | None = None,
                opened_before: datetime | None = None, slice_by: Literal["language", "market"] | None = None,
                principal: Principal = Depends(any_role), session: Session = Depends(get_session)) -> dict[str, Any]:
    now = utcnow()
    end = opened_before or now
    start = opened_from or end - timedelta(days=30)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(422, "timestamps must include a time zone")
    return cohort_metrics(session, principal.tenant_id, intent, start.astimezone(UTC), end.astimezone(UTC), now, slice_by)


@router.get("/audit", tags=["governance"])
def get_audit(limit: int = 200, principal: Principal = Depends(require("admin", "release_owner", "policy_owner")),
              session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.scalars(select(AuditRecord).where(AuditRecord.tenant_id == principal.tenant_id)
                           .order_by(AuditRecord.created_at.desc()).limit(min(limit, 1000)))
    return {"records": [{"at": r.created_at.isoformat(), "actor": r.actor, "action": r.action, "target": r.target,
                         "detail": r.detail} for r in rows]}


class KeyIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    roles: list[str] = Field(min_length=1)


@router.post("/admin/api-keys", status_code=201, tags=["governance"])
def create_key(body: KeyIn, principal: Principal = Depends(require("admin")),
               session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        token = issue_key(session, principal.tenant_id, body.name, body.roles)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(session, principal.tenant_id, principal.actor, "api_key.issued", body.name, roles=body.roles)
    return {"name": body.name, "roles": body.roles, "api_key": token,
            "note": "Store this key now; it is not shown again.", "known_roles": sorted(ROLES)}
