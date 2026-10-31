"use client";

import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { shortHash } from "@/api/format";
import type { ChainStatus } from "@/api/types";

/** Ledger health in the top bar: every hash and link rechecked on load. */
export function ChainPill() {
  const [chain, setChain] = useState<ChainStatus | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const load = () => api.chain().then(setChain, () => setFailed(true));
    load();
    window.addEventListener("ledger-changed", load);
    return () => window.removeEventListener("ledger-changed", load);
  }, []);

  if (failed) return <span className="chain-pill bad"><span className="dot" />backend unreachable</span>;
  if (!chain) return <span className="chain-pill"><span className="dot" />checking chain…</span>;
  return (
    <span className={`chain-pill ${chain.ok ? "" : "bad"}`} title={chain.error ?? `head ${chain.head_hash}`}>
      <span className="dot" />
      {chain.ok ? "chain verified" : `chain broken at #${chain.bad_seq}`} · {chain.records} records · {shortHash(chain.head_hash, 8)}
    </span>
  );
}
