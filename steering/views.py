from django.http import Http404
from django.views.generic import TemplateView

from steering.models import Project


class SteeringDashboardView(TemplateView):
    template_name = "steering/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project_key = kwargs["project_key"]
        try:
            project = Project.objects.get(key=project_key, is_active=True)
        except Project.DoesNotExist as error:
            raise Http404("Unknown project_key") from error

        context["project"] = project
        context["sections"] = [
            "KPI cards",
            "Funnel summary",
            "Cold-email summary",
            "Campaign activity timeline",
            "Source health",
            "Action-needed cards",
        ]
        return context
