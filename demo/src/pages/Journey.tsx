import { useEffect, useMemo, useState } from "react";
import { Chat, Chip, CriteriaList, EventList, Loading, Panel, VerdictHeader, useData } from "../components/ui";
import { ChapterHead, NextStep, Takeaway } from "../components/tour";
import { CRITERIA, CRITERION_ORDER, inr, outcomeInfo, type Tone } from "../lib/format";

type Moment = {
  key: string; clock: string; title: string; event_count: number; revision: number;
  headline: string; what: string; old: string; old_tone: "ok" | "bad" | "none"; now: string;
  money: { owed_minor: number; arrived_minor: number; in_transit_minor: number } | null;
  result: any;
};
export type Story = {
  id: string; customer: string; title: string; category: string; situation: string; lesson: string;
  systems: string[]; takeaway: string; contract_ref: string; events: any[]; moments: Moment[];
  wrong_claim?: { message: string; result: any };
};

const WORK_STATUS: Record<string, [string, Tone]> = {
  complete: ["Done, confirmed", "pass"],
  submitted: ["In progress", "pending"],
  not_started: ["Not started", "neutral"],
  incomplete: ["Overdue, not done", "fail"],
  failed: ["Failed", "fail"],
  unknown: ["No proof either way", "unknown"],
};

function ProofPanel({ story, m, result }: { story: Story; m: Moment; result: any }) {
  const [label, tone] = outcomeInfo(result.business_outcome);
  const headTone: Tone = result.critical_violation ? "fail" : tone;
  const headLabel = result.critical_violation ? `Critical issue · ${label.toLowerCase()}` : label;
  const said = story.events.slice(0, m.event_count).some((e) => e.event_type === "customer.feedback" && e.payload.resolved);
  const failing = CRITERION_ORDER.filter((n) => result.axes.bot_behaviour[n]?.result === "fail");

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div className="verdict-row">
        <span className="label">Proof checked in {story.systems.join(", ")}</span>
        <Chip tone={headTone}>{headLabel}</Chip>
      </div>
      {m.money ? (
        <div className="money" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
          <div className="money-item"><span className="where">Owed to {story.customer}</span><span className="amt">{inr(m.money.owed_minor)}</span></div>
          <div className="money-item"><span className="where">Arrived</span><span className="amt" style={{ color: "var(--pass)" }}>{inr(m.money.arrived_minor)}</span></div>
          <div className="money-item"><span className="where">Sent, not arrived</span><span className="amt" style={{ color: "var(--pending)" }}>{inr(m.money.in_transit_minor)}</span></div>
        </div>
      ) : (
        <div className="money">
          {Object.entries(result.axes.obligations).map(([k, o]: [string, any]) => {
            const [l, t] = WORK_STATUS[o.status] ?? [o.status, "neutral"];
            return (
              <div className="money-item" key={k}>
                <span className="where">{k === "ticket" ? "Ticket in ServiceNow" : `Proof: ${o.label}`}</span>
                <span className="amt" style={{ fontSize: 17 }}>{k === "ticket" ? (o.number ?? "None found") : o.source}</span>
                {k === "ticket" && o.assignment_group && <span className="where">{o.assignment_group}</span>}
                <Chip tone={t}>{l}</Chip>
              </div>
            );
          })}
          {Object.keys(result.axes.obligations).length === 0 && <p className="muted">Nothing to check yet.</p>}
          {said && (
            <div className="money-item">
              <span className="where">What {story.customer} said</span>
              <span className="amt" style={{ fontSize: 17 }}>“It’s fixed”</span>
              <Chip tone={result.business_outcome === "verified_resolved" ? "pass" : "warn"}>
                {result.business_outcome === "verified_resolved" ? "Now confirmed" : "Not confirmed by any system"}
              </Chip>
            </div>
          )}
        </div>
      )}
      {failing.length > 0 && (
        <div className="callout fail">
          <span className="label">Failed checks so far</span>
          {failing.map((n) => (
            <span key={n} style={{ fontSize: 14 }}><strong>{CRITERIA[n]}:</strong> {result.axes.bot_behaviour[n].reason}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function JourneyPage({ initial }: { initial?: string }) {
  const stories = useData<Story[]>("stories");
  const [sid, setSid] = useState(initial ?? "priya");
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [overPromise, setOverPromise] = useState(false);

  useEffect(() => { if (initial) setSid(initial); }, [initial]);
  useEffect(() => { setI(0); setPlaying(false); setOverPromise(false); }, [sid]);

  const story = stories?.find((s) => s.id === sid) ?? stories?.[0];
  useEffect(() => {
    if (!playing || !story) return;
    if (i >= story.moments.length - 1) { setPlaying(false); return; }
    const t = setTimeout(() => setI((x) => x + 1), 3800);
    return () => clearTimeout(t);
  }, [playing, i, story]);

  const view = useMemo(() => {
    if (!story) return null;
    const m = story.moments[Math.min(i, story.moments.length - 1)];
    const start = story.events.find((e) => e.event_type === "task.opened")!.occurred_at;
    const canTry = story.id === "priya" && m.key === "response" && !!story.wrong_claim;
    const wrong = overPromise && canTry;
    let events = story.events.slice(0, m.event_count);
    if (wrong) {
      events = events.map((e) => e.event_type === "conversation.message" && e.payload.text.startsWith("INR 60 is credited")
        ? { ...e, payload: { ...e.payload, text: story.wrong_claim!.message } } : e);
    }
    return { m, start, events, canTry, wrong, result: wrong ? story.wrong_claim!.result : m.result };
  }, [story, i, overPromise]);

  if (!stories || !story || !view) return <Loading />;
  const { m, start, events, canTry, wrong, result } = view;

  return (
    <>
      <ChapterHead id="journey" title="Four customers. Four chats that looked resolved. None of them were, when the chat ended.">
        Pick a case. Each one is replayed moment by moment: what the customer saw, what a transcript-only review
        would conclude, and what Evidence Desk concludes by checking the systems where the work actually happens.
      </ChapterHead>

      <div className="case-picker" role="tablist" aria-label="Customer cases">
        {stories.map((s) => (
          <button key={s.id} role="tab" className="case-pick" aria-selected={s.id === story.id} onClick={() => setSid(s.id)}>
            <span className="label">{s.category}</span>
            <span className="t">{s.customer}: {s.title}</span>
            <span className="d">{s.lesson}</span>
          </button>
        ))}
      </div>

      <Panel>
        <div className="timeline">
          <p style={{ fontSize: 15, color: "var(--ink-2)" }}><strong style={{ color: "var(--ink)" }}>The situation.</strong> {story.situation}</p>
          <div className="timeline-controls">
            <button className="btn primary" onClick={() => { if (i >= story.moments.length - 1) setI(0); setPlaying((p) => !p); }}>
              {playing ? "Pause" : i >= story.moments.length - 1 ? "Replay" : "▶ Play the case"}
            </button>
            <button className="btn" onClick={() => setI((x) => Math.max(0, x - 1))} disabled={i === 0}>← Back</button>
            <button className="btn" onClick={() => setI((x) => Math.min(story.moments.length - 1, x + 1))} disabled={i === story.moments.length - 1}>Next moment →</button>
            <span className="muted" style={{ fontSize: 13 }}>Moment {i + 1} of {story.moments.length}</span>
          </div>
          <div className="steps" style={{ gridTemplateColumns: `repeat(${story.moments.length}, minmax(96px, 1fr))` }} role="tablist" aria-label="Moments">
            {story.moments.map((mm, k) => (
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
            <h2>{m.headline}</h2>
            <p>{m.what}</p>
            <div className="compare">
              <div className="compare-col old">
                <span className="label">A transcript-only review says</span>
                <span className="say" style={{ color: m.old_tone === "bad" ? "var(--fail)" : undefined }}>{wrong ? "Accurate and reassuring." : m.old}</span>
              </div>
              <div className="compare-col new">
                <span className="label">Evidence Desk says</span>
                <span className="say">{wrong ? "The bot over-promised: the card refund had not arrived." : m.now}</span>
              </div>
            </div>
            {canTry && (
              <div className={`callout ${wrong ? "fail" : ""}`}>
                <label className="switch">
                  <input type="checkbox" checked={overPromise} onChange={(e) => setOverPromise(e.target.checked)} />
                  Try it: what if the bot had said “your entire refund has reached you”?
                </label>
              </div>
            )}
            <ProofPanel story={story} m={m} result={result} />
          </div>
        </Panel>

        <div style={{ display: "grid", gap: 16 }}>
          <Panel title={`What ${story.customer} saw`}>
            <Chat events={events} start={start} flagged={wrong ? story.wrong_claim!.message : undefined} />
          </Panel>
          <Panel title="Behind the scenes" aside={<span className="label">records the customer never sees</span>}>
            <EventList events={events.filter((e) => !["census.task_assigned", "customer.profile", "task.opened", "bot.claim"].includes(e.event_type))} start={start} limit={6} />
          </Panel>
        </div>
      </div>

      <Takeaway>{story.takeaway}</Takeaway>

      <Panel>
        <details className="more">
          <summary>For specialists: every check, the full evidence and the verdict history for {story.customer}’s case</summary>
          <div className="journey-grid" style={{ padding: 16 }}>
            <Panel title="Engine verdict" aside={<span className="label">{story.contract_ref}</span>}>
              <VerdictHeader result={result} revision={wrong ? undefined : m.revision} />
              <CriteriaList behaviour={result.axes.bot_behaviour} />
            </Panel>
            <Panel title="All evidence"><EventList events={events} start={start} /></Panel>
            <Panel title="Verdict history">
              <div className="rule-list">
                {story.moments.filter((mm, k) => k === 0 || mm.revision !== story.moments[k - 1].revision).map((mm) => {
                  const [l, t] = outcomeInfo(mm.result.business_outcome);
                  return (
                    <div className="rule" key={mm.key}>
                      <span className="mono muted" style={{ fontSize: 11 }}>v{mm.revision}</span>
                      <span>{mm.clock} · {mm.title}</span>
                      <Chip tone={mm.result.critical_violation ? "fail" : t}>{mm.result.critical_violation ? "Critical" : l}</Chip>
                    </div>
                  );
                })}
              </div>
            </Panel>
          </div>
        </details>
      </Panel>

      <NextStep id="journey" />
    </>
  );
}
