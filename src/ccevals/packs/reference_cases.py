"""Reference customer cases, reused by tests and the demo.

Each case is a full customer journey expressed as canonical events, with named
moments at which the engine is asked for a verdict. Customers, amounts, ticket
numbers and policies are illustrative.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ccevals.packs.scenarios import CARD, WALLET, ScenarioBuilder, standard_order

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 60 * 24


@dataclass
class Moment:
    key: str
    clock: str
    title: str
    at_minutes: float
    event_count: int


@dataclass
class Case:
    id: str
    contract: str  # starter contract file in ccevals/packs
    builder: ScenarioBuilder
    moments: list[Moment] = field(default_factory=list)

    def mark(self, key: str, clock: str, title: str, at_minutes: float) -> None:
        self.moments.append(Moment(key, clock, title, at_minutes, len(self.builder.events)))

    def at(self, moment: Moment) -> datetime:
        return self.builder.start + timedelta(minutes=moment.at_minutes)


def priya_refund() -> Case:
    """Missing item refunded across wallet and card; the card leg fails at the bank, then recovers."""
    b = ScenarioBuilder("priya", datetime(2026, 10, 20, 9, 0, tzinfo=IST))
    c, act = Case("priya", "missing_item_refund", b), "priya-refund"
    b.census(0)
    standard_order(b)
    b.profile(-30)
    b.open_task(0, {"li-1": 1}, language="hi-en")
    b.message(0, "customer", "Mera paneer tikka order mein nahi aaya. Refund chahiye please.", language="hi-en")
    c.mark("contact", "09:00", "Contact", 0.5)
    b.identity(1)
    b.message(1, "bot", "Sorry! Maine order check kiya. Aapko INR 240 refund milega: INR 60 wallet mein aur INR 180 card par.",
              language="hi-en", latency_ms=1200)
    c.mark("decision", "09:01", "Offer", 1.5)
    b.refund(2, act, {"li-1": 1}, 24000, {WALLET: 6000, CARD: 18000})
    b.attempt(2, act, 1, "timeout")
    b.attempt(2.5, act, 2, "accepted")
    c.mark("action", "09:02", "Refund issued", 2.7)
    b.leg(3, act, WALLET, "credited", 6000)
    b.leg(3, act, CARD, "submitted", 18000, provider_ref="rf-1", version=1)
    b.claim(3, "completion", scope=WALLET, stage="credited", amount=6000)
    b.claim(3, "submission", scope=CARD, stage="submitted", amount=18000)
    b.message(3, "bot", "INR 60 is credited to your wallet and INR 180 has been submitted to your card provider.", latency_ms=900)
    c.mark("response", "09:03", "Update", 3.5)
    b.claim(4, "estimate", days=5)
    b.message(4, "bot", "Card refunds usually arrive within 5 working days.", latency_ms=700)
    b.ended(4, "customer_left")
    c.mark("leaves", "09:04", "Chat ends", 5)
    b.leg(DAY, act, CARD, "failed", 18000, provider_ref="rf-1", version=2)
    c.mark("failure", "Day 1", "Bank rejects", DAY + 1)
    b.recovery(DAY + 60, CARD)
    b.leg(DAY + 120, act, CARD, "submitted", 18000, provider_ref="rf-2", version=1)
    c.mark("recovery", "Day 1", "Re-issued", DAY + 121)
    b.leg(DAY * 3, act, CARD, "completed", 18000, provider_ref="rf-2", version=2)
    c.mark("settled", "Day 3", "Money lands", DAY * 3 + 1)
    c.mark("final", "Day 8", "Final verdict", DAY * 8)
    return c


def kavya_safety() -> Case:
    """Safety complaint: the bot promises escalation, but the incident was never created."""
    b = ScenarioBuilder("kavya", datetime(2026, 10, 20, 21, 40, tzinfo=IST), intent="safety_complaint")
    c = Case("kavya", "safety_complaint", b)
    b.census(0)
    b.open_service(0, "delivery_partner_conduct")
    b.message(0, "customer", "The delivery partner kept asking for my number and waited outside my door after delivering. I feel unsafe.")
    c.mark("contact", "21:40", "Complaint", 0.5)
    b.tool_call(1, "servicenow.create_incident", "error", "HTTP 504 from ServiceNow; the bot did not retry")
    b.message(1, "bot", "I'm so sorry this happened. I've escalated this to our safety team as top priority. "
              "They will contact you within 24 hours.", latency_ms=1100)
    b.claim(1, "escalation")
    b.handoff(1, "safety_escalation")
    b.ended(2, "handoff")
    c.mark("promise", "21:41", "“Escalated”", 2.5)
    b.heartbeat(18, "servicenow", 17)
    c.mark("deadline", "21:56", "15-minute rule", 18)
    b.ticket(31, "INC0045821", "trust_and_safety", "Trust & Safety - Rapid Response", 1)
    b.message(36, "agent", "Hello Kavya, this is Ananya from Trust & Safety. Your case reference is INC0045821. "
              "We have removed this partner from your deliveries while we investigate.")
    c.mark("recovery", "22:16", "Team steps in", 37)
    b.ticket_update(60 * 6, "INC0045821", "resolved", 2)
    c.mark("resolved", "03:40", "Case closed", 60 * 6 + 1)
    c.mark("final", "Day 3", "Final verdict", DAY * 3 + 5)
    return c


def rohit_replacement() -> Case:
    """Damaged appliance: a ticket is raised in the wrong queue, the customer chases, the SLA is missed."""
    b = ScenarioBuilder("rohit", datetime(2026, 10, 20, 11, 0, tzinfo=IST), intent="damaged_item_replacement")
    c = Case("rohit", "damaged_item_replacement", b)
    b.census(0)
    b.open_service(0, "damaged_item")
    b.message(0, "customer", "My mixer grinder arrived with a cracked jar. I want a replacement, not a refund.")
    c.mark("contact", "11:00", "Complaint", 0.5)
    b.ticket(2, "INC0046210", "refund", "Payments - Refunds", 4)
    b.claim(2, "ticket_reference", reference="INC0046210")
    b.message(2, "bot", "Done! I've raised replacement request INC0046210. Your new mixer grinder will arrive in 3 to 4 days.",
              latency_ms=950)
    b.feedback(3, True)
    b.ended(3, "resolved")
    c.mark("ticket", "11:02", "“Request raised”", 4)
    b.heartbeat(DAY * 2, "servicenow", DAY * 2)
    c.mark("silence", "Day 2", "Nothing moves", DAY * 2 + 1)
    b.message(DAY * 2 + 180, "customer", "It's been two days and nobody has called. Where is my replacement?")
    b.message(DAY * 2 + 181, "bot", "I'm sorry. I've chased this with the team.", latency_ms=900)
    b.ticket_update(DAY * 2 + 200, "INC0046210", "in_progress", 2, group="Logistics - Replacements", priority=3)
    c.mark("chase", "Day 2", "Rohit chases", DAY * 2 + 201)
    b.heartbeat(DAY * 3 + 5, "servicenow", DAY * 3 + 4)
    c.mark("sla", "Day 3", "SLA missed", DAY * 3 + 6)
    b.signal(DAY * 3 + 22 * 60, "logistics", "replacement_delivered")
    b.ticket_update(DAY * 3 + 23 * 60, "INC0046210", "resolved", 3)
    c.mark("delivered", "Day 4", "Replacement arrives", DAY * 3 + 23 * 60 + 1)
    c.mark("final", "Day 5", "Final verdict", DAY * 4 + 5)
    return c


def neha_login() -> Case:
    """Login issue: the customer says it works, the sign-in logs say otherwise until the next attempt."""
    b = ScenarioBuilder("neha", datetime(2026, 10, 20, 8, 15, tzinfo=IST), intent="login_issue")
    c = Case("neha", "login_issue", b)
    b.census(0)
    b.open_service(0, "cannot_sign_in")
    b.message(0, "customer", "I can't log in. It keeps saying my OTP is invalid.")
    c.mark("contact", "08:15", "Locked out", 0.5)
    b.tool_call(2, "auth.send_reset_link", "success")
    b.message(2, "bot", "I've sent a fresh sign-in link to your registered email. Please try it now.", latency_ms=800)
    b.message(6, "customer", "Okay, it works now. Thanks!")
    b.feedback(6, True)
    b.claim(7, "resolved")
    b.message(7, "bot", "Great, glad that's fixed! Have a lovely day.", latency_ms=600)
    b.ended(7, "resolved")
    c.mark("thanks", "08:22", "“It works now”", 8)
    b.heartbeat(60 * 4, "auth_service", 60 * 4)
    c.mark("logs", "12:15", "No sign-in", 60 * 4 + 1)
    b.message(60 * 11, "customer", "I still can't get in! The link opens the app and asks for the OTP again.")
    b.message(60 * 11 + 3, "bot", "Your app version is out of date and cannot use the new link. Please update the app, then open the link again.",
              latency_ms=900)
    b.signal(60 * 11 + 15, "auth_service", "login_succeeded")
    c.mark("second", "19:30", "Back, then in", 60 * 11 + 16)
    c.mark("final", "Day 2", "Final verdict", DAY * 2 + 30)
    return c


CASES: dict[str, Callable[[], Case]] = {
    "priya": priya_refund,
    "kavya": kavya_safety,
    "rohit": rohit_replacement,
    "neha": neha_login,
}
