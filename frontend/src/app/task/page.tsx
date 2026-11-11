"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { authorizationText, canAuthorize } from "@/api/authorize";
import { money } from "@/api/format";
import type { ShopJob, ShopJobEvent, StoredMandate } from "@/api/types";
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
      return { text: `Receipt of intent logged: ${e.receipt_id}`, detail: `Recorded in the receipt exactly as you confirmed: ${read || "none"}` };
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
  const [parsed, setParsed] = useState<StoredMandate | null>(null); // what /mandate/parse read and stored, for this text
  const confirmed = parsed?.constraints ?? null;
  const [reading, setReading] = useState(false);
  const [job, setJob] = useState<ShopJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState<DemoLimitDetail | null>(null);
  const jobId = useRef<string | null>(null);

  const ok = canAuthorize(confirmed);
  const busy = starting || job?.status === "running";

  const edit = (next: string) => { setText(next); setParsed(null); setError(null); setLimit(null); };

  // Claude reads the request into constraints. Those exact constraints are what the buyer confirms,
  // what the server stores under a mandate_id, and what the receipt records once the buyer confirms.
  async function readRequest(request = text) {
    setError(null); setLimit(null); setParsed(null); setReading(true);
    try {
      setParsed(await api.parseMandate(request.trim()));
    } catch (e) {
      const d = demoLimitOf(e);
      if (d) setLimit(d); else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReading(false);
    }
  }

  // /task?q=<request>&read=1 prefills the request and reads it straight away (handy for demos).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("q");
    if (q) {
      setText(q);
      if (params.get("read") === "1") void readRequest(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    if (!parsed) return;
    setError(null); setLimit(null); setJob(null); setStarting(true);
    try {
      // Authorize is the confirmation: the server stamps its time, then /shop accepts only this id.
      await api.confirmMandate(parsed.mandate_id);
      const { job_id } = await api.startShop(parsed.mandate_id);
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
          id="task-text" className="task-text" rows={2} value={text} disabled={busy || reading}
          onChange={(e) => edit(e.target.value)}
        />
        <div className="task-examples">
          {EXAMPLES.map((x) => (
            <button key={x} type="button" className="chip-btn" disabled={busy || reading} onClick={() => edit(x)}>{x}</button>
          ))}
        </div>

        <div className={`authorization${confirmed && ok ? "" : " incomplete"}`}>
          <span className="eyebrow">Your authorization</span>
          {!confirmed ? (
            <>
              <p className="auth-text muted">{reading ? "Reading your request…" : "Read your request to see exactly what you would be authorizing."}</p>
              <div style={{ marginTop: 6 }}>
                <button className="btn primary" type="button" onClick={() => readRequest()} disabled={reading || busy || !text.trim()}>
                  {reading ? "Reading…" : "Read my request"}
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="auth-text">{authorizationText(confirmed)}</p>
              <ul className="auth-parts">
                <li><span>Spend limit</span><b>{confirmed.max_price != null ? money(confirmed.max_price, confirmed.currency) : "not found"}</b></li>
                <li><span>Category</span><b>{confirmed.category ?? "any"}</b></li>
                <li><span>Size</span><b>{confirmed.size ?? "any"}</b></li>
                <li><span>Currency</span><b>{confirmed.currency}</b></li>
              </ul>
              <p className="muted" style={{ fontSize: 13, margin: "8px 0 0" }}>
                {ok
                  ? "These are the constraints the agent is given as fixed and the receipt records, exactly as shown. PayPal sandbox only: no real money."
                  : "No spend limit was found. Add one, such as “under $120”, and read the request again: without a limit there is nothing to authorize."}
              </p>
              <div style={{ marginTop: 14 }}>
                <button className="btn primary" type="button" onClick={authorize} disabled={!ok || busy}>
                  {busy ? "Agent is working…" : "Authorize"}
                </button>
              </div>
            </>
          )}
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
