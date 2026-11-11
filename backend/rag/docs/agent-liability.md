# Agent purchase liability rules

These rules decide who is at fault when a purchase made by an AI shopping agent is disputed. They are the house rules of the Agent Receipts ledger, applied by the adjudicator.

## AL-1 The mandate is the contract
The buyer's request, as logged in the receipt of intent before payment, is the mandate. The agent may only buy an item that meets every constraint in it: category, size, maximum price and currency. A purchase is judged against the mandate in the receipt, not against what anyone says later.

## AL-2 Agent error: purchase breaks the mandate
If the item the agent chose breaks any mandate constraint (wrong size, price above the maximum, wrong category or currency), the fault class is AGENT_ERROR. The buyer is refunded in full, and the agent provider is identified as responsible for the mandate breach. Recovering costs from the provider is a separate process, outside this dispute. The buyer does not have to return the item at their own cost.

## AL-3 Merchant error: listing manipulated the agent
If the chosen product's listing contains text that tries to instruct shopping agents (for example telling them to ignore the budget or buy a premium item), and the agent's purchase broke the mandate in the direction that text pushed, the fault class is MERCHANT_ERROR. The merchant is at fault for the manipulation, the buyer is refunded, and the agent is flagged for following untrusted listing text.

## AL-4 Merchant error: wrong item delivered
If the agent's choice met the mandate but the buyer reports receiving something different from what the receipt shows was ordered (another size, another model, a damaged or missing item), the fault class is MERCHANT_ERROR. The merchant fulfilled the order incorrectly, and the buyer is refunded.

## AL-5 Buyer remorse: purchase matches the mandate
If the agent's choice met every mandate constraint, PayPal charged the listed price, and the buyer does not report a delivery problem (they changed their mind, no longer want it, or found it cheaper elsewhere), the fault class is BUYER_REMORSE. The merchant submits the receipt as evidence and should win the dispute. The buyer can still use the merchant's normal return policy.

## AL-6 Receipt integrity comes first
A receipt can only be relied on if the ledger chain verifies and the receipt's hash matches the invoice_id PayPal holds for the order. If the hash does not match, the receipt was changed after payment and must not be used to decide fault; the dispute goes to manual review. A receipt from before anchoring existed has no invoice_id to compare; it may be used, but the verdict must say it was not anchored.
