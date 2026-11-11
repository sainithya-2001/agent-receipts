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

export function VerdictCard({ verdict }: { verdict: Verdict }) {
  const tone = faultTone(verdict.fault_class, verdict.status);
  const label = verdict.status === "needs_review" ? "Needs review" : verdict.fault_class ? FAULT_LABEL[verdict.fault_class] : "—";
  return (
    <article className="verdict">
      <div className="verdict-head">
        <div>
          <div className="eyebrow">Ruling · {verdict.dispute_id}</div>
          <div className={`verdict-class ${tone}`}>{label}</div>
        </div>
        {verdict.confidence != null && <Gauge value={verdict.confidence} />}
      </div>
      <div className="verdict-body">
        <p className="explain">{verdict.explanation}</p>

        <div className="block" style={{ marginTop: 0 }}>
          <span className="eyebrow">Intent vs. purchase</span>
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

        {verdict.cited_rule && (
          <div className="block">
            <span className="eyebrow">Cited rule</span>
            <div className="rule-cite">
              <span className="rid">{verdict.cited_rule.id}</span>
              <span className="rtitle">{verdict.cited_rule.title}</span>
              <p>{verdict.cited_rule.text}</p>
            </div>
          </div>
        )}

        <div className="block">
          <span className="eyebrow">Outcome</span>
          <div style={{ fontWeight: 600 }}>{verdict.outcome}</div>
          {verdict.agent_flagged && (
            <div style={{ marginTop: 6 }}><span className="tag agent">agent flagged</span>{" "}
              <span className="muted" style={{ fontSize: 13 }}>followed instructions from a product listing</span></div>
          )}
          {verdict.injection && (
            <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>
              Listing text: “{verdict.injection.instruction}” — {verdict.injection.linked
                ? `the agent saw it, and it told the agent to override ${verdict.injection.matched_fields.join(", ")}, which is what was violated.`
                : "not shown to have caused the violation."}
            </div>
          )}
        </div>

        <div className="block">
          <span className="eyebrow">Receipt integrity</span>
          <div className="integrity"><IntegrityTag status={verdict.integrity.status} check={verdict.integrity.check} /><span>{verdict.integrity.note}</span></div>
        </div>

        {verdict.listing_source && (
          <div className="block">
            <span className="eyebrow">Listing judged against</span>
            <div style={{ fontSize: 13 }}>
              {verdict.listing_source === "receipt_snapshot"
                ? "Snapshot stored in the receipt at purchase time."
                : verdict.listing_source === "catalog_fallback"
                  ? "Today's catalog entry (catalog_fallback): this receipt predates listing snapshots, so the listing may have changed since."
                  : "No listing available."}
            </div>
          </div>
        )}

        {verdict.override && (
          <div className="block note-box">
            Model answered <span className="mono">{verdict.override.model_said}</span>; overridden. {verdict.override.reason}
          </div>
        )}
        {verdict.model && <div className="block eyebrow">adjudicated by {verdict.model}</div>}
      </div>
    </article>
  );
}
