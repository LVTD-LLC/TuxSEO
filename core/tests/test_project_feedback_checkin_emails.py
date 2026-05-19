from datetime import timedelta
from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from core.choices import EmailType
from core.models import EmailSent, Profile, Project
from core.scheduled_tasks import schedule_project_feedback_checkin_emails
from core.tasks import send_project_feedback_checkin_email


@pytest.mark.django_db
class TestSendProjectFeedbackCheckinEmail:
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    @patch("core.tasks.async_task")
    def test_sends_plain_text_email_and_tracks_it(self, mock_async_task):
        user = User.objects.create_user(
            username="feedback-user",
            email="feedback@example.com",
            password="password123",
        )
        profile = Profile.objects.get(user=user)

        Project.objects.create(
            profile=profile,
            url="https://feedback-project.example.com",
            name="Feedback Project",
        )

        result = send_project_feedback_checkin_email(profile.id)

        assert result == f"Email sent to {user.email}"
        assert len(mail.outbox) == 1

        sent_email_message = mail.outbox[0]
        assert sent_email_message.subject == "Quick check-in from Rasul at TuxSEO"
        assert sent_email_message.from_email == "rasul@tuxseo.com"
        assert sent_email_message.to == [user.email]
        assert "Do you have any suggestions for improvement?" in sent_email_message.body
        assert getattr(sent_email_message, "alternatives", None) in (None, [])

        mock_async_task.assert_called_once_with(
            "core.tasks.track_email_sent",
            email_address=user.email,
            email_type=EmailType.PROJECT_FEEDBACK_CHECKIN,
            profile=profile,
            group="Track Email Sent",
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    @patch("core.tasks.async_task")
    def test_skips_when_email_was_already_sent(self, mock_async_task):
        user = User.objects.create_user(
            username="already-sent-user",
            email="already-sent@example.com",
            password="password123",
        )
        profile = Profile.objects.get(user=user)

        Project.objects.create(
            profile=profile,
            url="https://already-sent-project.example.com",
            name="Already Sent Project",
        )

        EmailSent.objects.create(
            email_address=user.email,
            email_type=EmailType.PROJECT_FEEDBACK_CHECKIN,
            profile=profile,
        )

        result = send_project_feedback_checkin_email(profile.id)

        assert result == f"Email already sent to {user.email}, skipping"
        assert len(mail.outbox) == 0
        mock_async_task.assert_not_called()


@pytest.mark.django_db
class TestScheduleProjectFeedbackCheckinEmails:
    @patch("core.scheduled_tasks.async_task")
    def test_schedules_only_recent_verified_profiles_with_projects(self, mock_async_task):
        now = timezone.now()

        eligible_user = User.objects.create_user(
            username="eligible-user",
            email="eligible@example.com",
            password="password123",
        )
        eligible_user.date_joined = now - timedelta(hours=12)
        eligible_user.save(update_fields=["date_joined"])
        eligible_profile = Profile.objects.get(user=eligible_user)
        Project.objects.create(
            profile=eligible_profile,
            url="https://eligible-project.example.com",
            name="Eligible Project",
        )
        EmailAddress.objects.create(
            user=eligible_user,
            email=eligible_user.email,
            verified=True,
            primary=True,
        )

        older_user = User.objects.create_user(
            username="older-user",
            email="older@example.com",
            password="password123",
        )
        older_user.date_joined = now - timedelta(days=3)
        older_user.save(update_fields=["date_joined"])
        older_profile = Profile.objects.get(user=older_user)
        Project.objects.create(
            profile=older_profile,
            url="https://older-project.example.com",
            name="Older Project",
        )
        EmailAddress.objects.create(
            user=older_user,
            email=older_user.email,
            verified=True,
            primary=True,
        )

        unverified_user = User.objects.create_user(
            username="unverified-user",
            email="unverified@example.com",
            password="password123",
        )
        unverified_user.date_joined = now - timedelta(hours=8)
        unverified_user.save(update_fields=["date_joined"])
        unverified_profile = Profile.objects.get(user=unverified_user)
        Project.objects.create(
            profile=unverified_profile,
            url="https://unverified-project.example.com",
            name="Unverified Project",
        )

        sent_user = User.objects.create_user(
            username="sent-user",
            email="sent@example.com",
            password="password123",
        )
        sent_user.date_joined = now - timedelta(hours=4)
        sent_user.save(update_fields=["date_joined"])
        sent_profile = Profile.objects.get(user=sent_user)
        Project.objects.create(
            profile=sent_profile,
            url="https://sent-project.example.com",
            name="Sent Project",
        )
        EmailAddress.objects.create(
            user=sent_user,
            email=sent_user.email,
            verified=True,
            primary=True,
        )
        EmailSent.objects.create(
            email_address=sent_user.email,
            email_type=EmailType.PROJECT_FEEDBACK_CHECKIN,
            profile=sent_profile,
        )

        no_project_user = User.objects.create_user(
            username="no-project-user",
            email="no-project@example.com",
            password="password123",
        )
        no_project_user.date_joined = now - timedelta(hours=6)
        no_project_user.save(update_fields=["date_joined"])
        EmailAddress.objects.create(
            user=no_project_user,
            email=no_project_user.email,
            verified=True,
            primary=True,
        )

        result = schedule_project_feedback_checkin_emails()

        mock_async_task.assert_called_once_with(
            "core.tasks.send_project_feedback_checkin_email",
            eligible_profile.id,
            group="Project Feedback Check-in",
        )
        assert "Profiles scheduled: 1" in result
