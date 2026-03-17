from types import SimpleNamespace

from core.models import BlogPostTitleSuggestion
from core.utils import get_external_authority_link_candidates


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

def test_get_external_authority_link_candidates_relevance_and_domain_filters(monkeypatch, settings):
    settings.EXA_API_KEY = "test-key"

    def _fake_post(*_args, **_kwargs):
        return _FakeResponse(
            {
                "results": [
                    {
                        "url": "https://developers.google.com/search/docs/fundamentals/seo-starter-guide",
                        "title": "Google SEO starter guide",
                        "highlights": ["Google guidance for technical SEO and content quality."],
                        "score": 0.61,
                    },
                    {
                        "url": "https://reddit.com/r/seo/comments/abc",
                        "title": "SEO thread",
                        "highlights": ["community discussion"],
                        "score": 0.92,
                    },
                    {
                        "url": "https://www.example.org/unrelated-topic",
                        "title": "Unrelated topic",
                        "highlights": ["banana recipes and gardening"],
                        "score": 0.88,
                    },
                ]
            }
        )

    monkeypatch.setattr("core.utils.requests.post", _fake_post)

    links = get_external_authority_link_candidates(
        meta_description="Technical SEO checklist and search indexing best practices",
        max_links=2,
    )

    assert len(links) == 1
    assert links[0]["url"].startswith("https://developers.google.com/")


def test_blog_post_generation_context_includes_external_authority_links(monkeypatch):
    suggestion = BlogPostTitleSuggestion(
        title="How to improve technical SEO",
        category="GENERAL_AUDIENCE",
        description="A practical guide",
        suggested_meta_description="Technical SEO improvements for indexing and rankings",
    )
    suggestion._state.fields_cache["project"] = SimpleNamespace(
        project_details={
            "name": "TuxSEO",
            "type": "SaaS",
            "summary": "SEO automation product",
            "blog_theme": "- SEO",
            "founders": "- Founder",
            "key_features": "- Content generation",
            "target_audience_summary": "Marketers",
            "pain_points": "- Slow writing",
            "product_usage": "- Publish faster",
            "proposed_keywords": "seo automation",
            "links": "- https://tuxseo.com",
            "language": "English",
            "location": "Global",
        }
    )

    internal_page = SimpleNamespace(
        url="https://tuxseo.com/features",
        title="Features",
        description="Core features",
        summary="Feature summary",
        always_use=True,
    )

    monkeypatch.setattr(suggestion, "get_internal_links", lambda max_pages=2: [internal_page])
    monkeypatch.setattr(suggestion, "get_blog_post_keywords", lambda: ["technical seo"])
    monkeypatch.setattr(
        suggestion,
        "get_external_authority_links",
        lambda max_links=None: [
            {
                "url": "https://developers.google.com/search/docs/fundamentals/seo-starter-guide",
                "title": "Google SEO starter guide",
                "description": "Google documentation",
                "summary": "Google documentation for SEO",
                "link_source": "external",
            }
        ],
    )

    context = suggestion.get_blog_post_generation_context()

    assert len(context.project_pages) == 2
    assert [page.link_source for page in context.project_pages] == ["internal", "external"]
    assert context.project_pages[1].url == "https://developers.google.com/search/docs/fundamentals/seo-starter-guide"
