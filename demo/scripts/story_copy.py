"""Plain-language narration for the demo's case files.

Verdicts come from the engine; this file only holds the words a reader sees.
"A transcript review says" lines describe what a chat-only quality review would
typically conclude. They are illustrative, not engine output.
"""

STORIES = {
    "priya": {
        "customer": "Priya",
        "title": "The refund that bounced",
        "category": "Money",
        "situation": "Priya’s INR 1,200 food order arrived without one dish worth INR 240. She paid INR 300 from her wallet and INR 900 by card.",
        "lesson": "The chat ends long before the work does.",
        "systems": ["Order system", "Wallet ledger", "Card payment provider"],
        "takeaway": "The chat was flawless and a transcript review would have closed the case at 09:04. Priya was still owed INR 180 for three days. Only following the money until it landed showed that.",
        "moments": {
            "contact": ("Priya reports a missing dish", "She writes in Hindi and English. The bot links the complaint to her order.", "Nothing to review yet.", "none", "Opens a case and links it to Priya’s order and payment records."),
            "decision": ("The bot offers INR 240 back", "INR 60 to her wallet and INR 180 to her card, in the same proportion she paid.", "Polite and clear.", "ok", "Identity was verified before money moved, and the split follows the refund policy."),
            "action": ("The refund is issued, with a hiccup", "The payment system times out on the first try, so the bot retries.", "Not visible in the chat.", "none", "The retry reused the same refund. Priya was not paid twice."),
            "response": ("The bot tells Priya where her money is", "The wallet credit lands instantly. The card refund has only been sent to the bank, and the bot says exactly that.", "Accurate and helpful.", "ok", "Wallet confirmed by the ledger. Card sent, not arrived. The bot’s words match the records."),
            "leaves": ("Priya leaves happy", "The chat ends with an honest estimate of up to 5 days.", "Resolved. Score 9/10. Case closed.", "ok", "Not closed. It keeps watching the card refund until its 7-day deadline."),
            "failure": ("Next day, the bank rejects the card refund", "Priya has INR 60 but not the INR 180. Nobody has told her.", "Still “resolved”. Nobody re-reads the chat.", "bad", "Verdict updated to “only partly refunded” and routed to the payments team."),
            "recovery": ("The payments team re-issues the refund", "A new card refund goes to the bank.", "Still “resolved”.", "bad", "Recovery recorded. Still watching for the money to land."),
            "settled": ("Priya’s INR 180 arrives", "The payment provider confirms the re-issued refund completed.", "Still “resolved”. Right by luck, three days late.", "bad", "Fully refunded, confirmed by the provider’s own records."),
            "final": ("The deadline passes", "Every earlier verdict is kept, so anyone can audit what was known and when.", "One score, written on day one.", "bad", "Final verdict, with the full history of how it changed and why."),
        },
    },
    "kavya": {
        "customer": "Kavya",
        "title": "The escalation that never happened",
        "category": "Safety",
        "situation": "Late at night, Kavya reports that a delivery partner kept asking for her number and waited outside her door.",
        "lesson": "“I’ve escalated this” is a promise. The ticket is the proof.",
        "systems": ["ServiceNow incidents", "Bot tool-call log"],
        "takeaway": "The bot said all the right things, and a transcript review would score it as an empathetic, correct escalation. No incident existed. Checking ServiceNow within the 15-minute rule turned a silent failure into a 20-minute fix.",
        "moments": {
            "contact": ("Kavya reports feeling unsafe", "Safety complaints must reach the Trust & Safety team as a top-priority incident within 15 minutes.", "Nothing to review yet.", "none", "Opens a case under the safety policy: a P1 incident in ServiceNow within 15 minutes."),
            "promise": ("The bot says it has escalated", "“I’ve escalated this to our safety team as top priority.” Behind the scenes, the call to ServiceNow timed out and the bot did not retry.", "Empathetic, fast, correct escalation. 10/10.", "ok", "A promise has been made. The 15-minute clock to find the incident in ServiceNow has started."),
            "deadline": ("15 minutes pass. No incident exists.", "ServiceNow confirms it is up to date, and there is no incident for Kavya.", "Still 10/10. The chat looks perfect.", "bad", "Critical: the promised escalation does not exist. Alert sent to the Trust & Safety lead."),
            "recovery": ("The safety team steps in", "Alerted by the evaluator, the team creates incident INC0045821, contacts Kavya and removes the partner from her deliveries.", "Not visible: this happened outside the chat.", "none", "Incident found, correctly routed at top priority, and Kavya was given the reference number."),
            "resolved": ("The incident is closed", "The investigation is completed within the 24-hour target.", "Still the original 10/10.", "none", "Resolved within the target, but the critical failure stays on record: the bot’s promise was false."),
            "final": ("The verdict is final", "No repeat contact from Kavya in the following 72 hours.", "One score: 10/10.", "bad", "Customer outcome resolved. Bot behaviour failed a critical check, so this fix goes into the next release test."),
        },
    },
    "rohit": {
        "customer": "Rohit",
        "title": "The ticket in the wrong queue",
        "category": "Service request",
        "situation": "Rohit’s new mixer grinder arrived with a cracked jar. He wants a replacement, not a refund.",
        "lesson": "A ticket existing is not the same as the right team working on it.",
        "systems": ["ServiceNow tickets", "Logistics delivery records"],
        "takeaway": "On day one everything looked done: a ticket number, a happy customer, a closed chat. The ticket sat in the refunds queue at low priority. Rohit had to chase, and the 72-hour target was missed. Routing and timing are checked, not just whether a ticket exists.",
        "moments": {
            "contact": ("Rohit reports a damaged appliance", "Replacements must go to the logistics replacements team within 30 minutes, at priority 3 or higher, and be fixed within 72 hours.", "Nothing to review yet.", "none", "Opens a case under the replacement policy."),
            "ticket": ("The bot raises a request and gives a number", "Ticket INC0046210 is created, but in “Payments - Refunds” at priority 4. Rohit says thanks and leaves.", "Resolved. Ticket raised, customer happy. 9/10.", "ok", "Ticket exists and Rohit has the number, but it is in the wrong queue at the wrong priority. Rohit says it is resolved; nothing proves it yet."),
            "silence": ("Two days pass. Nothing moves.", "The refunds team does not handle replacements. The ticket sits untouched.", "Still 9/10.", "bad", "Still in the wrong queue. The 72-hour target is approaching with no progress."),
            "chase": ("Rohit chases", "He writes in again: nobody has called. The ticket is moved to the right team.", "A new, separate chat. Nobody links the two.", "bad", "Repeat contact recorded against the original case: the first resolution did not hold."),
            "sla": ("The 72-hour target is missed", "The ticket is still open when the deadline passes.", "Not measured.", "bad", "Target missed, confirmed by ServiceNow."),
            "delivered": ("The replacement arrives", "Logistics confirms delivery on day 4.", "Not measured.", "none", "Customer goal verified by the logistics system: replacement delivered."),
            "final": ("The verdict is final", "Rohit got his replacement, late, after chasing.", "First chat: 9/10.", "bad", "Resolved, but with three failures to fix: wrong routing, missed target, repeat contact."),
        },
    },
    "neha": {
        "customer": "Neha",
        "title": "“It works now, thanks!”",
        "category": "Account access",
        "situation": "Neha cannot sign in. The app keeps rejecting her one-time password.",
        "lesson": "A customer saying “fixed” is a signal, not proof.",
        "systems": ["Sign-in logs", "Bot tool-call log"],
        "takeaway": "Neha meant well when she said it worked, and the bot closed the case. The sign-in logs showed she never got in. Counting only verified sign-ins exposed a real fault: the reset link does not work on older app versions.",
        "moments": {
            "contact": ("Neha is locked out", "The goal is simple: Neha successfully signs in within 24 hours.", "Nothing to review yet.", "none", "Opens a case. Success means a real sign-in recorded by the sign-in service."),
            "thanks": ("“Okay, it works now. Thanks!”", "The bot sent a reset link. Neha says it works, and the bot replies “Great, glad that’s fixed!”", "Resolved on first contact. 10/10.", "ok", "Customer says it is fixed. The sign-in logs show no successful sign-in. The bot declared victory without proof."),
            "logs": ("Four hours later: still no sign-in", "The sign-in service confirms it is up to date. Neha has not got in.", "Still 10/10.", "bad", "Customer-reported only, not verified. Still watching."),
            "second": ("Neha comes back, then gets in", "She writes again in the evening. This time the bot spots her outdated app, she updates it, and signs in.", "A new chat, scored separately as another success.", "bad", "Verified: sign-in recorded. Repeat contact counted against the first chat."),
            "final": ("The verdict is final", "Resolved within the day, but not on first contact.", "Two separate 10/10 chats.", "bad", "One case, resolved on the second attempt. The root cause (old app versions) is visible to the product team."),
        },
    },
}
