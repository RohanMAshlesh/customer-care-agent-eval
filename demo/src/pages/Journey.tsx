import { useEffect, useMemo, useState } from "react";
import { Chat, Chip, CriteriaList, EventList, Loading, Panel, VerdictHeader, useData } from "../components/ui";
import { inr, outcomeInfo } from "../lib/format";

type Moment = { key: string; clock: string; title: string; text: string; event_count: number; revision: number; result: any };
type Journey = {
  order: { paid: Record<string, number>; item_minor: number; allocation: Record<string, number> };
  contract: { ref: string; policy: string; cap_minor: number };
  events: any[];
  moments: Moment[];
  wrong_claim: { message: string; result: any };
};

const RIGHT_MESSAGE = "INR 60 is credited to your wallet and INR 180 has been submitted to your card provider.";

export default function JourneyPage() {
  const data = useData<Journey>("journey");
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [wrongClaim, setWrongClaim] = useState(false);

  useEffect(() => {
    if (!playing || !data) return;
    if (i >= data.moments.length - 1) { setPlaying(false); return; }
    const t = setTimeout(() => setI((x) => x + 1), 2600);
    return () => clearTimeout(t);
  }, [playing, i, data]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!data || (e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "ArrowRight") setI((x) => Math.min(x + 1, data.moments.length - 1));
      if (e.key === "ArrowLeft") setI((x) => Math.max(x - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [data]);

  const view = useMemo(() => {
    if (!data) return null;
    const m = data.moments[i];
    const prev = i > 0 ? data.moments[i - 1] : null;
    const start = data.events.find((e) => e.event_type === "task.opened")!.occurred_at;
    let events = data.events.slice(0, m.event_count);
    const fresh = new Set(data.events.slice(prev?.event_count ?? 0, m.event_count).map((e) => e.event_id));
    const canToggle = ["response", "leaves"].includes(m.key);
    const useWrong = wrongClaim && canToggle;
    let result = m.result;
    if (useWrong) {
      result = data.wrong_claim.result;
      events = events.map((e) =>
        e.event_type === "conversation.message" && e.payload.text === RIGHT_MESSAGE
          ? { ...e, payload: { ...e.payload, text: data.wrong_claim.message } }
          : e,
      ).filter((e) => !(e.event_type === "conversation.message" && e.payload.text.startsWith("Card refunds usually")));
    }
    return { m, prev, start, events, fresh, result, canToggle, useWrong };
  }, [data, i, wrongClaim]);

  if (!data || !view) return <Loading />;
  const { m, prev, start, events, fresh, result, canToggle, useWrong } = view;

  return (
    <>
      <div className="page-head">
        <span className="eyebrow">The refund journey</span>
        <h1>A chat that sounds resolved is not a resolved refund.</h1>
        <p>
          Follow one missing-item refund from the first message to the money arriving days later. The verdict on
          the right is recomputed by the evaluation engine at every moment, and every change is kept as a new revision.
        </p>
      </div>

      <div className="scenario-strip">
        <div className="panel fact"><span className="v">{inr(data.order.paid.wallet + data.order.paid.card)}</span><span className="k">Order paid: {inr(data.order.paid.wallet)} wallet + {inr(data.order.paid.card)} card</span></div>
        <div className="panel fact"><span className="v">{inr(data.order.item_minor)}</span><span className="k">Approved value of the missing item</span></div>
        <div className="panel fact"><span className="v">{inr(data.order.allocation.wallet)} / {inr(data.order.allocation.card)}</span><span className="k">Pro-rata allocation the evaluator expects</span></div>
        <div className="panel fact"><span className="v">{data.contract.ref.replace("missing_item_refund", "contract")}</span><span className="k">Policy {data.contract.policy}, auto-refund cap {inr(data.contract.cap_minor)}</span></div>
      </div>

      <Panel>
        <div className="timeline">
          <div className="timeline-controls">
            <button className="btn primary" onClick={() => { if (i >= data.moments.length - 1) setI(0); setPlaying((p) => !p); }}>
              {playing ? "Pause" : i >= data.moments.length - 1 ? "Replay" : "Play the journey"}
            </button>
            <button className="btn" onClick={() => setI((x) => Math.max(0, x - 1))} disabled={i === 0} aria-label="Previous moment">←</button>
            <button className="btn" onClick={() => setI((x) => Math.min(data.moments.length - 1, x + 1))} disabled={i === data.moments.length - 1} aria-label="Next moment">→</button>
            <p className="moment-text"><strong>{m.clock} · {m.title}.</strong> {m.text}</p>
          </div>
          <div className="steps" role="tablist" aria-label="Journey moments">
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

      <div className="journey-grid">
        <Panel title="Conversation" aside={<span className="label">what the customer saw</span>}>
          <Chat events={events} start={start} flagged={useWrong ? data.wrong_claim.message : undefined} />
          {canToggle && (
            <div className="panel-body" style={{ borderTop: "1px solid var(--line)" }}>
              <label className="switch">
                <input type="checkbox" checked={wrongClaim} onChange={(e) => setWrongClaim(e.target.checked)} />
                What if the bot said “your entire refund has reached you”?
              </label>
            </div>
          )}
        </Panel>

        <Panel title="Business evidence" aside={<span className="label">{events.filter((e) => e.event_type !== "conversation.message").length} events</span>}>
          <EventList events={events} start={start} fresh={fresh} />
        </Panel>

        <Panel title="Verdict" aside={<span className="label">engine output</span>}>
          <VerdictHeader result={result} revision={useWrong ? undefined : m.revision} />
          {useWrong && (
            <div className="panel-body" style={{ borderBottom: "1px solid var(--line)" }}>
              <div className="callout fail">
                <strong>The claim fails.</strong>
                <span style={{ fontSize: 13 }}>
                  When the bot said this, the card leg was only <em>submitted</em>. The money action itself is still
                  correct; the conversation and the outcome are judged separately.
                </span>
              </div>
            </div>
          )}
          <CriteriaList
            behaviour={result.axes.bot_behaviour}
            previous={!useWrong && prev ? prev.result.axes.bot_behaviour : undefined}
            focus={useWrong ? ["claims_supported_by_evidence"] : []}
          />
        </Panel>
      </div>

      <Panel title="Revision history" aside={<span className="label">append-only · nothing is rewritten</span>}>
        <div className="revisions">
          {data.moments.filter((mm, k) => k === 0 || mm.revision !== data.moments[k - 1].revision).map((mm) => {
            const k = data.moments.indexOf(mm);
            const [label, tone] = outcomeInfo(mm.result.business_outcome);
            return (
              <button key={mm.key} className="rev" aria-current={k === i} onClick={() => { setPlaying(false); setI(k); }}>
                <span className="r">rev {mm.revision} · {mm.clock}</span>
                <Chip tone={tone}>{label}</Chip>
                <span style={{ fontSize: 12 }}>{mm.result.provisional ? "provisional" : "final"}</span>
              </button>
            );
          })}
        </div>
      </Panel>
    </>
  );
}
