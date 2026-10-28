from __future__ import annotations

from datetime import timedelta

from ccevals.contracts import registry
from ccevals.packs.scenarios import ScenarioBuilder, happy_path
from tests.conftest import START, make_tenant


def test_starter_contract_reproduces_required_fixtures():
    report = registry.validate_contract(registry.starter_contract())
    assert report["valid"], report
    assert {f["name"] for f in report["fixtures"]} == set(registry.REQUIRED_FIXTURES)


def test_invalid_rules_and_unknown_checks_are_reported():
    body = registry.starter_contract()
    body["eligibility"] = {"all": [{"field": "order.secret_field", "op": "~=", "value": 1}]}
    body["critical_checks"] = ["authorized_customer", "made_up_check"]
    report = registry.validate_contract(body)
    assert not report["valid"]
    joined = " ".join(report["schema_errors"])
    assert "unknown field" in joined and "unsupported operator" in joined and "made_up_check" in joined


def test_completion_stage_must_be_observable():
    body = registry.starter_contract()
    body["evidence"]["legs"]["card_refund"]["observable_stages"] = ["submitted"]
    report = registry.validate_contract(body)
    assert any("observable" in e for e in report["schema_errors"])


def test_client_fixture_mismatch_blocks_validity():
    b = happy_path(ScenarioBuilder("cf", START))
    report = registry.validate_contract(registry.starter_contract(), [
        {"name": "client_wrong_expectation", "events": b.events,
         "expect": {"criteria": {"correct_disposition": "fail"}}}])
    assert not report["valid"]
    assert report["fixtures"][-1]["mismatches"]


def test_governance_author_cannot_approve_and_versions_are_immutable(client):
    api = make_tenant(client, "gov")
    body = registry.starter_contract()
    assert api.post("/v1/contracts", {"contract": body}).status_code == 201
    # Author lacks the policy_owner role.
    r = api.post("/v1/contracts/missing_item_refund/versions/1:approve",
                 {"reviewed_fixtures": list(registry.REQUIRED_FIXTURES)})
    assert r.status_code == 403
    # Owner must review all four examples.
    r = api.post("/v1/contracts/missing_item_refund/versions/1:approve", {"reviewed_fixtures": ["positive"]}, role="owner")
    assert r.status_code == 422 and "missing_evidence" in r.text
    r = api.post("/v1/contracts/missing_item_refund/versions/1:approve",
                 {"reviewed_fixtures": list(registry.REQUIRED_FIXTURES)}, role="owner")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    # Approved versions cannot be edited.
    body["policy"]["processing_estimate_max_days"] = 30
    r = api.post("/v1/contracts", {"contract": body})
    assert r.status_code == 422 and "immutable" in r.text
    audit = api.get("/v1/audit", role="admin").json()["records"]
    assert {"contract.draft_saved", "contract.approved"} <= {a["action"] for a in audit}


def test_policy_time_selects_historical_contract(client):
    api = make_tenant(client, "pt")
    v1 = registry.starter_contract()
    v2 = registry.starter_contract()
    v2.update(version=2, effective_from="2026-10-25T00:00:00+05:30")
    v2["policy"] = dict(v2["policy"], document="returns@v8", effective_from="2026-10-25T00:00:00+05:30",
                        processing_estimate_max_days=3)
    for body in (v1, v2):
        assert api.post("/v1/contracts", {"contract": body}).status_code == 201
        r = api.post(f"/v1/contracts/{body['name']}/versions/{body['version']}:approve",
                     {"reviewed_fixtures": list(registry.REQUIRED_FIXTURES)}, role="owner")
        assert r.status_code == 200, r.text
    # An enquiry on 20 October concerns an order governed by the v1 terms.
    b = happy_path(ScenarioBuilder("pt", START))
    api.ingest(b.events)
    api.process(START + timedelta(minutes=10))
    current = api.evaluations(b.task_id)["current"]
    assert current["contract_ref"] == "missing_item_refund@v1"
    assert current["result"]["axes"]["bot_behaviour"]["estimate_within_policy"]["result"] == "pass"
