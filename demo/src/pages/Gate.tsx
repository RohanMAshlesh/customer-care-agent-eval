import { useState } from "react";
import { Chip, Loading, Panel, useData } from "../components/ui";
import { pct, pp } from "../lib/format";

type Cell = {
  decision: "pass" | "block" | "insufficient_evidence";
  reasons: { effect: string; code: string; message: string }[];
  aggregate: { candidate_mean: number; baseline_mean: number; difference: number; ci_low: number; ci_high: number; one_sided_lower_95: number; families: number; min_trials_per_family: number };
  latency: { candidate_p95_ms: number; baseline_p95_ms: number; increase?: number };
  family_diffs: number[];
};
type Gate = { policy: any; baseline_rate: number; families: number; trials: number; grid: Record<string, Cell> };

const WORD = { pass: "Pass", block: "Block", insufficient_evidence: "Insufficient evidence" } as const;
const SUB = {
  pass: "Every condition holds for this deployment, contract and evaluator bundle. Valid for 7 days.",
  block: "Shipping is blocked. The reasons below say exactly which condition failed.",
  insufficient_evidence: "The run cannot prove the candidate is not worse. Collect more evidence; this is not a pass.",
} as const;

const RULES = [
  { code: "critical_violation", text: "Any confirmed critical violation", effect: "Block" },
  { code: "invalid_test", text: "Invalid test or missing critical evidence", effect: "Insufficient" },
  { code: "census_incomplete", text: "Episode census below 99.5% of declared", effect: "Insufficient" },
  { code: "coverage_floor", text: "Fewer than 50 families or 5 trials per family", effect: "Insufficient" },
  { code: "demonstrated_regression", text: "Whole 95% interval below −2 pp", effect: "Block" },
  { code: "noninferiority_not_shown", text: "One-sided 95% lower bound below −2 pp", effect: "Insufficient" },
  { code: "latency_regression", text: "Bot p95 latency up more than 10%", effect: "Block" },
];

function IntervalChart({ a, margin }: { a: Cell["aggregate"]; margin: number }) {
  const W = 640, H = 120, L = 24, R = 24;
  const lo = -0.2, hi = 0.08;
  const x = (v: number) => L + ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (W - L - R);
  const ticks = [-0.2, -0.15, -0.1, -0.05, 0, 0.05];
  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ minWidth: 460 }} role="img"
        aria-label={`Difference ${pp(a.difference)}, 95% interval ${pp(a.ci_low)} to ${pp(a.ci_high)}`}>
        <rect x={L} y={22} width={x(margin) - L} height={52} fill="var(--fail-soft)" />
        <text x={L + 4} y={16}>worse than allowed</text>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={22} y2={74} stroke="var(--line)" />
            <text x={x(t)} y={92} textAnchor="middle">{t === 0 ? "0" : `${(t * 100).toFixed(0)} pp`}</text>
          </g>
        ))}
        <line x1={x(margin)} x2={x(margin)} y1={14} y2={80} stroke="var(--fail)" strokeDasharray="4 3" strokeWidth={1.5} />
        <text x={x(margin) + 4} y={110} style={{ fill: "var(--fail)" }}>margin −2 pp</text>
        <line x1={x(0)} x2={x(0)} y1={22} y2={74} stroke="var(--ink-2)" />
        <line x1={x(a.ci_low)} x2={x(a.ci_high)} y1={48} y2={48} stroke="var(--ink)" strokeWidth={4} strokeLinecap="round" style={{ transition: "all .3s" }} />
        <line x1={x(a.one_sided_lower_95)} x2={x(a.one_sided_lower_95)} y1={36} y2={60} stroke="var(--accent)" strokeWidth={3} />
        <circle cx={x(a.difference)} cy={48} r={7} fill="var(--surface)" stroke="var(--ink)" strokeWidth={3} />
      </svg>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 12.5, color: "var(--muted)", padding: "0 4px" }}>
        <span>● point estimate {pp(a.difference)}</span>
        <span>━ 95% interval {pp(a.ci_low)} to {pp(a.ci_high)}</span>
        <span style={{ color: "var(--accent)" }}>┃ one-sided lower bound {pp(a.one_sided_lower_95)}</span>
      </div>
    </div>
  );
}

function FamilyStrip({ diffs }: { diffs: number[] }) {
  const buckets = new Map<number, number>();
  diffs.forEach((d) => buckets.set(Math.round(d * 10) / 10, (buckets.get(Math.round(d * 10) / 10) ?? 0) + 1));
  const W = 360, H = 120, L = 22, R = 22;
  const x = (v: number) => L + ((v + 1) / 2) * (W - L - R);
  const max = Math.max(...buckets.values());
  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: 520 }} role="img" aria-label="Per-family difference distribution">
        {[-1, -0.5, 0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={10} y2={92} stroke="var(--line)" />
            <text x={x(t)} y={108} textAnchor="middle">{t > 0 ? `+${t * 100}` : t * 100}</text>
          </g>
        ))}
        {[...buckets.entries()].map(([v, n]) => {
          const h = (n / max) * 78;
          return <rect key={v} x={x(v) - 6} y={92 - h} width={12} height={Math.max(h, 2)} rx={2}
            fill={v < 0 ? "var(--fail)" : v > 0 ? "var(--pass)" : "var(--line-strong)"} style={{ transition: "all .3s" }} />;
        })}
      </svg>
    </div>
  );
}

export default function GatePage() {
  const data = useData<Gate>("gate");
  const [rate, setRate] = useState(0.95);
  const [lat, setLat] = useState(0);
  const [critical, setCritical] = useState(false);
  if (!data) return <Loading />;
  const cell = data.grid[`${rate.toFixed(2)}|${lat}|${critical ? 1 : 0}`];
  const a = cell.aggregate;
  const hit = new Set(cell.reasons.map((r) => r.code));

  return (
    <>
      <div className="page-head">
        <span className="eyebrow">Release gate</span>
        <h1>Pass, block, or not enough evidence. Never a reassuring average.</h1>
        <p>
          A candidate bot is replayed against {data.families} scenario families × {data.trials} trials and compared
          with the current bot on the same families. Move the controls to change the candidate and watch the gate,
          computed by the engine's paired family-level bootstrap.
        </p>
      </div>

      <div className="gate">
        <Panel title="Candidate bot">
          <div className="control">
            <label htmlFor="rate" className="label">Underlying success rate</label>
            <div className="verdict-row">
              <span className="mono" style={{ fontSize: 22 }}>{pct(rate, 0)}</span>
              <span className="muted" style={{ fontSize: 13 }}>baseline {pct(data.baseline_rate, 0)}</span>
            </div>
            <input id="rate" className="range" type="range" min={0.8} max={1} step={0.01} value={rate}
              onChange={(e) => setRate(Number(e.target.value))} />
          </div>
          <div className="control">
            <span className="label">p95 latency change</span>
            <div className="seg" role="group" aria-label="Latency change">
              {[0, 5, 10, 15, 20].map((v) => (
                <button key={v} aria-pressed={lat === v} onClick={() => setLat(v)}>+{v}%</button>
              ))}
            </div>
          </div>
          <div className="control">
            <label className="switch">
              <input type="checkbox" checked={critical} onChange={(e) => setCritical(e.target.checked)} />
              Inject one duplicate refund in one trial
            </label>
            <span className="muted" style={{ fontSize: 12.5 }}>A single confirmed critical violation in 300 episodes.</span>
          </div>
          <div className="control">
            <span className="label">Measured</span>
            <dl className="kv">
              <dt>Candidate</dt><dd>{pct(a.candidate_mean)}</dd>
              <dt>Baseline</dt><dd>{pct(a.baseline_mean)}</dd>
              <dt>Families</dt><dd>{a.families} × {a.min_trials_per_family} trials</dd>
              <dt>p95 latency</dt><dd>{Math.round(cell.latency.candidate_p95_ms)} ms vs {Math.round(cell.latency.baseline_p95_ms)} ms</dd>
            </dl>
          </div>
        </Panel>

        <div style={{ display: "grid", gap: 16 }}>
          <div className={`decision-banner ${cell.decision}`} aria-live="polite">
            <div style={{ display: "grid", gap: 6 }}>
              <span className="label">Gate decision</span>
              <span className="decision-word" style={{ color: `var(--${cell.decision === "pass" ? "pass" : cell.decision === "block" ? "fail" : "unknown"})` }}>
                {WORD[cell.decision]}
              </span>
            </div>
            <p style={{ flex: "1 1 260px", fontSize: 14 }}>{SUB[cell.decision]}</p>
          </div>

          <Panel title="Difference from baseline" aside={<span className="label">fully verified correct disposition</span>}>
            <div className="panel-body"><IntervalChart a={a} margin={data.policy.noninferiority_margin} /></div>
          </Panel>

          <div className="two">
            <Panel title="Policy conditions">
              <div className="rule-list">
                {RULES.map((r) => (
                  <div key={r.code} className={`rule${hit.has(r.code) ? " hit" : ""}`}>
                    <span className={`dot ${hit.has(r.code) ? (r.effect === "Block" ? "fail" : "unknown") : "pass"}`} />
                    <span>{r.text}</span>
                    {hit.has(r.code) ? <Chip tone={r.effect === "Block" ? "fail" : "unknown"}>{r.effect}</Chip> : <span className="muted" style={{ fontSize: 12 }}>clear</span>}
                  </div>
                ))}
              </div>
            </Panel>
            <Panel title="Per-family differences" aside={<span className="label">{data.families} families</span>}>
              <div className="panel-body" style={{ display: "grid", gap: 8 }}>
                <FamilyStrip diffs={cell.family_diffs} />
                <p className="muted" style={{ fontSize: 12.5 }}>
                  Candidate minus baseline per family, in percentage points. Trials are averaged inside each family first, so five copies of one scenario never count as five
                  independent pieces of evidence.
                </p>
              </div>
            </Panel>
          </div>

          <Panel title="Reasons recorded with the decision">
            <div className="panel-body reasons">
              {cell.reasons.map((r, k) => (
                <div className="reason" key={k}>
                  <Chip tone={r.effect === "block" ? "fail" : r.effect === "pass" ? "pass" : r.effect === "note" ? "neutral" : "unknown"}>
                    {r.effect.replace("_", " ")}
                  </Chip>
                  <span>{r.message}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}
