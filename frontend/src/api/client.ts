import type { PayPalAnchor, ShopJob, ChainStatus, Product, QueueRow, Receipt, ResolveResult, Scenario, StoredMandate } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { cache: "no-store", ...init });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body as T;
}

export const api = {
  queue: (live = true) => call<{ items: QueueRow[]; paypal_error: string | null }>(`/disputes?live=${live}`),
  receipt: (id: string) => call<Receipt>(`/receipts/${encodeURIComponent(id)}`),
  anchor: (id: string, captureId?: string | null) =>
    call<PayPalAnchor>(`/receipts/${encodeURIComponent(id)}/paypal-anchor${captureId ? `?capture_id=${encodeURIComponent(captureId)}` : ""}`),
  parseMandate: (requestText: string) =>
    call<StoredMandate>("/mandate/parse", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_text: requestText }),
    }),
  // The confirm token comes from /mandate/parse and is kept in page memory only (a per-mandate capability, not a buyer identity or account control).
  confirmMandate: (mandateId: string, confirmToken: string) =>
    call<StoredMandate>(`/mandate/${encodeURIComponent(mandateId)}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_token: confirmToken }),
    }),
  // /shop takes only the id of a mandate the buyer confirmed (plus its token); the server holds the request and limits.
  startShop: (mandateId: string, confirmToken: string) =>
    call<{ job_id: string }>("/shop", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mandate_id: mandateId, confirm_token: confirmToken, background: true }),
    }),
  shopJob: (jobId: string) => call<ShopJob>(`/shop/jobs/${encodeURIComponent(jobId)}`),
  catalog: () => call<Product[]>("/catalog"),
  chain: () => call<ChainStatus>("/ledger/verify"),
  scenarios: () => call<Scenario[]>("/scenarios"),
  runScenario: (name: string) =>
    call<ResolveResult & { scenario: string; receipt: Receipt }>(`/scenarios/${name}/run`, { method: "POST" }),
  resolve: (disputeId: string, file = false) =>
    call<ResolveResult>(`/disputes/${encodeURIComponent(disputeId)}/resolve?file=${file}`, { method: "POST" }),
};
