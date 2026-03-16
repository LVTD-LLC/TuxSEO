from pathlib import Path


LANDING_TEMPLATE_PATH = Path("frontend/templates/pages/landing.html")


def test_landing_how_it_works_section_mentions_humans_and_ai_agents():
    content = LANDING_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "How it works" in content
    assert "One workflow for founders and AI agents" in content
    assert "Choose your path: click through the UI yourself, or hand the same flow to an agent via TuxSEO skill.md." in content


def test_landing_how_it_works_steps_cover_shared_human_and_agent_flow():
    content = LANDING_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "Set up once" in content
    assert "Humans add the project URL in the dashboard; agents can read your project context from the prompt template and skill file." in content
    assert "Generate with context" in content
    assert "Use keyword and competitor insights to produce drafts manually, or let AI agents run the same SEO workflow end-to-end." in content
    assert "Ship and iterate" in content
    assert "Publish from one place, monitor output in Publish History, and keep both your team and agents aligned on what went live." in content


def test_landing_how_it_works_removes_legacy_simple_process_copy():
    content = LANDING_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "Simple Process" not in content
    assert "How It Works" not in content
    assert "Add Your Website" not in content
    assert "Generate Content" not in content
    assert "Publish & Grow" not in content
