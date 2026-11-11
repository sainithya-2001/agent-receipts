// The authorization sentence is built from the constraints the buyer confirms. Those same
// constraints are sent to /shop and recorded in the receipt, so what is shown here is exactly
// what the agent is bound by (no separate client-side reading of the request).
import type { MandateConstraints } from "./types";

const symbol = (currency: string) => (currency === "USD" ? "$" : `${currency} `);

/** Missing parts are said plainly rather than guessed. */
export function authorizationText(c: MandateConstraints): string {
  const spend = c.max_price != null ? `up to ${symbol(c.currency)}${c.max_price.toFixed(2)}` : "an amount you haven't set";
  const what = c.category ? ` on ${c.category}` : "";
  const size = c.size ? `, size ${c.size}` : "";
  return `I authorize this agent to spend ${spend}${what}${size}.`;
}

/** A spend limit is the one thing that must be present for there to be something to authorize. */
export const canAuthorize = (c: MandateConstraints | null) => c !== null && c.max_price != null;
