from pathlib import Path


HOME_TEMPLATE_PATH = Path("frontend/templates/pages/home.html")
PROJECT_CARD_TEMPLATE_PATH = Path("frontend/templates/components/project_information_card.html")
PROJECT_SEARCH_CONTROLLER_PATH = Path("frontend/src/controllers/project_search_controller.js")


def test_home_template_wires_project_search_controller_and_input():
    content = HOME_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert 'data-controller="scan-progress project-search"' in content
    assert 'data-action="input->project-search#filter"' in content
    assert 'data-project-search-target="list"' in content


def test_project_cards_expose_searchable_text_attribute():
    content = PROJECT_CARD_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert 'data-project-search-target="item"' in content
    assert 'data-project-search-text="' in content


def test_project_search_controller_is_client_side_only():
    content = PROJECT_SEARCH_CONTROLLER_PATH.read_text(encoding="utf-8")

    assert "fetch(" not in content
