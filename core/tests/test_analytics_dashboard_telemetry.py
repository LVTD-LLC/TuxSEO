from pathlib import Path

from core.analytics import EVENT_TAXONOMY


def test_analytics_dashboard_controller_tracks_v1_analytics_events():
    controller_path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "controllers" / "analytics_dashboard_controller.js"
    source = controller_path.read_text(encoding="utf-8")

    assert "window.posthog.capture" in source
    assert '"analytics_page_viewed"' in source
    assert '"analytics_date_range_changed"' in source
    assert '"analytics_refresh_clicked"' in source
    assert '"analytics_source_error_shown"' in source
    assert "project_id" in source
    assert "date_range_start" in source
    assert "date_range_end" in source
    assert "range_days" in source

    assert source.index("this.captureDateRangeChangedIfNeeded({") < source.index(
        'this.captureEvent("analytics_refresh_clicked", {'
    )
    assert "if (end.getTime() < start.getTime())" in source


def test_analytics_source_error_event_requires_source_status_property():
    required_properties = EVENT_TAXONOMY["events"]["analytics_source_error_shown"]["required_properties"]
    assert "source_status" in required_properties
