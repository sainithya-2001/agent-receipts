import Link from "next/link";
import { ApiError } from "@/api/client";

export interface DemoLimitDetail {
  error: "demo_limit";
  limit: "minute" | "day";
  message: string;
  retry_after: number;
  example: { receipt_id: string; dispute_id: string | null } | null;
}

/** The backend's 429 for the hosted demo's run limits, or null for any other error. */
export function demoLimitOf(e: unknown): DemoLimitDetail | null {
  if (e instanceof ApiError && e.status === 429) {
    const d = e.detail as DemoLimitDetail | undefined;
    if (d?.error === "demo_limit") return d;
  }
  return null;
}

function wait(seconds: number) {
  if (seconds < 90) return `${seconds} seconds`;
  if (seconds < 5400) return `${Math.ceil(seconds / 60)} minutes`;
  return `${Math.ceil(seconds / 3600)} hours`;
}

export function DemoLimitNotice({ detail }: { detail: DemoLimitDetail }) {
  const ex = detail.example;
  const href = ex ? `/receipts/${ex.receipt_id}${ex.dispute_id ? `?dispute=${encodeURIComponent(ex.dispute_id)}` : ""}` : null;
  return (
    <div className="note-box demo-limit" role="status">
      <strong>Demo limit reached.</strong> {detail.message} Each run calls Claude and places a PayPal sandbox order,
      so the hosted demo keeps a cap. Try again in {wait(detail.retry_after)}.
      {href && <> Meanwhile, <Link href={href}>replay a completed case →</Link></>}
    </div>
  );
}
