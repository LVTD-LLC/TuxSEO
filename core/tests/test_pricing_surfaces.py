from pathlib import Path


PRICING_TEMPLATE_PATH = Path("frontend/templates/pages/pricing.html")
SETTINGS_TEMPLATE_PATH = Path("frontend/templates/pages/user-settings.html")
PROJECT_SETTINGS_TEMPLATE_PATH = Path("frontend/templates/project/project_settings.html")
PROJECT_COMPETITORS_TEMPLATE_PATH = Path("frontend/templates/project/project_competitors.html")
API_VIEWS_PATH = Path("core/api/views.py")


def test_pricing_template_uses_free_and_pro_only_with_current_checkout_targets():
    content = PRICING_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "Free" in content
    assert "Pro" in content
    assert "$99" in content
    assert "$990" in content
    assert "product_name='Pro - Monthly'" in content
    assert "product_name='Pro - Yearly'" in content

    for legacy_copy in [
        "Starter",
        "Growth",
        "Agency",
        "Pro - Starter",
        "Pro - Growth",
        "Pro - Agency",
        "$79",
        "$199",
        "$299",
    ]:
        assert legacy_copy not in content


def test_user_settings_template_shows_updated_pro_prices_and_checkout_targets():
    content = SETTINGS_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "$99" in content
    assert "$990" in content
    assert "product_name='Pro - Monthly'" in content
    assert "product_name='Pro - Yearly'" in content
    assert "$100" not in content
    assert "$1000" not in content


def test_upgrade_to_pro_ctas_in_project_templates_point_to_checkout():
    settings_content = PROJECT_SETTINGS_TEMPLATE_PATH.read_text(encoding="utf-8")
    competitors_content = PROJECT_COMPETITORS_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert settings_content.count("{% url 'user_upgrade_checkout_session' product_name='Pro - Monthly' %}") >= 3
    assert "{% url 'settings' %}" not in settings_content

    assert (
        competitors_content.count(
            "{% url 'user_upgrade_checkout_session' product_name='Pro - Monthly' %}"
        )
        >= 2
    )
    assert "{% url 'pricing' %}" not in competitors_content


def test_api_upgrade_to_pro_links_use_checkout_route_instead_of_settings():
    content = API_VIEWS_PATH.read_text(encoding="utf-8")

    assert "pro_monthly_checkout_url" in content
    assert "href='/settings'>Upgrade to Pro</a>" not in content
    assert "href='{pro_monthly_checkout_url()}'>Upgrade to Pro</a>" in content
