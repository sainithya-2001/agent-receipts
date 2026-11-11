"use client";

import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { ANCHOR_LABEL, anchorState, anchorSummary } from "@/api/anchor";
import type { PayPalAnchor, Receipt } from "@/api/types";

/** Our recomputed intent hash against the invoice_id PayPal stored on the order. */
export function AnchorPanel({ receipt }: { receipt: Receipt }) {
  const [anchor, setAnchor] = useState<PayPalAnchor | null>(null);
  const [failed, setFailed] = useState(false);
  const captureId = (receipt.events.find((e) => e.kind === "paypal_captured")?.capture_id as string | undefined) ?? null;

  useEffect(() => {
    let live = true;
    setAnchor(null);
    setFailed(false);
    api.anchor(receipt.receipt_id, captureId).then(
      (a) => live && setAnchor(a),
      () => live && setFailed(true),
    );
    return () => { live = false; };
  }, [receipt.receipt_id, captureId]);

  const state = anchorState(anchor, failed);
  const hashesDiffer = state === "mismatch" && anchor?.intent_hash !== anchor?.invoice_id;
  return (
    <section className={`anchor ${state}`} aria-label="PayPal anchor">
      <div className="anchor-head">
        <div>
          <span className="eyebrow">PayPal anchor</span>
          <div className="anchor-state">{ANCHOR_LABEL[state]}</div>
        </div>
        <p className="anchor-note">{anchorSummary(state, anchor)}</p>
      </div>
      <dl className="anchor-grid">
        <dt>Intent hash <em>recomputed from the stored receipt</em></dt>
        <dd className={`mono${hashesDiffer ? " bad" : ""}`}>{anchor?.intent_hash ?? "—"}</dd>
        <dt>PayPal invoice_id <em>stored with the order</em></dt>
        <dd className={`mono${hashesDiffer ? " bad" : ""}`}>
          {anchor?.invoice_id ?? (anchor ? "none: order predates anchoring" : "—")}
        </dd>
        <dt>Order id</dt>
        <dd className="mono">{anchor?.order_id ?? "—"}</dd>
        <dt>Capture id</dt>
        <dd className="mono">{anchor?.capture_id ?? captureId ?? "—"}</dd>
        <dt>PayPal custom_id <em>should be this receipt</em></dt>
        <dd className={`mono${anchor && !anchor.custom_id_ok && anchor.order_id ? " bad" : ""}`}>
          {anchor?.custom_id ?? "—"}{anchor?.custom_id_ok ? "  ✓" : ""}
        </dd>
      </dl>
    </section>
  );
}
