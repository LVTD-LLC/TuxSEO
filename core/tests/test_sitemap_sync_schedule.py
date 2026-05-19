import pytest
from django.test import override_settings
from django_q.models import Schedule

from core.scheduled_tasks import ensure_periodic_sitemap_sync_schedule


@pytest.mark.django_db
@override_settings(SITEMAP_SYNC_SCHEDULER_ENABLED=True, SITEMAP_SYNC_INTERVAL_HOURS=4)
def test_ensure_periodic_sitemap_sync_schedule_creates_or_updates_record():
    created_message = ensure_periodic_sitemap_sync_schedule()
    assert "created" in created_message.lower() or "unchanged" in created_message.lower()

    schedule = Schedule.objects.get(name="Periodic sitemap sync")
    assert schedule.func == "core.tasks.sync_all_projects_with_sitemaps"
    assert schedule.schedule_type == Schedule.MINUTES
    assert schedule.minutes == 240

    schedule.minutes = 60
    schedule.save(update_fields=["minutes"])

    updated_message = ensure_periodic_sitemap_sync_schedule()
    assert "updated" in updated_message.lower() or "unchanged" in updated_message.lower()

    schedule.refresh_from_db()
    assert schedule.minutes == 240


@pytest.mark.django_db
@override_settings(SITEMAP_SYNC_SCHEDULER_ENABLED=False)
def test_ensure_periodic_sitemap_sync_schedule_respects_disable_flag():
    message = ensure_periodic_sitemap_sync_schedule()
    assert "disabled" in message.lower()
    assert not Schedule.objects.filter(name="Periodic sitemap sync").exists()
