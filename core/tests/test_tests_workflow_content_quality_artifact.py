import re
from pathlib import Path


WORKFLOW_FILE_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"


def test_tests_workflow_uploads_content_quality_report_even_on_failures():
    workflow_file_content = WORKFLOW_FILE_PATH.read_text(encoding="utf-8")
    upload_artifact_step_pattern = re.compile(
        r"- name: Upload content quality report\s+"
        r"if: always\(\)\s+"
        r"uses: actions/upload-artifact@v4\s+"
        r"with:\s+"
        r"name: content-quality-report\s+"
        r"path: artifacts/content-quality-report.json",
        re.MULTILINE,
    )

    assert upload_artifact_step_pattern.search(workflow_file_content)
