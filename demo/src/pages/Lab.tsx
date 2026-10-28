import { useState } from "react";
import { Chat, CriteriaList, EventList, Loading, Panel, VerdictHeader, useData } from "../components/ui";
import { CRITERIA } from "../lib/format";

type Case = { id: string; title: string; category: string; story: string; verdict: string; focus: string[]; events: any[]; result: any };

function tone(c: Case): string {
  const r = c.result;
  if (r.critical_violation) return "fail";
  const focus = c.focus.map((f) => r.axes.bot_behaviour[f]?.result);
  if (focus.includes("fail")) return "warn";
  if (focus.includes("unknown") || r.business_outcome === "unknown") return "unknown";
  return "pass";
}

export default function LabPage() {
  const cases = useData<Case[]>("lab");
  const [id, setId] = useState("duplicate");
  if (!cases) return <Loading />;
  const c = cases.find((x) => x.id === id) ?? cases[0];
  const categories = [...new Set(cases.map((x) => x.category))];
  const start = c.events.find((e) => e.event_type === "task.opened")!.occurred_at;
  const t = tone(c);

  return (
    <>
      <div className="page-head">
        <span className="eyebrow">Edge-case lab</span>
        <h1>Fifteen ways a refund goes wrong, and what the evaluator says.</h1>
        <p>
          Each case is a scenario run through the same engine that grades production traffic. Notice where the answer
          is <em>unknown</em> instead of pass or fail: missing evidence is reported to an owner, never guessed.
        </p>
      </div>

      <div className="lab">
        <Panel>
          <nav className="case-list" aria-label="Scenarios">
            {categories.map((cat) => (
              <div key={cat}>
                <div className="case-group label">{cat}</div>
                {cases.filter((x) => x.category === cat).map((x) => (
                  <button key={x.id} className="case" aria-current={x.id === c.id} onClick={() => setId(x.id)}>
                    <span className={`dot ${tone(x)}`} />
                    <span>{x.title}</span>
                  </button>
                ))}
              </div>
            ))}
          </nav>
        </Panel>

        <div className="case-detail">
          <Panel>
            <div className="panel-body" style={{ display: "grid", gap: 12 }}>
              <span className="label">{c.category}</span>
              <h2 style={{ fontSize: 26 }}>{c.title}</h2>
              <p style={{ color: "var(--ink-2)" }}>{c.story}</p>
              <div className={`callout ${t === "warn" ? "fail" : t}`}>
                <span className="label">What the engine concluded</span>
                <strong>{c.verdict}</strong>
                {c.focus.map((f) => {
                  const crit = c.result.axes.bot_behaviour[f];
                  return crit ? (
                    <span key={f} style={{ fontSize: 13 }}>
                      <strong>{CRITERIA[f]}:</strong> {crit.reason}
                    </span>
                  ) : null;
                })}
              </div>
              {c.focus.some((f) => c.result.axes.bot_behaviour[f]?.detail) && (
                <details>
                  <summary>Evidence detail</summary>
                  <pre className="json">{JSON.stringify(Object.fromEntries(c.focus.map((f) => [f, c.result.axes.bot_behaviour[f]?.detail]).filter(([, d]) => d)), null, 2)}</pre>
                </details>
              )}
            </div>
          </Panel>

          <div className="two">
            <Panel title="Verdict">
              <VerdictHeader result={c.result} />
              <CriteriaList behaviour={c.result.axes.bot_behaviour} focus={c.focus} />
            </Panel>
            <div style={{ display: "grid", gap: 16 }}>
              <Panel title="Conversation"><Chat events={c.events} start={start} /></Panel>
              <Panel title="Evidence" aside={<span className="label">newest first</span>}>
                <EventList events={c.events} start={start} />
              </Panel>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
