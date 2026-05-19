from django.urls import path

from steering.views import SteeringDashboardView

urlpatterns = [
    path("dashboard/<slug:project_key>", SteeringDashboardView.as_view(), name="steering_dashboard"),
]
