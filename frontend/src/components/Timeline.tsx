"use client";

import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { anchorState } from "@/api/anchor";
import { money, reasonLabel, shortHash, when, humanize } from "@/api/format";
import type { Frame } from "@/api/timeline";
import type { Constraints as ConstraintsT, LedgerEvent, Product, Receipt, Verdict } from "@/api/types";
import { complaintOf } from "@/api/complaint";
import { AnchorCtx } from "./AnchorContext";
import { FaultTag } from "./FaultTag";

const STEP_MS = 520;

/** The replay: frames appear one at a time, and the replay holds on the deviating step. */
export function Timeline({ frames }: { frames: Frame[] }) {
  const [shown, setShown] = useState(frames.length);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const replay = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(frames.length);
      return;
    }
    setShown(1);
    timer.current = setInterval(() => {
      setShown((n) => {
        if (n >= frames.length) {
          if (timer.current) clearInterval(timer.current);
          return n;
        }
        return n + 1;
      });
    }, STEP_MS);
  }, [frames.length]);

  useEffect(() => {
    replay();
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [replay]);

  const deviations = frames.filter((f) => f.tone === "deviation").length;
  const steps = frames.filter((f) => f.tone !== "system").length;
  return (
    <section>
      <div className="section-title">
        <h2>Replay</h2>
        <span className="muted" style={{ fontSize: 13 }}>
          {steps} frames · {deviations ? `${deviations} deviation${deviations > 1 ? "s" : ""}` : "no deviation"}
        </span>
        <div className="aside tl-controls">
          <span className="legend">
            <span><i className="dev" />deviation</span>
            <span><i className="warn" />warning</span>
            <span><i className="ok" />resolved</span>
          </span>
          <button className="btn small" onClick={replay}>▶ Replay</button>
        </div>
      </div>
      <div className="tl">
        {frames.map((f, i) => (
          <div
            key={f.key}
            className={`tl-row ${f.tone} ${i >= shown ? "hidden" : ""} ${i === shown - 1 ? "current" : ""}`}
          >
            <div className={`tl-phase ${i > 0 && frames[i - 1].phase === f.phase ? "repeat" : ""}`}>{f.phase}</div>
            <div className="tl-stamp">{f.at ? when(f.at) : f.stamp}</div>
            <div className="tl-spine"><span className="tl-node" /></div>
            {f.tone === "system" ? (
              <details className="tl-system">
                <summary>{f.title}</summary>
                <div className="tl-body"><FrameBody frame={f} /></div>
              </details>
            ) : (
              <div className="tl-frame">
                <div className="tl-title">{f.title}</div>
                {f.flag && <div className="tl-flag">{f.flag}</div>}
                {f.notes.length > 0 && <div className="tl-notes">{f.notes.map((n) => <span key={n}>{n}</span>)}</div>}
                <div className="tl-body"><FrameBody frame={f} /></div>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function FrameBody({ frame }: { frame: Frame }) {
  const b = frame.body;
  switch (b.type) {
    case "mandate": {
      const c = b.receipt.mandate.constraints;
      return (
        <>
          <div className="serif" style={{ fontSize: 17, fontStyle: "italic", color: "var(--ink)", marginBottom: 6 }}>
            “{b.receipt.mandate.request_text}”
          </div>
          <Constraints c={c} />
          {b.receipt.mandate.confirmed_by_buyer_at && (
            <div className="eyebrow" style={{ marginTop: 8 }}>
              Confirmed by buyer · {when(b.receipt.mandate.confirmed_by_buyer_at)}
            </div>
          )}
        </>
      );
    }
    case "search": {
      const input = b.step.input as Record<string, unknown>;
      const filters = ["category", "size", "max_price"].map((k) => [k, input[k]] as const);
      return (
        <>
          <div className="chips" style={{ marginBottom: 8 }}>
            {filters.map(([k, v]) => (
              <span className="chip" key={k}><b>{k}</b>{v == null ? "none" : String(v)}</span>
            ))}
          </div>
          <div className="results">
            {b.results.map((p) => <ResultChip key={p.product_id} p={p} />)}
          </div>
        </>
      );
    }
    case "listing":
      return b.product ? <Listing product={b.product} /> : null;
    case "choice": {
      const run = b.receipt.agent_run;
      const max = b.receipt.mandate.constraints.max_price;
      const size = b.receipt.mandate.constraints.size;
      return (
        <>
          <div>
            <span className="mono">{run.chosen_product_id}</span> · size{" "}
            <span className={`mono ${size && run.chosen_size !== size ? "bad" : ""}`}>{run.chosen_size ?? "—"}</span> ·{" "}
            <span className="mono">{money(b.product?.price)}</span>
          </div>
          {run.reasoning && <div className="serif" style={{ fontSize: 15, marginTop: 6 }}>{run.reasoning}</div>}
          <table className="options">
            <thead><tr><th>Option</th><th>Size</th><th style={{ textAlign: "right" }}>Price</th><th>Agent&apos;s note</th></tr></thead>
            <tbody>
              {run.options_considered.map((o) => (
                <tr key={o.product_id} className={o.product_id === run.chosen_product_id ? "chosen" : ""}>
                  <td className="mono">{o.product_id}</td>
                  <td className={`mono ${size && o.attrs?.size && o.attrs.size !== size ? "bad" : ""}`}>{o.attrs?.size ?? "—"}</td>
                  <td className={`num ${max != null && o.price > max ? "bad" : ""}`}>{money(o.price)}</td>
                  <td>{o.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      );
    }
    case "signed":
      return (
        <div className="hash-line">
          <span>receipt</span><span>{b.receipt.receipt_id}</span>
          <span>model</span><span>{b.receipt.agent_run.model}</span>
          <span>hash</span><span>{b.receipt.hash}</span>
          <span>prev</span><span>{b.receipt.prev_hash}</span>
        </div>
      );
    case "event":
      return <EventBody event={b.event} verdict={b.verdict} receipt={b.receipt} />;
  }
}

function Constraints({ c }: { c: ConstraintsT }) {
  return (
    <div className="chips">
      {Object.entries(c).map(([k, v]) => (
        <span className="chip" key={k}><b>{k.replace("_", " ")}</b>{k === "max_price" ? money(v as number) : String(v)}</span>
      ))}
    </div>
  );
}

function ResultChip({ p }: { p: Product }) {
  return <span className="result" title={p.description}>{p.product_id} · {p.name} · {money(p.price)}</span>;
}

function Listing({ product }: { product: Product }) {
  const d = product.description;
  const i = product.injection ? d.indexOf(product.injection) : -1;
  return (
    <>
      <div style={{ marginBottom: 6 }}>
        <span className="mono">{product.product_id}</span> · {product.brand} · {money(product.price)} · sizes {product.sizes.join(", ")}
      </div>
      <div className="listing">
        {i < 0 ? d : <>{d.slice(0, i)}<mark>{product.injection}</mark>{d.slice(i + product.injection!.length)}</>}
      </div>
    </>
  );
}

function EventBody({ event, verdict, receipt }: { event: LedgerEvent; verdict?: Verdict; receipt: Receipt }) {
  const e = event as LedgerEvent & Record<string, any>;
  const { anchor, failed } = useContext(AnchorCtx);
  const tail = <div className="hash-line" style={{ marginTop: 6 }}><span>chain #{e.seq}</span><span>{shortHash(e.hash, 16)}</span></div>;
  switch (e.kind) {
    case "paypal_order_created":
      return (
        <>
          <div>
            Order <span className="mono">{e.order_id}</span> · {money(e.amount, e.currency)} · {e.status}
            {e.vaulted ? " · vaulted, no redirect" : ""}
          </div>
          {e.guard === "off" && (
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
              Pre-payment guard off: an agent platform without a guard (the order was not checked against the mandate).
            </div>
          )}
          {anchor && anchorState(anchor, failed) === "mismatch" && anchor.order_id === e.order_id ? (
            <div className="hash-line anchor-mismatch" style={{ marginTop: 6 }}>
              <span>invoice_id</span>
              <span className="bad">
                ledger says {shortHash(e.invoice_id, 12)} · PayPal holds {shortHash(anchor.invoice_id ?? "", 12)}
              </span>
            </div>
          ) : (
            <div className="hash-line" style={{ marginTop: 6 }}>
              <span>invoice_id</span>
              <span>{e.invoice_id ? `${e.invoice_id} (receipt hash sent to PayPal, as the ledger records it)` : "none: order predates anchoring"}</span>
            </div>
          )}
          {tail}
        </>
      );
    case "purchase_blocked":
      return (
        <>
          <div>
            {e.product_id} · {money(e.price, e.currency)}{e.size ? ` · size ${e.size}` : ""} was not bought.
          </div>
          {(e.violations ?? []).map((v: any, i: number) => (
            <div key={i} className="mono" style={{ fontSize: 13 }}>{v.field}: confirmed {v.asked ?? "—"}, agent attempted {v.attempted ?? "—"}</div>
          ))}
          <div className="muted" style={{ fontSize: 12.5 }}>No PayPal call was made.</div>
          {tail}
        </>
      );
    case "paypal_captured":
      return <><div>Capture <span className="mono">{e.capture_id}</span> · {e.status}</div>{tail}</>;
    case "dispute_opened":
      return (
        <>
          <div>
            <span className="mono">{e.dispute_id}</span> · {reasonLabel(e.reason)} · {money(e.amount?.value, e.amount?.currency_code)}
            {e.simulated ? <span className="tag none" style={{ marginLeft: 8 }}>simulated</span> : null}
          </div>
          {complaintOf(receipt, e.dispute_id, e.buyer_messages).map((c, i) => (
            <div key={i} style={{ marginTop: 6 }}>
              <div className="serif" style={{ fontSize: 15, fontStyle: "italic" }}>“{c.text}”</div>
              {c.thread.length > 0 && <div className="muted" style={{ fontSize: 12.5 }}>Thread message: {c.thread.map((t) => `“${t}”`).join(", ")}</div>}
            </div>
          ))}
          {tail}
        </>
      );
    case "dispute_verdict":
      return verdict ? (
        <>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <FaultTag fault={verdict.fault_class} status={verdict.status} />
            {verdict.confidence != null && <span className="mono">confidence {verdict.confidence.toFixed(2)}</span>}
            {verdict.cited_rule && <span className="mono">rule {verdict.cited_rule.id}</span>}
            {verdict.agent_flagged && <span className="tag agent">agent flagged</span>}
          </div>
          <div className="serif" style={{ fontSize: 15, marginTop: 6 }}>{verdict.outcome}</div>
          {verdict.rejudged && (
            <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
              Re-judged: replaces chain #{verdict.rejudged.supersedes}. {verdict.rejudged.reason}
            </div>
          )}
          {tail}
        </>
      ) : tail;
    case "paypal_outcome": {
      const code = String(e.outcome_code ?? "").replace(/^RESOLVED_/, "").replace(/_/g, " ").toLowerCase();
      return (
        <>
          <div><b>{code || "resolved"}</b>{e.outcome_reason ? ` · ${String(e.outcome_reason).replace(/_/g, " ").toLowerCase()}` : ""}</div>
          {e.amount_refunded && <div>Refunded <span className="mono">{money(e.amount_refunded.value, e.amount_refunded.currency_code)}</span> to the buyer</div>}
          <div className="muted" style={{ fontSize: 12.5 }}>
            Read from PayPal{e.refund_id ? <> · refund <span className="mono">{e.refund_id}</span></> : null}{e.resolved_at ? ` · ${e.resolved_at}` : ""}
          </div>
          {tail}
        </>
      );
    }
    case "dispute_response":
      return <><div>Filed <span className="mono">{e.action}</span> with PayPal</div>{tail}</>;
    case "ledger_import":
      return <><div>Loaded from <span className="mono">{e.source}</span>. {e.note}</div>{tail}</>;
    default:
      return <><div className="mono">{humanize(e.kind)}</div>{tail}</>;
  }
}
