import json

from core.seo_analysis import analyze_json_ld_schema


def test_json_ld_analysis_detected_and_looks_ok_for_webpage():
    html = """
    <html><head>
      <script type=\"application/ld+json\">
      {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "Features",
        "url": "https://example.com/features"
      }
      </script>
    </head></html>
    """

    result = analyze_json_ld_schema(
        page_url="https://example.com/features",
        page_type="product page",
        title="Features",
        description="Feature overview",
        html_content=html,
    )

    assert result["state"] == "ok"
    assert result["status_label"] == "Detected & looks okay"
    assert result["detected_script_blocks"] == 1
    assert result["total_items"] == 1
    assert result["parse_errors"] == []
    assert result["starter_suggestion"] is None


def test_json_ld_analysis_detected_but_issues_and_starter_available():
    html = """
    <script type=\"application/ld+json\">
      {
        "@context": "https://not-schema.example",
        "@type": "Article",
        "headline": "Bad context article"
      }
    </script>
    """

    result = analyze_json_ld_schema(
        page_url="https://example.com/blog/post",
        page_type="blog post",
        title="Blog post",
        description="Description",
        html_content=html,
    )

    assert result["state"] == "issues"
    assert result["status_label"] == "Detected but issues"
    assert result["parse_errors"] == []
    assert result["items"][0]["is_valid"] is False
    assert "Missing required field for Article: author" in result["items"][0]["issues"]
    assert result["starter_suggestion"]["template_type"] == "Article"

    starter_json = result["starter_suggestion"]["json_ld_pretty"]
    parsed_starter = json.loads(starter_json)
    assert parsed_starter["@type"] == "Article"


def test_json_ld_analysis_missing_scripts_returns_missing_state_with_webpage_starter():
    result = analyze_json_ld_schema(
        page_url="https://example.com/pricing",
        page_type="pricing page",
        title="Pricing",
        description="Simple pricing",
        html_content="<html><body><h1>Pricing</h1></body></html>",
    )

    assert result["state"] == "missing"
    assert result["status_label"] == "Missing (suggested starter available)"
    assert result["detected_script_blocks"] == 0
    assert result["starter_suggestion"]["template_type"] == "WebPage"


def test_json_ld_analysis_malformed_json_reports_parse_errors_without_crashing():
    html = """
    <script type=\"application/ld+json\">
      {"@context":"https://schema.org", "@type":"WebPage",
    </script>
    """

    result = analyze_json_ld_schema(
        page_url="https://example.com/landing",
        page_type="landing page",
        title="Landing",
        description="Landing description",
        html_content=html,
    )

    assert result["state"] == "issues"
    assert result["detected_script_blocks"] == 1
    assert len(result["parse_errors"]) == 1
    assert "malformed JSON" in result["parse_errors"][0]
    assert result["starter_suggestion"] is not None
