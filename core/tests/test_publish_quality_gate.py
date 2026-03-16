from types import SimpleNamespace
from unittest.mock import patch

from core.publish_quality_gate import evaluate_pre_publish_quality_gate


def _build_generated_post(content: str, *, title: str = "How to improve SEO"):
    return SimpleNamespace(
        title=title,
        content=content,
        title_suggestion=SimpleNamespace(target_keywords=["seo", "content strategy"]),
    )


def test_quality_gate_blocks_placeholder_language():
    generated_post = _build_generated_post(
        """
        # Draft

        This article explains SEO workflows for SaaS teams.
        Insert image here and add screenshot below.
        """
    )

    result = evaluate_pre_publish_quality_gate(generated_post)

    assert result["decision"] == "block"
    assert any(check["code"] == "PLACEHOLDER_LANGUAGE" for check in result["checks"])


def test_quality_gate_warns_for_brief_content():
    short_paragraph = (
        "SEO teams need repeatable workflows for keyword discovery, topical clustering, and "
        "publishing cadence. A short post can still be useful when it is focused, practical, "
        "and uses concrete examples that map directly to product pages. "
    )
    generated_post = _build_generated_post(short_paragraph * 4)

    with patch(
        "core.publish_quality_gate.evaluate_generated_content_quality",
        return_value={"aggregate_score": 0.95},
    ):
        result = evaluate_pre_publish_quality_gate(generated_post)

    assert result["decision"] == "warn"
    assert any(check["code"] == "CONTENT_LENGTH_WARNING" for check in result["checks"])


def test_quality_gate_allows_publishable_content():
    publishable_content = (
        "SEO strategy improves when teams combine keyword intent, internal linking, and clear page ownership. "
        "A practical content plan starts with mapping each keyword cluster to one canonical page and one supporting article. "
        "Each article should answer a specific question, include examples from real product usage, and link naturally to related pages. "
        "Teams should track impressions, click-through rates, and conversion signals to identify weak sections. "
        "When performance drops, refresh intros, tighten headings, and remove repetitive filler. "
        "This process keeps content useful for readers and resilient for search visibility over time."
    )
    generated_post = _build_generated_post(publishable_content * 3)

    with patch(
        "core.publish_quality_gate.evaluate_generated_content_quality",
        return_value={"aggregate_score": 0.95},
    ):
        result = evaluate_pre_publish_quality_gate(generated_post)

    assert result["decision"] == "allow"
    assert result["checks"] == []
