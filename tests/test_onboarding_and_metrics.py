from __future__ import annotations

import json
from datetime import timedelta

from ccevals.audit import callback_body, sign, verify
from ccevals.models import OutboxMessage
from ccevals.packs.scenarios import ScenarioBuilder, happy_path
from tests.conftest import START, approve_starter, make_tenant, verify_all_sources


def test_readiness_names_missing_fields_and_owners(client):
    api = make_tenant(client, "fresh")
    ready = api.get("/v1/readiness").json()
    indicators = ready["indicators"]
    assert set(indicators) == {"conversations_connected", "policies_approved", "business_outcomes_verifiable",
                               "evaluators_calibrated"}
    assert not any(i["complete"] for i in indicators.values())
    assert indicators["policies_approved"]["missing"][0]["owner"] == "refund_policy_owner"
    assert "score" not in json.dumps(ready).lower().replace("no combined readiness score", "")

    onboarding = api.get("/v1/onboarding").json()
    assert onboarding["current_stage"] == 1
    assert onboarding["milestones"] == {"conversation_audit": True, "verified_outcome_claims": False}

    approve_starter(api)
    assert api.get("/v1/readiness").json()["indicators"]["policies_approved"]["complete"]
    verify_all_sources(api)
    ready = api.get("/v1/readiness").json()["indicators"]
    assert ready["business_outcomes_verifiable"]["complete"]
    assert api.get("/v1/onboarding").json()["milestones"]["verified_outcome_claims"] is True


def test_broken_adapter_fails_conformance(api):
    kit = api.get("/v1/connectors/payment_adapter/conformance-kit").json()["fixtures"]
    # This adapter maps a provider failure to "completed".
    for event in kit["known_failure"]["events"]:
        if event["event_type"] == "refund.leg_status" and event["payload"]["stage"] == "failed":
            event["payload"]["stage"] = "completed"
    report = api.post("/v1/connectors/payment_adapter/conformance", {"fixtures": kit}).json()
    assert report["passed"] is False
    assert report["fixtures"]["known_failure"]["passed"] is False
    ready = api.get("/v1/readiness").json()["indicators"]["business_outcomes_verifiable"]
    assert not ready["complete"] and ready["missing"][0]["sample"] == "known_failure"


def test_unverified_source_disables_verified_outcome_claims(client):
    api = make_tenant(client, "unverified")
    approve_starter(api)
    b = happy_path(ScenarioBuilder("uv", START))
    api.ingest(b.events)
    api.process(START + timedelta(days=3))
    result = api.evaluations(b.task_id)["current"]["result"]
    assert result["business_outcome"] == "unverified"
    assert "source_unverified" in {u["code"] for u in result["unknown_reasons"]}


def calibration_items(n: int, false_pass_critical: bool = False):
    items = []
    for i in range(n):
        truth = "fail" if i % 4 == 0 else "pass"
        evaluator = truth
        if false_pass_critical and i == 0:
            evaluator = "pass"
        items.append({"task_ref": f"t{i}", "reviewer_a": truth, "reviewer_b": truth if i % 10 else "pass",
                      "adjudicated": truth, "evaluator": evaluator, "severity": "critical" if i % 8 == 0 else "major"})
    return items


def test_calibration_requires_validation_split_and_no_critical_false_pass(api):
    ok = api.post("/v1/calibration", {"criterion": "issue_understanding", "language": "en", "split": "validation",
                                      "items": calibration_items(40)}).json()
    assert ok["passed"], ok
    calib_split = api.post("/v1/calibration", {"criterion": "issue_understanding", "language": "en",
                                               "split": "calibration", "items": calibration_items(40)}).json()
    assert not calib_split["passed"]
    bad = api.post("/v1/calibration", {"criterion": "issue_understanding", "language": "en", "split": "validation",
                                       "items": calibration_items(40, false_pass_critical=True)}).json()
    assert not bad["passed"]
    assert "evaluator passed a confirmed critical failure" in bad["metrics"]["problems"]


def test_cohort_metrics_reconcile_against_census(api):
    for i in range(4):
        api.ingest(happy_path(ScenarioBuilder(f"prod{i}", START)).events)
    lost = ScenarioBuilder("lost", START)
    lost.census(0)  # bot-assigned task whose conversation never arrived
    api.ingest(lost.events)
    api.process(START + timedelta(days=9))
    m = api.get("/v1/metrics", params={"opened_from": (START - timedelta(days=1)).isoformat(),
                                       "opened_before": (START + timedelta(days=1)).isoformat()}).json()
    s = m["slices"]["all"]
    assert s["census"]["eligible_tasks"] == 5
    assert s["census"]["missing_conversation"] == 1
    # The lost task matures and stays in the denominator as an unknown.
    assert s["census"]["matured"] == 5 and s["census"]["pending_maturation"] == 0
    assert s["verified_business_completion"]["denominator"] == 5
    assert s["unknown_rate"]["count"] == 1
    assert s["verified_business_completion"]["count"] == 4
    assert s["critical_violations"]["_none_observed"]["matured_tasks"] == 5
    assert m["measurement_health"]["verified_sources"]


def test_callbacks_are_signed_and_recorded(api):
    b = happy_path(ScenarioBuilder("cb", START))
    api.ingest(b.events)
    api.process(START + timedelta(minutes=10))
    from ccevals.db import new_session

    with new_session() as session:
        message = session.query(OutboxMessage).filter_by(tenant_id=api.tenant_id, event_type="verdict.created").first()
        assert message is not None and message.payload["decision_id"].startswith("dec_")
        body = callback_body(message)
    headers = sign("secret", body)
    assert verify("secret", body, headers)
    assert not verify("other", body, headers)
