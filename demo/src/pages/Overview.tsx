export default function OverviewPage() {
  return (
    <>
      <section className="hero">
        <span className="eyebrow">Customer support · AI quality</span>
        <h1>Your AI support agent can say “refund done” when the customer never got their money.</h1>
        <p className="lede">
          Evidence Desk checks every conversation against the systems that actually move money: the order system,
          the wallet and the payment provider. It tells you whether each customer’s problem was really fixed, and
          whether a new version of the bot is safe to launch.
        </p>
        <div className="hero-actions">
          <a className="btn primary xl" href="#journey">Start the 3-minute tour →</a>
          <a className="btn xl" href="#catches">Jump to what it catches</a>
        </div>
      </section>

      <div className="scr">
        <div className="panel scr-card">
          <span className="label">Today</span>
          <h3>Support bots now move real money</h3>
          <p>Delivery and shopping apps let AI agents issue refunds, cancel orders and change accounts without a human.</p>
        </div>
        <div className="panel scr-card">
          <span className="label">The problem</span>
          <h3>Teams judge the chat, not the outcome</h3>
          <p>
            Quality reviews read the transcript and ask “did it sound helpful?”. A polite chat can hide a refund that
            failed at the bank, a double refund, or money sent to the wrong account.
          </p>
        </div>
        <div className="panel scr-card resolution">
          <span className="label">The answer</span>
          <h3>Judge each request by the evidence</h3>
          <p>
            Join the chat with order, wallet and payment records. Follow the money until it lands. Say “we don’t know
            yet” when proof is missing, instead of guessing.
          </p>
        </div>
      </div>

      <div className="section-title">
        <h2>Three questions it answers</h2>
        <p>Each one is a step in the tour. Every result shown was produced by the real evaluation engine.</p>
      </div>
      <div className="questions">
        <a className="panel question" href="#journey">
          <span className="label">Step 1</span>
          <span className="q">Did this customer actually get their money back?</span>
          <p>Follow one refund through chat, a bank failure and the fix, and see where a transcript review goes wrong.</p>
          <span className="go">See one refund →</span>
        </a>
        <a className="panel question" href="#catches">
          <span className="label">Step 2</span>
          <span className="q">Did the bot follow the rules?</span>
          <p>Nine real mistakes, from double refunds to leaking a card number, that sound fine in a transcript.</p>
          <span className="go">See what it catches →</span>
        </a>
        <a className="panel question" href="#ship">
          <span className="label">Step 3</span>
          <span className="q">Is the new bot version safe to launch?</span>
          <p>A clear yes, no, or “not enough proof yet” before a new version reaches customers.</p>
          <span className="go">Try the launch check →</span>
        </a>
      </div>

      <div className="panel panel-body cols-2">
        <div style={{ display: "grid", gap: 8 }}>
          <span className="label">Who it is for</span>
          <p style={{ fontSize: 16 }}>
            Heads of customer support and AI teams at high-volume businesses, starting with refunds for missing items
            in food and grocery delivery.
          </p>
        </div>
        <div style={{ display: "grid", gap: 8 }}>
          <span className="label">What makes it different</span>
          <ul className="plain-list">
            <li><span className="b">✓</span>Checks the money, not just the words</li>
            <li><span className="b">✓</span>Keeps watching after the chat ends</li>
            <li><span className="b">✓</span>Says “unknown” and names who must fix it, instead of guessing</li>
          </ul>
        </div>
      </div>
    </>
  );
}
