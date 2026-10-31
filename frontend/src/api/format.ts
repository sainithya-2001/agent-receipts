import type { FaultClass } from "./types";

export const FAULT_LABEL: Record<FaultClass, string> = {
  AGENT_ERROR: "Agent error",
  MERCHANT_ERROR: "Merchant error",
  BUYER_REMORSE: "Buyer remorse",
};

const REASONS: Record<string, string> = {
  MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED: "Not as described",
  MERCHANDISE_OR_SERVICE_NOT_RECEIVED: "Not received",
  INCORRECT_AMOUNT: "Incorrect amount",
  UNAUTHORISED: "Unauthorised",
  DUPLICATE_TRANSACTION: "Duplicate",
  CREDIT_NOT_PROCESSED: "Credit not processed",
  CANCELED_RECURRING_BILLING: "Cancelled billing",
  PAYMENT_BY_OTHER_MEANS: "Paid by other means",
  PROBLEM_WITH_REMITTANCE: "Remittance problem",
  OTHER: "Other",
};

export const reasonLabel = (code?: string | null) => (code ? REASONS[code] ?? humanize(code) : "—");

export const humanize = (code: string) =>
  code.toLowerCase().replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export const money = (value?: number | string | null, currency = "USD") => {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? `${currency === "USD" ? "$" : `${currency} `}${n.toFixed(2)}` : String(value);
};

export const shortHash = (h?: string | null, n = 10) => (h ? `${h.slice(0, n)}…` : "—");

export function when(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

/** Queue-width timestamp: no seconds. */
export function whenShort(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}
