import { useState } from "react";
import { Loading, Panel, useData } from "../components/ui";
import { ChapterHead, NextStep, Takeaway } from "../components/tour";
import { pct, pp } from "../lib/format";

type Cell = {
  decision: "pass" | "block" | "insufficient_evidence";
  reasons: { effect: string; code: string; message: string }[];
  aggregate: { candidate_mean: number; baseline_mean: number; difference: number; ci_low: number; ci_high: number; one_sided_lower_95: number };
  latency: { candidate_p95_ms: number; baseline_p95_ms: number };
};
type Gate = { families: number; trials: number; grid: Record<string, Cell> };

const PRESETS = [
  { key: "0.97|0|0", t: "A better version", d: "Fixes more requests, same speed" },
  { key: "0.80|0|0", t: "A clearly worse version", d: "Gets many more requests wrong" },
  { key: "0.92|0|0", t: "Possibly a bit worse", d: "Slightly lower, hard to tell" },
  { key: "0.97|20|0", t: "Better, but slower", d: "Replies take 20% longer" },
  { key: "0.97|0|1", t: "Better, with one double refund", d: "One critical mistake in 300 tests" },
];

const ANSWER = {
  pass: { word: "Yes, safe to launch", color: "var(--pass)" },
  block: { word: "No, do not launch", color: "var(--fail)" },
  insufficient_evidence: { word: "Not enough proof yet", color: "var(--unknown)" },
};

const WHY: Record<string, string> = {
  critical_violation: "One test produced a double refund. A single critical mistake blocks a launch, however good the average looks.",
  demonstrated_regression: "It resolves clearly fewer requests correctly than the bot customers use today.",
  noninferiority_not_shown: "It may be slightly worse than today’s bot, and the tests cannot rule that out. Run more tests before deciding.",
  latency_regression: "Customers would wait noticeably longer: replies are more than 10% slower than today.",
  all_conditions_met: "It resolves at least as many requests correctly as today’s bot, with no critical mistakes and no slowdown.",
};

export default function ShipPage() {
  const data = useData<Gate>("gate");
  const [key, setKey] = useState(PRESETS[0].key);
  if (!data) return <Loading />;
  const [rate, lat, crit] = key.split("|");
  const cell = data.grid[key];
  const a = cell.aggregate;
  const ans = ANSWER[cell.decision];
  const why = cell.reasons.filter((r) => WHY[r.code]);
  const setCustom = (r: string, l: string, c: string) => setKey(`${r}|${l}|${c}`);

  return (
    <>
      <ChapterHead id="ship" title="Before a new bot version goes live, you get one of three clear answers.">
        The new version and today’s version both handle the same {data.families * data.trials} practice requests
        ({data.families} different situations, each tried {data.trials} times). Pick a scenario to see the launch decision.
      </ChapterHead>

      <div className="presets" role="group" aria-label="Scenarios">
        {PRESETS.map((p) => (
          <button key={p.key} className="preset" aria-pressed={key === p.key} onClick={() => setKey(p.key)}>
            <span className="t">{p.t}</span>
            <span className="d">{p.d}</span>
          </button>
        ))}
      </div>

      <div className={`answer ${cell.decision}`} aria-live="polite">
        <span className="label">Launch decision</span>
        <span className="word" style={{ color: ans.color }}>{ans.word}</span>
        <ul className="why-list">
          {why.map((r) => (
            <li key={r.code}><span style={{ color: ans.color }}>●</span><span>{WHY[r.code]}</span></li>
          ))}
        </ul>
      </div>

      <div className="cols-2">
        <Panel title="Requests resolved correctly">
          <div className="panel-body bars">
            {[["Today’s bot", a.baseline_mean, "var(--line-strong)"], ["New version", a.candidate_mean, "var(--accent)"]].map(([l, v, c]) => (
              <div className="bar-row" key={l as string}>
                <span>{l as string}</span>
                <span className="bar-track"><span className="bar-fill" style={{ display: "block", width: `${((v as number) - 0.6) / 0.4 * 100}%`, background: c as string }} /></span>
                <span className="mono">{pct(v as number)}</span>
              </div>
            ))}
            <p className="muted" style={{ fontSize: 13 }}>
              “Correctly” means the right outcome and every required check passed, not just a good-sounding reply.
              Difference: {pp(a.difference)}; with uncertainty, between {pp(a.ci_low)} and {pp(a.ci_high)}.
            </p>
          </div>
        </Panel>
        <Panel title="How long customers wait (slowest 5% of replies)">
          <div className="panel-body bars">
            {[["Today’s bot", cell.latency.baseline_p95_ms, "var(--line-strong)"], ["New version", cell.latency.candidate_p95_ms, "var(--accent)"]].map(([l, v, c]) => (
              <div className="bar-row" key={l as string}>
                <span>{l as string}</span>
                <span className="bar-track"><span className="bar-fill" style={{ display: "block", width: `${(v as number) / 1400 * 100}%`, background: c as string }} /></span>
                <span className="mono">{((v as number) / 1000).toFixed(2)}s</span>
              </div>
            ))}
            <p className="muted" style={{ fontSize: 13 }}>More than 10% slower blocks a launch unless the business has explicitly accepted the trade-off.</p>
          </div>
        </Panel>
      </div>

      <Panel title="The four rules behind every decision">
        <div className="panel-body">
          <ul className="plain-list">
            <li><span className="b">1</span><span><strong>Any critical mistake blocks.</strong> A double refund or a data leak is never averaged away.</span></li>
            <li><span className="b">2</span><span><strong>Clearly worse blocks.</strong> If the new version is certainly more than 2 points worse, it does not ship.</span></li>
            <li><span className="b">3</span><span><strong>Unproven is not a yes.</strong> If the tests cannot show it is at most 2 points worse, the answer is “not enough proof”, never a reluctant pass.</span></li>
            <li><span className="b">4</span><span><strong>Approvals expire.</strong> A yes applies to that exact version and policy, for 7 days.</span></li>
          </ul>
        </div>
      </Panel>

      <Panel>
        <details className="more">
          <summary>For specialists: set the scenario yourself</summary>
          <div className="panel-body" style={{ display: "grid", gap: 14 }}>
            <label className="label" htmlFor="rate">New version’s underlying success rate: {pct(Number(rate), 0)} (today’s bot: 95%)</label>
            <input id="rate" className="range" type="range" min={0.8} max={1} step={0.01} value={Number(rate)}
              onChange={(e) => setCustom(Number(e.target.value).toFixed(2), lat, crit)} />
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
              <div className="seg" role="group" aria-label="Slowdown">
                {["0", "5", "10", "15", "20"].map((v) => (
                  <button key={v} aria-pressed={lat === v} onClick={() => setCustom(rate, v, crit)}>+{v}% slower</button>
                ))}
              </div>
              <label className="switch">
                <input type="checkbox" checked={crit === "1"} onChange={(e) => setCustom(rate, lat, e.target.checked ? "1" : "0")} />
                One double refund
              </label>
            </div>
            <p className="muted" style={{ fontSize: 13 }}>
              Decisions come from the engine’s paired bootstrap over scenario families. One-sided 95% lower bound:{" "}
              {pp(a.one_sided_lower_95)} against a −2 pp margin.
            </p>
          </div>
        </details>
      </Panel>

      <Takeaway>
        Launch decisions stop being a judgment call on an average score. Critical mistakes always block, and “we are not
        sure yet” is reported as exactly that, so a risky version cannot slip through on a good-looking number.
      </Takeaway>
      <NextStep id="ship" />
    </>
  );
}
