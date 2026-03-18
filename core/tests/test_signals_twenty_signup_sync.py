from types import SimpleNamespace
from unittest.mock import patch

from core.signals import parse_sitemap_on_save


@patch("core.signals.async_task")
def test_project_create_schedules_twenty_signup_sync_task_when_enabled(mock_async_task):
    instance = SimpleNamespace(id=123, name="Signal Enabled", sitemap_url="")

    with patch("core.signals.settings.TWENTY_SIGNUP_SYNC_ENABLED", True):
        parse_sitemap_on_save(
            sender=None,
            instance=instance,
            created=True,
            update_fields=None,
        )

    twenty_calls = [
        call
        for call in mock_async_task.call_args_list
        if call.args and call.args[0] == "core.tasks.sync_signup_project_to_twenty"
    ]

    assert len(twenty_calls) == 1
    assert twenty_calls[0].args[1] == instance.id
    assert twenty_calls[0].kwargs["group"] == "Twenty CRM Signup Sync"


@patch("core.signals.async_task")
def test_project_create_does_not_schedule_twenty_signup_sync_when_disabled(mock_async_task):
    instance = SimpleNamespace(id=321, name="Signal Disabled", sitemap_url="")

    with patch("core.signals.settings.TWENTY_SIGNUP_SYNC_ENABLED", False):
        parse_sitemap_on_save(
            sender=None,
            instance=instance,
            created=True,
            update_fields=None,
        )

    twenty_calls = [
        call
        for call in mock_async_task.call_args_list
        if call.args and call.args[0] == "core.tasks.sync_signup_project_to_twenty"
    ]

    assert len(twenty_calls) == 0
