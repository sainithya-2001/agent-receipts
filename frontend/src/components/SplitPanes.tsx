"use client";

import { useState } from "react";
import { api, ApiError } from "@/api/client";
import { buyerReason } from "@/api/explain";
import { humanize, money, reasonLabel, when } from "@/api/format";
import type { LedgerEvent, NextStep, Product, Receipt, Verdict } from "@/api/types";
import { VerdictCard } from "./VerdictCard";
import { complaintOf } from "@/api/complaint";
import { DemoLimitNotice, demoLimitOf, type DemoLimitDetail } from "./DemoLimit";

interface Props {
  receipt: Receipt;
  product: Product | null;
  opened: (LedgerEvent & Record<string, any>) | null;
  verdict: Verdict | null;
  onChanged: () => void;
}

/** Buyer on the left, merchant on the right: the same receipt seen from both sides. */
export function SplitPanes(props: Props) {
  return (
    <section>
      <div className="section-title"><h2>Both sides</h2></div>
      <div className="split">
        <BuyerPane {...props} />
        <MerchantPane {...props} />
      </div>
    </section>
  );
}

function BuyerPane({ receipt, product, opened, verdict }: Props) {
  const run = receipt.agent_run;
  const paid = verdict?.diff.find((d) => d.field === "price")?.bought ?? product?.price;
  const refund = verdict?.status === "decided" && verdict.fault_class !== "BUYER_REMORSE";
  // A refund is only real once the merchant's accept_claim has been filed with PayPal.
  const refunded = receipt.events.find((e) =>
    e.kind === "dispute_response" && e.dispute_id === opened?.dispute_id && e.action === "accept_claim");
  return (
    <div className="pane buyer">
      <div className="pane-head"><span className="who" /><h3>Buyer</h3></div>

      <span className="eyebrow">What I asked for</span>
      <p className="quote">{receipt.mandate.request_text}</p>

      <div className="block">
        <span className="eyebrow">What my agent bought</span>
        <dl className="kv">
          <dt>Item</dt><dd>{product?.name ?? run.chosen_product_id} <span className="mono muted">{run.chosen_product_id}</span></dd>
          <dt>Size</dt><dd className="mono">{run.chosen_size ?? "—"}</dd>
          <dt>Charged</dt><dd className="mono">{money(paid)}</dd>
        </dl>
      </div>

      {opened && (
        <div className="block">
          <span className="eyebrow">My complaint · {reasonLabel(opened.reason)}</span>
          {complaintOf(receipt, opened.dispute_id, opened.buyer_messages).map((c, i) => (
            <div key={i}>
              <p className="quote small">{c.text}</p>
              {c.thread.length > 0 && <p className="muted" style={{ fontSize: 12, margin: "2px 0 0" }}>Thread message: {c.thread.map((t) => `“${t}”`).join(", ")}</p>}
            </div>
          ))}
        </div>
      )}

      {verdict && (
        <div className="block">
          <span className="eyebrow">What happens to my money</span>
          {verdict.status !== "decided" ? (
            <div className="outcome-box">
              <strong>Under manual review</strong>
              <div className="why">{buyerReason(verdict)}</div>
            </div>
          ) : refund ? (
            <div className="outcome-box refund">
              <div className="status">
                {refunded ? `Filed with PayPal · ${when(refunded.at)}` : "Adjudicator's recommendation · not yet filed with PayPal"}
              </div>
              <strong>{refunded ? `Refunded ${money(paid)}` : `Recommended: refund of ${money(paid)}`}</strong>
              <div className="why">{buyerReason(verdict)}</div>
            </div>
          ) : (
            <div className="outcome-box norefund">
              <strong>No refund through the dispute</strong>
              <div className="why">{buyerReason(verdict)}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MerchantPane({ opened, verdict, onChanged }: Props) {
  return (
    <div className="pane merchant">
      <div className="pane-head"><span className="who" /><h3>Merchant console</h3></div>
      {!opened ? (
        <div className="note-box">No dispute has been opened on this receipt.</div>
      ) : !verdict ? (
        <div className="note-box">Dispute <span className="mono">{opened.dispute_id}</span> is not adjudicated yet.</div>
      ) : (
        <>
          <VerdictCard verdict={verdict} />
          <div className="block">
            <PayPalStep disputeId={opened.dispute_id} simulated={!!opened.simulated} onChanged={onChanged} />
          </div>
        </>
      )}
    </div>
  );
}

/** The next PayPal Disputes API step. Checking is read-only; filing changes the dispute at PayPal. */
function PayPalStep({ disputeId, simulated, onChanged }: { disputeId: string; simulated: boolean; onChanged: () => void }) {
  const [next, setNext] = useState<NextStep | null>(null);
  const [filingEnabled, setFilingEnabled] = useState(false);
  const [filed, setFiled] = useState<{ action: string; note: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState<DemoLimitDetail | null>(null);

  if (simulated) {
    return (
      <div className="paypal-step">
        <span className="eyebrow">PayPal response</span>
        <div style={{ marginTop: 4 }}>Simulated dispute: seeded for the demo, so nothing is filed with PayPal.</div>
      </div>
    );
  }

  const run = async (file: boolean) => {
    if (file && next?.action &&
        !window.confirm(`File "${next.action}" on ${disputeId} with PayPal? This changes the dispute and can't be undone.`)) return;
    setBusy(true);
    setError(null);
    setLimit(null);
    try {
      const res = await api.resolve(disputeId, file);
      setNext(res.next ?? null);
      setFilingEnabled(res.filing_enabled === true);
      if (res.filed) {
        setFiled(res.filed);
        onChanged();
        window.dispatchEvent(new Event("ledger-changed"));
      }
    } catch (e) {
      const capped = demoLimitOf(e);
      if (capped) setLimit(capped);
      else setError(e instanceof ApiError ? JSON.stringify(e.detail, null, 1) : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="paypal-step">
      <span className="eyebrow">PayPal response</span>
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn small" disabled={busy} onClick={() => run(false)}>
          {busy && !next ? "Checking…" : "Check PayPal"}
        </button>
        {next && (
          <>
            <span>
              {!next.action ? "Nothing to file."
                : filingEnabled ? <>Next: <span className="mono">{next.action}</span></>
                : <>Would file: <span className="mono">{next.action}</span></>}
            </span>
            <span className="muted" style={{ fontSize: 13 }}>{next.why}</span>
          </>
        )}
        {next?.action && filingEnabled && (
          <button className="btn small danger" style={{ marginLeft: "auto" }} disabled={busy} onClick={() => run(true)}>
            File {humanize(next.action).toLowerCase()}
          </button>
        )}
      </div>
      {next?.action && !filingEnabled && (
        <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>
          Filing is disabled on this server (FILING_ENABLED=false). Nothing will be sent to PayPal.
        </div>
      )}
      {filed && (
        <>
          <div style={{ marginTop: 10 }}>Filed <span className="mono">{filed.action}</span>.</div>
          {filed.note && <div className="filed-note">{filed.note}</div>}
        </>
      )}
      {error && <div className="error-box" style={{ marginTop: 10 }}>{error}</div>}
      {limit && <div style={{ marginTop: 10 }}><DemoLimitNotice detail={limit} /></div>}
    </div>
  );
}
