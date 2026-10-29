"""Assemble a complete, versioned task result from objective and semantic evaluators."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ccevals.contracts.schema import Contract
from ccevals.contracts.service import SERVICE_PACK_ID, ServiceContract
from ccevals.evaluation.criteria import CriterionResult, passed
from ccevals.evaluation.evidence import EventView, SourceStatus, TaskFacts, of_type, usable
from ccevals.evaluation.refund import PACK_ID, PACK_VERSION, evaluate_refund_task
from ccevals.evaluation.semantic import Judge, NullJudge, evaluate_semantic
from ccevals.evaluation.service import PACK_VERSION as SERVICE_PACK_VERSION
from ccevals.evaluation.service import evaluate_service_task


@dataclass(frozen=True)
class AdjudicationView:
    criterion: str
    label: str
    reviewer: str
    note: str
    evidence_event_ids: tuple[str, ...]
    created_at: datetime


@dataclass
class TaskResult:
    result: dict[str, Any]
    provisional: bool
    finalize_at: datetime | None
    content_hash: str
    evidence_cutoff: datetime | None


def _hash(result: dict[str, Any]) -> str:
    material = {k: v for k, v in result.items() if k not in ("evaluated_at", "evidence_cutoff")}
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def _apply_adjudications(criteria: dict[str, CriterionResult], adjudications: list[AdjudicationView]) -> None:
    for adj in sorted(adjudications, key=lambda a: a.created_at):
        original = criteria.get(adj.criterion)
        if original is None:
            continue
        criteria[adj.criterion] = CriterionResult(
            criterion=adj.criterion,
            result=adj.label,  # type: ignore[arg-type]
            severity=original.severity,
            kind=original.kind,
            reason=f"adjudicated by {adj.reviewer}: {adj.note}",
            evidence=list(adj.evidence_event_ids) or original.evidence,
            source="adjudication",
            detail={"original_result": original.result, "original_reason": original.reason},
        )


def evaluate(contract: Contract | ServiceContract, tenant_id: str, task: TaskFacts, events: list[EventView], sources: SourceStatus,
             now: datetime, judge: Judge, adjudications: list[AdjudicationView] | None = None) -> TaskResult:
    good = usable(events)
    if isinstance(contract, ServiceContract):
        pack = f"{SERVICE_PACK_ID}@{SERVICE_PACK_VERSION}"
        refund = evaluate_service_task(contract, task, good, sources, now)
    elif contract.intent == PACK_ID:
        pack = f"{PACK_ID}@{PACK_VERSION}"
        refund = evaluate_refund_task(contract, task, good, sources, now)
    else:
        raise ValueError(f"no intent pack for {contract.intent!r}")
    criteria = dict(refund.criteria)

    languages = {m.payload.get("language") for m in of_type(good, "conversation.message") if m.payload.get("language")}
    unsupported = sorted(lang for lang in languages if lang not in contract.scope.languages)
    unknowns = list(refund.unknowns)
    if unsupported:
        unknowns.append({"code": "unsupported_language", "owner": contract.owner_for("unsupported_language"),
                         "detail": f"languages outside scope: {unsupported}"})

    risk = set()
    if any(c.result == "fail" for c in criteria.values()):
        risk.add("objective_failure")
    if refund.business_outcome in ("failed", "partial"):
        risk.add("outcome_failure")
    if of_type(good, "bot.handoff"):
        risk.add("handoff")
    semantic, sampling = evaluate_semantic(contract, tenant_id, task.task_key, task.environment, good, judge,
                                           risk, bool(unsupported))
    for res in semantic.values():
        if res.unknown_reason == "judge_unavailable":
            unknowns.append({"code": "judge_unavailable", "owner": contract.owner_for("judge_unavailable"),
                             "detail": f"judge unavailable for {res.criterion}"})
            break
    criteria.update(semantic)
    _apply_adjudications(criteria, adjudications or [])

    required = set(contract.critical_checks) | {"correct_disposition"}
    if task.environment != "production" and not isinstance(judge, NullJudge):
        # Offline release cases receive every configured check, including semantic ones.
        required |= {c.id for c in contract.semantic_criteria}
    critical_violation = any(c.result == "fail" and c.severity == "critical" for c in criteria.values())
    required_results = {name: criteria[name].result for name in sorted(required) if name in criteria}
    all_required_pass = all(passed(criteria[name]) for name in required_results)
    disposition_ok = criteria["correct_disposition"].result == "pass"
    outcome_ok = refund.business_outcome in ("verified_complete", "verified_resolved", "not_applicable")
    fully_verified = all_required_pass and disposition_ok and outcome_ok and not unknowns

    lifecycle = "closed" if of_type(good, "conversation.ended") else "open"
    finalize_at = refund.finalize_at
    provisional = finalize_at is None or now < finalize_at
    observed = [e.observed_at for e in events]
    cutoff = max(observed) if observed else None
    any_invalid = any(e.quarantined for e in events)

    result: dict[str, Any] = {
        "pack": pack,
        "contract_ref": contract.ref,
        "policy_ref": contract.policy.document,
        "evaluator_bundle": f"{pack}+{judge.bundle_id}",
        "evaluated_at": now.isoformat(),
        "evidence_cutoff": cutoff.isoformat() if cutoff else None,
        "provisional": provisional,
        "axes": {
            "lifecycle": lifecycle,
            "disposition": refund.disposition,
            "obligations": refund.obligations,
            "evidence_sufficiency": {name: c.sufficiency for name, c in criteria.items()},
            "timeliness": refund.timeliness,
            "bot_behaviour": {name: c.to_dict() for name, c in sorted(criteria.items())},
            "evaluation_execution": "complete",
        },
        "business_outcome": refund.business_outcome,
        "expected_dispositions": refund.expected_dispositions,
        "disposition_correct": {"pass": True, "fail": False}.get(criteria["correct_disposition"].result),
        "critical_violation": critical_violation,
        "required_checks": required_results,
        "required_check_coverage": (
            sum(1 for r in required_results.values() if r not in ("not_evaluated", "unknown", "abstain"))
            / len(required_results) if required_results else 1.0
        ),
        "fully_verified_safe_resolution": fully_verified,
        "unknown_reasons": sorted(unknowns, key=lambda u: u["code"]),
        "semantic_sampling": sampling,
        "quarantined_evidence": [e.id for e in events if e.quarantined],
        "has_invalid_evidence": any_invalid,
        "decision_time": refund.decision_time.isoformat() if refund.decision_time else None,
        "bot_latencies_ms": [
            int(m.payload["latency_ms"]) for m in of_type(good, "conversation.message")
            if m.payload.get("speaker") == "bot" and m.payload.get("latency_ms") is not None
        ],
        "languages": sorted(languages),
    }
    return TaskResult(result, provisional, finalize_at, _hash(result), cutoff)


def errored_result(contract_ref: str | None, error: str, now: datetime) -> dict[str, Any]:
    """An evaluator failure is recorded as an execution error, never as a bot failure."""
    return {
        "contract_ref": contract_ref,
        "evaluated_at": now.isoformat(),
        "provisional": True,
        "axes": {"evaluation_execution": "errored"},
        "error": error[:500],
        "business_outcome": "unknown",
        "critical_violation": False,
        "fully_verified_safe_resolution": False,
        "unknown_reasons": [],
    }
