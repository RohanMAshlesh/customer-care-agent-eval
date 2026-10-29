import type { ReactNode } from "react";

export const TOUR = [
  { id: "journey", short: "1 · Four cases", title: "Replay four cases that looked resolved but were not" },
  { id: "catches", short: "2 · What it catches", title: "See the broken promises a chat review misses" },
  { id: "ship", short: "3 · Ship or not", title: "Decide whether a new bot is safe to launch" },
  { id: "setup", short: "4 · Getting started", title: "How a company gets set up" },
];

export function ChapterHead({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  const idx = TOUR.findIndex((t) => t.id === id);
  return (
    <div className="chapter-head">
      <nav className="tourbar" aria-label="Tour steps">
        {TOUR.map((t, k) => (
          <a key={t.id} href={`#${t.id}`} aria-current={t.id === id ? "step" : undefined} className={k < idx ? "done" : ""}>
            {t.short}
          </a>
        ))}
      </nav>
      <span className="eyebrow">Step {idx + 1} of {TOUR.length}</span>
      <h1>{title}</h1>
      <p>{children}</p>
    </div>
  );
}

export function Takeaway({ children }: { children: ReactNode }) {
  return (
    <div className="takeaway">
      <span className="label">The takeaway</span>
      <p>{children}</p>
    </div>
  );
}

export function NextStep({ id }: { id: string }) {
  const idx = TOUR.findIndex((t) => t.id === id);
  const next = TOUR[idx + 1];
  if (!next) {
    return (
      <a className="panel next-card" href="#overview">
        <span><span className="label">You have seen the whole tour</span><div className="t">Back to the summary</div></span>
        <span className="btn primary">Start over</span>
      </a>
    );
  }
  return (
    <a className="panel next-card" href={`#${next.id}`}>
      <span><span className="label">Next · step {idx + 2} of {TOUR.length}</span><div className="t">{next.title}</div></span>
      <span className="btn primary">Continue →</span>
    </a>
  );
}
