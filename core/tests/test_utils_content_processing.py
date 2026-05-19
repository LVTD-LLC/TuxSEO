from unittest.mock import Mock, patch

import requests

from core.choices import OGImageStyle
from core.utils import (
    DivErrorList,
    download_image_from_url,
    extract_title_from_content,
    generate_random_key,
    get_html_content,
    get_jina_embedding,
    get_markdown_content,
    get_og_image_prompt,
    process_generated_blog_content,
)


class TestDivErrorList:
    def test_renders_as_html_divs(self):
        error_list = DivErrorList(["First error", "Second error"])

        html_content = str(error_list)

        assert "First error" in html_content
        assert "Second error" in html_content
        assert "bg-red-50" in html_content

    def test_returns_empty_string_when_no_errors(self):
        error_list = DivErrorList()

        assert str(error_list) == ""


class TestOgImagePrompt:
    def test_returns_style_specific_prompt(self):
        prompt = get_og_image_prompt(OGImageStyle.DARK_MODE, "SaaS")

        assert "Dark mode background" in prompt
        assert "SaaS" in prompt
        assert "NO TEXT, NO WORDS, NO LETTERS" in prompt

    def test_returns_default_prompt_for_unknown_style(self):
        prompt = get_og_image_prompt("Unknown Style", "Marketing")

        assert "Abstract modern geometric background" in prompt
        assert "Marketing" in prompt


class TestDownloadImageFromUrl:
    def test_returns_content_file_when_download_succeeds(self):
        expected_bytes = b"fake-image-bytes"
        fake_response = Mock()
        fake_response.read.return_value = expected_bytes

        with patch("core.utils.urlopen", return_value=fake_response):
            downloaded_content = download_image_from_url(
                image_url="https://example.com/image.png",
                field_name="image",
                instance_id=123,
            )

        assert downloaded_content is not None
        assert downloaded_content.read() == expected_bytes

    def test_returns_none_when_download_fails(self):
        with patch("core.utils.urlopen", side_effect=Exception("Download failed")):
            downloaded_content = download_image_from_url(
                image_url="https://example.com/image.png",
                field_name="image",
                instance_id=123,
            )

        assert downloaded_content is None


class TestJinaEmbedding:
    @patch("core.utils.requests.post")
    def test_returns_embedding_when_api_response_is_valid(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        embedding = get_jina_embedding("Hello world")

        assert embedding == [0.1, 0.2, 0.3]

    @patch("core.utils.requests.post")
    def test_returns_none_when_api_response_has_no_embedding_data(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        embedding = get_jina_embedding("Hello world")

        assert embedding is None

    @patch("core.utils.requests.post")
    def test_returns_none_when_api_request_raises_exception(self, mock_post):
        mock_post.side_effect = requests.exceptions.RequestException("Network issue")

        embedding = get_jina_embedding("Hello world")

        assert embedding is None


class TestContentFetchingHelpers:
    @patch("core.utils.requests.get")
    def test_get_html_content_returns_response_text_on_success(self, mock_get):
        mock_response = Mock()
        mock_response.text = "<html>ok</html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        html_content = get_html_content("https://example.com")

        assert html_content == "<html>ok</html>"

    @patch("core.utils.requests.get")
    def test_get_html_content_returns_empty_string_on_request_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.RequestException("Request failed")

        html_content = get_html_content("https://example.com")

        assert html_content == ""

    @patch("core.utils.requests.get")
    def test_get_markdown_content_returns_title_description_and_content(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": {
                "title": "Page Title",
                "description": "Page description",
                "content": "Page markdown content",
            }
        }
        mock_get.return_value = mock_response

        title, description, content = get_markdown_content("https://example.com")

        assert title == "Page Title"
        assert description == "Page description"
        assert content == "Page markdown content"

    @patch("core.utils.requests.get")
    def test_get_markdown_content_returns_empty_values_on_request_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.RequestException("Request failed")

        title, description, content = get_markdown_content("https://example.com")

        assert (title, description, content) == ("", "", "")


class TestContentProcessing:
    def test_extract_title_from_content_returns_title_and_remaining_content(self):
        source_content = "# My Blog Post\n\nThis is the first paragraph.\n\nMore content here."

        extracted_title, remaining_content = extract_title_from_content(source_content)

        assert extracted_title == "My Blog Post"
        assert remaining_content == "This is the first paragraph.\n\nMore content here."

    def test_extract_title_from_content_returns_none_when_h1_is_missing(self):
        source_content = "## Subtitle\n\nBody content"

        extracted_title, remaining_content = extract_title_from_content(source_content)

        assert extracted_title is None
        assert remaining_content == source_content

    def test_extract_title_from_content_returns_none_for_empty_content(self):
        extracted_title, remaining_content = extract_title_from_content("   ")

        assert extracted_title is None
        assert remaining_content == "   "

    def test_process_generated_blog_content_removes_unwanted_sections(self):
        generated_content = """# Better SaaS Onboarding

## Introduction

Hook paragraph.

## Main Section

Useful content.

---
## References
- https://example.com/reference
---
"""

        blog_post_title, blog_post_content = process_generated_blog_content(
            generated_content=generated_content,
            fallback_title="Fallback Title",
            title_suggestion_id=1,
            project_id=1,
        )

        assert blog_post_title == "Better SaaS Onboarding"
        assert "## Introduction" not in blog_post_content
        assert "## References" not in blog_post_content
        assert "---" not in blog_post_content
        assert "## Main Section" in blog_post_content

    def test_process_generated_blog_content_uses_fallback_title_without_h1(self):
        generated_content = "## Introduction\n\nNo H1 title in this content."

        blog_post_title, blog_post_content = process_generated_blog_content(
            generated_content=generated_content,
            fallback_title="Fallback Title",
            title_suggestion_id=2,
            project_id=2,
        )

        assert blog_post_title == "Fallback Title"
        assert blog_post_content == "No H1 title in this content."


class TestGenerateRandomKey:
    def test_generates_prefixed_high_entropy_hex_key(self):
        random_key = generate_random_key()

        assert random_key.startswith("tuxseo_")
        assert len(random_key) == 47
        suffix = random_key.removeprefix("tuxseo_")
        assert len(suffix) == 40
        assert all(char in "0123456789abcdef" for char in suffix)

    def test_generates_different_values_across_calls(self):
        first_key = generate_random_key()
        second_key = generate_random_key()

        assert first_key != second_key
