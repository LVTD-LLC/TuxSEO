import json

import pytest


@pytest.fixture(autouse=True)
def test_webpack_manifest(settings, tmp_path):
    """Provide a minimal webpack manifest for template-rendering tests."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "entrypoints": {
                    "index": {
                        "assets": {
                            "js": ["/static/index.js"],
                            "css": ["/static/index.css"],
                        }
                    }
                },
                "index.js": "/static/index.js",
                "index.css": "/static/index.css"
            }
        ),
        encoding="utf-8",
    )
    settings.WEBPACK_LOADER["MANIFEST_FILE"] = str(manifest_path)

    from webpack_boilerplate.loader import WebpackLoader

    WebpackLoader._assets = {}
