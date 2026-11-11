import type { PayPalAnchor, ChainStatus, Product, QueueRow, Receipt, ResolveResult, Scenario } from "./types";

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
  catalog: () => call<Product[]>("/catalog"),
  chain: () => call<ChainStatus>("/ledger/verify"),
  scenarios: () => call<Scenario[]>("/scenarios"),
  runScenario: (name: string) =>
    call<ResolveResult & { scenario: string; receipt: Receipt }>(`/scenarios/${name}/run`, { method: "POST" }),
  resolve: (disputeId: string, file = false) =>
    call<ResolveResult>(`/disputes/${encodeURIComponent(disputeId)}/resolve?file=${file}`, { method: "POST" }),
};
