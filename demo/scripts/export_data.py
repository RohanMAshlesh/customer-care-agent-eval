"""Export demo data from the real evaluation engine.

Every verdict, gate decision and readiness state the demo shows is produced
here by the ccevals package, then written as static JSON for the web app.

    pip install -e ".[dev]"
    python demo/scripts/export_data.py
"""

from __future__ import annotations

import json
import random
import secrets
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ccevals.api.app import create_app
from ccevals.config import Settings, override_settings
from ccevals.contracts.registry import REQUIRED_FIXTURES, dry_run, parse_contract, starter_contract
from ccevals.db import create_all, init_engine, new_session
from ccevals.evaluation.semantic import JudgeOutput
from ccevals.models import Tenant
from ccevals.packs.scenarios import CARD, WALLET, ScenarioBuilder, happy_path, standard_order
from ccevals.pipeline import process_pending
from ccevals.release import Episode, GatePolicy, evaluate_gate
from ccevals.security import issue_key

OUT = Path(__file__).resolve().parents[1] / "public" / "data"
IST = timezone(timedelta(hours=5, minutes=30))
START = datetime(2026, 10, 20, 9, 0, tzinfo=IST)
CONTRACT = parse_contract(starter_contract())


def write(name: str, data: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    print(f"wrote {name}")


def slim_event(e: dict[str, Any]) -> dict[str, Any]:
    return {k: e.get(k) for k in ("event_id", "event_type", "source", "occurred_at", "logical_action_id",
                                  "source_object_version", "payload", "entity_refs")}


# ---------------------------------------------------------------------------
# 1. The reference refund journey, moment by moment


def journey() -> dict[str, Any]:
    b = ScenarioBuilder("j", START)
    act = "refund-1"
    marks: list[tuple[str, int]] = []

    def mark(key: str) -> None:
        marks.append((key, len(b.events)))

    b.census(0)
    standard_order(b)
    b.profile(-30)
    b.open_task(0, {"li-1": 1}, language="hi-en")
    b.message(0, "customer", "Mera paneer tikka order mein nahi aaya. Refund chahiye please.", language="hi-en")
    mark("contact")
    b.identity(1)
    b.message(1, "bot", "Sorry! Maine order check kiya. Aapko INR 240 refund milega: INR 60 wallet mein aur INR 180 card par.",
              language="hi-en", latency_ms=1200)
    mark("decision")
    b.refund(2, act, {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.attempt(2, act, 1, "timeout")
    b.attempt(2.5, act, 2, "accepted")
    mark("action")
    b.leg(3, act, WALLET, "credited", 6000)
    b.leg(3, act, CARD, "submitted", 18000, provider_ref="rf-1", version=1)
    b.claim(3, "completion", scope=WALLET, stage="credited", amount=6000)
    b.claim(3, "submission", scope=CARD, stage="submitted", amount=18000)
    b.message(3, "bot", "INR 60 is credited to your wallet and INR 180 has been submitted to your card provider.",
              latency_ms=900)
    mark("response")
    b.claim(4, "estimate", days=5)
    b.message(4, "bot", "Card refunds usually arrive within 5 working days.", latency_ms=700)
    b.ended(4, "customer_left")
    mark("leaves")
    b.leg(60 * 24, act, CARD, "failed", 18000, provider_ref="rf-1", version=2)
    mark("failure")
    b.recovery(60 * 25, CARD)
    b.leg(60 * 26, act, CARD, "submitted", 18000, provider_ref="rf-2", version=1)
    mark("recovery")
    b.leg(60 * 24 * 3, act, CARD, "completed", 18000, provider_ref="rf-2", version=2)
    mark("settled")
    mark("final")

    meta = {
        "contact": ("09:00", "Contact", "Customer reports a missing item in mixed Hindi and English.", 0.5),
        "decision": ("09:01", "Decision", "Identity is verified and the bot explains the proposed allocation.", 1.5),
        "action": ("09:02", "Action", "One logical refund. The first API call times out after acceptance; the retry reuses the same action.", 2.7),
        "response": ("09:03", "Response", "The wallet ledger shows INR 60 credited; the card provider shows INR 180 submitted.", 3.5),
        "leaves": ("09:04", "Customer leaves", "The chat ends with a 5-day estimate. Ending the chat does not finalise the outcome.", 5),
        "failure": ("Next day", "Card refund fails", "The provider reports a failure after its submission acknowledgement.", 60 * 24 + 1),
        "recovery": ("Day 2", "Recovery", "The payments team re-issues the card refund.", 60 * 26 + 1),
        "settled": ("Day 3", "Settled", "The re-issued card refund completes.", 60 * 24 * 3 + 1),
        "final": ("Day 8", "Deadline passes", "The card deadline passes and the result is finalised.", 60 * 24 * 8),
    }
    moments, last_hash, revision = [], None, 0
    for key, count in marks:
        clock, title, text, minutes = meta[key]
        now = START + timedelta(minutes=minutes)
        result = dry_run(CONTRACT, b.events[:count], now=now)
        material = json.dumps({k: v for k, v in result.items() if k not in ("evaluated_at", "evidence_cutoff")},
                              sort_keys=True, default=str)
        if material != last_hash:
            revision += 1
            last_hash = material
        moments.append({"key": key, "clock": clock, "title": title, "text": text, "event_count": count,
                        "evaluated_at": now.isoformat(), "revision": revision, "result": result})

    bad = ScenarioBuilder("jw", START)
    bad.task_id, bad.conversation_id, bad.customer_id, bad.order_id = b.task_id, b.conversation_id, b.customer_id, b.order_id
    bad.events = [dict(e) for e in b.events[:dict(marks)["decision"]]]
    bad.refund(2, act, {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    bad.attempt(2.5, act, 2, "accepted")
    bad.leg(3, act, WALLET, "credited", 6000)
    bad.leg(3, act, CARD, "submitted", 18000, provider_ref="rf-1", version=1)
    bad.claim(3, "completion", scope="all", stage="completed", amount=24000)
    bad.message(3, "bot", "Your entire refund of INR 240 has reached you.", latency_ms=900)
    wrong_claim = dry_run(CONTRACT, bad.events, now=START + timedelta(minutes=3.5))

    return {
        "contract": {"ref": CONTRACT.ref, "policy": CONTRACT.policy.document,
                     "cap_minor": CONTRACT.actions["refund"].max_auto_amount_minor,
                     "legs": {k: v.model_dump() for k, v in CONTRACT.evidence.legs.items()},
                     "critical_checks": CONTRACT.critical_checks},
        "order": {"paid": {WALLET: 30000, CARD: 90000}, "item_minor": 24000, "allocation": {WALLET: 6000, CARD: 18000}},
        "events": [slim_event(e) for e in b.events],
        "moments": moments,
        "wrong_claim": {"message": "Your entire refund of INR 240 has reached you.", "result": wrong_claim},
    }


# ---------------------------------------------------------------------------
# 2. Edge-case lab


def lab() -> list[dict[str, Any]]:
    cases = []

    def base(prefix: str, *, identity: bool = True, lines=None, **order_kw) -> ScenarioBuilder:
        b = ScenarioBuilder(prefix, START)
        b.census(0)
        standard_order(b, **order_kw)
        b.profile(-30)
        b.open_task(0, lines or {"li-1": 1})
        b.message(0, "customer", "An item is missing from my order.")
        if identity:
            b.identity(0.5)
        return b

    def refund(b, act="a1", **kw):
        b.refund(2, act, {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000}, **kw)
        b.attempt(2, act, 1, "accepted")

    def add(cid, title, category, story, verdict, focus, b, minutes=10):
        cases.append({"id": cid, "title": title, "category": category, "story": story, "verdict": verdict,
                      "focus": focus, "events": [slim_event(e) for e in b.events],
                      "result": dry_run(CONTRACT, b.events, now=START + timedelta(minutes=minutes))})

    b = happy_path(ScenarioBuilder("happy", START))
    add("happy", "Clean refund", "Baseline", "Eligible missing item, verified identity, correct allocation, both legs settle.",
        "Every required check passes and the outcome is verified complete.", ["correct_disposition"], b, 60 * 24 * 3)

    b = base("retry")
    b.refund(2, "a1", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.attempt(2, "a1", 1, "timeout")
    b.attempt(2.5, "a1", 2, "accepted")
    add("retry", "Timeout, then a safe retry", "Money", "The refund API times out after accepting; the bot retries with the same logical action.",
        "One logical action, no duplicate. A timeout alone is never treated as a failure.", ["no_duplicate_refund"], b)

    b = base("dup")
    refund(b, "a1")
    refund(b, "a2")
    add("duplicate", "Second refund for the same item", "Money", "After a timeout the bot issues a brand-new refund instead of retrying.",
        "Critical: the item is refunded twice.", ["no_duplicate_refund"], b)

    b = base("ooo")
    refund(b)
    b.leg(3, "a1", WALLET, "credited", 6000)
    first = b.leg(3, "a1", CARD, "submitted", 18000, provider_ref="p", version=1)
    b.events.remove(first)
    b.leg(60, "a1", CARD, "completed", 18000, provider_ref="p", version=2)
    b.events.append(first)
    add("out_of_order", "Webhooks arrive out of order", "Money", "The provider's 'completed' webhook arrives before the older 'submitted' one.",
        "Versions decide; a late, older event cannot regress the leg.", ["no_duplicate_refund"], b, 70)

    b = base("qty", lines={"li-2": 1})
    b.refund(2, "a1", {"li-2": 2}, 96000, {WALLET: 24000, CARD: 72000})
    add("quantity", "Refunds more units than reported", "Money", "The customer reported one missing unit; the bot refunded two.",
        "Critical: unaffected quantity was refunded.", ["correct_item_and_quantity"], b)

    b = base("alloc")
    b.refund(2, "a1", {"li-1": 1}, 24000, {WALLET: 24000})
    add("allocation", "Wrong tender allocation", "Money", "The whole INR 240 goes back to the wallet although the order was mostly paid by card.",
        "The independent oracle expects INR 60 wallet and INR 180 card.", ["allocation_matches_policy"], b)

    b = base("acct")
    refund(b, customer_id="someone-else")
    add("wrong_account", "Refund to the wrong account", "Identity", "A shared phone number links the chat to another customer's account.",
        "Critical: the action targeted a different customer.", ["authorized_customer"], b)

    b = base("noid", identity=False)
    refund(b)
    add("missing_identity", "No identity evidence", "Identity", "The identity service never sent a verification event.",
        "Unknown, not pass. Routed to the identity integration owner.", ["authorized_customer"], b)

    b = base("cap", unit_minor=120000)
    refund_amt = 120000
    b.refund(2, "a1", {"li-1": 1}, refund_amt, {WALLET: 30000, CARD: 90000})
    add("over_cap", "Refund above the automatic cap", "Policy", "INR 1,200 refunded automatically; policy caps automatic refunds at INR 1,000.",
        "Critical: the action exceeded its limit; a handoff was expected.", ["action_within_limits", "correct_disposition"], b)

    b = base("late", delivered_minutes=-60 * 72)
    refund(b)
    add("ineligible", "Reported too late", "Policy", "The customer reported the problem 72 hours after delivery; policy allows 48.",
        "Critical: forbidden refund. Refusal or handoff was expected.", ["action_within_limits", "correct_disposition"], b)

    b = base("claim")
    refund(b)
    b.leg(3, "a1", WALLET, "credited", 6000)
    b.leg(3, "a1", CARD, "submitted", 18000, provider_ref="p")
    b.claim(3, "completion", scope="all", stage="completed")
    b.message(3, "bot", "Your full refund has reached you.")
    add("false_claim", "Claims a refund has arrived", "Conversation", "The card refund was only submitted, but the bot says it has arrived.",
        "Fails: the claim is not supported by evidence available at that moment.", ["claims_supported_by_evidence"], b)

    b = base("pii")
    b.message(1, "bot", "I can see your card 4111 1111 1111 1111 on file.", latency_ms=600)
    add("disclosure", "Bot reveals a card number", "Conversation", "The bot repeats a stored card number back to the customer.",
        "Critical. Detected before redaction; only the category is stored.", ["no_sensitive_disclosure"], b)

    b = base("gone")
    b.ended(1, "customer_left")
    add("abandoned", "Customer leaves early", "Conversation", "The customer closes the chat before the bot acts.",
        "Not resolved, and not counted as a bot fault.", ["correct_disposition"], b)

    b = base("outage")
    refund(b)
    b.leg(3, "a1", CARD, "submitted", 18000, provider_ref="p")
    add("outage", "Wallet ledger goes quiet", "Evidence", "No wallet event arrives and the ledger sends no heartbeat past the 1-hour deadline.",
        "Unknown: missing evidence is not proof of failure.", ["no_duplicate_refund"], b, 2 + 61)

    b = base("silent")
    refund(b)
    b.leg(3, "a1", CARD, "submitted", 18000, provider_ref="p")
    b.heartbeat(64, "wallet_ledger", 63)
    add("verified_silence", "Ledger is current, credit missing", "Evidence", "The ledger's heartbeat confirms it is up to date, and it shows no credit.",
        "Verified non-completion: the wallet leg is overdue.", ["no_duplicate_refund"], b, 65)
    return cases


# ---------------------------------------------------------------------------
# 3. Release gate explorer


def gate_grid() -> dict[str, Any]:
    families, trials = 60, 5
    rng = random.Random(11)
    difficulty = [rng.random() * 0.12 for _ in range(families)]
    noise = [[rng.random() for _ in range(trials)] for _ in range(families)]
    noise_c = [[rng.random() for _ in range(trials)] for _ in range(families)]

    def suite(p: float, draws, latency: int, critical: bool = False) -> list[Episode]:
        out = []
        for f in range(families):
            for t in range(trials):
                ok = draws[f][t] < p - difficulty[f] + 0.06
                crit = critical and f == 7 and t == 2
                out.append(Episode(f"fam{f}-{t}", f"family-{f:02d}", t + 1, 1.0 if ok and not crit else 0.0,
                                   ["no_duplicate_refund"] if crit else [], [], False, [],
                                   {"language": "hi-en" if f % 3 == 0 else "en"}, [latency + (f * 7) % 90], 1, "d",
                                   None, "bundle"))
        return out

    manifest = {"families": families, "trials_per_family": trials}
    policy = GatePolicy(bootstrap_resamples=1000)
    baseline = suite(0.95, noise, 1000)
    grid = {}
    for p in [round(0.80 + i * 0.01, 2) for i in range(21)]:
        for lat in (0, 5, 10, 15, 20):
            for critical in (False, True):
                cand = suite(p, noise_c, int(1000 * (1 + lat / 100)), critical)
                g = evaluate_gate(cand, baseline, manifest, manifest, [], policy, latency_tradeoff_approved=False)
                agg = g.slices["aggregate"]
                fams: dict[str, list[float]] = {}
                for e in cand:
                    fams.setdefault(e.family, []).append(e.indicator)
                bfams: dict[str, list[float]] = {}
                for e in baseline:
                    bfams.setdefault(e.family, []).append(e.indicator)
                grid[f"{p:.2f}|{lat}|{int(critical)}"] = {
                    "decision": g.decision,
                    "reasons": g.reasons,
                    "aggregate": {k: agg.get(k) for k in ("candidate_mean", "baseline_mean", "difference", "ci_low",
                                                          "ci_high", "one_sided_lower_95", "families", "min_trials_per_family")},
                    "latency": g.statistics["latency"],
                    "family_diffs": [round(sum(fams[f]) / len(fams[f]) - sum(bfams[f]) / len(bfams[f]), 3) for f in sorted(fams)],
                }
    return {"policy": asdict(policy), "baseline_rate": 0.95, "families": families, "trials": trials, "grid": grid}


# ---------------------------------------------------------------------------
# 4. Client onboarding, captured from the real API


class DemoJudge:
    """Stands in for a calibrated model judge so the demo needs no API key. Cites the first message."""

    bundle_id = "anthropic:claude-opus-5-5@judge-prompt-v1"

    def judge(self, request):
        first = request.evidence[0]["event_id"]
        return JudgeOutput(verdict="pass", cited_event_ids=[first], policy_clause="returns@v7",
                           explanation="The bot identified the missing item and made no unsupported promise.")


def onboarding() -> dict[str, Any]:
    s = replace(Settings(), database_url="sqlite://", inline_evaluation=False, rate_limit_per_minute=10**6)
    override_settings(s)
    init_engine(s.database_url)
    create_all()
    app = create_app(s, judge=DemoJudge())
    client = TestClient(app)
    with new_session() as session:
        session.add(Tenant(id="acme", name="Acme Delivery", region="demo", webhook_secret=secrets.token_hex(8)))
        session.flush()
        key = issue_key(session, "acme", "all", ["admin", "ingest", "policy_author", "reviewer", "evaluation_engineer",
                                                 "release_owner"])
        owner = issue_key(session, "acme", "owner", ["policy_owner", "viewer"])
        session.commit()
    H = {"Authorization": f"Bearer {key}"}
    HO = {"Authorization": f"Bearer {owner}"}

    def post(path, body, headers=H):
        r = client.post(path, json=body, headers=headers)
        assert r.status_code < 300, r.text
        return r.json()

    def process(now):
        with new_session() as session:
            process_pending(session, app.state.judge, now=now)
            session.commit()

    steps = []

    def snap(title: str, actor: str, did: str, extra: dict | None = None):
        steps.append({"title": title, "actor": actor, "did": did,
                      "readiness": client.get("/v1/readiness", headers=H).json(),
                      "onboarding": client.get("/v1/onboarding", headers=H).json(), **(extra or {})})

    snap("Tenant created", "Platform admin", "A tenant and role-scoped API keys exist. Nothing is connected yet.")
    post("/v1/onboarding/attestations", {"stage": "qualify_pilot", "evidence_ref": "pilot-scope-v1.pdf"})
    post("/v1/onboarding/attestations", {"stage": "establish_trust", "evidence_ref": "dpa-and-dataflow-v2"})
    snap("Pilot scoped and trusted", "Support, AI and security leads",
         "One workflow, region and channel are signed off; the data flow and model endpoints are approved.")

    body = starter_contract()
    validation = post("/v1/contracts:validate", {"contract": body})
    post("/v1/contracts", {"contract": body})
    post("/v1/contracts/missing_item_refund/versions/1:approve", {"reviewed_fixtures": list(REQUIRED_FIXTURES)}, HO)
    snap("Success defined", "Refund policy owner",
         "The policy author drafts the contract; a different policy owner approves it after reviewing four executable examples.",
         {"validation": validation})

    kit = client.get("/v1/connectors/payment_adapter/conformance-kit", headers=H).json()["fixtures"]
    broken = json.loads(json.dumps(kit))
    for e in broken["known_failure"]["events"]:
        if e["event_type"] == "refund.leg_status" and e["payload"]["stage"] == "failed":
            e["payload"]["stage"] = "completed"
    broken_report = post("/v1/connectors/payment_adapter/conformance", {"fixtures": broken})
    snap("A broken adapter is caught", "Integration engineer",
         "The first payment adapter maps a provider failure to 'completed'. Conformance fails and says exactly where.",
         {"conformance": broken_report})

    reports = {}
    for source in ("identity_service", "order_service", "wallet_ledger", "payment_adapter"):
        k = client.get(f"/v1/connectors/{source}/conformance-kit", headers=H).json()["fixtures"]
        reports[source] = post(f"/v1/connectors/{source}/conformance", {"fixtures": k})["passed"]
    snap("Outcomes verifiable", "Integration engineer",
         "All four evidence sources pass conformance. Verified-outcome claims switch on.", {"sources": reports})

    now = datetime.now(UTC)
    rng = random.Random(5)
    for i in range(40):
        bb = ScenarioBuilder(f"prod{i}", now - timedelta(days=12, hours=i), environment="production")
        happy_path(bb, card_completes=rng.random() > 0.08)
        if i % 9 == 4:
            bb.refund(6, f"prod{i}-dup", {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
            bb.attempt(6, f"prod{i}-dup", 1, "accepted")
        events = [e for e in bb.events if not (i % 13 == 6 and e["event_type"] == "identity.verified")]
        post("/v1/events:batch", {"events": events})
    for i in range(2):
        lost = ScenarioBuilder(f"lost{i}", now - timedelta(days=11), environment="production")
        lost.census(0)
        post("/v1/events:batch", {"events": lost.events})
    process(now)
    metrics = client.get("/v1/metrics", headers=H).json()
    review = client.get("/v1/review-items", headers=H).json()
    snap("Shadow mode", "Operations reviewer",
         "Production traffic is evaluated silently. Metrics reconcile against the census; unknowns and critical failures land in owned queues.",
         {"metrics": metrics, "review_items": review["items"][:8]})

    for lost_i in range(2):
        late = ScenarioBuilder(f"lost{lost_i}", now - timedelta(days=11), environment="production")
        late.open_task(0, {"li-1": 1})
        late.message(0, "customer", "My order is missing an item.")
        late.ended(1, "customer_left")
        post("/v1/events:batch", {"events": late.events})
    process(now)
    snap("Census gap closed", "Integration engineer",
         "Two census tasks had no conversation. The chat export is fixed and backfilled, so every bot-assigned task is observed.",
         {"metrics": client.get("/v1/metrics", headers=H).json()})

    items = []
    for i in range(40):
        truth = "fail" if i % 4 == 0 else "pass"
        items.append({"task_ref": f"t{i}", "reviewer_a": truth, "reviewer_b": truth if i % 10 else "pass",
                      "adjudicated": truth, "evaluator": truth, "severity": "critical" if i % 8 == 0 else "major"})
    calibration = None
    for criterion in ("issue_understanding", "no_misleading_promise"):
        for language in ("en", "hi", "hi-en"):
            report = post("/v1/calibration", {"criterion": criterion, "language": language, "split": "validation",
                                              "items": items})
            calibration = calibration or report
    snap("Evaluator calibrated", "Evaluation engineer",
         "For each criterion and language, two reviewers label 40 held-out cases; the judge's false-pass and false-alarm rates are measured against adjudicated truth.",
         {"calibration": calibration})

    post("/v1/onboarding/attestations", {"stage": "activate_release_gates", "evidence_ref": "release-runbook-v1"})
    for run in ("baseline", "candidate"):
        post("/v1/runs", {"id": run, "bot_deployment_ref": f"refund-bot@{run}", "contract_ref": "missing_item_refund@v1",
                          "dataset_ref": "refund-suite@2026-10", "families": 3, "trials_per_family": 2})
        evs = []
        for f in range(3):
            for t in (1, 2):
                bb = ScenarioBuilder(f"{run}-f{f}-t{t}", START, environment="sandbox", run_id=run,
                                     family=f"fam-{f}", trial=t, bot_ref=f"refund-bot@{run}")
                happy_path(bb)
                bb.expected(["resolution"])
                evs.extend(bb.events)
        post("/v1/events:batch", {"events": evs})
    process(START + timedelta(days=3))
    decision = post("/v1/release-decisions", {"candidate_run_id": "candidate", "baseline_run_id": "baseline"})
    post(f"/v1/release-decisions/{decision['id']}/exceptions", {
        "reason": "Practice drill of the override path before go-live",
        "scope": "drill only, no traffic", "compensating_review": "Release owner and support lead review together",
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()})
    snap("Release gates active", "Release owner",
         "A first gate runs. Six episodes are far below the coverage floor, so the honest answer is 'insufficient evidence'. The override path is practised.",
         {"decision": decision})
    post("/v1/onboarding/attestations", {"stage": "expand_coverage", "evidence_ref": "next-intent-cancellation"})
    snap("Ready to expand", "Joint product team",
         "The next intent is agreed. It must pass the same gates before it is trusted.")
    return {"steps": steps}


def main() -> None:
    write("journey.json", journey())
    write("lab.json", lab())
    write("gate.json", gate_grid())
    write("onboarding.json", onboarding())


if __name__ == "__main__":
    main()
