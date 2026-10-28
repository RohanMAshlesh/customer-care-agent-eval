import { useState } from "react";
import { Chip, Loading, Panel, useData } from "../components/ui";
import { ChapterHead, NextStep, Takeaway } from "../components/tour";
import { CRITERIA, pct } from "../lib/format";

type Step = { title: string; readiness: any; [k: string]: any };

const CHECKS: [string, string, string][] = [
  ["conversations_connected", "Conversations flowing in", "Every bot chat, matched to the company’s own list of cases"],
  ["policies_approved", "Rules approved", "Refund policy written down and signed off"],
  ["business_outcomes_verifiable", "Money can be verified", "Order, identity, wallet and payment links tested"],
  ["evaluators_calibrated", "Language checks calibrated", "AI reviewer agrees with human experts"],
];

const PHASES = [
  {
    n: 1, t: "Agree the scope", who: "Head of support, AI lead, security", snap: 1,
    does: "Pick one workflow to start with (refunds for missing items), one region and one channel. Security approves what data may be shared and where it is processed.",
    checks: "The signed scope and data approval are recorded. Nothing is connected yet, so the checklist is empty.",
  },
  {
    n: 2, t: "Write down the rules", who: "Refund policy owner", snap: 2,
    does: "The policy owner turns the refund policy into clear rules: who is eligible, the automatic refund limit, deadlines and how refunds are split across payment methods. A second person must approve it.",
    checks: "Before approval, the rules are tested on four worked examples. If the rules cannot reproduce them, they cannot be approved.",
  },
  {
    n: 3, t: "Connect the systems", who: "Integration engineers", snap: 4,
    does: "Engineers connect the order, identity, wallet and payment systems. Each connection is tested with tricky cases (failures, duplicates, delays, outages) before its data is trusted.",
    checks: "Until a connection passes, money outcomes are reported as “unverified”, never as done.",
  },
  {
    n: 4, t: "Watch quietly", who: "Operations reviewers", snap: 6,
    does: "Live conversations are checked in the background. Customers notice nothing. Problems go to the team that owns them: refund mistakes to the policy owner, missing data to the engineers.",
    checks: "Results are reconciled against the company’s own list of cases, so a chat that never arrived is counted, not silently skipped.",
  },
  {
    n: 5, t: "Turn on launch checks", who: "Evaluation engineer, release owner", snap: 9,
    does: "Human experts grade sample conversations to make sure the AI reviewer agrees with them. Then every new bot version must pass the launch check from step 3, and the team practises overriding it in an emergency.",
    checks: "All four checklist items are complete. The next workflow, such as cancellations, goes through the same steps.",
  },
];

function Highlight({ phase, steps }: { phase: number; steps: Step[] }) {
  if (phase === 2) {
    const v = steps[2].validation;
    const names: Record<string, string> = { positive: "A normal, valid refund", forbidden_action: "A refund over the limit", boundary: "A refund exactly at the limit", missing_evidence: "A refund with missing proof" };
    return (
      <Panel title="Worked examples the rules had to get right">
        <div className="panel-body"><ul className="plain-list">
          {v.fixtures.map((f: any) => <li key={f.name}><span className="b">{f.passed ? "✓" : "✕"}</span><span>{names[f.name] ?? f.name}</span></li>)}
        </ul></div>
      </Panel>
    );
  }
  if (phase === 3) {
    const broken = steps[3].conformance.fixtures;
    const failed = Object.entries(broken).filter(([, r]: any) => !r.passed).map(([k]) => k);
    return (
      <div className="callout fail">
        <span className="label">Caught before going live</span>
        <strong style={{ fontSize: 16 }}>The first payment connection reported a failed refund as “completed”.</strong>
        <span style={{ fontSize: 14 }}>
          The connection test failed ({failed.map((f) => f.replaceAll("_", " ")).join(", ")}), so its data was not trusted until it was fixed.
          Without this test, every failed card refund would have been counted as a success.
        </span>
      </div>
    );
  }
  if (phase === 4) {
    const m = steps[5].metrics.slices.all;
    const crit = Object.entries(m.critical_violations).filter(([k]) => !k.startsWith("_"));
    return (
      <div className="tiles">
        <div className="panel tile"><span className="v">{m.census.eligible_tasks}</span><span className="k">Customer cases handled by the bot</span></div>
        <div className="panel tile"><span className="v">{pct(m.verified_business_completion.rate, 0)}</span><span className="k">Verified as fully refunded</span></div>
        <div className="panel tile"><span className="v" style={{ color: "var(--fail)" }}>{crit.reduce((s, [, v]: any) => s + v.observed, 0)}</span><span className="k">Critical mistakes ({crit.map(([k]) => CRITERIA[k]?.toLowerCase()).join(", ")})</span></div>
        <div className="panel tile"><span className="v" style={{ color: "var(--unknown)" }}>{m.census.missing_conversation}</span><span className="k">Cases with no chat record, now fixed</span></div>
      </div>
    );
  }
  if (phase === 5) {
    const c = steps[7].calibration.metrics;
    return (
      <div className="tiles">
        <div className="panel tile"><span className="v">{c.items}</span><span className="k">Conversations graded by two human experts, per language</span></div>
        <div className="panel tile"><span className="v">{c.critical_false_passes}</span><span className="k">Serious mistakes the AI reviewer missed</span></div>
        <div className="panel tile"><span className="v">{pct(c.false_alarm_rate, 0)}</span><span className="k">False alarms</span></div>
      </div>
    );
  }
  return null;
}

export default function SetupPage() {
  const data = useData<{ steps: Step[] }>("onboarding");
  const [p, setP] = useState(1);
  if (!data) return <Loading />;
  const phase = PHASES[p - 1];
  const ready = data.steps[phase.snap].readiness.indicators;

  return (
    <>
      <ChapterHead id="setup" title="A company gets set up in five steps, and each step must prove itself before the next.">
        Evidence Desk does not ask anyone to trust it on day one. Each step produces evidence, and a simple four-item
        checklist shows what is ready and what is still missing. These states were captured from the real system.
      </ChapterHead>

      <div className="phases" role="tablist" aria-label="Setup steps">
        {PHASES.map((ph) => (
          <button key={ph.n} role="tab" className="phase" aria-current={p === ph.n} onClick={() => setP(ph.n)}>
            <span className="n">Step {ph.n}</span>
            <span className="t">{ph.t}</span>
          </button>
        ))}
      </div>

      <div className="cols-2">
        <Panel>
          <div className="narration">
            <span className="when">Step {phase.n} · {phase.who}</span>
            <h2>{phase.t}</h2>
            <div style={{ display: "grid", gap: 4 }}>
              <span className="label">What the company does</span>
              <p>{phase.does}</p>
            </div>
            <div style={{ display: "grid", gap: 4 }}>
              <span className="label">What Evidence Desk checks</span>
              <p>{phase.checks}</p>
            </div>
          </div>
        </Panel>
        <Panel title="Readiness checklist" aside={<Chip tone={Object.values(ready).every((r: any) => r.complete) ? "pass" : "neutral"}>{Object.values(ready).filter((r: any) => r.complete).length} of 4 ready</Chip>}>
          <div className="panel-body" style={{ display: "grid", gap: 10 }}>
            {CHECKS.map(([k, t, s]) => (
              <div key={k} className={`check${ready[k].complete ? " on" : ""}`}>
                <span className="tick">{ready[k].complete ? "✓" : ""}</span>
                <span><div className="t">{t}</div><div className="s">{s}</div></span>
              </div>
            ))}
            <p className="muted" style={{ fontSize: 12.5 }}>Kept as four separate items on purpose. One blended “readiness score” would hide which piece is missing.</p>
          </div>
        </Panel>
      </div>

      <Highlight phase={phase.n} steps={data.steps} />

      <div style={{ display: "flex", gap: 10 }}>
        <button className="btn" disabled={p === 1} onClick={() => setP(p - 1)}>← Previous step</button>
        <button className="btn primary" disabled={p === 5} onClick={() => setP(p + 1)}>Next step →</button>
      </div>

      <Takeaway>
        Companies can start with a simple conversation review straight away. Claims about money are switched on only
        after each system connection has passed its tests, so the numbers leaders see are ones they can defend.
      </Takeaway>
      <NextStep id="setup" />
    </>
  );
}
