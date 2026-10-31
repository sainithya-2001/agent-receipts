"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/api/client";
import { FAULT_LABEL, shortHash, when } from "@/api/format";
import { buildTimeline, pickVerdict } from "@/api/timeline";
import type { LedgerEvent, Product, Receipt } from "@/api/types";
import { faultTone } from "@/components/FaultTag";
import { SplitPanes } from "@/components/SplitPanes";
import { Timeline } from "@/components/Timeline";

export default function ReceiptPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const search = useSearchParams();
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [catalog, setCatalog] = useState<Product[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.receipt(id), api.catalog()]).then(
      ([r, c]) => { setReceipt(r); setCatalog(c); },
      (e) => setError(e instanceof ApiError && e.status === 404 ? `No receipt ${id} in the ledger.` : String(e)),
    );
  }, [id]);
  useEffect(load, [load]);

  const opened = useMemo(() => {
    const all = (receipt?.events ?? []).filter((e) => e.kind === "dispute_opened") as (LedgerEvent & Record<string, any>)[];
    const wanted = search.get("dispute");
    return (wanted ? all.find((e) => e.dispute_id === wanted) : all[all.length - 1]) ?? null;
  }, [receipt, search]);
  const disputeId = opened?.dispute_id ?? null;

  if (error) return <main className="page"><div className="error-box">{error}</div></main>;
  if (!receipt) return <main className="page"><span className="eyebrow">Loading receipt…</span></main>;

  const verdict = pickVerdict(receipt, disputeId);
  const frames = buildTimeline(receipt, catalog, disputeId);
  const product = catalog.find((p) => p.product_id === receipt.agent_run.chosen_product_id) ?? null;
  const tone = faultTone(verdict?.fault_class, verdict?.status);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <div className="crumbs"><Link href="/">Dispute queue</Link> / {disputeId ?? "no dispute"}</div>
          <h1 className="mono" style={{ fontSize: 28 }}>{receipt.receipt_id}</h1>
          <div className="eyebrow" style={{ marginTop: 4 }}>
            signed {when(receipt.created_at)} · hash {shortHash(receipt.hash, 16)}
          </div>
        </div>
      </div>

      <div className="ribbon">
        <span className={`stamp ${tone}`}>
          {verdict?.status === "needs_review" ? "Needs review" : verdict?.fault_class ? FAULT_LABEL[verdict.fault_class] : "No verdict"}
        </span>
        <span className="ribbon-ask">“{receipt.mandate.request_text}”</span>
        <span className="mono muted">{verdict?.cited_rule ? `rule ${verdict.cited_rule.id}` : ""}</span>
      </div>

      <Timeline key={`${receipt.receipt_id}-${receipt.events.length}`} frames={frames} />
      <SplitPanes receipt={receipt} product={product} opened={opened} verdict={verdict} onChanged={load} />
    </main>
  );
}
