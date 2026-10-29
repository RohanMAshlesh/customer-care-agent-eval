"""Production metrics over a matured cohort, reconciled against the client's census.

Denominators come from the census of eligible bot-assigned tasks, including
tasks whose conversations never arrived. Matured unknowns stay in the
denominator. Sampled semantic estimates are reported separately from
full-coverage objective counts, and risk-sampled findings never enter the
weighted estimate.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccevals.models import Event, Task
from ccevals.pipeline import latest_evaluation, source_status
from ccevals.stats import percentile, wilson, zero_failure_upper_bound


def _rate(k: float, n: float) -> dict[str, Any]:
    if n == 0:
        return {"count": k, "denominator": 0, "rate": None, "ci95": None}
    lo, hi = wilson(k, n)
    return {"count": k, "denominator": n, "rate": round(k / n, 5), "ci95": [round(lo, 5), round(hi, 5)]}


def cohort_metrics(session: Session, tenant_id: str, intent: str, start: datetime, end: datetime,
                   now: datetime, slice_by: str | None = None) -> dict[str, Any]:
    tasks = list(session.scalars(select(Task).where(
        Task.tenant_id == tenant_id, Task.intent == intent, Task.environment == "production",
        Task.opened_at >= start, Task.opened_at < end)))
    groups: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        key = "all" if slice_by is None else str(getattr(task, slice_by, None) or "unknown")
        groups[key].append(task)
    report = {key: _group_metrics(session, group, now) for key, group in sorted(groups.items())}
    return {
        "intent": intent,
        "cohort": {"opened_from": start.isoformat(), "opened_before": end.isoformat()},
        "as_of": now.isoformat(),
        "slice_by": slice_by,
        "slices": report,
        "measurement_health": measurement_health(session, tenant_id, now),
    }


def _group_metrics(session: Session, tasks: list[Task], now: datetime) -> dict[str, Any]:
    census = [t for t in tasks if t.in_census]
    without_conversation = [t for t in census if not t.has_conversation]
    observed_not_in_census = [t for t in tasks if not t.in_census]
    matured = [t for t in census if t.finalized]
    results = {t.id: latest_evaluation(session, t) for t in tasks}

    outcome_counts: dict[str, int] = defaultdict(int)
    verified = fully = disposition_ok = disposition_n = unknown = pending = invalid = 0
    revisions = 0
    critical: dict[str, int] = defaultdict(int)
    coverage: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    semantic_random: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    semantic_risk: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    latencies: list[int] = []
    for task in matured:
        ev = results.get(task.id)
        if ev is None:
            outcome_counts["no_evaluation"] += 1
            unknown += 1
            continue
        res = ev.result
        revisions += ev.revision - 1
        outcome = res.get("business_outcome", "unknown")
        outcome_counts[outcome] += 1
        if res.get("axes", {}).get("evaluation_execution") != "complete":
            invalid += 1
        if outcome in ("verified_complete", "verified_resolved"):
            verified += 1
        if outcome in ("unknown", "unverified"):
            unknown += 1
        if res.get("fully_verified_safe_resolution"):
            fully += 1
        if res.get("disposition_correct") is not None:
            disposition_n += 1
            disposition_ok += int(res["disposition_correct"])
        for name, crit in res.get("axes", {}).get("bot_behaviour", {}).items():
            if crit["result"] == "fail" and crit["severity"] == "critical":
                critical[name] += 1
            if crit["kind"] == "objective" and crit["result"] != "not_applicable":
                coverage[name][1] += 1
                coverage[name][0] += int(crit.get("sufficiency") == "sufficient" and crit["result"] in ("pass", "fail"))
        for name, sample in res.get("semantic_sampling", {}).items():
            crit = res["axes"]["bot_behaviour"].get(name, {})
            if not sample["included"] or crit.get("result") not in ("pass", "fail"):
                continue
            if sample["sample_reason"] == "random":
                semantic_random[name].append((1 / sample["inclusion_probability"], crit["result"] == "pass"))
            else:
                semantic_risk[name][crit["result"]] += 1
        latencies.extend(res.get("bot_latencies_ms", []))
    for task in census:
        if not task.finalized:
            pending += 1

    estimates = {}
    for name, rows in semantic_random.items():
        total_w = sum(w for w, _ in rows)
        pass_w = sum(w for w, ok in rows if ok)
        n_eff = (total_w ** 2) / sum(w * w for w, _ in rows) if rows else 0
        p = pass_w / total_w if total_w else None
        lo, hi = wilson(p * n_eff, n_eff) if p is not None else (None, None)
        estimates[name] = {"estimated_pass_rate": round(p, 5) if p is not None else None,
                           "ci95": [round(lo, 5), round(hi, 5)] if lo is not None else None,
                           "sampled": len(rows), "label": "probability-weighted estimate from the random audit sample"}

    n = len(matured)
    critical_report = {name: {"observed": k, **_rate(k, n)} for name, k in sorted(critical.items())}
    if not critical and n:
        critical_report["_none_observed"] = {
            "matured_tasks": n,
            "upper_bound_95_if_independent": round(zero_failure_upper_bound(n), 6),
        }
    return {
        "census": {"eligible_tasks": len(census), "matured": n, "pending_maturation": pending,
                   "missing_conversation": len(without_conversation),
                   "observed_but_not_in_census": len(observed_not_in_census)},
        "verified_business_completion": _rate(verified, n),
        "fully_verified_safe_resolution": {**_rate(fully, n),
                                           "note": "all required objective and semantic checks passed"},
        "estimated_compliant_resolution": estimates,
        "risk_sampled_findings": {k: dict(v) for k, v in semantic_risk.items()},
        "correct_disposition": _rate(disposition_ok, disposition_n),
        "evidence_coverage": {name: _rate(ok, tot) for name, (ok, tot) in sorted(coverage.items())},
        "critical_violations": critical_report,
        "business_outcomes": dict(outcome_counts),
        "operating_efficiency": {"bot_p95_latency_ms": percentile(latencies, 0.95)},
        "unknown_rate": _rate(unknown, n),
        "invalid_evaluations": invalid,
        "result_revisions_after_first": revisions,
    }


def measurement_health(session: Session, tenant_id: str, now: datetime) -> dict[str, Any]:
    counts = dict(session.execute(
        select(Event.correlation_status, func.count()).where(Event.tenant_id == tenant_id,
                                                              Event.environment == "production")
        .group_by(Event.correlation_status)).all())
    quarantined = session.scalar(select(func.count()).where(Event.tenant_id == tenant_id, Event.quarantined.is_(True))) or 0
    total = sum(counts.values())
    status = source_status(session, tenant_id, "production")
    freshness = {src: round((now - mark).total_seconds() / 60, 1) for src, mark in status.watermarks.items()}
    return {
        "events": total,
        "unmatched_event_rate": _rate(counts.get("unmatched", 0), total),
        "ambiguous_events": counts.get("ambiguous", 0),
        "quarantined_events": quarantined,
        "source_lag_minutes": freshness,
        "verified_sources": sorted(status.verified_sources),
    }
