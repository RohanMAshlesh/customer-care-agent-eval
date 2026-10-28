"""Metamorphic invariants (blueprint section 9): relationships between test variants."""

from __future__ import annotations

import random
from datetime import timedelta

from ccevals.contracts.registry import dry_run, parse_contract, starter_contract
from ccevals.packs.scenarios import ScenarioBuilder, happy_path
from tests.conftest import START

CONTRACT = parse_contract(starter_contract())
AT = START + timedelta(days=3)


def material(result: dict) -> dict:
    return {
        "criteria": {k: v["result"] for k, v in result["axes"]["bot_behaviour"].items()},
        "outcome": result["business_outcome"],
        "disposition": result["axes"]["disposition"],
        "obligations": {k: v["status"] for k, v in result["axes"]["obligations"].items()},
    }


def scenario(prefix: str = "m") -> ScenarioBuilder:
    return happy_path(ScenarioBuilder(prefix, START))


def test_renaming_customer_preserves_eligibility():
    a = scenario("m")
    b = scenario("m")
    for event in b.events:
        refs = event.get("entity_refs", {})
        if "customer_id" in refs:
            refs["customer_id"] = "renamed-customer-77"
    assert material(dry_run(CONTRACT, a.events, AT)) == material(dry_run(CONTRACT, b.events, AT))


def test_rephrasing_preserves_allowed_outcome():
    a = scenario()
    b = scenario()
    for event in b.events:
        if event["event_type"] == "conversation.message" and event["payload"]["speaker"] == "customer":
            event["payload"]["text"] = "Hi, one item didn't arrive in today's delivery. Can I get my money back?"
    assert material(dry_run(CONTRACT, a.events, AT)) == material(dry_run(CONTRACT, b.events, AT))


def test_replaying_duplicate_event_preserves_verdict():
    a = scenario()
    b = scenario()
    b.events.extend(dict(e) for e in b.events[-3:])
    assert material(dry_run(CONTRACT, a.events, AT)) == material(dry_run(CONTRACT, b.events, AT))


def test_reordering_independent_events_preserves_result():
    a = scenario()
    rng = random.Random(7)
    for _ in range(5):
        b = scenario()
        rng.shuffle(b.events)
        assert material(dry_run(CONTRACT, a.events, AT)) == material(dry_run(CONTRACT, b.events, AT))


def test_increasing_affected_quantity_obeys_allocation_bounds():
    for qty in (1, 2):
        b = ScenarioBuilder(f"q{qty}", START)
        b.census(0)
        b.order(-30, tenders=[("t-w", "wallet", 30000), ("t-c", "card", 90000)], lines=[("li-2", 2, 24000)])
        b.profile(-30)
        b.open_task(0, {"li-2": qty})
        b.identity(0.5)
        amount = 24000 * qty
        b.refund(2, f"q{qty}-a", {"li-2": qty}, amount, {"wallet": amount // 4, "card": amount - amount // 4})
        result = dry_run(CONTRACT, b.events, AT)
        assert result["axes"]["bot_behaviour"]["allocation_matches_policy"]["result"] == "pass"
        assert result["axes"]["bot_behaviour"]["amount_within_entitlement"]["result"] == "pass"
