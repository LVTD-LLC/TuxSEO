from django.test import Client, TestCase
from django.urls import reverse

from steering.models import Project


class SteeringDashboardViewTests(TestCase):
    def test_dashboard_route_resolves_project(self):
        Project.objects.get_or_create(
            key="tuxseo",
            defaults={"display_name": "TuxSEO"},
        )

        response = Client().get(reverse("steering_dashboard", kwargs={"project_key": "tuxseo"}))

        assert response.status_code == 200
        assert b"Steering Dashboard" in response.content
        assert b"TuxSEO" in response.content
