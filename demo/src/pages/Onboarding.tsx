import { useState } from "react";
import { Chip, Loading, Panel, useData } from "../components/ui";
import { CRITERIA, UNKNOWN_REASON, pct } from "../lib/format";

type Step = { title: string; actor: string; did: string; readiness: any; onboarding: any; [k: string]: any };

const INDICATORS: Record<string, string> = {
  conversations_connected: "Conversations connected",
  policies_approved: "Policies approved",
  business_outcomes_verifiable: "Business outcomes verifiable",
  evaluators_calibrated: "Evaluators calibrated",
};

function Extra({ step }: { step: Step }) {
  if (step.validation) {
    return (
      <Panel title="Contract validation" aside={<span className="label">four executable examples</span>}>
        <div className="panel-body fixture-grid">
          {step.validation.fixtures.map((f: any) => (
            <div className="fixture" key={f.name}>
              <div className="verdict-row"><strong>{f.name.replace("_", " ")}</strong><Chip tone={f.passed ? "pass" : "fail"}>{f.passed ? "Reproduced" : "Mismatch"}</Chip></div>
              <span className="muted">{f.result_summary.business_outcome} · {f.result_summary.critical_violation ? "critical" : "no critical"}</span>
            </div>
          ))}
        </div>
      </Panel>
    );
  }
  if (step.conformance) {
    const fx = step.conformance.fixtures;
    return (
      <Panel title="Connector conformance · payment_adapter" aside={<Chip tone="fail">Failed</Chip>}>
        <div className="panel-body fixture-grid">
          {Object.entries(fx).map(([name, r]: any) => (
            <div className={`fixture${r.passed ? "" : " bad"}`} key={name}>
              <div className="verdict-row"><strong>{name.replaceAll("_", " ")}</strong><Chip tone={r.passed ? "pass" : "fail"}>{r.passed ? "OK" : "Fail"}</Chip></div>
              <span className="muted">{r.message}</span>
            </div>
          ))}
        </div>
      </Panel>
    );
  }
  if (step.sources) {
    return (
      <Panel title="Evidence sources">
        <div className="panel-body fixture-grid">
          {Object.entries(step.sources).map(([s, ok]: any) => (
            <div className="fixture" key={s}><div className="verdict-row"><span className="mono">{s}</span><Chip tone={ok ? "pass" : "fail"}>{ok ? "Verified" : "Failed"}</Chip></div></div>
          ))}
        </div>
      </Panel>
    );
  }
  if (step.metrics) {
    const m = step.metrics.slices.all;
    const crit = Object.entries(m.critical_violations).filter(([k]) => !k.startsWith("_"));
    return (
      <>
        <div className="tiles">
          <div className="panel tile"><span className="v">{m.census.eligible_tasks}</span><span className="k">Bot-assigned tasks in the census</span></div>
          <div className="panel tile"><span className="v">{m.census.missing_conversation}</span><span className="k">Census tasks with no conversation</span></div>
          <div className="panel tile"><span className="v">{pct(m.verified_business_completion.rate)}</span><span className="k">Verified completion · 95% CI {pct(m.verified_business_completion.ci95?.[0], 0)}–{pct(m.verified_business_completion.ci95?.[1], 0)}</span></div>
          <div className="panel tile"><span className="v" style={{ color: "var(--fail)" }}>{crit.reduce((s, [, v]: any) => s + v.observed, 0)}</span><span className="k">Critical violations ({crit.map(([k]) => CRITERIA[k] ?? k).join(", ") || "none"})</span></div>
          <div className="panel tile"><span className="v" style={{ color: "var(--unknown)" }}>{pct(m.unknown_rate.rate)}</span><span className="k">Matured tasks still unknown</span></div>
        </div>
        {step.review_items && (
          <Panel title="Review queue" aside={<span className="label">routed by owner</span>}>
            <div className="rule-list">
              {step.review_items.map((r: any) => (
                <div className="rule" key={r.id}>
                  <span className={`dot ${r.kind === "critical" ? "fail" : "unknown"}`} />
                  <span>{r.kind === "critical" ? CRITERIA[r.criterion] ?? r.criterion : UNKNOWN_REASON[r.reason_code] ?? r.reason_code}<span className="muted mono" style={{ fontSize: 11 }}> · {r.task_key}</span></span>
                  <span className="owner">{r.owner_role}</span>
                </div>
              ))}
            </div>
          </Panel>
        )}
      </>
    );
  }
  if (step.calibration) {
    const m = step.calibration.metrics;
    return (
      <div className="tiles">
        <div className="panel tile"><span className="v">{m.items}</span><span className="k">Adjudicated validation cases</span></div>
        <div className="panel tile"><span className="v">{m.reviewer_kappa?.toFixed(2)}</span><span className="k">Agreement between reviewers (κ)</span></div>
        <div className="panel tile"><span className="v">{pct(m.false_pass_rate)}</span><span className="k">False passes</span></div>
        <div className="panel tile"><span className="v">{m.critical_false_passes}</span><span className="k">Critical failures the judge passed</span></div>
      </div>
    );
  }
  if (step.decision) {
    const d = step.decision;
    return (
      <Panel title="First release decision" aside={<Chip tone="unknown">Insufficient evidence</Chip>}>
        <div className="panel-body reasons">
          {d.reasons.map((r: any, k: number) => (
            <div className="reason" key={k}><Chip tone={r.effect === "block" ? "fail" : "unknown"}>{r.code.replaceAll("_", " ")}</Chip><span style={{ fontSize: 13 }}>{r.message}</span></div>
          ))}
          <div className="callout"><span className="label">Override drill</span><span style={{ fontSize: 13 }}>{d.exceptions?.[0]?.reason ?? "Exception recorded beside the gate; the decision itself is unchanged."}</span></div>
        </div>
      </Panel>
    );
  }
  return null;
}

export default function OnboardingPage() {
  const data = useData<{ steps: Step[] }>("onboarding");
  const [i, setI] = useState(0);
  if (!data) return <Loading />;
  const step = data.steps[i];
  const stages = step.onboarding.stages;
  const current = step.onboarding.current_stage;

  return (
    <>
      <div className="page-head">
        <span className="eyebrow">Client onboarding</span>
        <h1>From signed pilot to release gates, one piece of evidence at a time.</h1>
        <p>
          These are real API responses captured while a tenant moved through onboarding. Readiness is four separate
          indicators on purpose: each gap names the missing field and who owns it.
        </p>
      </div>

      <div className="onb">
        <Panel>
          <nav className="onb-steps" aria-label="Onboarding steps">
            {data.steps.map((s, k) => (
              <button key={s.title} className="onb-step" aria-current={k === i} onClick={() => setI(k)}>
                <span className="num">{k + 1}</span>
                <span><div style={{ fontWeight: 600, fontSize: 14 }}>{s.title}</div><div className="who">{s.actor}</div></span>
              </button>
            ))}
          </nav>
        </Panel>

        <div style={{ display: "grid", gap: 16 }}>
          <Panel>
            <div className="panel-body" style={{ display: "grid", gap: 8 }}>
              <span className="label">{step.actor}</span>
              <h2 style={{ fontSize: 24 }}>{step.title}</h2>
              <p style={{ color: "var(--ink-2)" }}>{step.did}</p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
                <Chip tone="pass">Conversation audit</Chip>
                <Chip tone={step.onboarding.milestones.verified_outcome_claims ? "pass" : "neutral"}>
                  Verified-outcome claims {step.onboarding.milestones.verified_outcome_claims ? "on" : "off"}
                </Chip>
              </div>
            </div>
          </Panel>

          <div className="indicators">
            {Object.entries(INDICATORS).map(([key, label]) => {
              const ind = step.readiness.indicators[key];
              return (
                <div className="panel indicator" key={key}>
                  <div className="verdict-row"><h3>{label}</h3><Chip tone={ind.complete ? "pass" : "neutral"}>{ind.complete ? "Ready" : "Missing"}</Chip></div>
                  {!ind.complete && (
                    <div className="missing">
                      <ul>
                        {ind.missing.slice(0, 2).map((m: any, k: number) => (
                          <li key={k}><span>{m.field}</span><span className="owner">{m.owner}</span></li>
                        ))}
                      </ul>
                      {ind.missing.length > 2 && <span className="muted">+{ind.missing.length - 2} more</span>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <Panel title="Journey stages" aside={<span className="label">{current ? `stage ${current} in progress` : "all stages complete"}</span>}>
            <div className="track">
              {stages.map((s: any) => (
                <div key={s.stage} className={`stage${s.complete ? " done" : ""}${s.stage === current ? " current" : ""}`} title={s.exit_evidence}>
                  <span className="s">{String(s.stage).padStart(2, "0")}</span>
                  <span className="n">{s.name}</span>
                </div>
              ))}
            </div>
          </Panel>

          <Extra step={step} />

          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn" disabled={i === 0} onClick={() => setI(i - 1)}>← Previous</button>
            <button className="btn primary" disabled={i === data.steps.length - 1} onClick={() => setI(i + 1)}>Next step →</button>
          </div>
        </div>
      </div>
    </>
  );
}
