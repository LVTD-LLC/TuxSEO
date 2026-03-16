from pathlib import Path


PROJECT_HOME_TEMPLATE_PATH = Path("frontend/templates/project/project_home.html")


def test_project_home_prompt_template_includes_api_key_usage_instructions():
    content = PROJECT_HOME_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "Prompt template for AI agents" in content
    assert "TuxSEO API base URL:" in content
    assert "TuxSEO API key:" in content
    assert "Use this key in every API request header:" in content
    assert "X-API-Key:" in content
    assert "Production rotation snippet" not in content
    assert "export TUXSEO_API_KEY=" not in content
    assert "X-API-Key: $TUXSEO_API_KEY" not in content
    assert "Need to rotate your production key?" in content
    assert "Settings → API Access" in content
