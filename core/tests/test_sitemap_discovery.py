from types import SimpleNamespace

from core.tasks import discover_sitemap_url


class MockResponse(SimpleNamespace):
    @property
    def ok(self):
        return 200 <= self.status_code < 300


def test_discover_sitemap_url_prefers_robots_txt_sitemap(monkeypatch):
    def fake_get(url, timeout):
        if url == "https://example.com/robots.txt":
            return MockResponse(
                status_code=200,
                text="User-agent: *\nSitemap: /custom-sitemap.xml\n",
                content=b"User-agent: *\nSitemap: /custom-sitemap.xml\n",
            )
        if url == "https://example.com/custom-sitemap.xml":
            return MockResponse(
                status_code=200,
                text="",
                content=b"<?xml version='1.0'?><urlset></urlset>",
            )

        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr("core.tasks.requests.get", fake_get)

    sitemap_url, status = discover_sitemap_url("https://example.com/product")

    assert status == "found"
    assert sitemap_url == "https://example.com/custom-sitemap.xml"


def test_discover_sitemap_url_falls_back_to_common_paths(monkeypatch):
    def fake_get(url, timeout):
        if url == "https://example.com/robots.txt":
            return MockResponse(status_code=404, text="", content=b"")
        if url == "https://example.com/sitemap.xml":
            return MockResponse(
                status_code=200,
                text="",
                content=b"<?xml version='1.0'?><sitemapindex></sitemapindex>",
            )

        return MockResponse(status_code=404, text="", content=b"")

    monkeypatch.setattr("core.tasks.requests.get", fake_get)

    sitemap_url, status = discover_sitemap_url("https://example.com")

    assert status == "found"
    assert sitemap_url == "https://example.com/sitemap.xml"


def test_discover_sitemap_url_reports_parse_fail_when_xml_is_invalid(monkeypatch):
    def fake_get(url, timeout):
        if url == "https://example.com/robots.txt":
            return MockResponse(status_code=404, text="", content=b"")

        return MockResponse(
            status_code=200,
            text="<html>This is not a sitemap</html>",
            content=b"<html>This is not a sitemap</html>",
        )

    monkeypatch.setattr("core.tasks.requests.get", fake_get)

    sitemap_url, status = discover_sitemap_url("https://example.com")

    assert sitemap_url is None
    assert status == "parse_fail"
