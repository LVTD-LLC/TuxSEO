import pytest
from django.contrib.auth.models import User

from core.backlink_prospects import discover_backlink_prospects, extract_backlink_topics
from core.models import Project, ProjectPage


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.django_db
def test_extract_backlink_topics_uses_project_and_page_fields():
    user = User.objects.create_user(
        username="topic-extraction-user",
        email="topic-extraction-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="SEO automation toolkit for startups",
        blog_theme="technical SEO, content strategy",
        key_features="rank tracking; content briefs; site audits",
        target_audience_summary="Growth marketers and founders",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/features/seo",
        title="AI SEO Content Optimization",
        summary="Practical technical SEO workflows for SaaS teams",
        type_ai_guess="Product page",
        description="Learn on-page optimization for search visibility",
    )

    topics = extract_backlink_topics(project, page, max_topics=5)

    assert len(topics) == 5
    assert any("technical SEO" in topic for topic in topics)
    assert any("content" in topic.lower() for topic in topics)


@pytest.mark.django_db
def test_discover_backlink_prospects_filters_and_deduplicates(monkeypatch, settings):
    settings.EXA_API_KEY = "test-key"

    user = User.objects.create_user(
        username="prospect-filter-user",
        email="prospect-filter-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="technical SEO indexing improvements",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/blog/technical-seo",
        title="Technical SEO",
        summary="technical SEO indexing improvements",
        description="Indexing and crawlability",
        type_ai_guess="Blog post",
    )

    responses = [
        {
            "results": [
                {
                    "url": "https://developers.google.com/search/docs/fundamentals/seo-starter-guide",
                    "title": "Google SEO Starter Guide",
                    "highlights": ["technical SEO indexing and crawl best practices"],
                    "score": 0.9,
                },
                {
                    "url": "https://developers.google.com/search/docs/fundamentals/seo-starter-guide",
                    "title": "Google SEO Starter Guide Duplicate",
                    "highlights": ["technical SEO indexing and crawl best practices"],
                    "score": 0.91,
                },
                {
                    "url": "https://reddit.com/r/seo/comments/abc",
                    "title": "SEO thread",
                    "highlights": ["technical seo indexing"],
                    "score": 0.95,
                },
                {
                    "url": "https://irrelevant.example.com/post",
                    "title": "Cooking and baking guide",
                    "highlights": ["sourdough recipes and kitchen equipment"],
                    "score": 0.8,
                },
            ]
        },
        {"results": []},
        {"results": []},
        {"results": []},
        {"results": []},
    ]

    def _fake_post(*_args, **_kwargs):
        payload = responses.pop(0)
        return _FakeResponse(payload)

    monkeypatch.setattr("core.backlink_prospects.requests.post", _fake_post)

    candidates = discover_backlink_prospects(page, max_candidates=8)

    assert len(candidates) == 1
    assert candidates[0]["url"].startswith("https://developers.google.com/")
    assert candidates[0]["source"] == "exa"


@pytest.mark.django_db
def test_discover_backlink_prospects_is_safe_without_api_key(settings):
    settings.EXA_API_KEY = ""

    user = User.objects.create_user(
        username="prospect-no-key-user",
        email="prospect-no-key-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="technical SEO indexing improvements",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/blog/technical-seo",
        title="Technical SEO",
        summary="technical SEO indexing improvements",
        description="Indexing and crawlability",
        type_ai_guess="Blog post",
    )

    assert discover_backlink_prospects(page) == []
