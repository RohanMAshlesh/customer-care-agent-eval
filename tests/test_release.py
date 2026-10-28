from __future__ import annotations

import random
from datetime import timedelta

from ccevals.models import utcnow
from ccevals.packs.scenarios import CARD, WALLET, ScenarioBuilder, happy_path
from ccevals.release import Episode, GatePolicy, evaluate_gate
from tests.conftest import START


def ep(family: str, trial: int, ok: bool, latency: int = 1000, critical: bool = False, slice_=None) -> Episode:
    return Episode(task_key=f"{family}-{trial}", family=family, trial=trial, indicator=1.0 if ok else 0.0,
                   critical_confirmed=["no_duplicate_refund"] if critical else [], critical_unconfirmed=[],
                   invalid=False, missing_critical=[], slice=slice_ or {"language": "en"}, latencies=[latency],
                   revision=1, decision_id="d", evidence_cutoff=None, evaluator_bundle="b")


def suite(p_ok: float, seed: int, families: int = 60, trials: int = 5, latency: int = 1000) -> list[Episode]:
    rng = random.Random(seed)
    out = []
    for f in range(families):
        difficulty = rng.random() * 0.1
        for t in range(1, trials + 1):
            out.append(ep(f"fam{f}", t, rng.random() < p_ok - difficulty, latency))
    return out


MANIFEST = {"families": 60, "trials_per_family": 5}


def gate(candidate, baseline, **kw):
    return evaluate_gate(candidate, baseline, MANIFEST, MANIFEST, [], GatePolicy(bootstrap_resamples=1000), **kw)


def test_equal_releases_pass():
    base = suite(0.97, seed=1)
    result = gate(base, base, latency_tradeoff_approved=False)
    assert result.decision == "pass", result.reasons


def test_demonstrated_regression_blocks():
    result = gate(suite(0.70, seed=2), suite(0.97, seed=1), latency_tradeoff_approved=False)
    assert result.decision == "block"
    assert "demonstrated_regression" in {r["code"] for r in result.reasons}


def test_unclear_small_regression_is_insufficient():
    result = gate(suite(0.94, seed=3), suite(0.97, seed=1), latency_tradeoff_approved=False)
    assert result.decision in ("insufficient_evidence", "block")
    assert {"noninferiority_not_shown", "demonstrated_regression"} & {r["code"] for r in result.reasons}


def test_critical_violation_always_blocks_even_when_incomplete():
    cand = suite(0.97, seed=1)[:20]
    cand[0] = ep("fam0", 1, True, critical=True)
    result = gate(cand, suite(0.97, seed=1), latency_tradeoff_approved=False)
    assert result.decision == "block"
    codes = {r["code"] for r in result.reasons}
    assert "critical_violation" in codes and "census_incomplete" in codes


def test_latency_regression_blocks_unless_tradeoff_approved():
    base, slow = suite(0.97, seed=1), suite(0.97, seed=1, latency=1200)
    assert gate(slow, base, latency_tradeoff_approved=False).decision == "block"
    approved = gate(slow, base, latency_tradeoff_approved=True)
    assert approved.decision == "pass"
    assert "latency_tradeoff_active" in {r["code"] for r in approved.reasons}


def test_required_slice_floor():
    base = suite(0.97, seed=1)
    result = evaluate_gate(base, base, MANIFEST, MANIFEST, [{"language": "hi-en"}], GatePolicy(bootstrap_resamples=200),
                           latency_tradeoff_approved=False)
    assert result.decision == "insufficient_evidence"
    assert any(r["code"] == "coverage_floor" and "hi-en" in r["slice"] for r in result.reasons)


# --- End to end through the API -------------------------------------------------


def run_suite(api, run_id: str, families: int, trials: int, *, duplicate_in: str | None = None) -> None:
    events = []
    for f in range(families):
        for t in range(1, trials + 1):
            b = ScenarioBuilder(f"{run_id}-f{f}-t{t}", START, environment="sandbox", run_id=run_id,
                                family=f"refund-family-{f}", trial=t, bot_ref=f"bot@{run_id}")
            happy_path(b)
            b.expected(["resolution"])
            if duplicate_in == f"f{f}-t{t}":
                b.refund(5, f"{b.prefix}-act-dup", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
                b.attempt(5, f"{b.prefix}-act-dup", 1, "accepted")
            events.extend(b.events)
    for i in range(0, len(events), 400):
        api.ingest(events[i:i + 400])
    api.process(START + timedelta(days=3))


def create_run(api, run_id: str, families: int, trials: int) -> None:
    r = api.post("/v1/runs", {"id": run_id, "bot_deployment_ref": f"bot@{run_id}", "contract_ref": "missing_item_refund@v1",
                              "dataset_ref": "refund-suite@2026-10", "families": families, "trials_per_family": trials})
    assert r.status_code == 201, r.text


def test_release_decision_end_to_end(api):
    create_run(api, "baseline", 3, 2)
    create_run(api, "candidate", 3, 2)
    run_suite(api, "baseline", 3, 2)
    run_suite(api, "candidate", 3, 2, duplicate_in="f1-t2")

    r = api.post("/v1/release-decisions", {"candidate_run_id": "candidate", "baseline_run_id": "baseline"})
    assert r.status_code == 201, r.text
    decision = r.json()
    assert decision["decision"] == "block"
    codes = {x["code"] for x in decision["reasons"]}
    assert "critical_violation" in codes and "coverage_floor" in codes
    assert decision["binding"]["bot_deployment_ref"] == "bot@candidate"
    assert decision["binding"]["contract_ref"] == "missing_item_refund@v1"
    assert decision["status"] == "active"

    # An exception sits beside the gate; it never changes the decision.
    r = api.post(f"/v1/release-decisions/{decision['id']}/exceptions", {
        "reason": "Hotfix for an outage; duplicate path is disabled by feature flag",
        "scope": "region demo_in, 5% traffic", "compensating_review": "Manual review of every refund for 48 hours",
        "expires_at": (utcnow() + timedelta(days=2)).isoformat()})
    assert r.status_code == 201, r.text
    assert r.json()["decision"] == "block" and len(r.json()["exceptions"]) == 1

    # A reviewer contests a candidate verdict: the decision needs review again.
    r = api.post("/v1/tasks/candidate-f1-t2-task/adjudications", {
        "criterion": "no_duplicate_refund", "label": "fail", "note": "Confirmed: two refunds reached the ledger."})
    assert r.status_code == 200, r.text
    again = api.get(f"/v1/release-decisions/{decision['id']}").json()
    assert again["status"] == "review_required"


def test_clean_runs_without_enough_coverage_are_insufficient(api):
    create_run(api, "base2", 2, 1)
    create_run(api, "cand2", 2, 1)
    run_suite(api, "base2", 2, 1)
    run_suite(api, "cand2", 2, 1)
    decision = api.post("/v1/release-decisions", {"candidate_run_id": "cand2", "baseline_run_id": "base2"}).json()
    assert decision["decision"] == "insufficient_evidence"
    assert decision["slices"]["aggregate"]["candidate_mean"] == 1.0
