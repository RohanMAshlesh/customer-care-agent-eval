import { useEffect, useMemo, useState } from "react";
import { Chat, Chip, CriteriaList, EventList, Loading, Panel, VerdictHeader, useData } from "../components/ui";
import { ChapterHead, NextStep, Takeaway } from "../components/tour";
import { inr } from "../lib/format";

type Moment = { key: string; clock: string; title: string; text: string; event_count: number; revision: number; result: any };
type Journey = { events: any[]; moments: Moment[]; wrong_claim: { message: string; result: any } };

const STORY: Record<string, { headline: string; what: string; old: string; oldTone: "ok" | "bad" | "none"; now: string }> = {
  contact: {
    headline: "Priya reports a missing item",
    what: "Her INR 1,200 order arrived without the paneer tikka, worth INR 240. She paid INR 300 from her wallet and INR 900 by card.",
    old: "Nothing to review yet.", oldTone: "none",
    now: "Opens a case and links it to Priya’s order and payment records.",
  },
  decision: {
    headline: "The bot offers a refund",
    what: "The bot confirms it is really Priya and offers INR 240 back: INR 60 to her wallet and INR 180 to her card, in the same proportion she paid.",
    old: "Polite and clear. Looks good.", oldTone: "ok",
    now: "Checks her identity was verified before any money moved, and that the split follows the refund policy.",
  },
  action: {
    headline: "The refund is issued, with a hiccup",
    what: "The payment system times out on the first try, so the bot tries again.",
    old: "Not visible in the chat at all.", oldTone: "none",
    now: "Confirms the retry reused the same refund and did not pay Priya twice.",
  },
  response: {
    headline: "The bot tells Priya where her money is",
    what: "The wallet credit lands instantly. The card refund has only been sent to the bank. The bot says exactly that.",
    old: "Accurate and helpful.", oldTone: "ok",
    now: "Wallet: confirmed by the ledger. Card: sent, not yet arrived. The bot’s words match the records.",
  },
  leaves: {
    headline: "Priya leaves happy. A typical review would close the case.",
    what: "The chat ends with an honest estimate of up to 5 days for the card refund.",
    old: "Resolved. Score 9/10. Case closed.", oldTone: "ok",
    now: "Not closed. It keeps watching the card refund until its 7-day deadline.",
  },
  failure: {
    headline: "Next day, the bank rejects the card refund",
    what: "Priya has her INR 60 but not the INR 180. She has not been told.",
    old: "Still says “resolved”. Nobody re-reads the chat.", oldTone: "bad",
    now: "Updates the verdict to “only partly refunded” and routes it to the payments team.",
  },
  recovery: {
    headline: "The payments team re-issues the refund",
    what: "A new card refund is sent to the bank.",
    old: "Still says “resolved”.", oldTone: "bad",
    now: "Records the recovery and keeps watching for the money to land.",
  },
  settled: {
    headline: "Priya’s INR 180 arrives",
    what: "The payment provider confirms the re-issued refund completed.",
    old: "Still says “resolved”. Right by luck, three days late.", oldTone: "bad",
    now: "Fully refunded, confirmed by the payment provider’s own records.",
  },
  final: {
    headline: "The deadline passes. The verdict is final.",
    what: "Every earlier verdict is kept, so anyone can audit what was known and when.",
    old: "One score, written on day one.", oldTone: "bad",
    now: "Final verdict plus a full history of how it changed and why.",
  },
};

const MONEY: Record<string, [string, "pass" | "pending" | "fail" | "neutral" | "unknown"]> = {
  complete: ["Arrived", "pass"],
  submitted: ["Sent to bank, not arrived", "pending"],
  failed: ["Failed at the bank", "fail"],
  not_started: ["Not sent yet", "neutral"],
  unknown: ["No proof either way", "unknown"],
};

const OVERALL: Record<string, [string, "pass" | "pending" | "fail" | "neutral" | "warn"]> = {
  verified_complete: ["Fully refunded, verified", "pass"],
  partial: ["Only partly refunded", "warn"],
  pending: ["Not finished, still watching", "pending"],
  not_applicable: ["No refund yet", "neutral"],
  failed: ["Refund failed", "fail"],
};

export default function JourneyPage() {
  const data = useData<Journey>("journey");
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [overPromise, setOverPromise] = useState(false);

  useEffect(() => {
    if (!playing || !data) return;
    if (i >= data.moments.length - 1) { setPlaying(false); return; }
    const t = setTimeout(() => setI((x) => x + 1), 3600);
    return () => clearTimeout(t);
  }, [playing, i, data]);

  const view = useMemo(() => {
    if (!data) return null;
    const m = data.moments[i];
    const start = data.events.find((e) => e.event_type === "task.opened")!.occurred_at;
    const canTry = m.key === "response";
    const wrong = overPromise && canTry;
    let events = data.events.slice(0, m.event_count);
    if (wrong) {
      events = events.map((e) => e.event_type === "conversation.message" && e.payload.text.startsWith("INR 60 is credited")
        ? { ...e, payload: { ...e.payload, text: data.wrong_claim.message } } : e);
    }
    return { m, start, events, canTry, wrong, result: wrong ? data.wrong_claim.result : m.result };
  }, [data, i, overPromise]);

  if (!data || !view) return <Loading />;
  const { m, start, events, canTry, wrong, result } = view;
  const s = STORY[m.key];
  const obligations = result.axes.obligations;
  const [overall, overallTone] = OVERALL[result.business_outcome] ?? [result.business_outcome, "neutral"];

  return (
    <>
      <ChapterHead id="journey" title="A chat can look resolved while the customer is still out of pocket.">
        Follow one real-world style refund. On the left is what a typical transcript review concludes; on the right,
        what Evidence Desk concludes by checking the money. Press play, or step through at your own pace.
      </ChapterHead>

      <Panel>
        <div className="timeline">
          <div className="timeline-controls">
            <button className="btn primary" onClick={() => { if (i >= data.moments.length - 1) setI(0); setPlaying((p) => !p); }}>
              {playing ? "Pause" : i >= data.moments.length - 1 ? "Replay" : "▶ Play the story"}
            </button>
            <button className="btn" onClick={() => setI((x) => Math.max(0, x - 1))} disabled={i === 0}>← Back</button>
            <button className="btn" onClick={() => setI((x) => Math.min(data.moments.length - 1, x + 1))} disabled={i === data.moments.length - 1}>Next moment →</button>
            <span className="muted" style={{ fontSize: 13 }}>Moment {i + 1} of {data.moments.length}</span>
          </div>
          <div className="steps" role="tablist" aria-label="Moments">
            {data.moments.map((mm, k) => (
              <button key={mm.key} role="tab" className={`step${k < i ? " done" : ""}`} aria-current={k === i ? "step" : undefined}
                onClick={() => { setPlaying(false); setI(k); }}>
                <span className="t">{mm.clock}</span>
                <span className="n">{mm.title}</span>
              </button>
            ))}
          </div>
        </div>
      </Panel>

      <div className="story">
        <Panel>
          <div className="narration">
            <span className="when">{m.clock}</span>
            <h2>{s.headline}</h2>
            <p>{s.what}</p>

            <div className="compare">
              <div className="compare-col old">
                <span className="label">A transcript-only review says</span>
                <span className="say" style={{ color: s.oldTone === "bad" ? "var(--fail)" : undefined }}>{s.old}</span>
              </div>
              <div className="compare-col new">
                <span className="label">Evidence Desk says</span>
                <span className="say">{wrong ? "The bot over-promised." : s.now}</span>
              </div>
            </div>

            {canTry && (
              <div className={`callout ${wrong ? "fail" : ""}`}>
                <label className="switch">
                  <input type="checkbox" checked={overPromise} onChange={(e) => setOverPromise(e.target.checked)} />
                  Try it: what if the bot had said “your entire refund has reached you”?
                </label>
                {wrong && (
                  <span style={{ fontSize: 14 }}>
                    Flagged. At that moment the card refund had only been sent to the bank, so the promise was false,
                    even though the refund itself was correct. A transcript review would have scored this as helpful.
                  </span>
                )}
              </div>
            )}

            <div style={{ display: "grid", gap: 8 }}>
              <div className="verdict-row">
                <span className="label">Where Priya’s INR 240 is</span>
                <Chip tone={overallTone}>{overall}</Chip>
              </div>
              <div className="money">
                {(["wallet", "card"] as const).map((t) => {
                  const o = obligations[t];
                  const [label, tone] = o ? MONEY[o.status] ?? [o.status, "neutral"] : ["Not sent yet", "neutral" as const];
                  return (
                    <div className="money-item" key={t}>
                      <span className="where">{t === "wallet" ? "Back to wallet" : "Back to card"}</span>
                      <span className="amt">{inr(t === "wallet" ? 6000 : 18000)}</span>
                      <Chip tone={tone}>{label}</Chip>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </Panel>

        <Panel title="What Priya saw in the chat">
          <Chat events={events} start={start} flagged={wrong ? data.wrong_claim.message : undefined} />
        </Panel>
      </div>

      <Takeaway>
        The chat was perfect and a transcript review would have closed the case at 09:04. Priya was still owed INR 180
        for three days. Evidence Desk caught it because it follows the money until it lands, not just the words.
      </Takeaway>

      <Panel>
        <details className="more">
          <summary>For specialists: the 12 checks, the raw evidence and the verdict history</summary>
          <div className="journey-grid" style={{ padding: 16 }}>
            <Panel title="Business evidence"><EventList events={events} start={start} /></Panel>
            <Panel title="Engine verdict">
              <VerdictHeader result={result} revision={wrong ? undefined : m.revision} />
              <CriteriaList behaviour={result.axes.bot_behaviour} focus={wrong ? ["claims_supported_by_evidence"] : []} />
            </Panel>
            <Panel title="Verdict history">
              <div className="rule-list">
                {data.moments.filter((mm, k) => k === 0 || mm.revision !== data.moments[k - 1].revision).map((mm) => (
                  <div className="rule" key={mm.key}>
                    <span className="mono muted" style={{ fontSize: 11 }}>v{mm.revision}</span>
                    <span>{mm.clock} · {mm.title}</span>
                    <Chip tone={(OVERALL[mm.result.business_outcome] ?? ["", "neutral"])[1]}>{(OVERALL[mm.result.business_outcome] ?? [mm.result.business_outcome])[0]}</Chip>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        </details>
      </Panel>

      <NextStep id="journey" />
    </>
  );
}
