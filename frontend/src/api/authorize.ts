// Reads the limits out of a shopping request so the person sees, in plain words, what they are
// authorizing before the agent runs. This is a preview only: the agent's own reading of the
// request is what gets logged in the receipt, and the page shows that too once it exists.

export interface ParsedRequest {
  maxPrice: number | null;
  size: string | null;
  category: string | null;
}

const PRICE_AFTER_WORD =
  /(?:under|below|less than|up to|at most|no more than|within|max(?:imum)?(?: of| price)?|budget(?: of)?|<=?)\s*(?:usd\s*)?\$?\s*(\d+(?:\.\d{1,2})?)/i;
const PRICE_DOLLAR = /\$\s*(\d+(?:\.\d{1,2})?)/;
const SIZE = /\bsize\s*(\d+(?:\.\d+)?)/i;

const stem = (word: string) => word.toLowerCase().replace(/s$/, "");

export function parseRequest(text: string, categories: string[]): ParsedRequest {
  const price = text.match(PRICE_AFTER_WORD) ?? text.match(PRICE_DOLLAR);
  const lower = text.toLowerCase();
  const category = categories.find((c) => lower.includes(c.toLowerCase()) || lower.includes(c.split(" ").map(stem).join(" "))) ?? null;
  return {
    maxPrice: price ? Number(price[1]) : null,
    size: text.match(SIZE)?.[1] ?? null,
    category,
  };
}

/** The authorization sentence. Missing parts are said plainly rather than guessed. */
export function authorizationText(p: ParsedRequest): string {
  const spend = p.maxPrice != null ? `up to $${p.maxPrice.toFixed(2)}` : "an amount you haven't set";
  const what = p.category ? ` on ${p.category}` : "";
  const size = p.size ? `, size ${p.size}` : "";
  return `I authorize this agent to spend ${spend}${what}${size}.`;
}

export const canAuthorize = (text: string, p: ParsedRequest) => text.trim().length > 0 && p.maxPrice != null;
