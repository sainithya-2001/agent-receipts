"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/api/client";
import { authorizationText, canAuthorize, parseRequest } from "@/api/authorize";
import { money } from "@/api/format";
import type { ShopJob, ShopJobEvent } from "@/api/types";
import { DemoLimitNotice, demoLimitOf, type DemoLimitDetail } from "@/components/DemoLimit";

const EXAMPLES = [
  "Buy me running shoes, size 8, under $120",
  "Get me a trail running shoe, size 9, up to $130",
];

/** One line of live progress for an event the backend reported. */
function describe(e: ShopJobEvent): { text: string; detail?: string; bad?: boolean } {
  switch (e.kind) {
    case "step": {
      const s = e.step!;
      if (s.error) return { text: `${s.tool} failed`, detail: s.error, bad: true };
      if (s.tool === "search_products") return { text: `Searched the catalog: ${s.result_ids?.length ?? 0} results` };
      if (s.tool === "get_product") return { text: `Opened listing ${s.result_ids?.[0] ?? ""}` };
      if (s.tool === "submit_choice") {
        const i = s.input ?? {};
        return { text: i.chosen_product_id ? `Chose ${i.chosen_product_id}, size ${i.chosen_size}` : "Found nothing that fits" };
      }
      return { text: s.tool };
    }
    case "logged": {
      const c = e.constraints ?? {};
      const read = [c.category, c.size ? `size ${c.size}` : null, c.max_price != null ? `max ${money(c.max_price, c.currency)}` : null]
        .filter(Boolean).join(", ");
      return { text: `Receipt of intent logged: ${e.receipt_id}`, detail: `The agent recorded your limits as: ${read || "none"}` };
    }
    case "ordering":
      return { text: "Creating the PayPal order" };
    case "ordered":
      return { text: `PayPal order ${e.checkout?.status ?? ""}`.trim() };
  }
}

export default function TaskPage() {
  const router = useRouter();
  const [text, setText] = useState(EXAMPLES[0]);
  const [categories, setCategories] = useState<string[]>([]);
  const [job, setJob] = useState<ShopJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState<DemoLimitDetail | null>(null);
  const jobId = useRef<string | null>(null);

  useEffect(() => {
    api.catalog().then((c) => setCategories([...new Set(c.map((p) => p.category))]), () => {});
  }, []);

  const parsed = useMemo(() => parseRequest(text, categories), [text, categories]);
  const ok = canAuthorize(text, parsed);
  const busy = starting || job?.status === "running";

  // Poll the run until it finishes, then go to its receipt (unless the buyer must approve at PayPal).
  const polling = job?.job_id;
  const status = job?.status;
  useEffect(() => {
    if (!polling || status !== "running") return;
    const timer = setInterval(() => {
      api.shopJob(polling).then(setJob, (e) => { setError(String(e.message ?? e)); clearInterval(timer); });
    }, 700);
    return () => clearInterval(timer);
  }, [polling, status]);

  const approveUrl = job?.result?.checkout?.approve_url;
  const receiptId = job?.result?.receipt_id;
  useEffect(() => {
    if (status === "done" && receiptId && !approveUrl) {
      const t = setTimeout(() => router.push(`/receipts/${receiptId}`), 1500);
      return () => clearTimeout(t);
    }
  }, [status, receiptId, approveUrl, router]);

  async function authorize() {
    setError(null); setLimit(null); setJob(null); setStarting(true);
    try {
      const { job_id } = await api.startShop(text.trim());
      jobId.current = job_id;
      setJob({ job_id, status: "running", events: [], result: null, error: null });
    } catch (e) {
      const d = demoLimitOf(e);
      if (d) setLimit(d); else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <div className="eyebrow">Buyer</div>
          <h1>Give your agent a task</h1>
        </div>
        <div className="spacer" />
        <p className="muted" style={{ maxWidth: 440, margin: 0, fontSize: 14 }}>
          The agent must log a receipt of intent before it can pay. Whatever limits it records there are
          what a later dispute is judged against.
        </p>
      </div>

      <section className="task">
        <label className="eyebrow" htmlFor="task-text">What should your agent buy?</label>
        <textarea
          id="task-text" className="task-text" rows={2} value={text} disabled={busy}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="task-examples">
          {EXAMPLES.map((x) => (
            <button key={x} type="button" className="chip-btn" disabled={busy} onClick={() => setText(x)}>{x}</button>
          ))}
        </div>

        <div className={`authorization${ok ? "" : " incomplete"}`}>
          <span className="eyebrow">Your authorization</span>
          <p className="auth-text">{authorizationText(parsed)}</p>
          <ul className="auth-parts">
            <li><span>Spend limit</span><b>{parsed.maxPrice != null ? money(parsed.maxPrice) : "not found"}</b></li>
            <li><span>Category</span><b>{parsed.category ?? "any"}</b></li>
            <li><span>Size</span><b>{parsed.size ?? "any"}</b></li>
          </ul>
          <p className="muted" style={{ fontSize: 13, margin: "8px 0 0" }}>
            {ok
              ? "This is how your request reads to us. The agent's own reading is logged in the receipt, and shown below once it exists. PayPal sandbox only: no real money."
              : "Add a spend limit, such as “under $120”: without one there is nothing to authorize."}
          </p>
          <div style={{ marginTop: 14 }}>
            <button className="btn primary" type="button" onClick={authorize} disabled={!ok || busy}>
              {busy ? "Agent is working…" : "Authorize"}
            </button>
          </div>
        </div>

        {limit && <DemoLimitNotice detail={limit} />}
        {error && <div className="error-box">{error}</div>}
      </section>

      {job && (
        <section className="progress" aria-live="polite">
          <div className="section-title">
            <h2>Live progress</h2>
            <span className="aside muted mono">{job.status === "running" ? "running" : job.status}</span>
          </div>
          <ol>
            <li className="done"><span className="dot" />Request received</li>
            {job.events.map((e, i) => {
              const d = describe(e);
              return (
                <li key={i} className={d.bad ? "bad" : "done"}>
                  <span className="dot" />{d.text}
                  {d.detail && <div className="muted detail">{d.detail}</div>}
                </li>
              );
            })}
            {job.status === "running" && <li className="active"><span className="dot" />Working…</li>}
          </ol>
          {job.status === "error" && <div className="error-box">{job.error}</div>}
          {job.status === "done" && receiptId && (
            <div className="note-box">
              {approveUrl ? (
                <>The order needs your approval at PayPal first. <a href={approveUrl}>Approve in PayPal →</a> Receipt:{" "}
                  <Link href={`/receipts/${receiptId}`}>{receiptId}</Link></>
              ) : (
                <>Done. Opening the receipt… <Link href={`/receipts/${receiptId}`}>{receiptId}</Link></>
              )}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
