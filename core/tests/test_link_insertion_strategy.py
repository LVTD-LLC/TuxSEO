from types import SimpleNamespace

from core.models import GeneratedBlogPost


class _FakeProjectPagesManager:
    def __init__(self, pages):
        self._pages = pages

    def filter(self, always_use=False):
        if always_use:
            return [page for page in self._pages if getattr(page, "always_use", False)]
        return list(self._pages)


def _page(url, project_id=1, participate=True, always_use=False):
    project = SimpleNamespace(particiate_in_link_exchange=participate)
    return SimpleNamespace(
        url=url,
        title=url,
        description=url,
        summary=url,
        project=project,
        project_id=project_id,
        always_use=always_use,
    )


def test_get_link_candidate_pages_builds_balanced_internal_external_mix(monkeypatch):
    always_use_internal = _page("https://owner.example.com/guide", always_use=True)
    duplicate_internal = _page("https://owner.example.com/guide")
    relevant_internal = _page("https://owner.example.com/pricing")

    external_same_project_a = _page("https://external-a.example.com/one", project_id=100)
    external_same_project_b = _page("https://external-a.example.com/two", project_id=100)
    external_non_participant = _page(
        "https://external-b.example.com/one", project_id=200, participate=False
    )
    external_participant = _page("https://external-c.example.com/one", project_id=300)

    fake_blog_post = SimpleNamespace(
        project=SimpleNamespace(
            particiate_in_link_exchange=True,
            project_pages=_FakeProjectPagesManager([always_use_internal]),
        ),
        title_suggestion=SimpleNamespace(suggested_meta_description="SEO links"),
        _dedupe_pages_by_url=GeneratedBlogPost._dedupe_pages_by_url,
        _dedupe_external_pages_by_project=GeneratedBlogPost._dedupe_external_pages_by_project,
    )

    monkeypatch.setattr(
        "core.models.get_relevant_pages_for_blog_post",
        lambda *_args, **_kwargs: [duplicate_internal, relevant_internal],
    )
    monkeypatch.setattr(
        "core.models.get_relevant_external_pages_for_blog_post",
        lambda *_args, **_kwargs: [
            external_same_project_a,
            external_same_project_b,
            external_non_participant,
            external_participant,
        ],
    )

    internal_pages, external_pages, manually_selected_pages = GeneratedBlogPost._get_link_candidate_pages(
        fake_blog_post,
        max_pages=4,
        max_external_pages=3,
    )

    assert [page.url for page in manually_selected_pages] == ["https://owner.example.com/guide"]
    assert [page.url for page in internal_pages] == [
        "https://owner.example.com/guide",
        "https://owner.example.com/pricing",
    ]
    assert [page.url for page in external_pages] == [
        "https://external-a.example.com/one",
        "https://external-c.example.com/one",
    ]


def test_build_page_contexts_includes_link_source_tags():
    internal_page = _page("https://owner.example.com/internal")
    external_page = _page("https://external.example.com/external", project_id=2)

    contexts = GeneratedBlogPost._build_page_contexts(
        internal_pages=[internal_page],
        external_pages=[external_page],
    )

    assert [context.url for context in contexts] == [
        "https://owner.example.com/internal",
        "https://external.example.com/external",
    ]
    assert [context.link_source for context in contexts] == ["internal", "external"]
