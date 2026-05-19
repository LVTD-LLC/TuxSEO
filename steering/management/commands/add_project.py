from django.core.management.base import BaseCommand, CommandError

from steering.models import Project


class Command(BaseCommand):
    help = "Onboard a steering-dashboard project without schema changes."

    def add_arguments(self, parser):
        parser.add_argument("--project-key", required=True)
        parser.add_argument("--display-name", required=True)
        parser.add_argument("--plausible-site-id", default="")
        parser.add_argument("--posthog-project-id", default="")
        parser.add_argument("--campaign-source", default="")

    def handle(self, *args, **options):
        project_key = options["project_key"].strip().lower()
        if not project_key:
            raise CommandError("--project-key cannot be blank")

        project, created = Project.objects.get_or_create(
            key=project_key,
            defaults={
                "display_name": options["display_name"],
                "plausible_site_id": options["plausible_site_id"],
                "posthog_project_id": options["posthog_project_id"],
                "campaign_source": options["campaign_source"],
                "is_active": True,
            },
        )

        if not created:
            project.display_name = options["display_name"]
            project.plausible_site_id = options["plausible_site_id"]
            project.posthog_project_id = options["posthog_project_id"]
            project.campaign_source = options["campaign_source"]
            project.is_active = True
            project.save(update_fields=[
                "display_name",
                "plausible_site_id",
                "posthog_project_id",
                "campaign_source",
                "is_active",
                "updated_at",
            ])
            self.stdout.write(self.style.WARNING(f"Updated existing project '{project_key}'"))
            return

        self.stdout.write(self.style.SUCCESS(f"Created project '{project_key}'"))
