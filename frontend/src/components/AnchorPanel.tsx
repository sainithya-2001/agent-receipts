"use client";

import { useContext } from "react";
import { ANCHOR_LABEL, anchorState, anchorSummary } from "@/api/anchor";
import type { Receipt } from "@/api/types";
import { AnchorCtx } from "./AnchorContext";

/** Our recomputed intent hash against the invoice_id PayPal stored on the order. */
export function AnchorPanel({ receipt }: { receipt: Receipt }) {
  const { anchor, failed, chain } = useContext(AnchorCtx);
  const captureId = (receipt.events.find((e) => e.kind === "paypal_captured")?.capture_id as string | undefined) ?? null;

  // blocked by the guard and never ordered: nothing to anchor, and nothing went wrong
  const blockedNoOrder = receipt.events.some((e) => e.kind === "purchase_blocked") &&
    !receipt.events.some((e) => e.kind === "paypal_order_created");
  const state = anchorState(anchor, failed, blockedNoOrder);
  const hashesDiffer = state === "mismatch" && anchor?.intent_hash !== anchor?.invoice_id;
  const customId = anchor?.custom_id ?? null;
  const customMatches = customId !== null && customId === receipt.receipt_id; // tick only on an exact match
  return (
    <section className={`anchor ${state}`} aria-label="PayPal anchor">
      <div className="anchor-head">
        <div>
          <span className="eyebrow">PayPal anchor</span>
          <div className="anchor-state">{ANCHOR_LABEL[state]}</div>
        </div>
        <div className="anchor-note">
          <p>{anchorSummary(state, anchor, blockedNoOrder)}</p>
          {state === "mismatch" && chain && (
            <p className="anchor-chain">
              {chain.ok
                ? "The ledger's own hash chain still verifies; only PayPal's independent copy exposes the edit."
                : `The ledger's own hash chain is broken too (at record ${chain.bad_seq}).`}
            </p>
          )}
        </div>
      </div>
      <dl className="anchor-grid">
        <dt>Intent hash <em>recomputed from the stored receipt</em></dt>
        <dd className={`mono${hashesDiffer ? " bad" : ""}`}>{anchor?.intent_hash ?? (blockedNoOrder ? receipt.hash : "—")}</dd>
        {!blockedNoOrder && (<>
        <dt>PayPal invoice_id <em>stored with the order</em></dt>
        <dd className={`mono${hashesDiffer ? " bad" : ""}`}>
          {anchor?.invoice_id ?? (anchor ? "none: order predates anchoring" : "—")}
        </dd>
        <dt>Order id</dt>
        <dd className="mono">{anchor?.order_id ?? "—"}</dd>
        <dt>Capture id</dt>
        <dd className="mono">{anchor?.capture_id ?? captureId ?? "—"}</dd>
        <dt>PayPal custom_id <em>should be this receipt</em></dt>
        <dd className={`mono${customId !== null && !customMatches ? " bad" : ""}`}>
          {customId ?? "—"}{customMatches ? "  ✓" : ""}
          {customId !== null && !customMatches ? "  ✗ not this receipt" : ""}
        </dd>
        </>)}
      </dl>
    </section>
  );
}
