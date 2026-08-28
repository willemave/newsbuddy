from app.services import vendor_costs


def test_estimate_vendor_cost_uses_seedream_image_pricing(monkeypatch) -> None:
    monkeypatch.setattr(
        vendor_costs,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "exa_search_request_cost_usd": 0.007,
                "exa_content_result_cost_usd": 0.001,
                "exa_summary_result_cost_usd": 0.001,
                "exa_search_included_results": 10,
                "x_posts_read_cost_usd": 0.005,
                "x_users_read_cost_usd": 0.01,
                "firecrawl_credit_cost_usd": 0.00083,
            },
        )(),
    )

    cost = vendor_costs.estimate_vendor_cost_usd(
        provider="runware",
        model="bytedance:seedream@5.0-lite",
        usage={"request_count": 1},
    )

    assert cost == 0.035
