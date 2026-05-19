from django.test import override_settings

from core.templatetags.markdown_extras import (
    markdown as markdown_filter,
    mjml_configured,
    replace,
    replace_quotes,
)


class TestMarkdownFilter:
    def test_converts_markdown_to_html(self):
        markdown_text = "# Heading"

        rendered_html = markdown_filter(markdown_text)

        assert "<h1>Heading</h1>" in rendered_html

    def test_renders_fenced_code_blocks_with_copy_controls(self):
        markdown_text = '```bash\necho "hello"\n```'

        rendered_html = markdown_filter(markdown_text)

        assert 'class="code-snippet"' in rendered_html
        assert 'data-controller="copy"' in rendered_html
        assert 'data-action="copy#copy"' in rendered_html
        assert 'data-copy-target="source"' in rendered_html
        assert 'data-copy-target="button"' in rendered_html
        assert 'echo "hello"' in rendered_html

    def test_adds_safe_new_tab_attributes_to_external_links(self):
        markdown_text = "[Example](https://example.com)"

        rendered_html = markdown_filter(markdown_text)

        assert 'href="https://example.com"' in rendered_html
        assert 'target="_blank"' in rendered_html
        assert 'rel="noopener noreferrer"' in rendered_html

    def test_renders_bullet_list_after_paragraph_without_blank_line(self):
        markdown_text = "Intro paragraph:\n- first\n- second"

        rendered_html = markdown_filter(markdown_text)

        assert "<p>Intro paragraph:</p>" in rendered_html
        assert "<ul>" in rendered_html
        assert "<li>first</li>" in rendered_html
        assert "<li>second</li>" in rendered_html

    def test_renders_numbered_list_after_paragraph_without_blank_line(self):
        markdown_text = "Steps\n1. one\n2. two"

        rendered_html = markdown_filter(markdown_text)

        assert "<p>Steps</p>" in rendered_html
        assert "<ol>" in rendered_html
        assert "<li>one</li>" in rendered_html
        assert "<li>two</li>" in rendered_html

    def test_does_not_change_list_markers_inside_fenced_code_blocks(self):
        markdown_text = "```\nNotes\n- keep literal\n1. keep literal\n```"

        rendered_html = markdown_filter(markdown_text)

        assert "<ul>" not in rendered_html
        assert "<ol>" not in rendered_html
        assert "- keep literal" in rendered_html
        assert "1. keep literal" in rendered_html


class TestReplaceQuotesFilter:
    def test_replaces_double_quotes_with_single_quotes(self):
        text_with_quotes = 'Say "hello" to "world"'

        replaced_text = replace_quotes(text_with_quotes)

        assert replaced_text == "Say 'hello' to 'world'"


class TestReplaceFilter:
    def test_replaces_first_argument_with_second_argument(self):
        source_text = "hello-world"

        replaced_text = replace(source_text, "-,_")

        assert replaced_text == "hello_world"

    def test_returns_original_value_when_arg_has_no_comma(self):
        source_text = "hello-world"

        replaced_text = replace(source_text, "invalid-arg")

        assert replaced_text == source_text


class TestMjmlConfiguredTag:
    @override_settings(MJML_URL="http://mjml-service")
    def test_returns_true_when_mjml_url_is_configured(self):
        assert mjml_configured() is True

    @override_settings(MJML_URL="")
    def test_returns_false_when_mjml_url_is_empty(self):
        assert mjml_configured() is False
