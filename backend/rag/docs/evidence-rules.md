# Responding to a dispute: evidence rules

How the evidence agent answers a dispute through the PayPal Disputes API.

## ER-1 Accept the claim when the buyer is right
When the verdict is AGENT_ERROR or MERCHANT_ERROR, the buyer is owed a refund. Accept the claim with a short note that says why, so the buyer is refunded without a long dispute. Do not argue a claim the receipt shows the buyer should win.

## ER-2 Provide evidence when the purchase matched the mandate
When the verdict is BUYER_REMORSE, submit evidence. The strongest evidence is the receipt of intent: what the buyer asked for, what the agent chose and why, and the price paid, with the receipt hash that PayPal holds as the order's invoice_id. Mention the merchant's return policy so the buyer knows they can still return the item.

## ER-3 What a good evidence note contains
Keep it factual and short (PayPal notes are limited to 2000 characters). State the buyer's request, each constraint and whether the purchase met it, the order and capture ids, the receipt id and hash, and the rule the decision rests on. Do not include the buyer's personal details beyond what PayPal already shows.

## ER-4 Evidence type in the sandbox
Submit text evidence with evidence type OTHER. PayPal's sandbox tests expect it, and it fits a receipt that is not a tracking number or a refund proof.

## ER-5 Escalate before evidence
If the dispute is still an inquiry and the seller wants to contest it, escalate it to a claim first. Evidence can be provided once PayPal offers the provide_evidence action.
