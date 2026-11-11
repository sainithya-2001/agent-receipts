// Shapes returned by the FastAPI backend (backend/app/main.py).

export type FaultClass = "AGENT_ERROR" | "MERCHANT_ERROR" | "BUYER_REMORSE";
export type IntegrityStatus = "verified" | "unanchored" | "failed";

export interface Constraints {
  category?: string;
  size?: string;
  max_price?: number;
  currency?: string;
}

export interface AgentStep {
  tool: string;
  input: Record<string, unknown>;
  result_ids?: string[];
  deviation?: string;
}

export interface OptionConsidered {
  product_id: string;
  price: number;
  attrs?: { size?: string };
  reason?: string;
}

export interface LedgerEvent {
  kind: string;
  at: string;
  seq: number;
  hash: string;
  prev_hash: string;
  [field: string]: unknown;
}

export interface Receipt {
  receipt_id: string;
  created_at: string;
  mandate: { request_text: string; constraints: Constraints; confirmed_by_buyer_at?: string; confirmation?: string };
  agent_run: {
    model: string;
    options_considered: OptionConsidered[];
    chosen_product_id: string;
    chosen_size?: string;
    reasoning?: string;
    steps?: AgentStep[];
  };
  prev_hash: string;
  hash: string;
  events: LedgerEvent[];
}

export interface PayPalAnchor {
  ok: boolean;
  anchored: boolean;
  custom_id_ok: boolean;
  receipt_id: string;
  order_id: string | null;
  capture_id: string | null;
  intent_hash: string | null;
  invoice_id: string | null;
  custom_id?: string | null;
  error: string | null;
}

/** What the buyer confirms on the task page: the constraints /mandate/parse read from the request. */
export interface MandateConstraints {
  category: string | null;
  size: string | null;
  max_price: number | null;
  currency: string;
}

export interface ShopJobEvent {
  at: number;
  kind: "step" | "logged" | "ordering" | "ordered";
  step?: { tool: string; input?: Record<string, unknown>; result_ids?: string[]; error?: string };
  receipt_id?: string;
  constraints?: Constraints;
  chosen_product_id?: string | null;
  checkout?: { status?: string; order_id?: string; approve_url?: string; capture_id?: string };
}

export interface ShopJob {
  job_id: string;
  status: "running" | "done" | "error";
  events: ShopJobEvent[];
  result: { receipt_id: string; checkout?: { status?: string; approve_url?: string } } | null;
  error: string | null;
}

export interface DiffRow {
  field: string;
  asked: string | null;
  bought: string | null;
  ok: boolean;
  delivered?: string | null; // what the buyer says arrived (parsed from their message); absent on older verdicts
  delivered_ok?: boolean | null;
}

export interface Rule {
  id: string;
  title: string;
  text: string;
}

export interface Verdict {
  receipt_id: string;
  dispute_id: string;
  status: "decided" | "needs_review";
  fault_class: FaultClass | null;
  confidence: number | null;
  action: string | null;
  outcome: string;
  cited_rule: Rule | null;
  explanation: string;
  buyer_claim: string | null;
  diff: DiffRow[];
  violations: string[];
  integrity: { status: IntegrityStatus; check: "chain" | "anchor" | "anchor_unavailable" | null; note: string };
  agent_flagged: boolean;
  injection?: {
    instruction: string; seen_by_agent: boolean; instructed_fields: string[];
    violated_fields: string[]; matched_fields: string[]; linked: boolean;
  } | null;
  listing_source?: "receipt_snapshot" | "catalog_fallback" | "unavailable" | "none";
  override: { model_said: string; reason: string } | null;
  model: string | null;
  /** Set when this verdict was appended to replace an earlier one (which stays in the ledger). */
  rejudged?: { supersedes: number; reason: string };
}

export interface NextStep {
  action: string | null;
  why: string;
}

export interface ResolveResult {
  dispute_id: string;
  receipt_id: string | null;
  status: "adjudicated" | "filed" | "no_receipt";
  verdict?: Verdict;
  next?: NextStep;
  filed?: { action: string; note: string | null } | null;
  filing_enabled?: boolean; // FILING_ENABLED on the backend; false = show the step, never file it
  why?: string;
}

export interface QueueRow {
  dispute_id: string;
  receipt_id: string | null;
  opened_at?: string;
  reason?: string;
  status?: string;
  stage?: string;
  amount?: string;
  currency?: string;
  simulated?: boolean;
  live?: boolean;
  filed: string[];
  verdict: {
    status: string;
    fault_class: FaultClass | null;
    confidence: number | null;
    agent_flagged: boolean;
    violations: string[];
    cited_rule: string | null;
    integrity: IntegrityStatus | null;
    integrity_check: "chain" | "anchor" | "anchor_unavailable" | null;
  } | null;
}

export interface Product {
  product_id: string;
  name: string;
  category: string;
  brand: string;
  price: number;
  currency: string;
  sizes: string[];
  description: string;
  injection: string | null;
}

export interface Scenario {
  name: string;
  title: string;
  breaks: string;
  expected_fault: FaultClass | null;
  expected_status: "decided" | "needs_review";
}

export interface ChainStatus {
  ok: boolean;
  records: number;
  head_hash: string;
  bad_seq: number | null;
  error: string | null;
}

/** A parsed request the server holds under mandate_id until the buyer confirms it. */
export interface StoredMandate {
  mandate_id: string;
  request_text: string;
  constraints: MandateConstraints;
  confirmed_by_buyer_at: string | null;
}
