from pathlib import Path


PROJECT_INFORMATION_CARD_TEMPLATE_PATH = Path("frontend/templates/components/project_information_card.html")


def test_homepage_view_details_cta_uses_project_overview_route_name():
    content = PROJECT_INFORMATION_CARD_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "View details" in content
    assert "{% url 'project_home' project.id %}" in content


def test_homepage_project_card_keeps_delete_cta_target():
    content = PROJECT_INFORMATION_CARD_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "{% url 'project_delete' project.id %}" in content
