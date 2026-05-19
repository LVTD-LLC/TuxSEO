from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from steering.models import Project


class AddProjectCommandTests(TestCase):
    def test_add_project_creates_project(self):
        call_command(
            "add_project",
            project_key="cleanapp",
            display_name="Cleanapp",
            plausible_site_id="cleanapp.dev",
            posthog_project_id="1122",
            campaign_source="beacon",
            stdout=StringIO(),
        )

        project = Project.objects.get(key="cleanapp")
        assert project.display_name == "Cleanapp"
        assert project.plausible_site_id == "cleanapp.dev"
        assert project.posthog_project_id == "1122"
        assert project.campaign_source == "beacon"
        assert project.is_active is True
