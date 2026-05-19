import json
import os
from pathlib import Path

from core.content_quality import evaluate_generated_content_quality


FIXTURES_FILE_PATH = Path(__file__).parent / "fixtures" / "content_quality_fixtures.json"
BASELINE_FILE_PATH = Path(__file__).parent / "fixtures" / "content_quality_baseline.json"
ARTIFACT_FILE_PATH = Path(__file__).resolve().parents[2] / "artifacts" / "content-quality-report.json"

MINIMUM_GOOD_FIXTURE_SCORE = 0.70
MINIMUM_OVERALL_SCORE = 0.48
BASELINE_SCORE_TOLERANCE = 0.0001
BASELINE_UPDATE_ENVIRONMENT_VARIABLE = "UPDATE_CONTENT_QUALITY_BASELINE"


def test_content_quality_evaluation_is_deterministic_and_regression_safe():
    fixture_cases = _load_json_file(FIXTURES_FILE_PATH)
    first_evaluation_report = _build_evaluation_report(fixture_cases=fixture_cases)
    second_evaluation_report = _build_evaluation_report(fixture_cases=fixture_cases)

    assert first_evaluation_report == second_evaluation_report

    aggregate_score = first_evaluation_report["aggregate_score"]
    quality_label_to_score = {
        fixture_result["quality_label"]: fixture_result["aggregate_score"]
        for fixture_result in first_evaluation_report["fixtures"]
    }

    assert quality_label_to_score["good"] >= MINIMUM_GOOD_FIXTURE_SCORE
    assert aggregate_score >= MINIMUM_OVERALL_SCORE
    assert quality_label_to_score["good"] > quality_label_to_score["medium"]
    assert quality_label_to_score["medium"] > quality_label_to_score["bad"]

    baseline_report = _load_json_file(BASELINE_FILE_PATH)
    if os.getenv(BASELINE_UPDATE_ENVIRONMENT_VARIABLE) == "1":
        _write_json_file(BASELINE_FILE_PATH, first_evaluation_report)
        baseline_report = first_evaluation_report

    regression_differences = _get_regression_differences(
        baseline_report=baseline_report,
        current_report=first_evaluation_report,
    )

    _write_artifact_report(
        current_report=first_evaluation_report,
        baseline_report=baseline_report,
        regression_differences=regression_differences,
    )

    assert not regression_differences, _format_regression_failures(regression_differences)


def _build_evaluation_report(fixture_cases: list[dict]) -> dict:
    fixture_results = []
    for fixture_case in fixture_cases:
        evaluation_result = evaluate_generated_content_quality(
            title=fixture_case["title"],
            target_keywords=fixture_case["target_keywords"],
            generated_content=fixture_case["generated_content"],
        )
        fixture_results.append(
            {
                "fixture_id": fixture_case["fixture_id"],
                "quality_label": fixture_case["quality_label"],
                "aggregate_score": evaluation_result["aggregate_score"],
                "category_scores": evaluation_result["category_scores"],
            }
        )

    aggregate_score = sum(
        fixture_result["aggregate_score"] for fixture_result in fixture_results
    ) / len(fixture_results)

    return {
        "fixtures": fixture_results,
        "aggregate_score": round(aggregate_score, 4),
    }


def _get_regression_differences(
    baseline_report: dict,
    current_report: dict,
) -> list[str]:
    regression_differences = []

    aggregate_delta = abs(current_report["aggregate_score"] - baseline_report["aggregate_score"])
    if aggregate_delta > BASELINE_SCORE_TOLERANCE:
        regression_differences.append(
            (
                "aggregate_score changed from "
                f"{baseline_report['aggregate_score']} to {current_report['aggregate_score']}"
            )
        )

    current_fixtures_by_id = {
        fixture_result["fixture_id"]: fixture_result for fixture_result in current_report["fixtures"]
    }
    baseline_fixtures_by_id = {
        fixture_result["fixture_id"]: fixture_result for fixture_result in baseline_report["fixtures"]
    }

    for fixture_id, current_fixture_result in current_fixtures_by_id.items():
        baseline_fixture_result = baseline_fixtures_by_id.get(fixture_id)
        if baseline_fixture_result is None:
            regression_differences.append(f"missing fixture {fixture_id} in baseline")
            continue

        fixture_delta = abs(
            current_fixture_result["aggregate_score"] - baseline_fixture_result["aggregate_score"]
        )
        if fixture_delta > BASELINE_SCORE_TOLERANCE:
            regression_differences.append(
                (
                    f"{fixture_id} aggregate_score changed from "
                    f"{baseline_fixture_result['aggregate_score']} "
                    f"to {current_fixture_result['aggregate_score']}"
                )
            )

        for category_name, current_category_score in current_fixture_result["category_scores"].items():
            baseline_category_score = baseline_fixture_result["category_scores"][category_name]
            category_delta = abs(current_category_score - baseline_category_score)
            if category_delta > BASELINE_SCORE_TOLERANCE:
                regression_differences.append(
                    (
                        f"{fixture_id} {category_name} changed from "
                        f"{baseline_category_score} to {current_category_score}"
                    )
                )

    return regression_differences


def _write_artifact_report(
    current_report: dict,
    baseline_report: dict,
    regression_differences: list[str],
) -> None:
    artifact_directory = ARTIFACT_FILE_PATH.parent
    artifact_directory.mkdir(parents=True, exist_ok=True)

    artifact_report = {
        "current_report": current_report,
        "baseline_report": baseline_report,
        "regression_differences": regression_differences,
        "baseline_tolerance": BASELINE_SCORE_TOLERANCE,
        "minimum_good_fixture_score": MINIMUM_GOOD_FIXTURE_SCORE,
        "minimum_overall_score": MINIMUM_OVERALL_SCORE,
    }
    _write_json_file(ARTIFACT_FILE_PATH, artifact_report)


def _load_json_file(file_path: Path) -> list | dict:
    with file_path.open("r", encoding="utf-8") as file_pointer:
        return json.load(file_pointer)


def _write_json_file(file_path: Path, content: dict) -> None:
    with file_path.open("w", encoding="utf-8") as file_pointer:
        json.dump(content, file_pointer, indent=2)
        file_pointer.write("\n")


def _format_regression_failures(regression_differences: list[str]) -> str:
    if not regression_differences:
        return ""

    joined_differences = "; ".join(regression_differences)
    return f"content quality baseline regression detected: {joined_differences}"
