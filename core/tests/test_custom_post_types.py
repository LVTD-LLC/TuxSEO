import base64
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from core.api.schemas import GenerateTitleSuggestionsIn
from core.api.views import generate_title_suggestions
from core.choices import ContentType
from core.models import BlogPostTitleSuggestion, Project, ProjectCustomPostType


TINY_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7+WZ0AAAAASUVORK5CYII="
)


@pytest.mark.django_db
def test_custom_post_type_name_is_unique_per_project_case_insensitive():
    user = User.objects.create_user("owner-custom-type", "owner-custom@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")

    ProjectCustomPostType.objects.create(
        project=project,
        name="Technical",
        prompt_guidance="Deep technical implementation details.",
    )

    duplicate = ProjectCustomPostType(
        project=project,
        name=" technical ",
        prompt_guidance="Another prompt",
    )

    with pytest.raises(ValidationError):
        duplicate.full_clean()


@pytest.mark.django_db
def test_generate_title_suggestions_applies_custom_post_type_prompt(monkeypatch):
    user = User.objects.create_user("owner-api", "owner-api@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")
    post_type = ProjectCustomPostType.objects.create(
        project=project,
        name="Technical",
        prompt_guidance="Focus on implementation details and trade-offs.",
    )

    captured = {}

    def fake_generate(
        self,
        content_type,
        num_titles,
        user_prompt="",
        custom_post_type_prompt="",
        model=None,
    ):
        captured["content_type"] = content_type
        captured["num_titles"] = num_titles
        captured["user_prompt"] = user_prompt
        captured["custom_post_type_prompt"] = custom_post_type_prompt
        suggestion = BlogPostTitleSuggestion.objects.create(
            project=self,
            title="Technical idea",
            description="desc",
            category="General Audience",
            content_type=content_type,
        )
        return [suggestion]

    monkeypatch.setattr("core.api.views.get_verified_email_gate_error", lambda *_a, **_k: None)
    monkeypatch.setattr("core.api.views.get_entitlement_error", lambda *_a, **_k: None)
    monkeypatch.setattr("core.models.Project.generate_title_suggestions", fake_generate)

    request = SimpleNamespace(auth=user.profile)
    response = generate_title_suggestions(
        request,
        GenerateTitleSuggestionsIn(
            project_id=project.id,
            content_type=ContentType.SHARING,
            num_titles=2,
            user_prompt="Include practical examples",
            post_type_id=post_type.id,
        ),
    )

    assert response["status"] == "success"
    assert captured["content_type"] == ContentType.SHARING
    assert captured["num_titles"] == 2
    assert captured["custom_post_type_prompt"] == "Focus on implementation details and trade-offs."
    assert captured["user_prompt"] == "Include practical examples"

    suggestion = BlogPostTitleSuggestion.objects.get(title="Technical idea")
    assert suggestion.custom_post_type_id == post_type.id


@pytest.mark.django_db
def test_generate_title_suggestions_rejects_foreign_custom_post_type(monkeypatch):
    owner = User.objects.create_user("owner-foreign", "owner-foreign@example.com", "secret")
    attacker = User.objects.create_user("attacker-foreign", "attacker-foreign@example.com", "secret")

    owner_project = Project.objects.create(profile=owner.profile, name="Owner", url="https://owner.test")
    foreign_post_type = ProjectCustomPostType.objects.create(
        project=owner_project,
        name="Technical",
        prompt_guidance="Prompt",
    )

    attacker_project = Project.objects.create(
        profile=attacker.profile,
        name="Attacker",
        url="https://attacker.test",
    )

    monkeypatch.setattr("core.api.views.get_verified_email_gate_error", lambda *_a, **_k: None)
    monkeypatch.setattr("core.api.views.get_entitlement_error", lambda *_a, **_k: None)

    request = SimpleNamespace(auth=attacker.profile)
    response = generate_title_suggestions(
        request,
        GenerateTitleSuggestionsIn(
            project_id=attacker_project.id,
            content_type=ContentType.SHARING,
            post_type_id=foreign_post_type.id,
        ),
    )

    assert response["status"] == "error"
    assert response["message"] == "Custom post type not found for this project."


@pytest.mark.django_db
def test_create_duplicate_custom_post_type_name_does_not_500(client):
    user = User.objects.create_user("owner-duplicate", "owner-duplicate@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")
    ProjectCustomPostType.objects.create(
        project=project,
        name="Technical",
        prompt_guidance="Focus on implementation details.",
    )

    client.force_login(user)
    response = client.post(
        reverse("project_custom_post_types", kwargs={"pk": project.id}),
        {
            "name": " technical ",
            "prompt_guidance": "Another guidance",
        },
    )

    assert response.status_code == 200
    assert ProjectCustomPostType.objects.filter(project=project).count() == 1


@pytest.mark.django_db
def test_update_duplicate_custom_post_type_name_does_not_500(client):
    user = User.objects.create_user("owner-update-duplicate", "owner-update-duplicate@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")
    first_type = ProjectCustomPostType.objects.create(
        project=project,
        name="Technical",
        prompt_guidance="Focus on implementation details.",
    )
    second_type = ProjectCustomPostType.objects.create(
        project=project,
        name="Beginner",
        prompt_guidance="Beginner guidance.",
    )

    client.force_login(user)
    response = client.post(
        reverse(
            "project_custom_post_type_update",
            kwargs={"pk": project.id, "post_type_pk": second_type.id},
        ),
        {
            "name": " technical ",
            "prompt_guidance": "Updated guidance",
        },
        follow=True,
    )

    assert response.status_code == 200
    second_type.refresh_from_db()
    assert second_type.name == "Beginner"
    assert first_type.name == "Technical"


@pytest.mark.django_db
def test_custom_post_type_prompt_is_included_in_blog_post_generation_context(monkeypatch):
    user = User.objects.create_user("owner-content", "owner-content@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")
    post_type = ProjectCustomPostType.objects.create(
        project=project,
        name="Case Study",
        prompt_guidance="Use a case-study narrative with concrete before/after outcomes.",
    )
    suggestion = BlogPostTitleSuggestion.objects.create(
        project=project,
        custom_post_type=post_type,
        title="How teams reduced content churn",
        description="desc",
        category="General Audience",
        content_type=ContentType.SHARING,
    )

    monkeypatch.setattr(BlogPostTitleSuggestion, "get_internal_links", lambda self, max_pages=2: [])
    monkeypatch.setattr(BlogPostTitleSuggestion, "get_external_authority_links", lambda self, max_links=None: [])

    context = suggestion.get_blog_post_generation_context(content_type=ContentType.SHARING)

    assert context.custom_post_type_prompt == post_type.prompt_guidance


@pytest.mark.django_db
def test_build_content_generation_prompt_does_not_duplicate_custom_post_type_guidance():
    user = User.objects.create_user("owner-prompt", "owner-prompt@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")
    post_type = ProjectCustomPostType.objects.create(
        project=project,
        name="Tutorial",
        prompt_guidance="Use step-by-step instructional tone with short code-like examples.",
    )
    suggestion = BlogPostTitleSuggestion.objects.create(
        project=project,
        custom_post_type=post_type,
        title="Practical setup guide",
        description="desc",
        category="General Audience",
        content_type=ContentType.SHARING,
    )

    generation_prompt = suggestion.build_content_generation_prompt()

    assert post_type.prompt_guidance not in generation_prompt


@pytest.mark.django_db
@override_settings(MEDIA_ROOT="/tmp/tuxseo-test-media")
def test_create_custom_post_type_with_logo_upload(client):
    user = User.objects.create_user("owner-logo", "owner-logo@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")

    client.force_login(user)
    response = client.post(
        reverse("project_custom_post_types", kwargs={"pk": project.id}),
        {
            "name": "Case Study",
            "prompt_guidance": "Use concrete outcomes and metrics.",
            "logo": SimpleUploadedFile("logo.png", TINY_PNG_BYTES, content_type="image/png"),
        },
    )

    assert response.status_code == 302
    created_type = ProjectCustomPostType.objects.get(project=project, name="Case Study")
    assert created_type.logo.name.startswith("custom_post_type_logos/")


@pytest.mark.django_db
@override_settings(MEDIA_ROOT="/tmp/tuxseo-test-media")
def test_create_custom_post_type_without_logo_works(client):
    user = User.objects.create_user("owner-no-logo", "owner-no-logo@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")

    client.force_login(user)
    response = client.post(
        reverse("project_custom_post_types", kwargs={"pk": project.id}),
        {
            "name": "Roundup",
            "prompt_guidance": "Summarize notable weekly updates.",
        },
    )

    assert response.status_code == 302
    created_type = ProjectCustomPostType.objects.get(project=project, name="Roundup")
    assert not created_type.logo


@pytest.mark.django_db
@override_settings(MEDIA_ROOT="/tmp/tuxseo-test-media")
def test_create_custom_post_type_rejects_unsupported_logo_type(client):
    user = User.objects.create_user("owner-bad-logo", "owner-bad-logo@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")

    client.force_login(user)
    response = client.post(
        reverse("project_custom_post_types", kwargs={"pk": project.id}),
        {
            "name": "Tutorial",
            "prompt_guidance": "Instructional tone.",
            "logo": SimpleUploadedFile("logo.txt", b"not-image", content_type="text/plain"),
        },
    )

    assert response.status_code == 200
    assert "Unsupported logo format" in response.content.decode("utf-8")


@pytest.mark.django_db
@override_settings(MEDIA_ROOT="/tmp/tuxseo-test-media")
def test_create_custom_post_type_rejects_oversized_logo(client):
    user = User.objects.create_user("owner-big-logo", "owner-big-logo@example.com", "secret")
    project = Project.objects.create(profile=user.profile, name="Site", url="https://site.test")

    oversized = b"0" * (ProjectCustomPostType.logo_max_file_size_bytes + 1)

    client.force_login(user)
    response = client.post(
        reverse("project_custom_post_types", kwargs={"pk": project.id}),
        {
            "name": "News",
            "prompt_guidance": "Fast-paced updates.",
            "logo": SimpleUploadedFile("logo.png", oversized, content_type="image/png"),
        },
    )

    assert response.status_code == 200
    assert "Logo must be 2MB or smaller" in response.content.decode("utf-8")
