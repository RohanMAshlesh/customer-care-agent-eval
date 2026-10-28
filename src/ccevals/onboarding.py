"""Client journey: evidence-gated onboarding, readiness indicators, connector
conformance and evaluator calibration.

Readiness is reported as four separate indicators, never one reassuring score.
Each incomplete indicator names the missing field, the accountable owner and a
sample case so the client knows exactly what to do next.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccevals.contracts.registry import dry_run
from ccevals.contracts.schema import Contract
from ccevals.models import (
    Attestation,
    CalibrationReport,
    ConformanceRun,
    Event,
    ReleaseDecision,
    ReleaseException,
    Task,
)
from ccevals.models import (
    Contract as ContractRow,
)
from ccevals.packs.scenarios import CARD, WALLET, ScenarioBuilder, standard_order
from ccevals.schemas import EventRejection
from ccevals.stats import cohen_kappa

ATTESTED_STAGES = {
    "qualify_pilot": "Signed scope and exclusions",
    "establish_trust": "Approved data flow, credentials, retention and permitted model endpoints",
    "activate_release_gates": "Approved gate thresholds, rollback authority and review rota",
    "expand_coverage": "Next intent, market or channel agreed",
}

# ---------------------------------------------------------------------------
# Connector conformance


def source_roles(contract: Contract) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, Any]] = {
        contract.evidence.identity: {"role": "identity"},
        contract.evidence.order: {"role": "order"},
    }
    for name, leg in contract.evidence.legs.items():
        roles[leg.source] = {"role": "leg", "tender_type": leg.tender_type, "leg": name,
                             "completion_stage": leg.completion_stage, "deadline_hours": leg.deadline_hours}
    return roles


REQUIRED_BY_ROLE = {
    "leg": ["known_success", "known_failure", "missing_key", "duplicate_delivery", "out_of_order",
            "late_correction", "source_outage"],
    "identity": ["known_success", "known_failure", "wrong_customer", "missing_key", "duplicate_delivery", "source_outage"],
    "order": ["known_success", "wrong_customer", "missing_key", "duplicate_delivery", "out_of_order", "source_outage"],
}


def _base(b: ScenarioBuilder, *, identity: bool = True, order: bool = True) -> str:
    action = f"{b.prefix}-act"
    b.census(0)
    if order:
        standard_order(b)
    b.profile(-30)
    b.open_task(0, {"li-1": 1})
    b.message(0, "customer", "An item is missing from my order")
    if identity:
        b.identity(0.5)
    b.refund(2, action, {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.attempt(2, action, 1, "accepted")
    b.ended(4)
    return action


def conformance_kit(contract: Contract, source: str) -> dict[str, Any]:
    """Reference scenarios. The client replaces this source's events with its adapter's output."""
    roles = source_roles(contract)
    if source not in roles:
        raise KeyError(f"{source} is not an evidence source in {contract.ref}")
    spec = roles[source]
    start = max(contract.effective_from, contract.policy.effective_from) + timedelta(days=1)
    kit: dict[str, Any] = {}
    for fixture in REQUIRED_BY_ROLE[spec["role"]]:
        b = ScenarioBuilder(f"cf-{fixture}", start, environment="conformance",
                            sources={"identity": contract.evidence.identity, "order": contract.evidence.order})
        evaluate_after_minutes: float | None = None
        if spec["role"] == "leg":
            tender, stage = spec["tender_type"], spec["completion_stage"]
            amount = 6000 if tender == WALLET else 18000
            action = _base(b)

            def leg(minutes, st, version, _b=b, _a=action, _t=tender, _amt=amount):
                return _b.leg(minutes, _a, _t, st, _amt, source=source, version=version, provider_ref="ref-1")

            if fixture == "known_success":
                if stage == "completed":
                    leg(3, "submitted", 1)
                leg(30, stage, 2)
            elif fixture == "known_failure":
                leg(3, "submitted", 1)
                leg(30, "failed", 2)
            elif fixture == "missing_key":
                leg(30, stage, 2).pop("logical_action_id")
            elif fixture == "duplicate_delivery":
                final = leg(30, stage, 2)
                b.events.append(copy.deepcopy(final))
            elif fixture == "out_of_order":
                first = leg(3, "submitted", 1)
                b.events.remove(first)
                leg(30, stage, 2)
                b.events.append(first)  # delivered last although it happened first
            elif fixture == "late_correction":
                leg(30, stage, 2)
                leg(60 * 24, "reversed", 3)
            elif fixture == "source_outage":
                evaluate_after_minutes = 2 + spec["deadline_hours"] * 60 + 60
        elif spec["role"] == "identity":
            if fixture in ("known_success", "duplicate_delivery"):
                _base(b)
                if fixture == "duplicate_delivery":
                    dup = next(e for e in b.events if e["event_type"] == "identity.verified")
                    b.events.append(copy.deepcopy(dup))
            elif fixture == "known_failure":
                _base(b, identity=False)
                b.heartbeat(5, source, 10)
            elif fixture == "wrong_customer":
                _base(b, identity=False)
                b.identity(0.5, customer_id=f"{b.prefix}-someone-else")
                b.heartbeat(5, source, 10)
            elif fixture == "missing_key":
                _base(b, identity=False)
                b.identity(0.5)["entity_refs"] = {}
            elif fixture == "source_outage":
                _base(b, identity=False)
        elif spec["role"] == "order":
            if fixture in ("known_success", "duplicate_delivery"):
                _base(b)
                if fixture == "duplicate_delivery":
                    dup = next(e for e in b.events if e["event_type"] == "order.snapshot")
                    b.events.append(copy.deepcopy(dup))
            elif fixture == "wrong_customer":
                _base(b, order=False)
                standard_order(b, customer_id=f"{b.prefix}-someone-else")
            elif fixture == "missing_key":
                _base(b, order=False)
                standard_order(b)["entity_refs"] = {}
            elif fixture == "out_of_order":
                _base(b, order=False)
                b.order(-60, tenders=[("t-wallet", WALLET, 30000), ("t-card", CARD, 90000)],
                        lines=[("li-9", 1, 1000)], version=1)
                newer = standard_order(b, minutes=-30, version=2)
                b.events.remove(newer)
                b.events.insert(0, newer)
            elif fixture == "source_outage":
                _base(b, order=False)
        kit[fixture] = {"events": b.events, "evaluate_after_minutes": evaluate_after_minutes,
                        "start": start.isoformat(), "source_events": [e["event_id"] for e in b.events if e["source"] == source]}
    return {"source": source, "role": spec, "contract_ref": contract.ref, "fixtures": kit}


def _check(role: dict[str, Any], fixture: str, result: dict[str, Any] | None, rejected: list[str]) -> str | None:
    """Return a mismatch message, or None when the adapter behaved as specified."""
    if fixture == "missing_key":
        return None if rejected else "an event without its required key was accepted"
    if result is None:
        return "fixture could not be evaluated"
    crit = {k: v["result"] for k, v in result["axes"]["bot_behaviour"].items()}
    if role["role"] == "leg":
        status = result["axes"]["obligations"].get(role["tender_type"], {}).get("status")
        want = {"known_success": "complete", "known_failure": "failed", "duplicate_delivery": "complete",
                "out_of_order": "complete", "late_correction": "failed", "source_outage": "unknown"}[fixture]
        if status != want:
            return f"expected leg status {want}, got {status}"
        if fixture == "duplicate_delivery" and crit.get("no_duplicate_refund") != "pass":
            return "a duplicated delivery was counted as a second financial effect"
        return None
    if role["role"] == "identity":
        got = crit.get("authorized_customer")
        want = {"known_success": "pass", "duplicate_delivery": "pass", "known_failure": "fail",
                "wrong_customer": "fail", "source_outage": "unknown"}[fixture]
        return None if got == want else f"expected authorized_customer {want}, got {got}"
    got_items, got_auth = crit.get("correct_item_and_quantity"), crit.get("authorized_customer")
    if fixture in ("known_success", "duplicate_delivery", "out_of_order"):
        return None if got_items == "pass" else f"expected correct_item_and_quantity pass, got {got_items}"
    if fixture == "wrong_customer":
        return None if got_auth == "fail" else f"expected authorized_customer fail, got {got_auth}"
    if fixture == "source_outage":
        return None if got_items == "unknown" else f"expected correct_item_and_quantity unknown, got {got_items}"
    return f"unhandled fixture {fixture}"


def run_conformance(contract: Contract, source: str, fixtures: dict[str, Any]) -> dict[str, Any]:
    from ccevals.ingestion import normalize  # local import keeps module load order simple

    roles = source_roles(contract)
    if source not in roles:
        raise KeyError(f"{source} is not an evidence source in {contract.ref}")
    role = roles[source]
    report: dict[str, Any] = {}
    for name in REQUIRED_BY_ROLE[role["role"]]:
        fixture = fixtures.get(name)
        if fixture is None:
            report[name] = {"passed": False, "message": "fixture not supplied"}
            continue
        events = fixture["events"] if isinstance(fixture, dict) else fixture
        rejected, accepted = [], []
        for raw in events:
            try:
                normalize("conformance", "conformance", raw, datetime.now().astimezone())
                accepted.append(raw)
            except EventRejection as exc:
                rejected.append(f"{raw.get('event_id')}: {exc.code}")
        result = None
        if name != "missing_key":
            try:
                now = None
                if isinstance(fixture, dict) and fixture.get("evaluate_after_minutes") is not None:
                    now = datetime.fromisoformat(fixture["start"]) + timedelta(minutes=fixture["evaluate_after_minutes"])
                result = dry_run(contract, accepted, now=now)
            except Exception as exc:
                report[name] = {"passed": False, "message": f"evaluation error: {exc}", "rejected": rejected}
                continue
        mismatch = _check(role, name, result, rejected)
        report[name] = {"passed": mismatch is None, "message": mismatch or "behaved as specified", "rejected": rejected}
    return {"source": source, "role": role["role"], "passed": all(r["passed"] for r in report.values()),
            "fixtures": report,
            "not_covered": ["source-census reconciliation and checkpoint backfill are demonstrated in shadow mode"]}


# ---------------------------------------------------------------------------
# Calibration


def calibration_report(items: list[dict[str, Any]], split: str, min_items: int = 30,
                       max_false_pass: float = 0.05, max_false_alarm: float = 0.10) -> dict[str, Any]:
    """Compare an evaluator with adjudicated expert labels.

    Two reviewers label independently; differences are adjudicated. Only a
    held-out validation split can pass. Critical false passes are reported
    separately because overall agreement can hide them.
    """
    a = [i["reviewer_a"] for i in items]
    b = [i["reviewer_b"] for i in items]
    decided = [i for i in items if i["evaluator"] in ("pass", "fail")]
    adjudicated_fail = [i for i in decided if i["adjudicated"] == "fail"]
    adjudicated_pass = [i for i in decided if i["adjudicated"] == "pass"]
    false_pass = [i for i in adjudicated_fail if i["evaluator"] == "pass"]
    false_alarm = [i for i in adjudicated_pass if i["evaluator"] == "fail"]
    critical_fail = [i for i in adjudicated_fail if i.get("severity") == "critical"]
    critical_false_pass = [i for i in false_pass if i.get("severity") == "critical"]
    metrics = {
        "items": len(items),
        "split": split,
        "reviewer_kappa": cohen_kappa(a, b),
        "reviewer_disagreements": sum(1 for x, y in zip(a, b, strict=True) if x != y),
        "abstain_rate": round(1 - len(decided) / len(items), 4) if items else None,
        "false_pass_rate": round(len(false_pass) / len(adjudicated_fail), 4) if adjudicated_fail else None,
        "false_alarm_rate": round(len(false_alarm) / len(adjudicated_pass), 4) if adjudicated_pass else None,
        "critical_failures_in_set": len(critical_fail),
        "critical_false_passes": len(critical_false_pass),
        "thresholds": {"min_items": min_items, "max_false_pass": max_false_pass, "max_false_alarm": max_false_alarm},
    }
    problems = []
    if split != "validation":
        problems.append("only a held-out validation split can approve an evaluator")
    if len(items) < min_items:
        problems.append(f"needs at least {min_items} adjudicated items")
    if not adjudicated_fail:
        problems.append("set contains no confirmed failures, so false passes cannot be measured")
    if critical_false_pass:
        problems.append("evaluator passed a confirmed critical failure")
    if metrics["false_pass_rate"] is not None and metrics["false_pass_rate"] > max_false_pass:
        problems.append("false-pass rate above threshold")
    if metrics["false_alarm_rate"] is not None and metrics["false_alarm_rate"] > max_false_alarm:
        problems.append("false-alarm rate above threshold")
    metrics["problems"] = problems
    return {"passed": not problems, "metrics": metrics}


# ---------------------------------------------------------------------------
# Readiness and onboarding stages


def _approved_contracts(session: Session, tenant_id: str) -> list[tuple[ContractRow, Contract]]:
    rows = session.scalars(select(ContractRow).where(ContractRow.tenant_id == tenant_id, ContractRow.status == "approved")
                           .order_by(ContractRow.effective_from.desc()))
    return [(r, Contract.model_validate(r.body)) for r in rows]


def readiness(session: Session, tenant_id: str, now: datetime, judge_bundle: str) -> dict[str, Any]:
    contracts = _approved_contracts(session, tenant_id)
    indicators: dict[str, Any] = {}

    recent = session.scalar(select(func.count()).where(
        Event.tenant_id == tenant_id, Event.environment == "production", Event.event_type == "conversation.message",
        Event.ingested_at >= now - timedelta(days=7))) or 0
    census = session.scalar(select(func.count()).where(Task.tenant_id == tenant_id, Task.environment == "production",
                                                       Task.in_census.is_(True))) or 0
    with_conv = session.scalar(select(func.count()).where(Task.tenant_id == tenant_id, Task.environment == "production",
                                                          Task.in_census.is_(True), Task.has_conversation.is_(True))) or 0
    sample_missing = session.scalar(select(Task.task_key).where(
        Task.tenant_id == tenant_id, Task.in_census.is_(True), Task.has_conversation.is_(False)).limit(1))
    missing = []
    if recent == 0:
        missing.append({"field": "conversation.message events (last 7 days)", "owner": "integration_engineer",
                        "sample": None})
    if census == 0:
        missing.append({"field": "census.task_assigned events from an independent task census",
                        "owner": "integration_engineer", "sample": None})
    elif with_conv / census < 0.995:
        missing.append({"field": f"conversations for {census - with_conv} census tasks", "owner": "integration_engineer",
                        "sample": sample_missing})
    indicators["conversations_connected"] = {"complete": not missing, "missing": missing,
                                             "detail": {"messages_7d": recent, "census_tasks": census,
                                                        "census_tasks_with_conversation": with_conv}}

    missing = [] if contracts else [{"field": "an approved intent contract", "owner": "refund_policy_owner",
                                     "sample": "POST /v1/contracts:validate with the starter contract"}]
    indicators["policies_approved"] = {"complete": not missing, "missing": missing,
                                       "detail": {"approved": [r.ref for r, _ in contracts]}}

    latest: dict[str, ConformanceRun] = {}
    for run in session.scalars(select(ConformanceRun).where(ConformanceRun.tenant_id == tenant_id)
                               .order_by(ConformanceRun.created_at)):
        latest[run.source] = run
    missing = []
    for _, contract in contracts[:1]:
        for source, role in source_roles(contract).items():
            run = latest.get(source)
            if run is None or not run.passed:
                failed = [k for k, v in (run.results.get("fixtures", {}) if run else {}).items() if not v["passed"]]
                missing.append({"field": f"connector conformance for {source} ({role['role']})",
                                "owner": contract.owner_for("source_unverified"),
                                "sample": failed[0] if failed else f"GET /v1/connectors/{source}/conformance-kit"})
    if not contracts:
        missing.append({"field": "an approved contract naming the evidence sources", "owner": "refund_policy_owner",
                        "sample": None})
    indicators["business_outcomes_verifiable"] = {"complete": not missing, "missing": missing,
                                                  "detail": {"verified_sources": sorted(s for s, r in latest.items() if r.passed)}}

    missing = []
    if judge_bundle == "none":
        missing.append({"field": "a semantic judge (CCEVALS_JUDGE_PROVIDER)", "owner": "evaluation_engineer",
                        "sample": None})
    calibrated = {(r.criterion, r.language) for r in session.scalars(select(CalibrationReport).where(
        CalibrationReport.tenant_id == tenant_id, CalibrationReport.passed.is_(True),
        CalibrationReport.evaluator_bundle == judge_bundle))}
    for _, contract in contracts[:1]:
        for criterion in contract.semantic_criteria:
            for language in contract.scope.languages:
                if (criterion.id, language) not in calibrated:
                    missing.append({"field": f"validation calibration for {criterion.id} in {language}",
                                    "owner": "evaluation_engineer", "sample": f"{criterion.id}/{language}"})
    indicators["evaluators_calibrated"] = {"complete": not missing, "missing": missing,
                                           "detail": {"evaluator_bundle": judge_bundle}}
    return {"as_of": now.isoformat(), "indicators": indicators,
            "note": "Indicators are reported separately on purpose; there is no combined readiness score."}


def onboarding_stages(session: Session, tenant_id: str, now: datetime, judge_bundle: str) -> dict[str, Any]:
    ready = readiness(session, tenant_id, now, judge_bundle)["indicators"]
    attestations = {a.stage: a for a in session.scalars(select(Attestation).where(Attestation.tenant_id == tenant_id))}

    def attested(stage: str) -> dict[str, Any]:
        a = attestations.get(stage)
        return {"complete": a is not None, "exit_evidence": ATTESTED_STAGES[stage],
                "evidence_ref": a.evidence_ref if a else None, "attested_by": a.attested_by if a else None}

    events = session.scalar(select(func.count()).where(Event.tenant_id == tenant_id, Event.environment == "production")) or 0
    unmatched = session.scalar(select(func.count()).where(Event.tenant_id == tenant_id, Event.environment == "production",
                                                          Event.correlation_status.in_(["unmatched", "ambiguous"]))) or 0
    finalized = session.scalar(select(func.count()).where(Task.tenant_id == tenant_id, Task.environment == "production",
                                                          Task.finalized.is_(True))) or 0
    decisions = session.scalar(select(func.count()).where(ReleaseDecision.tenant_id == tenant_id)) or 0
    overrides = session.scalar(select(func.count()).where(ReleaseException.tenant_id == tenant_id)) or 0
    calibrated_rows = session.scalar(select(func.count()).where(CalibrationReport.tenant_id == tenant_id,
                                                                CalibrationReport.passed.is_(True))) or 0
    stages = [
        {"stage": 1, "name": "Qualify the pilot", "owner": "Support and AI leads", **attested("qualify_pilot")},
        {"stage": 2, "name": "Establish trust", "owner": "Security and platform", **attested("establish_trust")},
        {"stage": 3, "name": "Connect observations", "owner": "Integration engineer",
         "complete": ready["conversations_connected"]["complete"] and ready["business_outcomes_verifiable"]["complete"],
         "exit_evidence": "Conformance, source freshness and census reconciliation",
         "missing": ready["conversations_connected"]["missing"] + ready["business_outcomes_verifiable"]["missing"]},
        {"stage": 4, "name": "Map business entities", "owner": "Domain and data owners",
         "complete": events > 0 and unmatched / events < 0.01,
         "exit_evidence": "Reviewed joins and unmatched-event report",
         "detail": {"events": events, "unmatched_or_ambiguous": unmatched}},
        {"stage": 5, "name": "Define success", "owner": "Policy owner",
         "complete": ready["policies_approved"]["complete"], "exit_evidence": "Versioned intent contract",
         "missing": ready["policies_approved"]["missing"]},
        {"stage": 6, "name": "Validate evaluation", "owner": "QA and domain experts",
         "complete": ready["evaluators_calibrated"]["complete"] or (judge_bundle == "none" and calibrated_rows == 0
                                                                   and ready["policies_approved"]["complete"]),
         "exit_evidence": "Adjudicated labels and calibration report",
         "missing": ready["evaluators_calibrated"]["missing"]},
        {"stage": 7, "name": "Run in shadow", "owner": "Operations and release owner",
         "complete": finalized > 0, "exit_evidence": "Measured coverage, cost, false alarms and drift",
         "detail": {"finalized_production_tasks": finalized}},
        {"stage": 8, "name": "Activate release gates", "owner": "Release and support leads",
         **attested("activate_release_gates"),
         "practised_override": overrides > 0, "release_decisions": decisions},
        {"stage": 9, "name": "Expand coverage", "owner": "Joint product team", **attested("expand_coverage")},
    ]
    stages[7]["complete"] = stages[7]["complete"] and overrides > 0 and decisions > 0
    current = next((s["stage"] for s in stages if not s["complete"]), None)
    verified_claims = ready["business_outcomes_verifiable"]["complete"]
    return {"as_of": now.isoformat(), "current_stage": current, "stages": stages,
            "milestones": {"conversation_audit": True, "verified_outcome_claims": verified_claims},
            "note": "Verified-outcome claims stay disabled until business mappings pass conformance."}
