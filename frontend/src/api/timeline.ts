// Builds the replay timeline from a receipt: the buyer's mandate, each agent step, the hash-anchored
// receipt, then the ledger events (PayPal and dispute) in chain order.
//
// The deviating step is marked red. Seeded scenarios name it (steps[].deviation); for real
// receipts it comes from the verdict: a mandate violation is pinned on the agent's choice, and
// a merchant error with no violation (wrong item delivered) is pinned on the dispute.
import type { AgentStep, LedgerEvent, Product, Receipt, Verdict } from "./types";

export type Tone = "plain" | "deviation" | "warning" | "good" | "system"; // system: ledger maintenance, shown dimmed and collapsed
export type Phase = "Buyer" | "Agent" | "Ledger" | "PayPal" | "Dispute";

export interface Frame {
  key: string;
  phase: Phase;
  stamp: string; // a time, or the step number for agent steps (they carry no timestamps)
  at?: string;
  title: string;
  tone: Tone;
  flag?: string; // the red/amber callout
  notes: string[]; // small amber notes (e.g. a filter the agent dropped)
  body: FrameBody;
}

export type FrameBody =
  | { type: "mandate"; receipt: Receipt }
  | { type: "search"; step: AgentStep; results: Product[] }
  | { type: "listing"; product: Product | null }
  | { type: "choice"; step: AgentStep; receipt: Receipt; product: Product | null }
  | { type: "signed"; receipt: Receipt }
  | { type: "event"; event: LedgerEvent; verdict?: Verdict };

export const verdictEvents = (receipt: Receipt) =>
  receipt.events.filter((e) => e.kind === "dispute_verdict").map((e) => e.verdict as Verdict);

/** The verdict for one dispute, or the receipt's latest one. */
export function pickVerdict(receipt: Receipt, disputeId?: string | null): Verdict | null {
  const all = verdictEvents(receipt);
  return (disputeId ? all.find((v) => v.dispute_id === disputeId) : all[all.length - 1]) ?? null;
}

function violationText(verdict: Verdict) {
  return verdict.diff
    .filter((d) => !d.ok)
    .map((d) => `${d.field}: asked ${d.asked ?? "—"}, bought ${d.bought ?? "—"}`)
    .join("; ");
}

const EVENT_TITLES: Record<string, string> = {
  paypal_order_created: "PayPal order created",
  paypal_captured: "Payment captured",
  paypal_cancelled: "Buyer cancelled at PayPal",
  ledger_import: "System note: receipt restored to the ledger",
  dispute_opened: "Buyer opened a dispute",
  dispute_verdict: "Adjudicator verdict",
  dispute_response: "Response filed with PayPal",
};

const PHASE_OF = (kind: string): Phase =>
  kind.startsWith("paypal_") ? "PayPal" : kind.startsWith("dispute_") ? "Dispute" : "Ledger";

export function buildTimeline(receipt: Receipt, catalog: Product[], disputeId?: string | null): Frame[] {
  const byId = new Map(catalog.map((p) => [p.product_id, p]));
  const verdict = pickVerdict(receipt, disputeId);
  const { constraints } = receipt.mandate;
  const steps = receipt.agent_run.steps ?? [];
  const seededDeviation = steps.some((s) => s.deviation);
  const frames: Frame[] = [];

  frames.push({
    key: "mandate", phase: "Buyer", stamp: "ask", title: "Buyer gives the mandate", tone: "plain", notes: [],
    body: { type: "mandate", receipt },
  });

  steps.forEach((step, i) => {
    const stamp = `step ${i + 1}/${steps.length}`;
    const key = `step-${i}`;
    if (step.tool === "search_products") {
      const notes: string[] = [];
      const input = step.input as { size?: string | null; max_price?: number | null };
      if (constraints.size && !input.size) notes.push(`Size filter dropped (mandate: ${constraints.size})`);
      if (constraints.max_price != null && input.max_price == null)
        notes.push(`Budget filter dropped (mandate: ≤ $${constraints.max_price})`);
      const results = (step.result_ids ?? []).map((id) => byId.get(id)).filter((p): p is Product => !!p);
      frames.push({
        key, phase: "Agent", stamp, title: `Searched the catalog · ${results.length} results`,
        tone: notes.length ? "warning" : "plain", notes, body: { type: "search", step, results },
      });
    } else if (step.tool === "get_product") {
      const product = byId.get(String(step.input.product_id)) ?? null;
      frames.push({
        key, phase: "Agent", stamp, title: `Opened listing · ${product?.name ?? step.input.product_id}`,
        tone: product?.injection ? "warning" : "plain", notes: [],
        flag: product?.injection ? "This listing contains text addressed to shopping agents." : undefined,
        body: { type: "listing", product },
      });
    } else if (step.tool === "submit_choice") {
      const product = byId.get(receipt.agent_run.chosen_product_id) ?? null;
      let flag = step.deviation;
      if (!flag && !seededDeviation && verdict?.violations.length) flag = violationText(verdict);
      frames.push({
        key, phase: "Agent", stamp, title: `Chose ${product?.name ?? receipt.agent_run.chosen_product_id}`,
        tone: flag ? "deviation" : "plain", flag, notes: [], body: { type: "choice", step, receipt, product },
      });
    }
  });

  // A receipt that fails its integrity check is the deviation: what it says can't be trusted.
  const unchecked = verdict?.integrity.check === "anchor_unavailable"; // PayPal couldn't be reached: not evidence of tampering
  const altered = verdict?.integrity.status === "failed" && !unchecked;
  frames.push({
    key: "signed", phase: "Ledger", stamp: "", at: receipt.created_at, title: "Receipt of intent logged (hash-anchored)",
    tone: altered ? "deviation" : "plain", notes: [], body: { type: "signed", receipt },
    flag: altered
      ? `${verdict!.integrity.check === "chain" ? "Ledger chain broken" : "Altered after purchase"}: ${verdict!.integrity.note}`
      : unchecked ? `Not checked against PayPal: ${verdict!.integrity.note}` : undefined,
  });

  for (const event of receipt.events) {
    const dispute = event.dispute_id as string | undefined;
    if (disputeId && dispute && dispute !== disputeId) continue;
    const frame: Frame = {
      key: `ev-${event.seq}`, phase: PHASE_OF(event.kind), stamp: "", at: event.at,
      title: EVENT_TITLES[event.kind] ?? event.kind, tone: "plain", notes: [], body: { type: "event", event },
    };
    if (event.kind === "dispute_opened" && verdict?.fault_class === "MERCHANT_ERROR" && !verdict.violations.length) {
      frame.tone = "deviation";
      frame.flag = "The agent's choice met the mandate. The buyer reports receiving something else.";
    }
    if (event.kind === "dispute_verdict") {
      const v = event.verdict as Verdict;
      frame.body = { type: "event", event, verdict: v };
      frame.tone = v.status !== "decided" ? "warning" : v.fault_class === "BUYER_REMORSE" ? "good" : "plain";
    }
    if (event.kind === "dispute_response") frame.tone = "good";
    if (event.kind === "ledger_import") frame.tone = "system";
    frames.push(frame);
  }
  return frames;
}
