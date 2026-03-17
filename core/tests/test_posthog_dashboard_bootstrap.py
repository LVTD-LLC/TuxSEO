from scripts.posthog_dashboard_bootstrap import dashboard_specs


def test_dashboard_specs_cover_required_surfaces():
    specs = dashboard_specs()
    names = {spec.name for spec in specs}

    assert "TuxSEO – Operational Health (Logs + Failures)" in names
    assert "TuxSEO – Product Funnel Health" in names
    assert "TuxSEO – LLM Analytics Health" in names


def test_has_ingestion_health_tile_and_llm_cost_token_tiles():
    specs = dashboard_specs()
    all_insight_names = {insight.name for spec in specs for insight in spec.insights}

    assert "Ingestion heartbeat: key event volume (daily)" in all_insight_names
    assert "LLM token trend (sum of $ai_total_tokens)" in all_insight_names
    assert "LLM estimated cost trend (sum of $ai_total_cost_usd)" in all_insight_names
