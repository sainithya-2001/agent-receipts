import type { Receipt } from "./types";

/** A thread message shorter than this ("no") isn't a complaint on its own. */
const SHORT = 20;

/**
 * What the buyer is complaining about. PayPal's buyer form keeps the real account in the disputed
 * item's notes, so when the thread message is short or empty the notes are shown as the complaint.
 * Notes come from the dispute_opened event, or (disputes opened before we recorded them) from the
 * paypal_outcome event for the same dispute.
 */
export function complaintOf(
  receipt: Receipt, disputeId: string | null | undefined, messages: string[] | undefined,
): { text: string; thread: string[] }[] {
  const thread = (messages ?? []).filter(Boolean);
  const notes = receipt.events
    .filter((e) => (e.kind === "dispute_opened" || e.kind === "paypal_outcome") && (!disputeId || e.dispute_id === disputeId))
    .flatMap((e) => (e.item_notes as string[] | undefined) ?? []);
  const note = notes.find(Boolean);
  const thin = thread.every((m) => m.trim().length < SHORT);
  if (note && thin) return [{ text: note, thread }];
  return thread.map((m) => ({ text: m, thread: [] }));
}
