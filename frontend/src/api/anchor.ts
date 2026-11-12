import type { PayPalAnchor } from "./types";

export type AnchorState = "loading" | "no_payment" | "match" | "mismatch" | "not_anchored" | "unavailable";

/** The one word the panel shows. mismatch means PayPal holds a hash (or a receipt id) that is not this receipt's. */
export function anchorState(anchor: PayPalAnchor | null, failed: boolean, blockedNoOrder = false): AnchorState {
  if (blockedNoOrder) return "no_payment"; // the guard stopped it: nothing to anchor, and nothing went wrong
  if (failed) return "unavailable";
  if (!anchor) return "loading";
  if (anchor.ok) return "match";
  if (!anchor.custom_id_ok && anchor.order_id) return "mismatch"; // the payment names another receipt
  if (anchor.anchored) return "mismatch"; // invoice_id present but not this receipt's hash
  return "not_anchored";
}

export const ANCHOR_LABEL: Record<AnchorState, string> = {
  loading: "Checking PayPal…",
  no_payment: "No payment",
  match: "Match",
  mismatch: "Mismatch",
  not_anchored: "Not anchored",
  unavailable: "PayPal check unavailable",
};

export function anchorSummary(state: AnchorState, anchor: PayPalAnchor | null, blocked = false): string {
  if (blocked) {
    return "The pre-payment guard blocked this purchase, so no PayPal order exists and nothing was charged. There is no hash at PayPal to compare.";
  }
  switch (state) {
    case "match":
      return "PayPal holds the same hash this receipt has now. It was not changed after the purchase.";
    case "mismatch":
      return anchor && !anchor.custom_id_ok && !anchor.anchored
        ? "Receipt altered after purchase, or this payment belongs to another receipt."
        : "Receipt altered after purchase: its hash no longer matches the one PayPal stored with the order.";
    case "not_anchored":
      return anchor?.order_id || anchor?.capture_id
        ? "PayPal has this order but no receipt hash on it: it was placed before anchoring existed. Nothing to compare, and nothing shows tampering."
        : "No PayPal order is linked to this receipt yet, so there is no hash at PayPal to compare.";
    case "unavailable":
      return "PayPal could not be reached, so the receipt was not checked. This says nothing about whether it was changed.";
    default:
      return "";
  }
}
