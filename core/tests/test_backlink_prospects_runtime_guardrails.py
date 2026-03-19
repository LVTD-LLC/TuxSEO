from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from django.test import override_settings

from core.backlink_prospects import discover_backlink_prospects, refresh_backlink_prospects_cache
from core.models import Project, ProjectPage


@pytest.mark.django_db
@override_settings(
    EXA_API_KEY="exa_test",
    DETAIL_VIEW_CONTACT_ENRICHMENT_ENABLED=False,
)
def test_discover_backlink_prospects_skips_contact_enrichment_when_flag_off(monkeypatch):
    user = User.objects.create_user("bp-user", "bp-user@example.com", "secret")
    user_project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example",
    )
    page = ProjectPage.objects.create(
        project=user_project,
        url="https://example.com/page",
        title="Page",
        summary="Summary",
    )

    monkeypatch.setattr("core.backlink_prospects.extract_backlink_topics", lambda *_args, **_kwargs: ["seo"])
    monkeypatch.setattr(
        "core.backlink_prospects._search_exa_for_topic",
        lambda **_kwargs: [
            {
                "url": "https://docs.python.org/3/tutorial/",
                "title": "Python docs tutorial",
                "highlights": ["tutorial and documentation"],
                "score": 0.8,
            }
        ],
    )
    monkeypatch.setattr(
        "core.backlink_prospects._rank_backlink_candidates",
        lambda *_args, **_kwargs: [
            {
                "url": "https://docs.python.org/3/tutorial/",
                "title": "Python docs tutorial",
                "domain": "docs.python.org",
                "snippet": "tutorial",
                "topic": "seo",
                "source": "exa",
                "relevance_score": 0.9,
            }
        ],
    )

    called = {"enrich": 0}

    def _fake_enrich(*_args, **_kwargs):
        called["enrich"] += 1
        return [], []

    monkeypatch.setattr("core.backlink_prospects._enrich_candidate_contacts", _fake_enrich)

    candidates = discover_backlink_prospects(page, max_candidates=2, max_topics=1)

    assert candidates
    assert called["enrich"] == 0


@override_settings(BACKLINK_PROSPECTS_CONFIG={"PROVIDER_MAX_RETRIES": 2, "PROVIDER_RETRY_BACKOFF_SECONDS": 0})
def test_search_exa_for_topic_retries_before_success(monkeypatch):
    from core.backlink_prospects import _search_exa_for_topic

    attempts = {"count": 0}

    def _fake_post(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise Exception("boom")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"results": [{"url": "https://example.org", "score": 0.9}]}
        return response

    monkeypatch.setattr("core.backlink_prospects.requests.post", _fake_post)
    monkeypatch.setattr("core.backlink_prospects.requests.RequestException", Exception)
    monkeypatch.setattr("core.backlink_prospects.time.sleep", lambda *_args, **_kwargs: None)

    results = _search_exa_for_topic(exa_api_key="exa_test", topic="seo", num_results=5)

    assert attempts["count"] == 3
    assert len(results) == 1


@pytest.mark.django_db
def test_refresh_backlink_prospects_cache_sets_debug_state_on_failure(monkeypatch):
    user = User.objects.create_user("bp-failure-user", "bp-failure-user@example.com", "secret")
    project = Project.objects.create(profile=user.profile, url="https://example.com", name="Example")
    page = ProjectPage.objects.create(project=project, url="https://example.com/page", title="x")

    def _explode(_project_page):
        raise RuntimeError("provider timeout")

    monkeypatch.setattr("core.backlink_prospects.discover_backlink_prospects", _explode)

    with pytest.raises(RuntimeError):
        refresh_backlink_prospects_cache(page)

    from core.backlink_prospects import get_backlink_discovery_debug_state

    debug_state = get_backlink_discovery_debug_state(page.id)
    assert debug_state["status"] == "failed"
    assert debug_state["reason"] == "unexpected_exception"
