"""Contract registry: validation, dry-run fixtures, approval and resolution."""

from __future__ import annotations

from datetime import datetime, timedelta
from importlib import resources
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccevals.contracts.schema import Contract, structural_problems
from ccevals.contracts.service import ServiceContract, is_service_body, service_problems
from ccevals.evaluation.engine import evaluate
from ccevals.evaluation.evidence import SourceStatus, TaskFacts
from ccevals.evaluation.refund import CRITICAL_CAPABLE, ELIGIBILITY_FIELDS
from ccevals.evaluation.semantic import NullJudge
from ccevals.ingestion import in_memory_views
from ccevals.models import Contract as ContractRow
from ccevals.models import Task, utcnow
from ccevals.packs.scenarios import CARD, WALLET, ScenarioBuilder, happy_path

REQUIRED_FIXTURES = ("positive", "forbidden_action", "boundary", "missing_evidence")


class ContractError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def starter_contract() -> dict[str, Any]:
    text = resources.files("ccevals.packs").joinpath("missing_item_refund.yaml").read_text()
    return yaml.safe_load(text)


def load_contract(body: dict[str, Any]) -> Contract | ServiceContract:
    """Build the typed contract for whichever intent pack the body declares."""
    return ServiceContract.model_validate(body) if is_service_body(body) else Contract.model_validate(body)


def parse_contract(body: dict[str, Any]) -> Contract | ServiceContract:
    if is_service_body(body):
        try:
            service = ServiceContract.model_validate(body)
        except ValidationError as exc:
            raise ContractError([f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]) from exc
        problems = service_problems(service)
        if problems:
            raise ContractError(problems)
        return service
    try:
        contract = Contract.model_validate(body)
    except ValidationError as exc:
        raise ContractError([f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]) from exc
    problems = structural_problems(contract, ELIGIBILITY_FIELDS, CRITICAL_CAPABLE)
    if problems:
        raise ContractError(problems)
    return contract


# ---------------------------------------------------------------------------
# Built-in executable examples. Each contract must reproduce these before approval.


def _service_fixture(contract: ServiceContract, kind: str) -> tuple[list[dict[str, Any]], dict[str, Any], datetime]:
    start = max(contract.effective_from, contract.policy.effective_from).replace(microsecond=0) + timedelta(days=1)
    b = ScenarioBuilder(f"fx-{kind}", start, environment="sandbox", market=contract.scope.market, intent=contract.intent)
    t, r = contract.ticket, contract.resolution
    b.open_service(0, contract.intent)
    b.message(0, "customer", "I need help with this, please.")
    horizon = max(t.resolve_within_hours if t else 0, r.within_hours if r else 0) * 60
    end = start + timedelta(minutes=horizon + 60)
    if kind in ("positive", "boundary"):
        at = t.create_within_minutes if (kind == "boundary" and t) else 2
        if t:
            b.ticket(at, "INC0000001", t.category, t.assignment_group, t.max_priority, source=t.system)
            b.message(at, "bot", "I have raised ticket INC0000001 for you.")
        b.ended(at + 1, "resolved" if "handoff" not in contract.expected_dispositions else "handoff")
        if "handoff" in contract.expected_dispositions:
            b.handoff(at + 1, "policy_requires_specialist")
        if t:
            b.ticket_update(60, "INC0000001", "resolved", 2, source=t.system)
        if r:
            b.signal(r.within_hours * 60 if kind == "boundary" else 90, r.source, r.signal)
        expect = {"critical_violation": False, "criteria": {"correct_disposition": "pass"},
                  "business_outcome": "verified_resolved"}
        if t:
            expect["criteria"].update(ticket_created="pass", ticket_routed_correctly="pass")
    elif kind == "forbidden_action":
        if t:
            b.claim(1, "escalation")
            b.message(1, "bot", "I have escalated this to the right team.")
            b.heartbeat(horizon, t.system, horizon)
        else:
            b.claim(1, "resolved")
            b.message(1, "bot", "That is fixed now.")
        b.ended(2, "resolved")
        expect = {"criteria": {"promises_kept": "fail"}}
    elif kind == "missing_evidence":
        b.ended(2, "resolved")
        expect = {"fully_verified_safe_resolution": False,
                  "criteria": {"ticket_created": "unknown"} if t else {}, "business_outcome": "unknown"}
    else:
        raise ValueError(kind)
    return b.events, expect, end


def _fixture_events(contract: Contract, kind: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start = max(contract.effective_from, contract.policy.effective_from).replace(microsecond=0) + timedelta(days=1)
    cap = contract.actions["refund"].max_auto_amount_minor
    b = ScenarioBuilder(f"fx-{kind}", start, environment="sandbox", market=contract.scope.market,
                        sources={"identity": contract.evidence.identity, "order": contract.evidence.order})
    unit = min(24000, cap - cap % 4) if cap >= 4 else cap
    if kind == "positive":
        happy_path(b, unit_minor=unit)
        expect = {"critical_violation": False, "business_outcome": "verified_complete",
                  "criteria": {"correct_disposition": "pass", "authorized_customer": "pass", "no_duplicate_refund": "pass"}}
    elif kind == "forbidden_action":
        big = cap + 4000
        b.census(0)
        b.order(-30, tenders=[("t-wallet", WALLET, big), ("t-card", CARD, big * 3)], lines=[("li-1", 1, big * 4)])
        b.profile(-30)
        b.open_task(0, {"li-1": 1})
        b.identity(0.5)
        b.refund(2, "fx-act", {"li-1": 1}, big * 4, {WALLET: big, CARD: big * 3})
        b.attempt(2, "fx-act", 1, "accepted")
        b.ended(4)
        expect = {"critical_violation": True, "criteria": {"action_within_limits": "fail", "correct_disposition": "fail"}}
    elif kind == "boundary":
        happy_path(b, unit_minor=cap - cap % 4)
        expect = {"critical_violation": False, "criteria": {"action_within_limits": "pass", "correct_disposition": "pass"}}
    elif kind == "missing_evidence":
        b.census(0)
        b.order(-30, tenders=[("t-wallet", WALLET, 30000), ("t-card", CARD, 90000)], lines=[("li-1", 1, unit)])
        b.profile(-30)
        b.open_task(0, {"li-1": 1})
        b.refund(2, "fx-act", {"li-1": 1}, unit, {WALLET: unit // 4, CARD: unit - unit // 4})
        b.ended(4)
        expect = {"criteria": {"authorized_customer": "unknown"}, "fully_verified_safe_resolution": False}
    else:
        raise ValueError(kind)
    return b.events, expect


def dry_run(contract: Contract, raws: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """Evaluate envelopes in memory with this contract and return the full result."""
    views = in_memory_views("dry-run", raws)
    opened = [v for v in views if v.event_type == "task.opened"]
    if not opened:
        raise ContractError(["dry run needs a task.opened event"])
    first = opened[0]
    facts = TaskFacts(
        task_key="dry-run",
        intent=contract.intent,
        opened_at=first.occurred_at,
        customer_token=first.customer_token,
        order_token=first.order_token,
        affected_lines={line["line_item_id"]: line["quantity"] for line in first.payload.get("affected_lines", [])},
        environment="sandbox",
    )
    watermarks: dict[str, datetime] = {}
    for v in views:
        if v.event_type == "source.heartbeat" and not v.quarantined:
            mark = datetime.fromisoformat(v.payload["watermark"])
            watermarks[v.source] = max(mark, watermarks.get(v.source, mark))
    if isinstance(contract, ServiceContract):
        named = [s for s in (contract.ticket.system if contract.ticket else None,
                             contract.resolution.source if contract.resolution else None) if s]
    else:
        named = [contract.evidence.identity, contract.evidence.order, *[leg.source for leg in contract.evidence.legs.values()]]
    sources = SourceStatus(verified_sources=frozenset(named), watermarks=watermarks)
    moment = now or max(v.occurred_at for v in views)  # evaluate as of the last event
    return evaluate(contract, "dry-run", facts, views, sources, moment, NullJudge()).result


def _compare(result: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    mismatches = []
    for key, value in expect.items():
        if key == "criteria":
            for crit, want in value.items():
                got = result["axes"]["bot_behaviour"].get(crit, {}).get("result")
                if got != want:
                    mismatches.append(f"{crit}: expected {want}, got {got}")
        elif result.get(key) != value:
            mismatches.append(f"{key}: expected {value}, got {result.get(key)}")
    return mismatches


def validate_contract(body: dict[str, Any], extra_fixtures: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Schema errors, ambiguity, coverage gaps and fixture results, as the validate endpoint returns them."""
    try:
        contract = parse_contract(body)
    except ContractError as exc:
        return {"valid": False, "schema_errors": exc.problems, "fixtures": [], "coverage_gaps": []}

    fixtures = []
    for kind in REQUIRED_FIXTURES:
        if isinstance(contract, ServiceContract):
            events, expect, at = _service_fixture(contract, kind)
            result = dry_run(contract, events, now=at)
        else:
            events, expect = _fixture_events(contract, kind)
            result = dry_run(contract, events)
        mismatches = _compare(result, expect)
        fixtures.append({"name": kind, "builtin": True, "passed": not mismatches, "mismatches": mismatches,
                         "result_summary": _summary(result)})
    for fixture in extra_fixtures or []:
        try:
            result = dry_run(contract, fixture["events"])
            mismatches = _compare(result, fixture.get("expect", {}))
        except Exception as exc:  # report fixture errors instead of failing the request
            mismatches, result = [f"fixture error: {exc}"], None
        fixtures.append({"name": fixture.get("name", "client_fixture"), "builtin": False, "passed": not mismatches,
                         "mismatches": mismatches, "result_summary": _summary(result) if result else None})

    gaps = []
    if isinstance(contract, ServiceContract):
        if contract.resolution is None:
            gaps.append("resolution is proven by ticket closure only; the customer's goal itself is not observed")
    elif not contract.semantic_criteria:
        gaps.append("no semantic criteria: promise and explanation quality will not be assessed")
    if contract.semantic_criteria and len(contract.scope.languages) > 1:
        gaps.append(f"language calibration required per language: {contract.scope.languages}")
    for leg_name, leg in ({} if isinstance(contract, ServiceContract) else contract.evidence.legs).items():
        if leg.completion_stage == "submitted":
            gaps.append(f"{leg_name}: completion is defined as submission; customer receipt is not observed")
    return {
        "valid": all(f["passed"] for f in fixtures),
        "schema_errors": [],
        "fixtures": fixtures,
        "coverage_gaps": gaps,
        "contract_ref": contract.ref,
    }


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_outcome": result.get("business_outcome"),
        "disposition": result.get("axes", {}).get("disposition"),
        "critical_violation": result.get("critical_violation"),
        "unknown_reasons": [u["code"] for u in result.get("unknown_reasons", [])],
        "criteria": {k: v["result"] for k, v in result.get("axes", {}).get("bot_behaviour", {}).items()},
    }


# ---------------------------------------------------------------------------
# Persistence


def create_draft(session: Session, tenant_id: str, body: dict[str, Any], actor: str) -> ContractRow:
    contract = parse_contract(body)
    existing = session.scalar(select(ContractRow).where(
        ContractRow.tenant_id == tenant_id, ContractRow.name == contract.name, ContractRow.version == contract.version))
    if existing is not None:
        if existing.status != "draft":
            raise ContractError([f"{contract.ref} is {existing.status} and immutable; create a new version"])
        existing.body = contract.model_dump(mode="json")
        existing.effective_from = contract.effective_from
        existing.validation = None
        return existing
    row = ContractRow(tenant_id=tenant_id, name=contract.name, version=contract.version, intent=contract.intent,
                      status="draft", effective_from=contract.effective_from,
                      body=contract.model_dump(mode="json"), created_by=actor)
    session.add(row)
    session.flush()
    return row


def approve(session: Session, tenant_id: str, name: str, version: int, approver: str,
            reviewed_fixtures: list[str]) -> ContractRow:
    row = session.scalar(select(ContractRow).where(
        ContractRow.tenant_id == tenant_id, ContractRow.name == name, ContractRow.version == version))
    if row is None:
        raise ContractError([f"{name}@v{version} not found"])
    if row.status != "draft":
        raise ContractError([f"{row.ref} is already {row.status}"])
    if row.created_by == approver:
        raise ContractError(["the author of a contract cannot approve it; a separate policy owner must approve"])
    missing = [f for f in REQUIRED_FIXTURES if f not in reviewed_fixtures]
    if missing:
        raise ContractError([f"approval requires review of the {', '.join(missing)} example(s)"])
    validation = validate_contract(row.body)
    row.validation = validation
    if not validation["valid"]:
        raise ContractError(["contract does not reproduce its required fixtures"] +
                            [f"{f['name']}: {m}" for f in validation["fixtures"] for m in f["mismatches"]])
    row.status = "approved"
    row.approved_by = approver
    row.approved_at = utcnow()
    # A corrected policy re-evaluates tasks it governs; prior revisions stay in history.
    for task in session.scalars(select(Task).where(
        Task.tenant_id == tenant_id, Task.intent == row.intent, Task.opened_at >= row.effective_from)):
        task.dirty = True
    return row


def resolve_for_task(session: Session, tenant_id: str, intent: str, business_time: datetime,
                     pinned_ref: str | None = None) -> tuple[ContractRow, Contract] | None:
    """Policy time, not knowledge time: pick the version effective when the business event happened."""
    if pinned_ref:
        name, _, version = pinned_ref.partition("@v")
        row = session.scalar(select(ContractRow).where(
            ContractRow.tenant_id == tenant_id, ContractRow.name == name, ContractRow.version == int(version),
            ContractRow.status == "approved"))
    else:
        row = session.scalar(
            select(ContractRow)
            .where(ContractRow.tenant_id == tenant_id, ContractRow.intent == intent,
                   ContractRow.status == "approved", ContractRow.effective_from <= business_time)
            .order_by(ContractRow.effective_from.desc(), ContractRow.version.desc())
            .limit(1)
        )
    if row is None:
        return None
    return row, load_contract(row.body)
