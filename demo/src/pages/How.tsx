const FLOW = [
  ["Ingest", "Chat, order, identity, wallet and payment events arrive in one envelope. Duplicates are dropped by source ID; the tenant comes from the API key."],
  ["Correlate", "Events join to one customer task by client task ID, logical action or conversation. Ambiguous matches stay unmerged."],
  ["Evaluate", "Deterministic checks for identity, items, money and duplicates; sampled, cited judges for language. Missing evidence is unknown."],
  ["Reconcile", "Obligations per tender leg are watched until their deadline. Late events append a new revision; history is never rewritten."],
  ["Decide", "Census-reconciled metrics and a release gate that answers pass, block or insufficient evidence, bound to exact versions."],
];

const PRINCIPLES = [
  ["The unit is a task with evidence", "A transcript can sound resolved while the card refund fails the next day. The platform follows the money, not the tone."],
  ["Unknown is a first-class answer", "When identity or ledger evidence is missing, the result is unknown and routed to a named owner instead of guessed."],
  ["Claims are graded at the time they were made", "“Submitted to your card” is honest at 09:03 even if the refund later fails. “Has reached you” is not."],
  ["Judges cite or abstain", "Language judges see redacted conversation only, must cite events from this task, and a timeout is never a pass."],
  ["Policy owners approve, not models", "AI can draft rules; a separate policy owner approves a typed contract after reviewing executable examples."],
  ["Gates refuse false comfort", "Five copies of one scenario are not five pieces of evidence. Too few families means insufficient evidence, not a pass."],
];

export default function HowPage() {
  return (
    <>
      <div className="page-head">
        <span className="eyebrow">How it works</span>
        <h1>Configure business truth once. Reuse it for releases, monitoring and policy changes.</h1>
        <p>
          The same pure evaluation function grades production traffic, sandbox release runs, contract fixtures and
          connector conformance kits, so the test oracle is always independent of the bot under test.
        </p>
      </div>
      <div className="arch">
        {FLOW.map(([t, d], k) => (
          <div className="panel arch-box" key={t}>
            <span className="label">{String(k + 1).padStart(2, "0")}</span>
            <h3>{t}</h3>
            <p>{d}</p>
          </div>
        ))}
      </div>
      <div className="principles">
        {PRINCIPLES.map(([t, d]) => (
          <div className="panel principle" key={t}><h3>{t}</h3><p>{d}</p></div>
        ))}
      </div>
      <div className="panel panel-body" style={{ display: "grid", gap: 8 }}>
        <span className="label">Run it yourself</span>
        <p style={{ fontSize: 14 }}>
          The service is a Python API and worker on PostgreSQL. Every number in this demo was exported from it with{" "}
          <code className="mono">python demo/scripts/export_data.py</code>.
        </p>
        <a href="https://github.com/RohanMAshlesh/customer-care-agent-eval" target="_blank" rel="noreferrer">github.com/RohanMAshlesh/customer-care-agent-eval</a>
      </div>
    </>
  );
}
