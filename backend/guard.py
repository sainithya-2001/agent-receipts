"""Pre-payment guard: check what the agent chose against the limits the buyer confirmed, before any
PayPal order exists. A violation means no PayPal call at all; the block is recorded as a
`purchase_blocked` ledger event instead.

The price checked is the catalog price, the one checkout would charge, never one the agent reported.
"""
from typing import Any

from backend.catalog import load_products


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


def matching_products(constraints: dict[str, Any]) -> list[tuple[dict[str, Any], str | None]]:
    """Every (product, size) the confirmed constraints allow. Without a size constraint a product
    qualifies in any of its sizes (the size is None in the pair)."""
    found = []
    for product in load_products():
        sizes = [constraints["size"]] if constraints.get("size") else [None]
        for size in sizes:
            if constraints.get("size") and size not in product["sizes"]:
                continue
            if not violations(constraints, product, size):
                found.append((product, size))
    return found


def no_match_message(constraints: dict[str, Any]) -> str:
    """Why nothing fits, in the buyer's terms, computed from the catalog (no model involved)."""
    category, size = constraints.get("category"), constraints.get("size")
    same_item = [p for p in load_products()
                 if (not category or p["category"].lower() == str(category).lower())
                 and (not size or size in p["sizes"])
                 and (not constraints.get("currency") or p.get("currency") == constraints["currency"])]
    noun = (str(category)[:-1] if str(category).endswith("s") else str(category)) if category else "item"
    if same_item and constraints.get("max_price") is not None:
        cheapest = min(p["price"] for p in same_item)
        label = f"size-{size} {noun}" if size else noun
        detail = f"cheapest {label} is ${cheapest:.2f}"
    else:
        detail = "nothing in that category and size is listed"
    return f"No product matches your limits ({detail}). Nothing was charged."
