from core.agents.competitor_vs_blog_post_agent import (
    build_competitor_vs_research_brief,
    build_research_targets,
)
from core.agents.schemas import CompetitorVsPostContext, ProjectPageContext


def test_build_research_targets_contains_key_page_categories():
    targets = build_research_targets("https://example.com/")

    assert targets == [
        "https://example.com/",
        "https://example.com/pricing",
        "https://example.com/docs",
        "https://example.com/features",
        "https://example.com/integrations",
        "https://example.com/use-cases",
    ]


def test_build_competitor_vs_research_brief_requires_balanced_structure_and_evidence():
    context = CompetitorVsPostContext(
        project_name="TuxSEO",
        project_url="https://tuxseo.com",
        project_summary="AI SEO workflow for product-led teams",
        competitor_name="CompetitorX",
        competitor_url="https://competitorx.com",
        competitor_description="SEO automation suite",
        title="TuxSEO vs CompetitorX",
        language="English",
        project_pages=[
            ProjectPageContext(
                url="https://tuxseo.com/pricing",
                title="Pricing",
                description="Plans and limits",
                summary="Plan comparison",
            )
        ],
    )

    brief = build_competitor_vs_research_brief(context)

    assert "Research Requirements (must follow):" in brief
    assert "Required Comparison Structure (must follow in this order):" in brief
    assert "Trade-offs and limitations (for both)" in brief
    assert "Recommendation logic" in brief
    assert "Never force biased claims or spammy persuasion." in brief
    assert "include inline markdown links" in brief
    assert "https://tuxseo.com/pricing" in brief
    assert "https://competitorx.com/pricing" in brief
