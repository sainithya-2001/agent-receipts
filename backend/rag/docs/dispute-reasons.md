# PayPal dispute reasons and how we read them

PayPal reports a reason code with every dispute. The code is chosen by the buyer from a menu, so it is a hint, not a finding. The buyer's own message usually says more.

## DR-1 Item not as described
Reason MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED. The buyer says the item differs from what was described or ordered. PayPal's buyer menu files "wrong size" here (under "I got a different item", then "Size"); the size detail is only in the buyer's message. Compare the buyer's claim with the receipt: if the receipt shows the agent chose the wrong item, it is an agent error; if the receipt shows the right item, the merchant shipped or listed the wrong thing.

## DR-2 Item not received
Reason MERCHANDISE_OR_SERVICE_NOT_RECEIVED. The buyer says nothing arrived. The receipt cannot prove delivery; the merchant needs tracking or proof of delivery. Without it, the buyer is normally refunded.

## DR-3 Unauthorized or wrong amount
Reasons UNAUTHORISED and INCORRECT_AMOUNT. The buyer says they did not approve the payment, or not this amount. For agent purchases, check the price against the mandate's maximum: a charge above the buyer's limit was not authorized by the mandate, which is an agent error.

## DR-4 Other reasons
Reasons such as OTHER, CREDIT_NOT_PROCESSED or DUPLICATE_TRANSACTION. Read the buyer's message to find the actual complaint, then apply the agent liability rules.

## DR-5 Dispute stages
A dispute starts as an inquiry, where buyer and seller can message each other and the seller can accept the claim or make an offer. It can be escalated to a claim, where PayPal decides. In the sandbox, the seller can provide evidence once the claim stage is reached, and PayPal's adjudicate action becomes available some time after evidence is submitted.
