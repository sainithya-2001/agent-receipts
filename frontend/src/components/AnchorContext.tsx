"use client";

import { createContext, useEffect, useState } from "react";
import { api } from "@/api/client";
import type { ChainStatus, PayPalAnchor, Receipt } from "@/api/types";

export interface AnchorCheck {
  anchor: PayPalAnchor | null;
  failed: boolean; // the check itself could not run (PayPal unreachable, no credentials)
  chain: ChainStatus | null; // the ledger's own hash-chain verification
}

/** One PayPal anchor check per receipt page, shared by the anchor panel and the timeline. */
export const AnchorCtx = createContext<AnchorCheck>({ anchor: null, failed: false, chain: null });

export function useAnchorCheck(receipt: Receipt | null): AnchorCheck {
  const [state, setState] = useState<AnchorCheck>({ anchor: null, failed: false, chain: null });
  const id = receipt?.receipt_id;
  const captureId = (receipt?.events.find((e) => e.kind === "paypal_captured")?.capture_id as string | undefined) ?? null;

  useEffect(() => {
    if (!id) return;
    let live = true;
    setState({ anchor: null, failed: false, chain: null });
    api.anchor(id, captureId).then(
      (anchor) => live && setState((s) => ({ ...s, anchor })),
      () => live && setState((s) => ({ ...s, failed: true })),
    );
    api.chain().then((chain) => live && setState((s) => ({ ...s, chain })), () => {});
    return () => { live = false; };
  }, [id, captureId]);

  return state;
}
