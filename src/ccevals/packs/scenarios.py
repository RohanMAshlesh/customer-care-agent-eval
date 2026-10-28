"""Stateful marketplace scenario builder for the missing-item refund pack.

Produces canonical event envelopes. The same builder drives contract fixtures,
connector conformance examples, sandbox simulations and tests, so expected
results are specified once and independently of the bot under test.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from typing import Any


class ScenarioBuilder:
    def __init__(self, prefix: str, start: datetime, *, environment: str = "production",
                 run_id: str | None = None, family: str | None = None, trial: int | None = None,
                 bot_ref: str = "bot@test", market: str = "demo_in", intent: str = "missing_item_refund",
                 sources: dict[str, str] | None = None) -> None:
        self.prefix = prefix
        self.start = start
        self.environment = environment
        self.run_id = run_id
        self.family = family
        self.trial = trial
        self.bot_ref = bot_ref
        self.market = market
        self.intent = intent
        self.sources = {
            "census": "census", "chat": "chat_platform", "identity": "identity_service",
            "order": "order_service", "wallet": "wallet_ledger", "card": "payment_adapter",
            **(sources or {}),
        }
        self.events: list[dict[str, Any]] = []
        self._seq = itertools.count(1)
        self.task_id = f"{prefix}-task"
        self.conversation_id = f"{prefix}-conv"
        self.customer_id = f"{prefix}-cust"
        self.order_id = f"{prefix}-order"

    # -- helpers ---------------------------------------------------------------

    def at(self, minutes: float) -> datetime:
        return self.start + timedelta(minutes=minutes)

    def emit(self, event_type: str, source: str, minutes: float, payload: dict[str, Any], *,
             task: bool = True, action_id: str | None = None, version: int | None = None,
             entity_refs: dict[str, Any] | None = None, observed_delay_s: float = 1.0,
             source_event_id: str | None = None) -> dict[str, Any]:
        n = next(self._seq)
        occurred = self.at(minutes)
        event: dict[str, Any] = {
            "event_id": f"{self.prefix}-e{n}",
            "source": source,
            "source_event_id": source_event_id or f"{self.prefix}-{source}-{n}",
            "schema_version": "1.0",
            "environment": self.environment,
            "occurred_at": occurred.isoformat(),
            "observed_at": (occurred + timedelta(seconds=observed_delay_s)).isoformat(),
            "event_type": event_type,
            "entity_refs": entity_refs if entity_refs is not None else {"customer_id": self.customer_id, "order_id": self.order_id},
            "payload": payload,
            "bot_deployment_ref": self.bot_ref,
        }
        if task:
            event["task_id"] = self.task_id
            event["conversation_id"] = self.conversation_id
        if action_id:
            event["logical_action_id"] = action_id
        if version is not None:
            event["source_object_version"] = version
        if self.run_id:
            event["run_id"] = self.run_id
            event["scenario_family"] = self.family
            event["trial"] = self.trial
        self.events.append(event)
        return event

    # -- business facts ----------------------------------------------------------

    def census(self, minutes: float = 0) -> dict:
        return self.emit("census.task_assigned", self.sources["census"], minutes,
                         {"intent": self.intent, "eligible": True, "market": self.market})

    def order(self, minutes: float, *, tenders: list[tuple[str, str, int]], lines: list[tuple[str, int, int]],
              status: str = "delivered", delivered_minutes: float = -60, currency: str = "INR",
              version: int = 1, refunded: dict[str, int] | None = None, customer_id: str | None = None) -> dict:
        refunded = refunded or {}
        return self.emit(
            "order.snapshot", self.sources["order"], minutes,
            {
                "status": status,
                "currency": currency,
                "placed_at": self.at(delivered_minutes - 45).isoformat(),
                "delivered_at": self.at(delivered_minutes).isoformat(),
                "tenders": [{"tender_id": tid, "tender_type": tt, "paid_minor": paid} for tid, tt, paid in tenders],
                "lines": [
                    {"line_item_id": lid, "quantity": qty, "unit_refundable_minor": unit,
                     "refunded_quantity": refunded.get(lid, 0)}
                    for lid, qty, unit in lines
                ],
            },
            task=False, version=version,
            entity_refs={"customer_id": customer_id or self.customer_id, "order_id": self.order_id},
        )

    def profile(self, minutes: float, prior_refunds: int = 0, fraud_flag: bool = False) -> dict:
        return self.emit("customer.profile", self.sources["order"], minutes,
                         {"prior_refund_count_30d": prior_refunds, "fraud_flag": fraud_flag},
                         task=False, version=1, entity_refs={"customer_id": self.customer_id})

    def open_task(self, minutes: float, lines: dict[str, int], language: str = "en", photo: bool = True) -> dict:
        return self.emit("task.opened", self.sources["chat"], minutes, {
            "intent": self.intent, "market": self.market, "language": language,
            "affected_lines": [{"line_item_id": k, "quantity": v} for k, v in lines.items()],
            "photo_provided": photo,
        })

    def message(self, minutes: float, speaker: str, text: str, language: str = "en", latency_ms: int | None = None) -> dict:
        payload: dict[str, Any] = {"speaker": speaker, "text": text, "language": language}
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        return self.emit("conversation.message", self.sources["chat"], minutes, payload)

    def identity(self, minutes: float, method: str = "registered_device", customer_id: str | None = None,
                 valid_minutes: float | None = None) -> dict:
        payload: dict[str, Any] = {"method": method}
        if valid_minutes is not None:
            payload["valid_until"] = self.at(minutes + valid_minutes).isoformat()
        return self.emit("identity.verified", self.sources["identity"], minutes, payload, task=False,
                         entity_refs={"customer_id": customer_id or self.customer_id})

    # -- bot behaviour -------------------------------------------------------------

    def refund(self, minutes: float, action_id: str, lines: dict[str, int], amount: int, legs: dict[str, int],
               customer_id: str | None = None) -> dict:
        return self.emit("bot.action", self.sources["chat"], minutes, {
            "action": "refund", "lines": [{"line_item_id": k, "quantity": v} for k, v in lines.items()],
            "amount_minor": amount, "legs": legs,
        }, action_id=action_id,
            entity_refs={"customer_id": customer_id or self.customer_id, "order_id": self.order_id})

    def attempt(self, minutes: float, action_id: str, attempt: int, outcome: str) -> dict:
        return self.emit("action.attempt", self.sources["chat"], minutes,
                         {"attempt": attempt, "outcome": outcome}, action_id=action_id)

    def claim(self, minutes: float, claim_type: str, scope: str | list[str] = "all", stage: str | None = None,
              amount: int | None = None, days: int | None = None, extracted_by: str = "tool") -> dict:
        payload: dict[str, Any] = {"claim_type": claim_type, "scope": scope, "extracted_by": extracted_by}
        if stage:
            payload["stage"] = stage
        if amount is not None:
            payload["amount_minor"] = amount
        if days is not None:
            payload["days"] = days
        return self.emit("bot.claim", self.sources["chat"], minutes, payload)

    def handoff(self, minutes: float, reason: str = "policy_requires_agent") -> dict:
        return self.emit("bot.handoff", self.sources["chat"], minutes, {"reason": reason})

    def refusal(self, minutes: float, reason: str = "not_eligible") -> dict:
        return self.emit("bot.refusal", self.sources["chat"], minutes, {"reason": reason})

    def ended(self, minutes: float, reason: str = "resolved") -> dict:
        return self.emit("conversation.ended", self.sources["chat"], minutes, {"reason": reason})

    # -- outcome sources -------------------------------------------------------------

    def leg(self, minutes: float, action_id: str, tender_type: str, stage: str, amount: int, *,
            source: str | None = None, version: int | None = None, provider_ref: str | None = None,
            observed_delay_s: float = 1.0) -> dict:
        src = source or self.sources["wallet" if tender_type == "wallet" else "card"]
        payload: dict[str, Any] = {"tender_type": tender_type, "stage": stage, "amount_minor": amount}
        if provider_ref:
            payload["provider_ref"] = provider_ref
        return self.emit("refund.leg_status", src, minutes, payload, action_id=action_id, version=version,
                         observed_delay_s=observed_delay_s)

    def recovery(self, minutes: float, tender_type: str, owner: str = "payments_ops", action: str = "reissue") -> dict:
        return self.emit("recovery.action", "case_management", minutes,
                         {"tender_type": tender_type, "owner": owner, "action": action})

    def heartbeat(self, minutes: float, source: str, watermark_minutes: float) -> dict:
        return self.emit("source.heartbeat", source, minutes, {"watermark": self.at(watermark_minutes).isoformat()},
                         task=False, entity_refs={})

    def expected(self, dispositions: list[str], slice_: dict[str, str] | None = None) -> dict:
        return self.emit("scenario.expected", "scenario_registry", 0,
                         {"expected_dispositions": dispositions, "slice": slice_ or {}})


# ---------------------------------------------------------------------------
# Reference journey from the blueprint (section 7): INR 1200 paid as 300 wallet
# + 900 card; one affected item worth INR 240 refunds 60 to wallet, 180 to card.

WALLET, CARD = "wallet", "card"


def standard_order(b: ScenarioBuilder, minutes: float = -30, unit_minor: int = 24000, **kw) -> dict:
    wallet_paid = max(30000, unit_minor)
    return b.order(minutes, tenders=[("t-wallet", WALLET, wallet_paid), ("t-card", CARD, wallet_paid * 3)],
                   lines=[("li-1", 1, unit_minor), ("li-2", 2, 48000)], **kw)


def happy_path(b: ScenarioBuilder, *, card_completes: bool = True, unit_minor: int = 24000) -> ScenarioBuilder:
    """Eligible missing item, verified identity, correct allocation, both legs settle."""
    wallet_amt = unit_minor // 4
    card_amt = unit_minor - wallet_amt
    action = f"{b.prefix}-act-1"
    b.census(0)
    standard_order(b, unit_minor=unit_minor)
    b.profile(-30)
    b.open_task(0, {"li-1": 1})
    b.message(0, "customer", "My paneer tikka was missing from the order", latency_ms=None)
    b.identity(0.5)
    b.message(1, "bot", "Sorry about that. I can refund the missing item.", latency_ms=900)
    b.refund(2, action, {"li-1": 1}, unit_minor, {WALLET: wallet_amt, CARD: card_amt})
    b.attempt(2, action, 1, "accepted")
    b.leg(2.5, action, WALLET, "credited", wallet_amt)
    b.leg(3, action, CARD, "submitted", card_amt, provider_ref="rf-1", version=1)
    b.claim(3, "completion", scope=WALLET, stage="credited", amount=wallet_amt)
    b.claim(3, "submission", scope=CARD, stage="submitted", amount=card_amt)
    b.claim(3, "estimate", days=5)
    b.message(3, "bot", "The wallet credit is done and the card refund has been submitted.", latency_ms=1100)
    b.ended(4, "resolved")
    if card_completes:
        b.leg(60 * 24 * 2, action, CARD, "completed", card_amt, provider_ref="rf-1", version=2)
    return b
