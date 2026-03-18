from unittest.mock import patch

from core.scheduled_tasks import sync_connected_project_analytics


def test_sync_connected_project_analytics_alias_calls_dispatcher():
    with patch("core.scheduled_tasks.schedule_all_connected_project_analytics_syncs") as mock_schedule:
        mock_schedule.return_value = {"scheduled": 3}

        result = sync_connected_project_analytics()

    assert result == "Analytics sync scheduling completed: scheduled=3"
    mock_schedule.assert_called_once_with()
