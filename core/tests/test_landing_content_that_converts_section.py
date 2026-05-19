from pathlib import Path


LANDING_TEMPLATE_PATH = Path("frontend/templates/pages/landing.html")


def test_landing_content_that_converts_section_uses_new_conversion_copy():
    content = LANDING_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "Content that converts" in content
    assert "Everything you need to ship SEO content that drives signups" in content
    assert "TuxSEO combines research, writing, and publishing" in content


def test_landing_content_that_converts_section_highlights_real_product_capabilities():
    content = LANDING_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "keyword, page, and competitor insights" in content
    assert "Generate SEO-focused or eye-catching post ideas and drafts" in content
    assert "Publish in one click and track what went live in Publish History" in content


def test_landing_content_that_converts_section_removes_legacy_heading_copy():
    content = LANDING_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "Complete Content Solution" not in content
    assert "Blog Content That Converts" not in content
