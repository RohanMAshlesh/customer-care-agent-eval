import { Component, useEffect, useState, type ReactNode } from "react";
import {
  CRITERIA, CRITERION_ORDER, DISPOSITION, UNKNOWN_REASON, clock, describeEvent, inr, legInfo, outcomeInfo,
  resultChip, timeliness, type Tone,
} from "../lib/format";

// Data ships inside the bundle (code-split per file), so pages never depend on a runtime fetch.
const LOADERS: Record<string, () => Promise<{ default: unknown }>> = {
  stories: () => import("../data/stories.json"),
  lab: () => import("../data/lab.json"),
  gate: () => import("../data/gate.json"),
  onboarding: () => import("../data/onboarding.json"),
};

export function useData<T>(name: string): T | null {
  const [data, setData] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    const load = LOADERS[name];
    if (!load) { setFailed(true); return; }
    load()
      .then((mod) => { if (live) setData(mod.default as T); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [name]);
  if (failed) throw new Error(`Could not load the ${name} data.`);
  return data;
}

export function Chip({ tone, children, lg }: { tone: Tone; children: ReactNode; lg?: boolean }) {
  return <span className={`chip ${tone}${lg ? " lg" : ""}`}>{children}</span>;
}

export function ResultChip({ result }: { result: string }) {
  const [label, tone] = resultChip(result);
  return <Chip tone={tone}>{label}</Chip>;
}

export function Panel({ title, aside, children, className }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className ?? ""}`}>
      {title != null && (
        <header className="panel-head">
          <h2>{title}</h2>
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

export function Loading() {
  return <div className="loading">Loading evaluation data…</div>;
}

export class PageBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  render() {
    if (this.state.error) {
      return (
        <div className="callout fail" role="alert">
          <strong>This page could not load.</strong>
          <span style={{ fontSize: 14 }}>{this.state.error} Refresh the page to try again.</span>
        </div>
      );
    }
    return this.props.children;
  }
}

type Crit = { result: string; severity: string; kind: string; reason: string; source?: string; detail?: any };

export function CriteriaList({ behaviour, focus = [], previous }: { behaviour: Record<string, Crit>; focus?: string[]; previous?: Record<string, Crit> }) {
  const names = CRITERION_ORDER.filter((n) => behaviour[n]);
  return (
    <div className="criteria">
      {names.map((name) => {
        const c = behaviour[name];
        const changed = previous && previous[name] && previous[name].result !== c.result;
        return (
          <div key={name} className={`crit${focus.includes(name) ? " focus" : ""}${changed ? " changed" : ""}`}>
            <div className="name">
              {CRITERIA[name] ?? name}
              <span className={`sev ${c.severity}`}>{c.severity}</span>
              {c.kind === "semantic" && <span className="sev">judge</span>}
            </div>
            <ResultChip result={c.result} />
            <div className="why">{c.reason}</div>
          </div>
        );
      })}
    </div>
  );
}

export function Legs({ obligations }: { obligations: Record<string, any> }) {
  const entries = Object.entries(obligations);
  if (!entries.length) {
    return <p className="muted" style={{ fontSize: 13 }}>No money is owed yet. Obligations appear once the bot takes a refund action.</p>;
  }
  return (
    <div className="legs">
      {entries.map(([tender, o]) => {
        const [label, tone, width] = legInfo(o.status);
        return (
          <div className="leg" key={tender}>
            <div className="verdict-row">
              <span className="label">{tender} leg</span>
              <Chip tone={tone}>{label}</Chip>
            </div>
            <div className="amt">{inr(o.expected_minor)}</div>
            <div className="leg-bar"><span style={{ width: `${width}%`, background: `var(--${tone === "neutral" ? "line-strong" : tone})` }} /></div>
            <div className="muted" style={{ fontSize: 12 }}>
              {o.source} · {timeliness(o.timeliness)}
              {o.recovery ? ` · recovery ${o.recovery}` : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function VerdictHeader({ result, revision }: { result: any; revision?: number }) {
  const [label, tone, note] = outcomeInfo(result.business_outcome);
  const disposition = result.axes.disposition;
  return (
    <div className="verdict-top">
      <div className="verdict-row">
        <span className="label">Business outcome</span>
        <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {revision != null && <Chip tone="neutral">Revision {revision}</Chip>}
          <Chip tone={result.provisional ? "pending" : "pass"}>{result.provisional ? "Provisional" : "Final"}</Chip>
        </span>
      </div>
      <div>
        <div className="big-outcome" style={{ color: `var(--${tone === "neutral" ? "ink" : tone})` }}>{label}</div>
        <p className="muted" style={{ fontSize: 13, marginTop: 4 }}>{note}</p>
      </div>
      <Legs obligations={result.axes.obligations} />
      <div className="verdict-row" style={{ fontSize: 13 }}>
        <span>
          <span className="label">Disposition </span>{disposition ? DISPOSITION[disposition] ?? disposition : "Not yet"}
        </span>
        {result.critical_violation ? <Chip tone="fail">Critical violation</Chip> : <Chip tone="pass">No critical violation</Chip>}
      </div>
      {result.unknown_reasons?.length > 0 && (
        <div className="callout unknown">
          <span className="label">Unknown, routed to an owner</span>
          {result.unknown_reasons.map((u: any) => (
            <div key={u.code} style={{ fontSize: 13 }}>
              {UNKNOWN_REASON[u.code] ?? u.code} <span className="owner">→ {u.owner}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function EventList({ events, start, fresh = new Set<string>(), limit }: { events: any[]; start: string; fresh?: Set<string>; limit?: number }) {
  const shown = [...events]
    .filter((e) => e.event_type !== "conversation.message")
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
    .reverse()
    .slice(0, limit ?? 999);
  return (
    <div className="evlist">
      {shown.map((e) => {
        const [title, sub] = describeEvent(e);
        return (
          <div key={e.event_id} className={`ev-row${fresh.has(e.event_id) ? " new" : ""}`}>
            <span className="ev-time">{clock(e.occurred_at, start)}</span>
            <span className="ev-what">
              <div className="t">{title}</div>
              <div className="s">{sub}</div>
            </span>
            <span className="src">{e.source}</span>
          </div>
        );
      })}
    </div>
  );
}

export function Chat({ events, start, flagged }: { events: any[]; start: string; flagged?: string }) {
  const shown = events
    .filter((e) => ["conversation.message", "bot.action", "action.attempt", "conversation.ended", "identity.verified", "bot.handoff", "bot.refusal"].includes(e.event_type))
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at));
  return (
    <div className="chat" aria-live="polite">
      {shown.length === 0 && <p className="muted" style={{ fontSize: 13 }}>Waiting for the customer…</p>}
      {shown.map((e) => {
        if (e.event_type === "conversation.message") {
          const who = e.payload.speaker === "bot" ? "bot" : "customer";
          return (
            <div key={e.event_id} className={`bubble ${who}${flagged && e.payload.text === flagged ? " flagged" : ""}`}>
              {e.payload.text}
              <div className="bubble-meta">{clock(e.occurred_at, start)}{e.payload.language && e.payload.language !== "en" ? ` · ${e.payload.language}` : ""}</div>
            </div>
          );
        }
        const [t, s] = describeEvent(e);
        return <div key={e.event_id} className="sys">{t}{s ? ` · ${s}` : ""}</div>;
      })}
    </div>
  );
}
