const CASES = [
  { id: "priya", category: "Money", who: "Priya: the refund that bounced",
    what: "Refund promised in chat. The card refund failed at the bank the next day.",
    old: "Resolved · 9/10", truth: "INR 180 still owed for 3 days" },
  { id: "kavya", category: "Safety", who: "Kavya: the escalation that never happened",
    what: "“I’ve escalated this to our safety team.” The ServiceNow call had failed silently.",
    old: "Empathetic escalation · 10/10", truth: "No incident existed" },
  { id: "rohit", category: "Service request", who: "Rohit: the ticket in the wrong queue",
    what: "Replacement ticket raised, but with the refunds team at low priority.",
    old: "Ticket raised · 9/10", truth: "Nothing moved for 2 days; target missed" },
  { id: "neha", category: "Account access", who: "Neha: “It works now, thanks!”",
    what: "The customer said sign-in worked. The sign-in logs said she never got in.",
    old: "First-contact resolution · 10/10", truth: "Still locked out; came back that evening" },
];

export default function OverviewPage() {
  return (
    <>
      <section className="hero">
        <span className="eyebrow">Customer support · AI quality</span>
        <h1>AI support is graded on how conversations sound. Customers judge it on whether their problem was fixed.</h1>
        <p className="lede">
          Evidence Desk measures resolution, not conversation. It checks every support case in the systems where the
          work actually happens (payments, ServiceNow, logistics, sign-in) and follows it until the outcome is proven.
        </p>
        <div className="hero-actions">
          <a className="btn primary xl" href="#journey">Start the 3-minute tour →</a>
          <a className="btn xl" href="#catches">Jump to what it catches</a>
        </div>
      </section>

      <div className="scr">
        <div className="panel scr-card">
          <span className="label">Situation</span>
          <h3>AI agents now close support cases on their own</h3>
          <p>Refunds, safety complaints, replacements and account problems are increasingly handled end to end by a bot.</p>
        </div>
        <div className="panel scr-card">
          <span className="label">Complication</span>
          <h3>The transcript only holds the promise</h3>
          <p>
            Quality reviews read the chat. But the work happens elsewhere: the refund at the bank, the ticket in
            ServiceNow, the parcel with logistics, the sign-in. Nobody checks that the promise was kept.
          </p>
        </div>
        <div className="panel scr-card resolution">
          <span className="label">Resolution</span>
          <h3>Score every case on proven outcomes</h3>
          <p>
            Join each chat to the systems of record. Check the right action happened, in the right place, on time.
            Report “unknown” when proof is missing, and name the team that must fix it.
          </p>
        </div>
      </div>

      <div className="section-title">
        <h2>Four cases, one pattern</h2>
        <p>Every case below would score highly in a transcript review. In all four, the customer’s problem was not solved when the chat ended.</p>
      </div>
      <div className="cases4">
        {CASES.map((c) => (
          <a key={c.id} className="panel case-card" href={`#journey-${c.id}`}>
            <span className="label">{c.category}</span>
            <span className="who">{c.who}</span>
            <p>{c.what}</p>
            <div className="vs">
              <div className="o"><span className="label">Transcript review</span>{c.old}</div>
              <div className="n"><span className="label">What actually happened</span>{c.truth}</div>
            </div>
            <span className="go" style={{ fontWeight: 600, fontSize: 14, color: "var(--accent)" }}>Replay this case →</span>
          </a>
        ))}
      </div>

      <div className="section-title">
        <h2>What a support leader gets</h2>
        <p>Three answers that a transcript score cannot give.</p>
      </div>
      <div className="outcomes3">
        <a className="panel question" href="#journey">
          <span className="label">1 · Truth</span>
          <span className="q">A resolution rate you can defend</span>
          <p>The share of cases where the customer’s goal is confirmed by a system of record, not by the tone of a chat.</p>
        </a>
        <a className="panel question" href="#catches">
          <span className="label">2 · Accountability</span>
          <span className="q">A list of broken promises, each with an owner</span>
          <p>Missing tickets, wrong queues, false “it’s fixed” claims and money errors, routed to the team that can fix them.</p>
        </a>
        <a className="panel question" href="#ship">
          <span className="label">3 · Control</span>
          <span className="q">A clear launch decision for every new bot version</span>
          <p>Yes, no, or “not enough proof yet”, before customers are exposed to a change.</p>
        </a>
      </div>

      <div className="panel panel-body cols-2">
        <div style={{ display: "grid", gap: 8 }}>
          <span className="label">Where to start</span>
          <p className="big-claim">One workflow, one region, one channel. Prove it there, then expand.</p>
        </div>
        <div style={{ display: "grid", gap: 8 }}>
          <span className="label">What makes it different</span>
          <ul className="plain-list">
            <li><span className="b">✓</span>Checks the work in the systems of record, not just the words</li>
            <li><span className="b">✓</span>Keeps watching after the chat ends, until the deadline</li>
            <li><span className="b">✓</span>Separates “the customer says fixed” from “proven fixed”</li>
            <li><span className="b">✓</span>Says “unknown” and names an owner, instead of guessing</li>
          </ul>
        </div>
      </div>
    </>
  );
}
