// The buyer's side of the verdict, in plain words, built from the verdict's facts (the diff,
// the integrity check, the fault class) rather than from the buyer's own complaint.
import { money } from "./format";
import type { DiffRow, Verdict } from "./types";

const limit = (asked: string | null) => money((asked ?? "").replace(/^at most\s*/, ""));

function value(row: DiffRow, v: string | null) {
  if (v == null) return "—";
  if (row.field === "size") return `size ${v}`;
  if (row.field === "price") return money(v);
  return v;
}

/** "you asked for size 8; your agent bought size 9" (or the price/limit version). */
function brokeIt(row: DiffRow) {
  if (row.field === "price") return `you set a limit of ${limit(row.asked)}; your agent paid ${money(row.bought)}`;
  return `you asked for ${value(row, row.asked)}; your agent bought ${value(row, row.bought)}`;
}

export function buyerReason(v: Verdict): string {
  if (v.status !== "decided") {
    if (v.integrity.check === "anchor_unavailable") {
      return `Your receipt couldn't be checked against PayPal (${v.integrity.note.replace(/\.$/, "")}), so a person reviews the dispute before anything is refunded.`;
    }
    return `Your receipt can't be relied on (${v.integrity.note.replace(/\.$/, "")}), so a person reviews the dispute before anything is refunded.`;
  }
  // Only a receipt anchored at PayPal "proves"; an unanchored one only "shows".
  const verb = v.integrity.status === "verified" ? "proves" : "shows";
  const broken = v.diff.find((d) => !d.ok);
  const misdelivered = v.diff.find((d) => d.delivered_ok === false);
  switch (v.fault_class) {
    case "AGENT_ERROR":
      return broken
        ? `Your receipt ${verb} ${brokeIt(broken)}. That broke your instructions, so the agent provider is liable.`
        : "Your agent broke your instructions, so the agent provider is liable.";
    case "MERCHANT_ERROR":
      if (v.agent_flagged && broken) {
        const asked = broken.field === "price" ? `set a limit of ${limit(broken.asked)}` : `asked for ${value(broken, broken.asked)}`;
        return `Your receipt ${verb} you ${asked}. The product listing told your agent to ignore that, and it bought ${value(broken, broken.bought)}, so the merchant is at fault.`;
      }
      if (misdelivered) {
        return `Your receipt ${verb} your agent ordered ${value(misdelivered, misdelivered.bought)}; the merchant shipped ${value(misdelivered, misdelivered.delivered ?? null)}, so the merchant is at fault.`;
      }
      return `Your receipt ${verb} your agent's order matched your request; you report receiving something different, so the merchant is at fault.`;
    case "BUYER_REMORSE": {
      const size = v.diff.find((d) => d.field === "size");
      const price = v.diff.find((d) => d.field === "price");
      const parts = [size && value(size, size.bought), price && `${money(price.bought)} within your ${limit(price.asked)} limit`].filter(Boolean);
      return `Your receipt ${verb} your agent bought exactly what you asked for${parts.length ? ` (${parts.join(", ")})` : ""}. The merchant's normal return policy still applies.`;
    }
    default:
      return v.outcome;
  }
}
