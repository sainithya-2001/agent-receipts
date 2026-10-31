"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/api/client";
import type { QueueRow, Scenario } from "@/api/types";
import { DisputeQueue } from "@/components/DisputeQueue";
import { FaultTag } from "@/components/FaultTag";

export default function QueuePage() {
  const router = useRouter();
  const [rows, setRows] = useState<QueueRow[] | null>(null);
  const [paypalError, setPaypalError] = useState<string | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [running, setRunning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.queue().then((q) => { setRows(q.items); setPaypalError(q.paypal_error); }, (e) => setError(String(e)));
  }, []);
  useEffect(() => {
    load();
    api.scenarios().then(setScenarios, () => {});
  }, [load]);

  const seed = async (name: string) => {
    setRunning(name);
    setError(null);
    try {
      const res = await api.runScenario(name);
      window.dispatchEvent(new Event("ledger-changed"));
      router.push(`/receipts/${res.receipt_id}?dispute=${encodeURIComponent(res.dispute_id)}`);
    } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
      setRunning(null);
    }
  };

  const count = (pred: (r: QueueRow) => boolean) => rows?.filter(pred).length ?? "–";
  const fault = (f: string) => (r: QueueRow) => r.verdict?.fault_class === f;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <div className="eyebrow">Merchant console · Test Store</div>
          <h1>Dispute queue</h1>
        </div>
        <div className="spacer" />
        <p className="muted" style={{ maxWidth: 440, margin: 0, fontSize: 14 }}>
          Every purchase here was made by an AI agent that signed a receipt of intent before it paid.
          Open a dispute to replay what the agent was asked to do and what it actually did.
        </p>
      </div>

      <div className="tally">
        <div><span className="eyebrow">Disputes</span><div className="n">{rows?.length ?? "–"}</div></div>
        <div className="agent"><span className="eyebrow">Agent error</span><div className="n">{count(fault("AGENT_ERROR"))}</div></div>
        <div className="merchant"><span className="eyebrow">Merchant error</span><div className="n">{count(fault("MERCHANT_ERROR"))}</div></div>
        <div className="remorse"><span className="eyebrow">Buyer remorse</span><div className="n">{count(fault("BUYER_REMORSE"))}</div></div>
        <div><span className="eyebrow">Awaiting verdict</span><div className="n">{count((r) => !r.verdict || r.verdict.status !== "decided")}</div></div>
      </div>

      <div className="section-title">
        <h2>Break the agent</h2>
        <span className="muted" style={{ fontSize: 13 }}>
          Each run buys the item for real (PayPal sandbox, vaulted, receipt hash anchored). Only the dispute is simulated, and it is never filed.
        </span>
      </div>
      <div className="seed-strip">
        {scenarios.map((s) => (
          <div key={s.name} className={`seed ${s.expected_status === "needs_review" ? "attack" : ""}`}>
            <span className="seed-title">{s.title}</span>
            <span className="seed-breaks">{s.breaks}</span>
            <span className="seed-foot">
              <span>
                <span className="eyebrow" style={{ display: "block", marginBottom: 4 }}>expect</span>
                <FaultTag fault={s.expected_fault} status={s.expected_status} />
              </span>
              <button className="btn small primary" disabled={running !== null} onClick={() => seed(s.name)}>
                {running === s.name ? "Running…" : "Run ▸"}
              </button>
            </span>
          </div>
        ))}
      </div>
      {error && <div className="error-box" style={{ marginTop: 10 }}>{error}</div>}

      <div className="section-title">
        <h2>Queue</h2>
        <span className="aside" style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {paypalError && <span className="tag unanchored" title={paypalError}>PayPal list unavailable: ledger view only</span>}
          <button className="btn small" onClick={load}>Refresh</button>
        </span>
      </div>
      {rows === null ? <span className="eyebrow">Loading queue…</span> : <DisputeQueue rows={rows} onChanged={load} />}
    </main>
  );
}
