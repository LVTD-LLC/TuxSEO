from core.models import BlogPostTitleSuggestion


class TestBlogPostKeywordGenerationValidation:
    def test_validation_fails_when_no_target_keyword_is_present(self, monkeypatch):
        suggestion = BlogPostTitleSuggestion()
        monkeypatch.setattr(
            suggestion,
            "get_blog_post_keywords",
            lambda: ["seo automation", "content strategy"],
        )

        is_valid, validation_error = suggestion.validate_generated_blog_post_content(
            "This draft focuses on growth loops and distribution without mentioning the target terms."
        )

        assert is_valid is False
        assert "naturally include at least one selected target keyword" in validation_error

    def test_validation_fails_when_target_keyword_uses_forced_markdown_formatting(self, monkeypatch):
        suggestion = BlogPostTitleSuggestion()
        monkeypatch.setattr(suggestion, "get_blog_post_keywords", lambda: ["seo automation"])

        is_valid, validation_error = suggestion.validate_generated_blog_post_content(
            "Use `SEO automation` to build repeatable publishing workflows."
        )

        assert is_valid is False
        assert "wrapped in markdown emphasis/code" in validation_error

    def test_validation_passes_for_natural_keyword_usage(self, monkeypatch):
        suggestion = BlogPostTitleSuggestion()
        monkeypatch.setattr(suggestion, "get_blog_post_keywords", lambda: ["seo automation"])

        is_valid, validation_error = suggestion.validate_generated_blog_post_content(
            "Teams that invest in seo automation can publish higher-quality posts with less manual effort."
        )

        assert is_valid is True
        assert validation_error == ""

    def test_prompt_includes_keyword_naturalness_constraints(self, monkeypatch):
        suggestion = BlogPostTitleSuggestion()
        monkeypatch.setattr(
            suggestion,
            "get_blog_post_keywords",
            lambda: ["seo automation", "content strategy"],
        )

        generation_prompt = suggestion.build_content_generation_prompt()

        assert "Target keywords to use naturally when relevant" in generation_prompt
        assert "Never wrap target keywords in backticks" in generation_prompt
        assert "Avoid keyword stuffing" in generation_prompt
