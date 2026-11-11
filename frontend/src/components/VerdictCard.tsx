import type { ReactNode } from "react";
import { FAULT_LABEL } from "@/api/format";
import type { Verdict } from "@/api/types";
import { faultTone, IntegrityTag } from "./FaultTag";

function Gauge({ value }: { value: number }) {
  const on = Math.round(value * 20);
  return (
    <div className="gauge" title={`Confidence ${value.toFixed(2)}`}>
      <div className="gauge-ticks" aria-hidden>
        {Array.from({ length: 20 }, (_, i) => <i key={i} className={i < on ? "on" : ""} />)}
      </div>
      <span className="gauge-num">{value.toFixed(2)}</span>
    </div>
  );
}

const LISTING_SOURCE: Record<string, string> = {
  receipt_snapshot: "Snapshot stored in the receipt at purchase time.",
  catalog_fallback:
    "Today's catalog entry (catalog_fallback): this receipt predates listing snapshots, so the listing may have changed since.",
  unavailable: "No listing available.",
  none: "No product was chosen.",
};

function Section({ kind, title, sub, children }: { kind: "code" | "ai"; title: string; sub: string; children: ReactNode }) {
  return (
    <section className={`vsec ${kind}`}>
      <h4 className="vsec-title"><span>{title}</span><span className="vsec-sub">{sub}</span></h4>
      {children}
    </section>
  );
}

export function VerdictCard({ verdict }: { verdict: Verdict }) {
  const tone = faultTone(verdict.fault_class, verdict.status);
  const review = verdict.status === "needs_review";
  const label = review ? "Needs review" : verdict.fault_class ? FAULT_LABEL[verdict.fault_class] : "—";
  const broken = verdict.diff.filter((d) => !d.ok);
  const reported = verdict.diff.filter((d) => d.delivered != null);
  const inj = verdict.injection;
  return (
    <article className="verdict">
      <div className="verdict-head">
        <div>
          <div className="eyebrow">Ruling · {verdict.dispute_id}</div>
          <div className={`verdict-class ${tone}`}>{label}</div>
        </div>
      </div>
      {verdict.rejudged && (
        <p className="muted" style={{ fontSize: 13, margin: "0 0 10px" }}>
          <b>Re-judged.</b> This replaces the verdict at chain #{verdict.rejudged.supersedes}, which stays in the ledger.
          Reason: {verdict.rejudged.reason}
        </p>
      )}

      <Section kind="code" title="Established by code" sub="checked from the receipt and PayPal; no AI involved">
        <div className="block" style={{ marginTop: 0 }}>
          <span className="eyebrow">Mandate vs. purchase vs. buyer&apos;s report</span>
          <table className="diff">
            <thead>
              <tr><th>Field</th><th>Mandate</th><th>Purchase</th><th>Delivered (buyer reports)</th><th className="mark" /></tr>
            </thead>
            <tbody>
              {verdict.diff.map((d) => {
                const misdelivered = d.delivered_ok === false;
                return (
                  <tr key={d.field} className={d.ok ? "" : "bad"}>
                    <td>{d.field}</td><td>{d.asked ?? "—"}</td><td>{d.bought ?? "—"}</td>
                    <td className={misdelivered ? "bad-cell" : "muted"}>{d.delivered ?? "—"}</td>
                    <td className={`mark ${misdelivered ? "bad-cell" : ""}`}>{d.ok && !misdelivered ? "✓" : "✗"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <dl className="kv facts">
          <dt>PayPal confirmation</dt>
          <dd className="integrity">
            <IntegrityTag status={verdict.integrity.status} check={verdict.integrity.check} />
            <span>{verdict.integrity.note}</span>
          </dd>
          <dt>Violations</dt>
          <dd>
            {broken.length
              ? broken.map((d) => <span key={d.field} className="chip bad">{d.field}: asked {d.asked ?? "—"}, bought {d.bought ?? "—"}</span>)
              : <span className="muted">none: the purchase met every constraint</span>}
          </dd>
          <dt>Buyer&apos;s report</dt>
          <dd>
            {reported.length
              ? reported.map((d) => (
                  <span key={d.field} className={`chip ${d.delivered_ok === false ? "bad" : ""}`}>
                    {d.field} {d.delivered} arrived{d.delivered_ok === false ? ` (ordered ${d.bought})` : ""}
                  </span>
                ))
              : <span className="muted">no delivery detail stated</span>}
          </dd>
          <dt>Injection link</dt>
          <dd>
            {!inj ? (
              <span className="muted">no agent-directed text in the chosen listing</span>
            ) : (
              <>
                <span className={`chip ${inj.linked ? "bad" : ""}`}>{inj.linked ? "linked to the violation" : "not shown to have caused it"}</span>
                <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
                  “{inj.instruction}”<br />
                  agent saw this listing: {inj.seen_by_agent ? "yes" : "no"} · told it to override: {inj.instructed_fields.join(", ") || "—"} · violated: {inj.violated_fields.join(", ") || "none"}
                </div>
              </>
            )}
          </dd>
          {verdict.listing_source && (
            <>
              <dt>Listing source</dt>
              <dd>{LISTING_SOURCE[verdict.listing_source]}</dd>
            </>
          )}
        </dl>
      </Section>

      <Section kind="ai" title="AI's ruling" sub={verdict.model ? `${verdict.model}, limited to what the facts allow` : "no model call was made"}>
        {verdict.override && (
          <div className="note-box override">
            <strong>The facts overrode the model.</strong> It answered <span className="mono">{verdict.override.model_said}</span>. {verdict.override.reason}
          </div>
        )}
        {review && (
          <div className="note-box">
            <strong>Needs review: no ruling was made.</strong> A person decides this case.
          </div>
        )}
        <div className="ai-top">
          <div>
            <span className="eyebrow">Outcome</span>
            <div className="outcome-line">{verdict.outcome}</div>
            {verdict.agent_flagged && <span className="tag agent">agent flagged</span>}
          </div>
          {verdict.confidence != null && (
            <div>
              <span className="eyebrow">Confidence</span>
              <Gauge value={verdict.confidence} />
            </div>
          )}
        </div>

        {verdict.explanation && (
          <div className="block">
            <span className="eyebrow">Interpretation</span>
            <p className="explain">{verdict.explanation}</p>
            {verdict.buyer_claim && (
              <p className="muted" style={{ fontSize: 13, margin: "6px 0 0" }}>Buyer&apos;s claim, as summarised: {verdict.buyer_claim}</p>
            )}
          </div>
        )}

        {verdict.cited_rule && (
          <div className="block">
            <span className="eyebrow">Rule cited</span>
            <div className="rule-cite">
              <span className="rid">{verdict.cited_rule.id}</span>
              <span className="rtitle">{verdict.cited_rule.title}</span>
              <p>{verdict.cited_rule.text}</p>
            </div>
          </div>
        )}
      </Section>
    </article>
  );
}
