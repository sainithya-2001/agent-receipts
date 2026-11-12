"""Pre-payment guard: check what the agent chose against the limits the buyer confirmed, before any
PayPal order exists. A violation means no PayPal call at all; the block is recorded as a
`purchase_blocked` ledger event instead.

The price checked is the catalog price, the one checkout would charge, never one the agent reported.
"""
from typing import Any


def violations(constraints: dict[str, Any], product: dict[str, Any], size: str | None) -> list[dict[str, Any]]:
    """Every confirmed limit the purchase would break: [{field, asked, attempted}]. Empty = allowed."""
    found: list[dict[str, Any]] = []
    price, currency = product["price"], product.get("currency")
    if constraints.get("size") and size != constraints["size"]:
        found.append({"field": "size", "asked": constraints["size"], "attempted": size})
    if constraints.get("max_price") is not None and price > constraints["max_price"]:
        found.append({"field": "price", "asked": f"at most {constraints['max_price']:.2f}", "attempted": f"{price:.2f}"})
    if constraints.get("currency") and currency != constraints["currency"]:
        found.append({"field": "currency", "asked": constraints["currency"], "attempted": currency})
    if constraints.get("category") and str(product.get("category", "")).lower() != str(constraints["category"]).lower():
        found.append({"field": "category", "asked": constraints["category"], "attempted": product.get("category")})
    return found
