"""The blueprint's reference marketplace journey (section 7), end to end through the API.

A customer paid INR 1200 (300 wallet + 900 card). The missing item is worth
INR 240, allocated pro rata as INR 60 wallet and INR 180 card.
"""

from __future__ import annotations

from datetime import timedelta

from ccevals.packs.scenarios import CARD, WALLET, ScenarioBuilder, standard_order
from tests.conftest import START


def build_journey(claim_entire_refund: bool = False) -> ScenarioBuilder:
    b = ScenarioBuilder("j1", START)
    action = "j1-refund"
    b.census(0)
    standard_order(b)
    b.profile(-30)
    # 09:00 contact, mixed Hindi and English
    b.open_task(0, {"li-1": 1}, language="hi-en")
    b.message(0, "customer", "Mera paneer tikka order mein nahi tha, please refund karo", language="hi-en")
    # 09:01 decision: entitlement and identity
    b.identity(1)
    b.message(1, "bot", "Aapka refund INR 240 hoga: INR 60 wallet aur INR 180 card par.", language="hi-en", latency_ms=1200)
    # 09:02 one logical action with wallet and card legs; the API times out after acceptance and is retried
    b.refund(2, action, {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.attempt(2, action, 1, "timeout")
    b.attempt(2.5, action, 2, "accepted")
    # 09:03 wallet credited, card submitted
    b.leg(3, action, WALLET, "credited", 6000)
    b.leg(3, action, CARD, "submitted", 18000, provider_ref="rf-1", version=1)
    if claim_entire_refund:
        b.claim(3, "completion", scope="all", stage="completed", amount=24000)
        b.message(3, "bot", "Your entire refund has reached you.", language="en", latency_ms=900)
    else:
        b.claim(3, "completion", scope=WALLET, stage="credited", amount=6000)
        b.claim(3, "submission", scope=CARD, stage="submitted", amount=18000)
        b.message(3, "bot", "INR 60 is credited to your wallet and INR 180 has been submitted to your card provider.",
                  language="en", latency_ms=900)
    # 09:04 processing estimate that matches policy; customer leaves
    b.claim(4, "estimate", days=5)
    b.ended(4, "customer_left")
    return b


def test_reference_journey_revisions(api):
    b = build_journey()
    receipt = api.ingest(b.events)
    assert receipt["rejected"] == 0 and receipt["quarantined"] == 0

    # Shortly after the chat: provisional result, partial business completion.
    api.process(START + timedelta(minutes=5))
    first = api.evaluations(b.task_id)
    assert first["current_revision"] == 1
    r1 = first["current"]["result"]
    assert first["current"]["provisional"] is True
    assert r1["business_outcome"] == "pending"
    assert r1["axes"]["obligations"][WALLET]["status"] == "complete"
    assert r1["axes"]["obligations"][CARD]["status"] == "submitted"
    crit = {k: v["result"] for k, v in r1["axes"]["bot_behaviour"].items()}
    assert crit["claims_supported_by_evidence"] == "pass"  # accurate submission wording passes
    assert crit["no_duplicate_refund"] == "pass"  # retry after timeout reused the logical action
    assert crit["allocation_matches_policy"] == "pass"
    assert crit["authorized_customer"] == "pass"
    assert crit["correct_disposition"] == "pass"
    assert r1["critical_violation"] is False
    assert r1["axes"]["lifecycle"] == "closed"  # ending the chat does not finalise resolution

    # Next day: a card-refund failure arrives after the submission acknowledgement.
    late = ScenarioBuilder("j1-late", START)
    late.task_id = b.task_id
    late.leg(60 * 24, "j1-refund", CARD, "failed", 18000, provider_ref="rf-1", version=2, source="payment_adapter")
    api.ingest(late.events)
    api.process(START + timedelta(days=1, minutes=1))
    second = api.evaluations(b.task_id)
    assert second["current_revision"] == 2
    r2 = second["current"]["result"]
    assert second["current"]["reason"] == "new_evidence"
    assert r2["business_outcome"] == "partial"
    assert r2["axes"]["obligations"][CARD]["status"] == "failed"
    assert r2["axes"]["obligations"][CARD]["recovery"] == "pending"
    # Earlier wording is still graded on the evidence available at the time.
    assert r2["axes"]["bot_behaviour"]["claims_supported_by_evidence"]["result"] == "pass"

    # Recovery: the assigned team re-issues the card refund.
    rec = ScenarioBuilder("j1-rec", START)
    rec.task_id = b.task_id
    rec.recovery(60 * 25, CARD)
    rec.leg(60 * 26, "j1-refund", CARD, "submitted", 18000, provider_ref="rf-2", version=1, source="payment_adapter")
    rec.leg(60 * 24 * 3, "j1-refund", CARD, "completed", 18000, provider_ref="rf-2", version=2, source="payment_adapter")
    api.ingest(rec.events)
    api.process(START + timedelta(days=3, minutes=1))
    third = api.evaluations(b.task_id)["current"]["result"]
    assert third["business_outcome"] == "verified_complete"
    assert third["axes"]["obligations"][CARD]["status"] == "complete"
    assert third["axes"]["bot_behaviour"]["no_duplicate_refund"]["result"] == "pass"

    # After the card deadline the result is finalised; history is preserved, never rewritten.
    api.process(START + timedelta(days=8))
    final = api.evaluations(b.task_id)
    assert final["finalized"] is True
    assert final["current"]["provisional"] is False
    assert final["history"][-1]["reason"] == "finalized_at_deadline"
    assert [h["revision"] for h in final["history"]] == list(range(1, len(final["history"]) + 1))
    first_again = api.get(f"/v1/tasks/{b.task_id}/evaluations/1").json()
    assert first_again["result"]["business_outcome"] == "pending"


def test_entire_refund_claim_fails_completion_criterion(api):
    b = build_journey(claim_entire_refund=True)
    api.ingest(b.events)
    api.process(START + timedelta(minutes=5))
    result = api.evaluations(b.task_id)["current"]["result"]
    claims = result["axes"]["bot_behaviour"]["claims_supported_by_evidence"]
    assert claims["result"] == "fail"
    assert {f["tender"] for f in claims["detail"]["failures"]} == {CARD}
    # The same conversation can have a correct money action and an unsupported claim.
    assert result["axes"]["bot_behaviour"]["amount_within_entitlement"]["result"] == "pass"


def test_late_event_after_final_appends_revision(api):
    b = build_journey()
    b.leg(60 * 24 * 2, "j1-refund", CARD, "completed", 18000, provider_ref="rf-1", version=2)
    api.ingest(b.events)
    api.process(START + timedelta(days=8))
    before = api.evaluations(b.task_id)
    assert before["finalized"] and before["current"]["result"]["business_outcome"] == "verified_complete"

    late = ScenarioBuilder("j1-rev", START)
    late.task_id = b.task_id
    late.leg(60 * 24 * 9, "j1-refund", CARD, "reversed", 18000, provider_ref="rf-1", version=3, source="payment_adapter")
    api.ingest(late.events)
    api.process(START + timedelta(days=9, minutes=5))
    after = api.evaluations(b.task_id)
    assert after["current_revision"] == before["current_revision"] + 1
    assert after["current"]["reason"] == "late_event"
    assert after["current"]["result"]["business_outcome"] == "partial"
    # The superseded verdict is still retrievable.
    old = api.get(f"/v1/tasks/{b.task_id}/evaluations/{before['current_revision']}").json()
    assert old["result"]["business_outcome"] == "verified_complete"
