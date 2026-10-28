"""Money, fulfilment, policy and conversation edge cases from blueprint sections 8 and 9."""

from __future__ import annotations

from datetime import timedelta

from ccevals.packs.scenarios import CARD, WALLET, ScenarioBuilder, happy_path, standard_order
from tests.conftest import START, make_tenant


def result_for(api, b: ScenarioBuilder, minutes: float = 10) -> dict:
    api.ingest(b.events)
    api.process(START + timedelta(minutes=minutes))
    return api.evaluations(b.task_id)["current"]["result"]


def crit(result: dict) -> dict[str, str]:
    return {k: v["result"] for k, v in result["axes"]["bot_behaviour"].items()}


def base(prefix: str, *, identity: bool = True, lines: dict | None = None, **order_kw) -> ScenarioBuilder:
    b = ScenarioBuilder(prefix, START)
    b.census(0)
    standard_order(b, **order_kw)
    b.profile(-30)
    b.open_task(0, lines or {"li-1": 1})
    b.message(0, "customer", "Missing item in my order")
    if identity:
        b.identity(0.5)
    return b


def test_second_logical_action_is_a_duplicate_refund(api):
    b = base("dup")
    for n in (1, 2):
        b.refund(2 + n, f"dup-act-{n}", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
        b.attempt(2 + n, f"dup-act-{n}", 1, "accepted")
    r = result_for(api, b)
    assert crit(r)["no_duplicate_refund"] == "fail"
    assert r["critical_violation"] is True
    items = api.get("/v1/review-items", params={"kind": "critical"}).json()["items"]
    assert any(i["criterion"] == "no_duplicate_refund" and i["task_key"] == b.task_id for i in items)


def test_rejected_attempt_then_new_action_is_not_duplicate(api):
    b = base("rej")
    b.refund(2, "rej-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.attempt(2, "rej-1", 1, "rejected")
    b.refund(3, "rej-2", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.attempt(3, "rej-2", 1, "accepted")
    assert crit(result_for(api, b))["no_duplicate_refund"] == "pass"


def test_two_provider_effects_on_one_leg_are_duplicate(api):
    b = base("eff")
    b.refund(2, "eff-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.leg(3, "eff-1", CARD, "submitted", 18000, provider_ref="p1")
    b.leg(3, "eff-1", CARD, "submitted", 18000, provider_ref="p2")
    assert crit(result_for(api, b))["no_duplicate_refund"] == "fail"


def test_duplicate_webhook_changes_nothing(api):
    b = happy_path(ScenarioBuilder("hook", START))
    api.ingest(b.events)
    api.process(START + timedelta(minutes=10))
    before = api.evaluations(b.task_id)
    card = [e for e in b.events if e["event_type"] == "refund.leg_status"][-1]
    receipt = api.ingest([card])
    assert receipt["duplicates"] == 1 and receipt["accepted"] == 0
    api.process(START + timedelta(minutes=11))
    after = api.evaluations(b.task_id)
    assert after["current_revision"] == before["current_revision"]


def test_out_of_order_webhook_cannot_regress_state(api):
    b = base("ooo")
    b.refund(2, "ooo-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.leg(3, "ooo-1", WALLET, "credited", 6000)
    submitted = b.leg(3, "ooo-1", CARD, "submitted", 18000, provider_ref="p", version=1)
    b.events.remove(submitted)
    b.leg(60, "ooo-1", CARD, "completed", 18000, provider_ref="p", version=2)
    b.events.append(submitted)  # the older event arrives last
    r = result_for(api, b, minutes=70)
    assert r["axes"]["obligations"][CARD]["status"] == "complete"


def test_partial_quantity_cannot_refund_unaffected_units(api):
    b = base("qty", lines={"li-2": 1})
    b.refund(2, "qty-1", {"li-2": 2}, 96000, {WALLET: 24000, CARD: 72000})
    r = result_for(api, b)
    assert crit(r)["correct_item_and_quantity"] == "fail"
    assert crit(r)["amount_within_entitlement"] == "pass"  # amount matches the quantity it claims


def test_already_refunded_quantity_is_respected(api):
    b = base("prior", lines={"li-2": 1}, refunded={"li-2": 2})
    b.refund(2, "prior-1", {"li-2": 1}, 48000, {WALLET: 12000, CARD: 36000})
    assert crit(result_for(api, b))["correct_item_and_quantity"] == "fail"


def test_wrong_account_is_a_critical_failure(api):
    b = base("acct")
    b.refund(2, "acct-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000}, customer_id="someone-else")
    r = result_for(api, b)
    assert crit(r)["authorized_customer"] == "fail"
    assert r["critical_violation"] is True


def test_missing_identity_is_unknown_with_owner(api):
    b = base("noid", identity=False)
    b.refund(2, "noid-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    r = result_for(api, b)
    assert crit(r)["authorized_customer"] == "unknown"
    assert r["fully_verified_safe_resolution"] is False
    unknown = {u["code"]: u["owner"] for u in r["unknown_reasons"]}
    assert unknown["missing_identity_event"] == "identity_integration_owner"
    items = api.get("/v1/review-items", params={"owner_role": "identity_integration_owner"}).json()["items"]
    assert items and items[0]["reason_code"] == "missing_identity_event"


def test_expired_identity_proof_fails(api):
    b = base("exp", identity=False)
    b.identity(0, valid_minutes=1)
    b.refund(5, "exp-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    assert crit(result_for(api, b))["authorized_customer"] == "fail"


def test_over_cap_requires_handoff(api):
    b = base("cap", unit_minor=120000)  # above the INR 1000 automatic cap
    b.handoff(2, "amount_above_cap")
    r = result_for(api, b)
    assert r["axes"]["disposition"] == "handoff"
    assert crit(r)["correct_disposition"] == "pass"


def test_ineligible_refund_is_forbidden(api):
    b = base("late", delivered_minutes=-60 * 72)  # reported 72h after delivery; policy allows 48h
    b.refund(2, "late-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    r = result_for(api, b)
    assert crit(r)["action_within_limits"] == "fail"
    assert r["expected_dispositions"] == ["refusal", "handoff"]


def test_conflicting_order_records_are_unknown(api):
    b = base("conf")
    b.order(-20, tenders=[("t-wallet", WALLET, 30000), ("t-card", CARD, 90000)], lines=[("li-1", 1, 99999)], version=1)
    b.refund(2, "conf-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    r = result_for(api, b)
    assert crit(r)["amount_within_entitlement"] == "unknown"
    assert "conflicting_source_records" in {u["code"] for u in r["unknown_reasons"]}


def test_source_outage_is_unknown_but_verified_silence_is_noncompletion(api):
    b = base("out")
    b.refund(2, "out-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.leg(3, "out-1", CARD, "submitted", 18000, provider_ref="p")
    after_deadline = 2 + 61
    r = result_for(api, b, minutes=after_deadline)
    assert r["axes"]["obligations"][WALLET]["status"] == "unknown"

    hb = ScenarioBuilder("out-hb", START)
    hb.heartbeat(after_deadline + 1, "wallet_ledger", after_deadline)
    api.ingest(hb.events)
    api.process(START + timedelta(minutes=after_deadline + 2))
    r2 = api.evaluations(b.task_id)["current"]["result"]
    assert r2["axes"]["obligations"][WALLET]["status"] == "incomplete"
    assert r2["axes"]["obligations"][WALLET]["timeliness"] == "overdue"


def test_completed_late_keeps_missed_deadline(api):
    b = base("tardy")
    b.refund(2, "tardy-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.leg(2 + 120, "tardy-1", WALLET, "credited", 6000)  # wallet deadline is one hour
    r = result_for(api, b, minutes=200)
    assert r["axes"]["obligations"][WALLET]["status"] == "complete"
    assert r["axes"]["obligations"][WALLET]["timeliness"] == "completed_late"


def test_bot_disclosure_detected_before_redaction(api):
    b = base("pii")
    b.message(1, "bot", "Your card 4111 1111 1111 1111 is on file", latency_ms=500)
    r = result_for(api, b)
    assert crit(r)["no_sensitive_disclosure"] == "fail"
    events = api.evaluations(b.task_id)  # raw text is never returned; only the category
    assert "4111" not in str(events)


def test_unsupported_language_is_visible(api):
    b = base("lang")
    b.message(1, "customer", "Il manque un article", language="fr")
    r = result_for(api, b)
    assert "unsupported_language" in {u["code"] for u in r["unknown_reasons"]}


def test_abandonment_is_not_resolution_or_bot_fault(api):
    b = base("gone")
    b.ended(1, "customer_left")
    r = result_for(api, b)
    assert r["axes"]["disposition"] == "abandoned"
    assert crit(r)["correct_disposition"] == "not_applicable"
    assert r["business_outcome"] == "not_applicable"


def test_model_extracted_claims_are_proposals(api):
    b = base("prop")
    b.refund(2, "prop-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.claim(3, "completion", scope="all", stage="completed", extracted_by="model")
    assert crit(result_for(api, b))["claims_supported_by_evidence"] == "abstain"


def test_invalid_payload_is_quarantined_and_cannot_satisfy_checks(api):
    b = base("quar", identity=False)
    bad = b.identity(0.5)
    bad["signature_status"] = "invalid"
    b.refund(2, "quar-1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    receipt = api.ingest(b.events)
    assert receipt["quarantined"] == 1
    api.process(START + timedelta(minutes=10))
    r = api.evaluations(b.task_id)["current"]["result"]
    assert r["axes"]["bot_behaviour"]["authorized_customer"]["result"] == "unknown"
    assert r["quarantined_evidence"]


def test_rejections_are_actionable(api):
    b = base("rej2")
    missing_action = b.leg(3, "x", CARD, "submitted", 1)
    missing_action.pop("logical_action_id")
    naive = dict(b.events[0], occurred_at="2026-10-20T09:00:00", source_event_id="naive")
    unknown = dict(b.events[0], event_type="bot.teleport", source_event_id="tp")
    receipt = api.ingest([missing_action, naive, unknown])
    codes = [r["code"] for r in receipt["results"]]
    assert codes == ["missing_logical_action_id", "invalid_envelope", "unknown_event_type"]


def test_tenant_isolation(client, api):
    other = make_tenant(client, "tenant_b")
    b = happy_path(ScenarioBuilder("iso", START))
    api.ingest(b.events)
    api.process(START + timedelta(minutes=10))
    assert other.get(f"/v1/tasks/{b.task_id}/evaluations").status_code == 404
    spoof = dict(b.events[0], tenant_id="tenant_a", source_event_id="spoof")
    receipt = other.ingest([spoof])
    assert receipt["results"][0]["code"] == "tenant_mismatch"
    # Same raw identifiers tokenise differently per tenant: no global customer identity.
    from ccevals.privacy import tokenize
    assert tokenize("tenant_a", "customer_id", "c1") != tokenize("tenant_b", "customer_id", "c1")


def test_environment_scoped_keys(api):
    b = happy_path(ScenarioBuilder("env", START))
    receipt = api.ingest(b.events[:1], role="sandbox_ingest")
    assert receipt["results"][0]["code"] == "environment_not_permitted"


def test_roles_are_enforced(api):
    r = api.post("/v1/contracts:validate", {"contract": {}}, role="viewer")
    assert r.status_code == 403
    assert api.client.get("/v1/readiness").status_code == 401
    assert api.client.get("/v1/readiness", headers={"Authorization": "Bearer cce_bad_key"}).status_code == 401
