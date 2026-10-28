"""Executable release policy: pass, block or insufficient evidence, with visible reasons.

The default policy follows the blueprint's example gate for one pilot workflow.
Tolerances are configuration agreed with the client, recorded in the decision,
and cannot be weakened inside a decision: changing them needs a new run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccevals.audit import audit, enqueue_callback
from ccevals.models import Event, ReleaseDecision, ReleaseException, Run, new_id, utcnow
from ccevals.pipeline import latest_results_for_run
from ccevals.stats import paired_cluster_bootstrap, percentile, zero_failure_upper_bound

PASSING = {"pass", "not_applicable"}


@dataclass(frozen=True)
class GatePolicy:
    noninferiority_margin: float = -0.02
    min_families_per_slice: int = 50
    min_trials_per_family: int = 5
    census_completeness_min: float = 0.995
    max_latency_increase: float = 0.10
    validity_days: int = 7
    bootstrap_resamples: int = 4000


@dataclass
class Episode:
    task_key: str
    family: str
    trial: int
    indicator: float
    critical_confirmed: list[str]
    critical_unconfirmed: list[str]
    invalid: bool
    missing_critical: list[str]
    slice: dict[str, str]
    latencies: list[int]
    revision: int | None
    decision_id: str | None
    evidence_cutoff: str | None
    evaluator_bundle: str | None


@dataclass
class GateResult:
    decision: str
    reasons: list[dict[str, Any]] = field(default_factory=list)
    slices: dict[str, Any] = field(default_factory=dict)
    statistics: dict[str, Any] = field(default_factory=dict)


def _expected_by_task(session: Session, tenant_id: str, run_id: str) -> dict[str, dict[str, Any]]:
    rows = session.scalars(select(Event).where(Event.tenant_id == tenant_id, Event.run_id == run_id,
                                               Event.event_type == "scenario.expected", Event.quarantined.is_(False)))
    return {e.task_key: e.payload for e in rows if e.task_key}


def episodes(session: Session, tenant_id: str, run: Run) -> list[Episode]:
    expected = _expected_by_task(session, tenant_id, run.id)
    out = []
    for task, ev in latest_results_for_run(session, tenant_id, run.id):
        result = ev.result if ev else {}
        execution = result.get("axes", {}).get("evaluation_execution")
        invalid = ev is None or execution != "complete"
        behaviour = result.get("axes", {}).get("bot_behaviour", {})
        required = result.get("required_checks", {})
        scenario = expected.get(task.task_key, {})
        allowed = scenario.get("expected_dispositions") or result.get("expected_dispositions") or []
        disposition = result.get("axes", {}).get("disposition")
        # Objective rule failures and reviewer-adjudicated failures are confirmed;
        # a semantic judge's critical failure needs adjudication first.
        critical = [n for n, c in behaviour.items() if c["result"] == "fail" and c["severity"] == "critical"]
        critical_confirmed = [n for n in critical
                              if behaviour[n]["kind"] == "objective" or behaviour[n].get("source") == "adjudication"]
        critical_unconfirmed = [n for n in critical if n not in critical_confirmed]
        missing_critical = [n for n, r in required.items()
                            if r in ("unknown", "not_evaluated", "abstain") and behaviour.get(n, {}).get("severity") == "critical"]
        all_required = bool(required) and all(r in PASSING for r in required.values())
        indicator = 1.0 if (not invalid and disposition in allowed and all_required) else 0.0
        slice_ = {"language": task.language or "unknown", "market": task.market or "unknown", **scenario.get("slice", {})}
        out.append(Episode(
            task_key=task.task_key, family=task.scenario_family or task.task_key, trial=task.trial or 1,
            indicator=indicator, critical_confirmed=critical_confirmed, critical_unconfirmed=critical_unconfirmed,
            invalid=invalid, missing_critical=missing_critical, slice=slice_,
            latencies=result.get("bot_latencies_ms", []), revision=ev.revision if ev else None,
            decision_id=ev.decision_id if ev else None, evidence_cutoff=result.get("evidence_cutoff"),
            evaluator_bundle=result.get("evaluator_bundle"),
        ))
    return out


def _by_family(eps: list[Episode]) -> dict[str, list[float]]:
    fams: dict[str, list[float]] = {}
    for e in eps:
        fams.setdefault(e.family, []).append(e.indicator)
    return fams


def _matches(slice_: dict[str, str], wanted: dict[str, str]) -> bool:
    return all(slice_.get(k) == v for k, v in wanted.items())


def evaluate_gate(candidate: list[Episode], baseline: list[Episode], candidate_manifest: dict[str, Any],
                  baseline_manifest: dict[str, Any], required_slices: list[dict[str, str]],
                  policy: GatePolicy, latency_tradeoff_approved: bool) -> GateResult:
    reasons: list[dict[str, Any]] = []
    block, insufficient = False, False

    def add(kind: str, code: str, message: str, **detail: Any) -> None:
        nonlocal block, insufficient
        reasons.append({"effect": kind, "code": code, "message": message, **detail})
        if kind == "block":
            block = True
        elif kind == "insufficient_evidence":
            insufficient = True

    confirmed = [(e.task_key, c) for e in candidate for c in e.critical_confirmed]
    if confirmed:
        add("block", "critical_violation", f"{len(confirmed)} confirmed critical violation(s) in required tests",
            examples=confirmed[:10])
    unconfirmed = [(e.task_key, c) for e in candidate for c in e.critical_unconfirmed]
    if unconfirmed:
        add("insufficient_evidence", "critical_needs_review",
            "semantic critical failures need adjudication before the gate can pass", examples=unconfirmed[:10])
    invalid = [e.task_key for e in candidate + baseline if e.invalid]
    if invalid:
        add("insufficient_evidence", "invalid_test", f"{len(invalid)} episode(s) have no valid evaluation", examples=invalid[:10])
    missing = [(e.task_key, m) for e in candidate for m in e.missing_critical]
    if missing:
        add("insufficient_evidence", "missing_critical_evidence", "critical checks lack evidence", examples=missing[:10])

    for name, eps, manifest in (("candidate", candidate, candidate_manifest), ("baseline", baseline, baseline_manifest)):
        declared = int(manifest.get("families", 0)) * int(manifest.get("trials_per_family", 0))
        completeness = len(eps) / declared if declared else 0.0
        if declared == 0 or completeness < policy.census_completeness_min:
            add("insufficient_evidence", "census_incomplete",
                f"{name} run evaluated {len(eps)} of {declared} declared episodes",
                run=name, completeness=round(completeness, 4))

    slices_out: dict[str, Any] = {}
    slice_specs = [("aggregate", {})] + [(json.dumps(s, sort_keys=True), s) for s in required_slices]
    comparison_stats: dict[str, Any] = {}
    for label, spec in slice_specs:
        cand = [e for e in candidate if _matches(e.slice, spec)]
        base = [e for e in baseline if _matches(e.slice, spec)]
        cf, bf = _by_family(cand), _by_family(base)
        matched = sorted(set(cf) & set(bf))
        min_trials = min((min(len(cf[f]), len(bf[f])) for f in matched), default=0)
        info: dict[str, Any] = {"families": len(matched), "min_trials_per_family": min_trials,
                                "candidate_episodes": len(cand), "baseline_episodes": len(base)}
        if len(matched) < policy.min_families_per_slice or min_trials < policy.min_trials_per_family:
            add("insufficient_evidence", "coverage_floor",
                f"slice {label}: {len(matched)} families and at least {min_trials} trials per family; "
                f"policy needs {policy.min_families_per_slice} and {policy.min_trials_per_family}. "
                "Floors are coverage minimums, not proof of statistical precision.", slice=label)
        if matched:
            cmp = paired_cluster_bootstrap({f: cf[f] for f in matched}, {f: bf[f] for f in matched},
                                           resamples=policy.bootstrap_resamples)
            info.update({k: round(v, 5) if isinstance(v, float) else v for k, v in asdict(cmp).items()})
            if cmp.ci_high < policy.noninferiority_margin:
                add("block", "demonstrated_regression",
                    f"slice {label}: whole 95% interval is below {policy.noninferiority_margin:+.0%}", slice=label)
            elif cmp.one_sided_lower_95 < policy.noninferiority_margin:
                add("insufficient_evidence", "noninferiority_not_shown",
                    f"slice {label}: one-sided 95% lower bound {cmp.one_sided_lower_95:+.2%} is below "
                    f"{policy.noninferiority_margin:+.0%}", slice=label)
            comparison_stats[label] = cmp.method
        clean = sum(1 for e in cand if not e.critical_confirmed)
        info["critical_free_episodes"] = clean
        info["critical_rate_upper_bound_if_independent"] = round(zero_failure_upper_bound(len(_by_family(cand))), 5) \
            if clean == len(cand) and cand else None
        slices_out[label] = info

    cand_lat = [ms for e in candidate for ms in e.latencies]
    base_lat = [ms for e in baseline for ms in e.latencies]
    cp95, bp95 = percentile(cand_lat, 0.95), percentile(base_lat, 0.95)
    latency = {"candidate_p95_ms": cp95, "baseline_p95_ms": bp95}
    if cp95 is not None and bp95:
        latency["increase"] = round(cp95 / bp95 - 1, 4)
        if cp95 > bp95 * (1 + policy.max_latency_increase):
            if latency_tradeoff_approved:
                reasons.append({"effect": "note", "code": "latency_tradeoff_active",
                                "message": "p95 latency regression accepted under an approved tradeoff"})
            else:
                add("block", "latency_regression",
                    f"bot p95 latency rose {cp95 / bp95 - 1:.1%} against the matched baseline")
    elif not cand_lat or not base_lat:
        add("insufficient_evidence", "latency_unmeasured", "bot response latency missing from one of the runs")

    decision = "block" if block else "insufficient_evidence" if insufficient else "pass"
    if decision == "pass":
        reasons.append({"effect": "pass", "code": "all_conditions_met",
                        "message": "all gate conditions pass for the bound deployment and scope"})
    stats = {"latency": latency, "policy": asdict(policy), "comparison_method": comparison_stats}
    return GateResult(decision, reasons, slices_out, stats)


def _snapshot_hash(eps: list[Episode]) -> str:
    material = sorted((e.task_key, e.decision_id or "", e.revision or 0) for e in eps)
    return hashlib.sha256(json.dumps(material).encode()).hexdigest()


def decide(session: Session, tenant_id: str, candidate_run_id: str, baseline_run_id: str, owner: str,
           required_slices: list[dict[str, str]], latency_tradeoff_approved: bool = False,
           policy: GatePolicy | None = None, now: datetime | None = None) -> ReleaseDecision:
    policy = policy or GatePolicy()
    now = now or utcnow()
    cand_run = session.get(Run, candidate_run_id)
    base_run = session.get(Run, baseline_run_id)
    if not cand_run or cand_run.tenant_id != tenant_id or not base_run or base_run.tenant_id != tenant_id:
        raise LookupError("run not found")
    cand_eps, base_eps = episodes(session, tenant_id, cand_run), episodes(session, tenant_id, base_run)
    gate = evaluate_gate(cand_eps, base_eps, cand_run.manifest, base_run.manifest, required_slices, policy,
                         latency_tradeoff_approved)
    if cand_run.contract_ref != base_run.contract_ref:
        gate.reasons.append({"effect": "note", "code": "contract_differs",
                             "message": "candidate and baseline used different contracts; compare policy effects separately"})
    binding = {
        "bot_deployment_ref": cand_run.bot_deployment_ref,
        "baseline_deployment_ref": base_run.bot_deployment_ref,
        "contract_ref": cand_run.contract_ref,
        "evaluator_bundles": sorted({e.evaluator_bundle for e in cand_eps if e.evaluator_bundle}),
        "dataset_ref": cand_run.dataset_ref,
        "evidence_snapshot": {"candidate": _snapshot_hash(cand_eps), "baseline": _snapshot_hash(base_eps),
                              "latest_evidence_cutoff": max((e.evidence_cutoff or "" for e in cand_eps), default=None)},
        "source_census": {"candidate": {"declared": cand_run.manifest, "evaluated": len(cand_eps)},
                          "baseline": {"declared": base_run.manifest, "evaluated": len(base_eps)}},
        "required_slices": required_slices,
        "latency_tradeoff_approved": latency_tradeoff_approved,
    }
    decision = ReleaseDecision(
        id="rd_" + new_id(), tenant_id=tenant_id, candidate_run_id=cand_run.id, baseline_run_id=base_run.id,
        decision=gate.decision, status="active", reasons=gate.reasons, slices=gate.slices, binding=binding,
        statistics=gate.statistics, accountable_owner=owner, created_at=now,
        valid_until=now + timedelta(days=policy.validity_days),
    )
    session.add(decision)
    audit(session, tenant_id, owner, "release_decision.created", decision.id, decision=gate.decision)
    enqueue_callback(session, tenant_id, "release_decision.created",
                     {"decision_id": decision.id, "decision": gate.decision, "valid_until": decision.valid_until.isoformat()})
    return decision


def effective_status(decision: ReleaseDecision, now: datetime | None = None) -> str:
    now = now or utcnow()
    if decision.status == "active" and now >= decision.valid_until:
        return "expired"
    return decision.status


def add_exception(session: Session, tenant_id: str, decision: ReleaseDecision, actor: str, reason: str, scope: str,
                  compensating_review: str, expires_at: datetime) -> ReleaseException:
    """Record a time-limited exception. It sits beside the gate; it never turns a failure into a pass."""
    if expires_at <= utcnow():
        raise ValueError("exception expiry must be in the future")
    if expires_at > decision.valid_until + timedelta(days=30):
        raise ValueError("exceptions may not outlive the decision by more than 30 days")
    exc = ReleaseException(tenant_id=tenant_id, decision_id=decision.id, reason=reason, scope=scope,
                           compensating_review=compensating_review, expires_at=expires_at, granted_by=actor)
    session.add(exc)
    audit(session, tenant_id, actor, "release_exception.granted", decision.id, scope=scope,
          expires_at=expires_at.isoformat(), original_decision=decision.decision)
    return exc
