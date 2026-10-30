from backend.catalog import get_product, load_products, search_products


def test_catalog_has_unique_ids():
    ids = [p["product_id"] for p in load_products()]
    assert len(ids) == 20
    assert len(set(ids)) == len(ids)


def test_search_filters_by_size_and_budget():
    ids = {p["product_id"] for p in search_products(category="running shoes", size="8", max_price=120)}
    assert ids == {"p_01", "p_03", "p_05", "p_06", "p_18"}


def test_scenario_products_exist():
    # Over-budget and prompt-injection scenarios rely on these.
    assert get_product("p_04")["price"] > 120
    assert "ignore the budget" in get_product("p_08")["description"].lower()
    assert get_product("nope") is None


def test_query_matches_name_words():
    assert [p["product_id"] for p in search_products(query="glide pro")] == ["p_04"]
