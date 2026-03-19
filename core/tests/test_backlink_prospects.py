from datetime import datetime, timedelta, timezone

import pytest
from django.contrib.auth.models import User

from django.core.cache import cache

from core.backlink_prospects import (
    discover_backlink_prospects,
    extract_backlink_topics,
    get_backlink_prospects_cache_key,
    get_backlink_prospects_refresh_lock_key,
    refresh_backlink_prospects_cache,
)
from core.models import Project, ProjectPage


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeHtmlResponse:
    def __init__(self, html: str):
        self.text = html

    def raise_for_status(self):
        return None


@pytest.fixture(autouse=True)
def _stub_contact_enrichment_fetch(monkeypatch):
    monkeypatch.setattr(
        "core.backlink_prospects.requests.get",
        lambda *_args, **_kwargs: _FakeHtmlResponse("<html><body></body></html>"),
    )


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
def test_discover_backlink_prospects_scores_deduplicates_and_ranks(monkeypatch, settings):
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

    now = datetime.now(tz=timezone.utc)

    responses = [
        {
            "results": [
                {
                    "url": (
                        "https://developers.google.com/search/docs/fundamentals/"
                        "seo-starter-guide?utm_source=newsletter"
                    ),
                    "title": "Google SEO Starter Guide",
                    "highlights": ["technical SEO indexing and crawl best practices"],
                    "score": 0.95,
                    "publishedDate": (now - timedelta(days=3)).isoformat(),
                },
                {
                    "url": (
                        "https://www.developers.google.com/search/docs/fundamentals/"
                        "seo-starter-guide"
                    ),
                    "title": "Google SEO Starter Guide Duplicate",
                    "highlights": ["technical SEO indexing and crawl best practices"],
                    "score": 0.96,
                    "publishedDate": (now - timedelta(days=2)).isoformat(),
                },
                {
                    "url": "https://moz.com/blog/technical-seo-audit-checklist",
                    "title": "Technical SEO Audit Checklist",
                    "highlights": ["technical SEO indexing and crawlability guide for teams"],
                    "score": 0.81,
                    "publishedDate": (now - timedelta(days=60)).isoformat(),
                },
                {
                    "url": "https://reddit.com/r/seo/comments/abc",
                    "title": "SEO thread",
                    "highlights": ["technical seo indexing"],
                    "score": 0.95,
                    "publishedDate": (now - timedelta(days=1)).isoformat(),
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

    assert len(candidates) == 2
    assert candidates[0]["url"] == (
        "https://developers.google.com/search/docs/fundamentals/seo-starter-guide"
    )
    assert candidates[0]["canonical_domain"] == "google.com"
    assert candidates[0]["relevance_score"] >= candidates[1]["relevance_score"]

    first = candidates[0]
    assert first["source"] == "exa"
    assert first["score_breakdown"]["topic_match_strength"] > 0
    assert first["score_breakdown"]["content_type_fit"] > 0
    assert first["score_breakdown"]["domain_credibility"] > 0
    assert first["score_breakdown"]["freshness_signal"] > 0
    assert "Topical match" in first["explanation"]["summary"]


@pytest.mark.django_db
def test_discover_backlink_prospects_filters_low_signal_junk_pages(monkeypatch, settings):
    settings.EXA_API_KEY = "test-key"

    user = User.objects.create_user(
        username="prospect-junk-filter-user",
        email="prospect-junk-filter-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="seo platform for startups",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/blog/seo-content-strategy",
        title="SEO Content Strategy",
        summary="SEO content strategy for startup teams",
        description="On-page and technical SEO strategy",
        type_ai_guess="Blog post",
    )

    responses = [
        {
            "results": [
                {
                    "url": "https://example.com/tag/seo",
                    "title": "Tag Archive",
                    "highlights": ["seo"],
                    "score": 0.9,
                },
                {
                    "url": "https://example.com/author/jane-doe",
                    "title": "Author Page",
                    "highlights": ["content"],
                    "score": 0.88,
                },
                {
                    "url": "https://backlinko.com/seo-content-strategy-guide",
                    "title": "SEO Content Strategy Guide",
                    "highlights": [
                        "A practical guide for SEO content strategy and keyword mapping."
                    ],
                    "score": 0.82,
                },
            ]
        },
        {"results": []},
        {"results": []},
        {"results": []},
        {"results": []},
    ]

    def _fake_post(*_args, **_kwargs):
        return _FakeResponse(responses.pop(0))

    monkeypatch.setattr("core.backlink_prospects.requests.post", _fake_post)

    candidates = discover_backlink_prospects(page, max_candidates=8)

    assert len(candidates) == 1
    assert candidates[0]["url"] == "https://backlinko.com/seo-content-strategy-guide"


@pytest.mark.django_db
def test_discover_backlink_prospects_filters_trailing_slash_taxonomy_pages(monkeypatch, settings):
    settings.EXA_API_KEY = "test-key"

    user = User.objects.create_user(
        username="prospect-taxonomy-slash-user",
        email="prospect-taxonomy-slash-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="seo platform for startups",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/blog/seo-content-strategy",
        title="SEO Content Strategy",
        summary="SEO content strategy for startup teams",
        description="On-page and technical SEO strategy",
        type_ai_guess="Blog post",
    )

    responses = [
        {
            "results": [
                {
                    "url": "https://example.com/tags/?utm_source=feed",
                    "title": "Tags",
                    "highlights": ["seo"],
                    "score": 0.95,
                },
                {
                    "url": "https://backlinko.com/seo-content-strategy-guide",
                    "title": "SEO Content Strategy Guide",
                    "highlights": [
                        "A practical guide for SEO content strategy and keyword mapping."
                    ],
                    "score": 0.82,
                },
            ]
        },
        {"results": []},
        {"results": []},
        {"results": []},
        {"results": []},
    ]

    def _fake_post(*_args, **_kwargs):
        return _FakeResponse(responses.pop(0))

    monkeypatch.setattr("core.backlink_prospects.requests.post", _fake_post)

    candidates = discover_backlink_prospects(page, max_candidates=8)

    assert len(candidates) == 1
    assert candidates[0]["url"] == "https://backlinko.com/seo-content-strategy-guide"


@pytest.mark.django_db
def test_discover_backlink_prospects_limits_exa_calls_with_overcollect_cap(
    monkeypatch,
    settings,
):
    settings.EXA_API_KEY = "test-key"
    settings.BACKLINK_PROSPECTS_CONFIG = {"OVERCOLLECT_FACTOR": 1}

    user = User.objects.create_user(
        username="prospect-overcollect-user",
        email="prospect-overcollect-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="seo platform for startups",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/blog/seo-content-strategy",
        title="SEO Content Strategy",
        summary="SEO content strategy for startup teams",
        description="On-page and technical SEO strategy",
        type_ai_guess="Blog post",
    )

    monkeypatch.setattr(
        "core.backlink_prospects.extract_backlink_topics",
        lambda *_args, **_kwargs: ["topic a", "topic b", "topic c"],
    )

    api_calls = {"count": 0}

    def _fake_post(*_args, **_kwargs):
        api_calls["count"] += 1
        return _FakeResponse(
            {
                "results": [
                    {
                        "url": f"https://example{api_calls['count']}.com/guide",
                        "title": "SEO Guide",
                        "highlights": ["topic a topic b topic c guide"],
                        "score": 0.95,
                        "publishedDate": datetime.now(tz=timezone.utc).isoformat(),
                    }
                ]
            }
        )

    monkeypatch.setattr("core.backlink_prospects.requests.post", _fake_post)

    candidates = discover_backlink_prospects(page, max_candidates=1)

    assert len(candidates) == 1
    assert api_calls["count"] == 1


@pytest.mark.django_db
def test_discover_backlink_prospects_enriches_contact_methods_from_public_signals(
    monkeypatch,
    settings,
):
    settings.EXA_API_KEY = "test-key"

    user = User.objects.create_user(
        username="prospect-contact-enrichment-user",
        email="prospect-contact-enrichment-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="seo platform for startups",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/blog/seo-content-strategy",
        title="SEO Content Strategy",
        summary="SEO content strategy for startup teams",
        description="On-page and technical SEO strategy",
        type_ai_guess="Blog post",
    )

    monkeypatch.setattr(
        "core.backlink_prospects.requests.post",
        lambda *_args, **_kwargs: _FakeResponse(
            {
                "results": [
                    {
                        "url": "https://example.org/seo-playbook",
                        "title": "SEO Playbook",
                        "highlights": ["technical seo indexing playbook for teams"],
                        "score": 0.92,
                    }
                ]
            }
        ),
    )

    html = """
        <html>
          <body>
            <a href="/contact">Contact us</a>
            <a href="mailto:hello@example.org">Email</a>
            <a href="https://x.com/example_org">X</a>
            <a href="https://www.linkedin.com/company/example-org/">LinkedIn</a>
            <a href="/author/jane-doe">About the author</a>
          </body>
        </html>
    """
    monkeypatch.setattr(
        "core.backlink_prospects.requests.get",
        lambda *_args, **_kwargs: _FakeHtmlResponse(html),
    )

    candidates = discover_backlink_prospects(page, max_candidates=1)

    assert len(candidates) == 1
    candidate = candidates[0]
    methods_by_type = {method["type"]: method for method in candidate["contact_methods"]}

    assert methods_by_type["contact_page_url"]["status"] == "found"
    assert methods_by_type["contact_page_url"]["confidence"] == "high"
    assert methods_by_type["contact_page_url"]["value"] == "https://example.org/contact"

    assert methods_by_type["public_email"]["status"] == "found"
    assert methods_by_type["public_email"]["value"] == "hello@example.org"

    assert methods_by_type["x_twitter"]["status"] == "found"
    assert methods_by_type["x_twitter"]["value"] == "https://x.com/example_org"

    assert methods_by_type["linkedin"]["status"] == "found"
    assert methods_by_type["linkedin"]["value"] == "https://www.linkedin.com/company/example-org"

    assert methods_by_type["author_profile"]["status"] == "found"
    assert methods_by_type["author_profile"]["value"] == "https://example.org/author/jane-doe"

    assert candidate["actionable_outreach_count"] == 5


@pytest.mark.django_db
def test_discover_backlink_prospects_marks_low_confidence_vs_not_found(monkeypatch, settings):
    settings.EXA_API_KEY = "test-key"

    user = User.objects.create_user(
        username="prospect-contact-confidence-user",
        email="prospect-contact-confidence-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="seo platform for startups",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/blog/seo-content-strategy",
        title="SEO Content Strategy",
        summary="SEO content strategy for startup teams",
        description="On-page and technical SEO strategy",
        type_ai_guess="Blog post",
    )

    monkeypatch.setattr(
        "core.backlink_prospects.requests.post",
        lambda *_args, **_kwargs: _FakeResponse(
            {
                "results": [
                    {
                        "url": "https://example.net/guide",
                        "title": "SEO Guide",
                        "highlights": ["technical seo guide"],
                        "score": 0.9,
                    }
                ]
            }
        ),
    )

    html = """
        <html>
          <body>
            <a href="/about">About us</a>
            <p>No social links are listed on this page.</p>
          </body>
        </html>
    """
    monkeypatch.setattr(
        "core.backlink_prospects.requests.get",
        lambda *_args, **_kwargs: _FakeHtmlResponse(html),
    )

    candidates = discover_backlink_prospects(page, max_candidates=1)

    methods_by_type = {method["type"]: method for method in candidates[0]["contact_methods"]}
    assert methods_by_type["contact_page_url"]["status"] == "low_confidence"
    assert methods_by_type["contact_page_url"]["confidence"] == "low"
    assert methods_by_type["contact_page_url"]["value"] == "https://example.net/about"

    assert methods_by_type["public_email"]["status"] == "not_found"
    assert methods_by_type["x_twitter"]["status"] == "not_found"
    assert methods_by_type["linkedin"]["status"] == "not_found"
    assert methods_by_type["author_profile"]["status"] == "not_found"
    assert candidates[0]["actionable_outreach_count"] == 0


@pytest.mark.django_db
def test_discover_backlink_prospects_avoids_common_false_positive_contact_hints(
    monkeypatch,
    settings,
):
    settings.EXA_API_KEY = "test-key"

    user = User.objects.create_user(
        username="prospect-contact-false-positive-user",
        email="prospect-contact-false-positive-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
        summary="seo platform for startups",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/blog/seo-content-strategy",
        title="SEO Content Strategy",
        summary="SEO content strategy for startup teams",
        description="On-page and technical SEO strategy",
        type_ai_guess="Blog post",
    )

    monkeypatch.setattr(
        "core.backlink_prospects.requests.post",
        lambda *_args, **_kwargs: _FakeResponse(
            {
                "results": [
                    {
                        "url": "https://example.net/seo-outreach-guide",
                        "title": "SEO Outreach Guide",
                        "highlights": ["technical seo outreach tactics"],
                        "score": 0.9,
                    }
                ]
            }
        ),
    )

    html = """
        <html>
          <body>
            <a href="/seo-outreach">SEO outreach playbook</a>
            <a href="https://agency.example/powered-by">Powered by Agency</a>
          </body>
        </html>
    """
    monkeypatch.setattr(
        "core.backlink_prospects.requests.get",
        lambda *_args, **_kwargs: _FakeHtmlResponse(html),
    )

    candidates = discover_backlink_prospects(page, max_candidates=1)

    methods_by_type = {method["type"]: method for method in candidates[0]["contact_methods"]}
    assert methods_by_type["contact_page_url"]["status"] == "not_found"
    assert methods_by_type["author_profile"]["status"] == "not_found"


@pytest.mark.django_db
def test_refresh_backlink_prospects_cache_smoke_sets_cache_and_clears_lock(monkeypatch):
    user = User.objects.create_user(
        username="prospect-refresh-cache-user",
        email="prospect-refresh-cache-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/features/seo",
        title="SEO Features",
        type_ai_guess="Product page",
    )

    expected_candidates = [
        {
            "url": "https://example.org/seo-guide",
            "domain": "example.org",
            "canonical_domain": "example.org",
            "title": "SEO Guide",
            "snippet": "Great SEO guide",
            "topic": "seo workflows",
            "source": "exa",
            "relevance_score": 0.91,
            "score_breakdown": {
                "topic_match_strength": 0.9,
                "content_type_fit": 0.8,
                "domain_credibility": 0.7,
                "freshness_signal": 1.0,
                "topic_overlap_ratio": 0.8,
                "exa_score": 0.9,
            },
            "explanation": {"summary": "Topical and authority signals align."},
            "discovered_at": datetime.now(tz=timezone.utc).isoformat(),
            "contact_methods": [],
            "actionable_outreach_paths": [],
            "actionable_outreach_count": 0,
        }
    ]

    monkeypatch.setattr(
        "core.backlink_prospects.discover_backlink_prospects",
        lambda _project_page: expected_candidates,
    )

    lock_key = get_backlink_prospects_refresh_lock_key(page.id)
    cache.set(lock_key, True, timeout=60)

    cached_candidates = refresh_backlink_prospects_cache(page)

    assert cached_candidates == expected_candidates
    assert cache.get(lock_key) is None

    cached_payload = cache.get(get_backlink_prospects_cache_key(page.id))
    assert cached_payload is not None
    assert cached_payload["candidates"] == expected_candidates


@pytest.mark.django_db
def test_refresh_backlink_prospects_cache_clears_lock_on_discovery_error(monkeypatch):
    user = User.objects.create_user(
        username="prospect-refresh-error-user",
        email="prospect-refresh-error-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://tuxseo.com",
        name="TuxSEO",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://tuxseo.com/features/seo",
        title="SEO Features",
        type_ai_guess="Product page",
    )

    def _raise_error(_project_page):
        raise RuntimeError("simulated refresh failure")

    monkeypatch.setattr("core.backlink_prospects.discover_backlink_prospects", _raise_error)

    lock_key = get_backlink_prospects_refresh_lock_key(page.id)
    cache.set(lock_key, True, timeout=60)

    with pytest.raises(RuntimeError, match="simulated refresh failure"):
        refresh_backlink_prospects_cache(page)

    assert cache.get(lock_key) is None


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
