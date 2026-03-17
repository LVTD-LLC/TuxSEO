from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from core.api.schemas import GenerateTitleSuggestionsIn
from core.api.views import generate_title_suggestions
from core.choices import ContentType
from core.models import BlogPostTitleSuggestion, Project, ProjectCustomPostType


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

    def fake_generate(self, content_type, num_titles, user_prompt="", model=None):
        captured["content_type"] = content_type
        captured["num_titles"] = num_titles
        captured["user_prompt"] = user_prompt
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
    assert "Focus on implementation details and trade-offs." in captured["user_prompt"]
    assert "Include practical examples" in captured["user_prompt"]

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
