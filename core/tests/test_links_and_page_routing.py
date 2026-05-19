from pathlib import Path

from core.public_api.views import public_api

PROJECT_HOME_TEMPLATE_PATH = Path("frontend/templates/project/project_home.html")


def test_project_home_template_points_api_docs_link_to_api_docs_route():
    content = PROJECT_HOME_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert '/api/docs' in content
    assert '/api/redoc' not in content


def test_project_home_template_prompt_template_points_to_public_skill_file():
    content = PROJECT_HOME_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "https://tuxseo.com/skill.md" in content


def test_skill_markdown_is_public_and_links_api_docs(client):
    response = client.get("/skill.md")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/markdown")

    content = response.content.decode()
    assert "# TuxSEO Agent Skill" in content
    assert "https://tuxseo.com/api/docs" in content
    assert "https://tuxseo.com/public-api" in content


def test_public_api_openapi_schema_includes_pages_and_competitors_surfaces():
    schema_paths = public_api.get_openapi_schema()["paths"]

    assert "/public-api/projects/{project_id}/pages" in schema_paths
    assert "/public-api/projects/{project_id}/pages/{page_id}" in schema_paths
    assert "/public-api/projects/{project_id}/competitors" in schema_paths
    assert "/public-api/projects/{project_id}/competitors/{competitor_id}" in schema_paths

    assert "get" in schema_paths["/public-api/projects/{project_id}/pages"]
    assert "post" in schema_paths["/public-api/projects/{project_id}/pages"]
    assert "get" in schema_paths["/public-api/projects/{project_id}/pages/{page_id}"]

    assert "get" in schema_paths["/public-api/projects/{project_id}/competitors"]
    assert "post" in schema_paths["/public-api/projects/{project_id}/competitors"]
    assert "get" in schema_paths["/public-api/projects/{project_id}/competitors/{competitor_id}"]
