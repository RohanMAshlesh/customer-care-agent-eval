import { useState } from "react";
import { Chat, Chip, CriteriaList, Loading, Panel, VerdictHeader, useData } from "../components/ui";
import { ChapterHead, NextStep, Takeaway } from "../components/tour";
import { CRITERIA } from "../lib/format";

type Case = { id: string; title: string; focus: string[]; events: any[]; result: any };

const CARDS: { id: string; group: string; title: string; what: string; cost: string; verdict: string }[] = [
  { id: "no_ticket", group: "Broken promise", title: "Promised an escalation that never happened",
    what: "“I’ve escalated this to our safety team.” The bot’s call to ServiceNow had failed and it never retried.",
    cost: "A customer who reported a safety concern is waiting for a call that will never come.", verdict: "Critical: no incident exists" },
  { id: "wrong_queue", group: "Broken promise", title: "Raised the ticket in the wrong queue",
    what: "A replacement request was filed with the refunds team at the lowest priority.",
    cost: "Nobody works on it. The customer chases, and the target time is missed.", verdict: "Flagged: wrong team and priority" },
  { id: "said_fixed", group: "Broken promise", title: "Closed the case on the customer’s word",
    what: "The customer said “it works now”. The sign-in logs showed she never got in.",
    cost: "First-contact resolution is overstated, and the real fault stays hidden.", verdict: "Flagged: fixed not proven" },
  { id: "duplicate", group: "Money", title: "Refunded the same item twice",
    what: "The bot issued two separate refunds for the same missing item instead of checking whether the first one went through.",
    cost: "The company pays INR 240 twice. At scale this is a direct, silent loss.", verdict: "Critical: double refund" },
  { id: "over_cap", group: "Policy", title: "Refunded more than it is allowed to",
    what: "The bot refunded INR 1,200 on its own. Policy says anything over INR 1,000 must go to a person.",
    cost: "Large refunds skip the human check that exists to stop fraud.", verdict: "Critical: broke a limit" },
  { id: "wrong_account", group: "Security", title: "Sent money to the wrong person",
    what: "A shared phone number linked the chat to another customer’s account, and the refund went there.",
    cost: "One customer is still unpaid; another received money that is not theirs.", verdict: "Critical: wrong account" },
  { id: "disclosure", group: "Security", title: "Read out a customer’s card number",
    what: "The bot repeated a stored card number back in the chat.",
    cost: "A data-protection incident. Only the type of leak is stored, never the number.", verdict: "Critical: data leak" },
  { id: "false_claim", group: "Honesty", title: "Said the money had arrived when it had not",
    what: "The card refund had only been sent to the bank, but the bot said it had reached the customer.",
    cost: "A broken promise that turns into a complaint and a repeat contact.", verdict: "Flagged: false promise" },
  { id: "missing_identity", group: "Missing proof", title: "No record that the customer was verified",
    what: "The identity system never sent proof that the customer was checked before the refund.",
    cost: "Cannot prove the refund was safe. Guessing “probably fine” is how risks hide.", verdict: "Unknown: sent to the identity team" },
];

export default function CatchesPage() {
  const cases = useData<Case[]>("lab");
  const [open, setOpen] = useState<string | null>("duplicate");
  if (!cases) return <Loading />;
  const byId = Object.fromEntries(cases.map((c) => [c.id, c]));
  const selected = open ? byId[open] : null;
  const card = CARDS.find((c) => c.id === open);

  const tone = (c: Case) => (c.result.critical_violation ? "fail" : c.result.unknown_reasons?.length ? "unknown" : "warn");

  return (
    <>
      <ChapterHead id="catches" title="Nine broken promises that sound fine in a transcript, caught by checking the systems of record.">
        Each card is a scenario run through the same engine that would grade live traffic. On the left of each card is
        what a transcript review would conclude; on the right, Evidence Desk’s verdict. Click a card to see the chat.
      </ChapterHead>

      <div className="legend">
        <span><Chip tone="fail">Critical</Chip> must never happen; blocks a launch</span>
        <span><Chip tone="warn">Flagged</Chip> breaks a rule</span>
        <span><Chip tone="unknown">Unknown</Chip> proof missing; a named team must fix it</span>
      </div>

      <div className="catch-grid">
        {CARDS.map((c) => {
          const data = byId[c.id];
          if (!data) return null;
          const t = tone(data);
          return (
            <button key={c.id} className="catch" aria-expanded={open === c.id}
              onClick={() => { setOpen(c.id); setTimeout(() => document.getElementById("catch-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }), 30); }}>
              <span className="label">{c.group}</span>
              <h3>{c.title}</h3>
              <p>{c.what}</p>
              <div className="verdict-pair">
                <div className="vp old"><span className="label">Transcript review</span>Looks fine</div>
                <div className={`vp new${t === "unknown" ? " unknown" : ""}`} style={t === "warn" ? { background: "var(--warn-soft)", color: "var(--warn)" } : undefined}>
                  <span className="label">Evidence Desk</span>{c.verdict}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {selected && card && (
        <div id="catch-detail" style={{ display: "grid", gap: 16, scrollMarginTop: 80 }}>
          <div className="cols-2">
            <Panel>
              <div className="narration">
                <span className="when">{card.group}</span>
                <h2>{card.title}</h2>
                <p>{card.what}</p>
                <div className="callout">
                  <span className="label">Why it matters</span>
                  <span style={{ fontSize: 15 }}>{card.cost}</span>
                </div>
                <div className={`callout ${tone(selected) === "unknown" ? "unknown" : "fail"}`}>
                  <span className="label">What the engine recorded</span>
                  {selected.focus.map((f) => selected.result.axes.bot_behaviour[f] && (
                    <span key={f} style={{ fontSize: 14 }}><strong>{CRITERIA[f]}:</strong> {selected.result.axes.bot_behaviour[f].reason}</span>
                  ))}
                  {selected.result.unknown_reasons?.map((u: any) => (
                    <span key={u.code} style={{ fontSize: 14 }}>Routed to <strong>{u.owner.replaceAll("_", " ")}</strong></span>
                  ))}
                </div>
              </div>
            </Panel>
            <Panel title="The chat">
              <Chat events={selected.events} start={selected.events.find((e) => e.event_type === "task.opened").occurred_at} />
            </Panel>
          </div>
          <Panel>
            <details className="more">
              <summary>For specialists: all 12 checks for this scenario</summary>
              <VerdictHeader result={selected.result} />
              <CriteriaList behaviour={selected.result.axes.bot_behaviour} focus={selected.focus} />
            </details>
          </Panel>
        </div>
      )}

      <Takeaway>
        Most costly support mistakes are invisible in the words of the chat. They show up only when the chat is joined
        with the order, wallet and payment records. And when a record is missing, the honest answer is “unknown”, with a
        named team responsible for fixing it.
      </Takeaway>
      <NextStep id="catches" />
    </>
  );
}
