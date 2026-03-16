from django.core.management.base import BaseCommand, CommandError

from core.models import Project
from core.outcome_attribution import backfill_project_outcome_attribution


class Command(BaseCommand):
    help = "Backfill Outcome Attribution v1 events for a project"

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=int, required=True)

    def handle(self, *args, **options):
        project_id = options["project_id"]
        project = Project.objects.filter(id=project_id).first()
        if project is None:
            raise CommandError(f"Project {project_id} not found")

        result = backfill_project_outcome_attribution(project=project)
        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete for project={project_id}. created_events={result['created_events']}"
            )
        )
