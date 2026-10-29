"""Service-resolution pack: tickets, routing, SLAs, repeat contact and verified resolution."""

from __future__ import annotations

from importlib import resources

import yaml

from ccevals.contracts import registry
from ccevals.contracts.registry import dry_run, parse_contract
from ccevals.packs.reference_cases import CASES
from tests.conftest import make_tenant


def contract_body(name: str) -> dict:
    return yaml.safe_load(resources.files("ccevals.packs").joinpath(f"{name}.yaml").read_text())


def verdicts(case_id: str) -> dict[str, dict]:
    case = CASES[case_id]()
    contract = parse_contract(contract_body(case.contract))
    return {m.key: dry_run(contract, case.builder.events[:m.event_count], now=case.at(m)) for m in case.moments}


def crit(result: dict, name: str) -> str:
    return result["axes"]["bot_behaviour"][name]["result"]


def test_starter_service_contracts_reproduce_fixtures():
    for name in ("safety_complaint", "damaged_item_replacement", "login_issue"):
        report = registry.validate_contract(contract_body(name))
        assert report["valid"], (name, report)


def test_promised_escalation_without_ticket_is_critical():
    v = verdicts("kavya")
    assert crit(v["promise"], "ticket_created") == "not_evaluated"  # window still open
    deadline = v["deadline"]
    assert crit(deadline, "ticket_created") == "fail"
    assert crit(deadline, "promises_kept") == "fail"
    assert "did not retry" not in deadline["axes"]["bot_behaviour"]["ticket_created"]["reason"]
    assert "servicenow.create_incident" in deadline["axes"]["bot_behaviour"]["ticket_created"]["reason"]
    assert deadline["critical_violation"] is True
    # Later closure resolves the case but never erases the broken promise.
    assert v["final"]["business_outcome"] == "verified_resolved"
    assert v["final"]["critical_violation"] is True


def test_misrouted_ticket_sla_and_repeat_contact():
    v = verdicts("rohit")
    raised = v["ticket"]
    assert crit(raised, "ticket_created") == "pass"
    assert crit(raised, "ticket_routed_correctly") == "fail"
    assert raised["business_outcome"] == "customer_reported_only"
    assert crit(v["chase"], "no_repeat_contact") == "fail"
    assert crit(v["sla"], "sla_met") == "fail"
    assert v["delivered"]["business_outcome"] == "verified_resolved"


def test_customer_reported_resolution_is_not_verified():
    v = verdicts("neha")
    thanks = v["thanks"]
    assert thanks["business_outcome"] == "customer_reported_only"
    assert crit(thanks, "promises_kept") == "fail"  # the bot declared it fixed without a sign-in
    assert v["logs"]["business_outcome"] == "customer_reported_only"
    assert v["second"]["business_outcome"] == "verified_resolved"
    assert crit(v["second"], "no_repeat_contact") == "fail"


def test_missing_ticket_evidence_is_unknown_not_failure():
    case = CASES["kavya"]()
    events = [e for e in case.builder.events if e["event_type"] not in ("source.heartbeat", "ticket.created",
                                                                         "ticket.updated")]
    contract = parse_contract(contract_body("safety_complaint"))
    result = dry_run(contract, events, now=case.at(case.moments[-1]))
    assert crit(result, "ticket_created") == "unknown"
    assert "ticket_evidence_unavailable" in {u["code"] for u in result["unknown_reasons"]}


def test_service_contract_through_the_api(client):
    api = make_tenant(client, "svc")
    body = contract_body("safety_complaint")
    assert api.post("/v1/contracts", {"contract": body}).status_code == 201
    r = api.post("/v1/contracts/safety_complaint/versions/1:approve",
                 {"reviewed_fixtures": list(registry.REQUIRED_FIXTURES)}, role="owner")
    assert r.status_code == 200, r.text
    case = CASES["kavya"]()
    receipt = api.ingest(case.builder.events)
    assert receipt["rejected"] == 0, receipt
    deadline = next(m for m in case.moments if m.key == "deadline")
    api.process(case.at(deadline))
    current = api.evaluations(case.builder.task_id)["current"]
    assert current["contract_ref"] == "safety_complaint@v1"
    assert current["result"]["critical_violation"] is True
    items = api.get("/v1/review-items", params={"kind": "critical"}).json()["items"]
    assert {i["criterion"] for i in items} >= {"ticket_created", "promises_kept"}
