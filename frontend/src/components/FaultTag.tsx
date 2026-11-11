import { FAULT_LABEL } from "@/api/format";
import type { FaultClass, IntegrityStatus } from "@/api/types";

export const faultTone = (fault: FaultClass | null | undefined, status?: string | null) =>
  status === "needs_review" ? "review"
    : fault === "AGENT_ERROR" ? "agent"
    : fault === "MERCHANT_ERROR" ? "merchant"
    : fault === "BUYER_REMORSE" ? "remorse"
    : "none";

export function FaultTag({ fault, status }: { fault: FaultClass | null | undefined; status?: string | null }) {
  const tone = faultTone(fault, status);
  const label = status === "needs_review" ? "Needs review" : fault ? FAULT_LABEL[fault] : "Not adjudicated";
  return <span className={`tag ${tone}`}>{label}</span>;
}

const INTEGRITY_LABEL: Record<IntegrityStatus, string> = {
  verified: "anchor verified",
  unanchored: "not anchored",
  failed: "altered after purchase",
};

/** check is which check decided it: "anchor" (PayPal's copy of the hash) or "chain" (the ledger). */
export function IntegrityTag({ status, check, compact = false }: {
  status: IntegrityStatus | null | undefined; check?: string | null; compact?: boolean;
}) {
  if (!status) return <span className="tag none">—</span>;
  let label = INTEGRITY_LABEL[status];
  if (status === "failed" && check === "chain") label = "chain broken";
  else if (status === "failed" && check === "anchor_unavailable") label = compact ? "unchecked" : "PayPal check unavailable";
  else if (compact && status === "failed") label = "altered";
  else if (compact && status === "verified") label = "verified";
  return <span className={`tag ${status}${status === "failed" ? " solid" : ""}`}>{label}</span>;
}
