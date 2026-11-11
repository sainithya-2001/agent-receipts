"""Demo product catalog. The data is fake and fixed so demo scenarios are deterministic.

p_08's description contains a prompt injection on purpose (scenario 3).
"""
import json
from functools import lru_cache
from pathlib import Path

PRODUCTS_PATH = Path(__file__).with_name("products.json")


@lru_cache
def load_products() -> tuple[dict, ...]:
    return tuple(json.loads(PRODUCTS_PATH.read_text(encoding="utf-8")))


def get_product(product_id: str) -> dict | None:
    return next((p for p in load_products() if p["product_id"] == product_id), None)


def listing_snapshot(product_id: str | None, size: str | None = None) -> dict | None:
    """The chosen listing as the agent saw it, for the receipt (agent_run.chosen_listing): name,
    category, price, currency, description and attributes. The catalog can change later; the
    receipt keeps this copy, so a dispute is judged against what was actually listed."""
    product = get_product(product_id) if product_id else None
    if product is None:
        return None
    return {
        "product_id": product["product_id"], "name": product["name"], "category": product["category"],
        "price": product["price"], "currency": product["currency"], "description": product["description"],
        "attrs": {"brand": product["brand"], "size": size, "sizes": list(product["sizes"])},
    }


def search_products(
    query: str | None = None,
    category: str | None = None,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """Filter the catalog. Every filter is optional; matching is case-insensitive."""
    results = []
    for p in load_products():
        if category and p["category"].lower() != category.lower():
            continue
        if size and size not in p["sizes"]:
            continue
        if max_price is not None and p["price"] > max_price:
            continue
        if query:
            haystack = f"{p['name']} {p['brand']} {p['category']}".lower()
            if not all(word in haystack for word in query.lower().split()):
                continue
        results.append(p)
    return results
