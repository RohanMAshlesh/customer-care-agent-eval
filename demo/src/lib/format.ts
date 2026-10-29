export type Tone = "pass" | "fail" | "unknown" | "pending" | "warn" | "neutral";

export const CRITERIA: Record<string, string> = {
  authorized_customer: "Authorized customer",
  correct_item_and_quantity: "Right item and quantity",
  amount_within_entitlement: "Amount within entitlement",
  allocation_matches_policy: "Allocation matches policy",
  no_duplicate_refund: "No duplicate refund",
  action_within_limits: "Action within limits",
  claims_supported_by_evidence: "Claims match evidence",
  estimate_within_policy: "Estimate within policy",
  no_sensitive_disclosure: "No sensitive disclosure",
  correct_disposition: "Correct disposition",
  issue_understanding: "Understood the issue",
  ticket_created: "Ticket created in time",
  ticket_routed_correctly: "Ticket in the right queue",
  reference_shared: "Customer given a real ticket number",
  promises_kept: "Promises backed by records",
  sla_met: "Fixed within the target time",
  no_repeat_contact: "No repeat contact",
  no_misleading_promise: "No misleading promise",
};

export const CRITERION_ORDER = [
  "promises_kept",
  "ticket_created",
  "ticket_routed_correctly",
  "reference_shared",
  "sla_met",
  "no_repeat_contact",
  "authorized_customer",
  "correct_item_and_quantity",
  "amount_within_entitlement",
  "no_duplicate_refund",
  "action_within_limits",
  "no_sensitive_disclosure",
  "allocation_matches_policy",
  "claims_supported_by_evidence",
  "correct_disposition",
  "estimate_within_policy",
  "issue_understanding",
  "no_misleading_promise",
];

const RESULT: Record<string, [string, Tone]> = {
  pass: ["Pass", "pass"],
  fail: ["Fail", "fail"],
  unknown: ["Unknown", "unknown"],
  abstain: ["Abstain", "unknown"],
  not_evaluated: ["Not evaluated", "neutral"],
  not_applicable: ["N/A", "neutral"],
};
export const resultChip = (r: string): [string, Tone] => RESULT[r] ?? [r, "neutral"];

const OUTCOME: Record<string, [string, Tone, string]> = {
  verified_complete: ["Verified complete", "pass", "Every refund leg reached its completion stage, confirmed by the source of record."],
  partial: ["Partial", "warn", "Some money arrived; at least one leg failed or did not complete."],
  failed: ["Failed", "fail", "No refund leg completed."],
  pending: ["Pending", "pending", "Still inside the deadline. Evidence is being watched."],
  unknown: ["Unknown", "unknown", "The deadline passed without evidence that settles it either way."],
  unverified: ["Unverified", "unknown", "An evidence source has not passed connector conformance."],
  not_applicable: ["No money owed", "neutral", "The bot refused, handed off, or the customer left before an action."],
  verified_resolved: ["Resolved, verified", "pass", "The system of record confirms the customer’s goal was met."],
  customer_reported_only: ["Customer says fixed, not verified", "warn", "The customer said it is resolved; no system has confirmed it yet."],
  unresolved: ["Not resolved", "fail", "The deadline passed and the system of record shows the work was not done."],
};
export const outcomeInfo = (o: string): [string, Tone, string] => OUTCOME[o] ?? [o, "neutral", ""];

const LEG: Record<string, [string, Tone, number]> = {
  not_started: ["Not started", "neutral", 4],
  submitted: ["Submitted", "pending", 50],
  complete: ["Complete", "pass", 100],
  partial: ["Partial", "warn", 70],
  failed: ["Failed", "fail", 100],
  incomplete: ["Overdue", "fail", 50],
  unknown: ["Unknown", "unknown", 50],
};
export const legInfo = (s: string): [string, Tone, number] => LEG[s] ?? [s, "neutral", 0];

const TIMELINESS: Record<string, string> = {
  within_deadline: "within deadline",
  completed_late: "completed late",
  overdue: "overdue",
  pending: "deadline ahead",
  unknown: "unconfirmed",
};
export const timeliness = (t?: string) => (t ? TIMELINESS[t] ?? t : "");

export const inr = (minor: number | null | undefined) =>
  minor == null ? "—" : `INR ${(minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

export const pct = (x: number | null | undefined, digits = 1) =>
  x == null ? "—" : `${(x * 100).toFixed(digits)}%`;

export const pp = (x: number | null | undefined, digits = 1) =>
  x == null ? "—" : `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(digits)} pp`;

export const DISPOSITION: Record<string, string> = {
  resolution: "Resolved by the bot",
  refusal: "Refused",
  handoff: "Handed to an agent",
  abandoned: "Customer left",
  awaiting_customer: "Waiting on customer",
};

export const UNKNOWN_REASON: Record<string, string> = {
  missing_identity_event: "No identity verification event",
  missing_order_snapshot: "No order record at decision time",
  conflicting_source_records: "Order records disagree",
  outcome_evidence_unavailable: "Outcome source has gone quiet",
  source_unverified: "Source not yet verified",
  unsupported_language: "Language outside calibrated scope",
  eligibility_unknown: "Eligibility could not be decided",
  judge_unavailable: "Judge unavailable",
};

export function describeEvent(e: { event_type: string; payload: any; logical_action_id?: string | null }): [string, string] {
  const p = e.payload ?? {};
  switch (e.event_type) {
    case "census.task_assigned": return ["Task assigned to the bot", "Independent census record"];
    case "order.snapshot": return ["Order record", `${p.status} · ${p.tenders?.map((t: any) => `${t.tender_type} ${inr(t.paid_minor)}`).join(" + ")}`];
    case "customer.profile": return ["Customer profile", `${p.prior_refund_count_30d ?? 0} refunds in 30 days`];
    case "task.opened": return ["Task opened", `${p.affected_lines?.map((l: any) => `${l.line_item_id} × ${l.quantity}`).join(", ")} · ${p.language ?? ""}`];
    case "conversation.message": return [p.speaker === "bot" ? "Bot message" : "Customer message", p.text];
    case "conversation.ended": return ["Chat ended", p.reason?.replaceAll("_", " ")];
    case "identity.verified": return ["Identity verified", p.method?.replaceAll("_", " ")];
    case "bot.action": return ["Refund action", `${inr(p.amount_minor)} → ${Object.entries(p.legs ?? {}).map(([k, v]) => `${k} ${inr(v as number)}`).join(", ")}`];
    case "action.attempt": return [`API attempt ${p.attempt}`, p.outcome];
    case "refund.leg_status": return [`${p.tender_type} leg ${p.stage}`, `${inr(p.amount_minor)}${p.provider_ref ? ` · ${p.provider_ref}` : ""}`];
    case "bot.claim": return ["Customer-visible claim", `${p.claim_type}${p.scope ? ` · ${Array.isArray(p.scope) ? p.scope.join("+") : p.scope}` : ""}${p.stage ? ` · ${p.stage}` : ""}${p.days ? ` · ${p.days} days` : ""}`];
    case "bot.handoff": return ["Handoff", p.reason];
    case "bot.refusal": return ["Refusal", p.reason];
    case "recovery.action": return ["Recovery started", `${p.owner} · ${p.action}`];
    case "tool.call": return [p.outcome === "error" ? `Bot tool call failed: ${p.tool}` : `Bot tool call: ${p.tool}`, p.detail ?? p.outcome];
    case "ticket.created": return [`Ticket ${p.number} created`, `${p.assignment_group} · P${p.priority} · ${p.category}`];
    case "ticket.updated": return [`Ticket ${p.number} ${p.state.replaceAll("_", " ")}`, [p.assignment_group, p.priority ? `P${p.priority}` : null].filter(Boolean).join(" · ")];
    case "service.signal": return [p.signal.replaceAll("_", " "), "confirmed by the system of record"];
    case "customer.feedback": return [p.resolved ? "Customer says it is resolved" : "Customer says it is not resolved", "customer-reported"];
    case "source.heartbeat": return ["Source heartbeat", `current through ${new Date(p.watermark).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" })}`];
    default: return [e.event_type, ""];
  }
}

export function clock(iso: string, start: string): string {
  const d = new Date(iso).getTime() - new Date(start).getTime();
  const mins = Math.round(d / 60000);
  if (Math.abs(mins) < 60 * 20) {
    const t = new Date(iso);
    return t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
  }
  return `Day ${Math.floor(mins / (60 * 24))}`;
}
