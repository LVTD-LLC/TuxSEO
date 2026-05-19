# Generated manually for steering dashboard foundations.

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def seed_tuxseo_project(apps, schema_editor):
    Project = apps.get_model("steering", "Project")
    Project.objects.get_or_create(
        key="tuxseo",
        defaults={
            "display_name": "TuxSEO",
            "is_active": True,
        },
    )


def unseed_tuxseo_project(apps, schema_editor):
    Project = apps.get_model("steering", "Project")
    Project.objects.filter(key="tuxseo").delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Project",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=64, unique=True)),
                ("display_name", models.CharField(max_length=128)),
                ("is_active", models.BooleanField(default=True)),
                ("plausible_site_id", models.CharField(blank=True, max_length=255)),
                ("posthog_project_id", models.CharField(blank=True, max_length=255)),
                ("campaign_source", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("key",)},
        ),
        migrations.CreateModel(
            name="ActionCard",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64)),
                ("metric_name", models.CharField(max_length=128)),
                ("current_value", models.CharField(max_length=128)),
                ("threshold", models.CharField(max_length=128)),
                ("source", models.CharField(max_length=128)),
                ("recommendation", models.TextField()),
                (
                    "severity",
                    models.CharField(
                        choices=[("high", "High"), ("medium", "Medium"), ("low", "Low")],
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AgentEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(max_length=64)),
                ("metric_key", models.CharField(max_length=128)),
                ("value", models.DecimalField(decimal_places=4, max_digits=16)),
                ("period_start", models.DateTimeField()),
                ("period_end", models.DateTimeField()),
                ("campaign_id", models.CharField(blank=True, max_length=128)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CampaignContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_id", models.CharField(blank=True, max_length=255)),
                ("email", models.EmailField(max_length=254)),
                ("full_name", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CampaignDaily",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("sent", models.PositiveIntegerField(default=0)),
                ("delivered", models.PositiveIntegerField(default=0)),
                ("replied", models.PositiveIntegerField(default=0)),
                ("positive", models.PositiveIntegerField(default=0)),
                ("meetings_booked", models.PositiveIntegerField(default=0)),
                ("outbound_7d", models.PositiveIntegerField(default=0)),
                ("inbound_7d", models.PositiveIntegerField(default=0)),
                ("median_first_reply_lag_hours", models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CampaignThread",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("campaign_id", models.CharField(blank=True, max_length=128)),
                ("external_thread_id", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="steering.campaigncontact",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CampaignMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_message_id", models.CharField(max_length=255)),
                (
                    "direction",
                    models.CharField(
                        choices=[("outbound", "Outbound"), ("inbound", "Inbound")], max_length=16
                    ),
                ),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("received_at", models.DateTimeField(blank=True, null=True)),
                ("replied", models.BooleanField(default=False)),
                ("positive", models.BooleanField(default=False)),
                ("meeting_booked", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
                (
                    "thread",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="steering.campaignthread",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PosthogDaily",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("signups", models.PositiveIntegerField(default=0)),
                ("activations", models.PositiveIntegerField(default=0)),
                ("checkout_started", models.PositiveIntegerField(default=0)),
                ("synced_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PlausibleDaily",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("visitors", models.PositiveIntegerField(default=0)),
                ("pageviews", models.PositiveIntegerField(default=0)),
                ("pricing_visitors", models.PositiveIntegerField(default=0)),
                ("signup_page_visitors", models.PositiveIntegerField(default=0)),
                ("synced_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ScorecardDaily",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("payload", models.JSONField(default=dict)),
                ("generated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SourceHealth",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "source",
                    models.CharField(
                        choices=[("plausible", "Plausible"), ("posthog", "PostHog"), ("beacon", "Beacon")],
                        max_length=32,
                    ),
                ),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("last_error_at", models.DateTimeField(blank=True, null=True)),
                ("last_error_message", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="steering.project"),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="actioncard",
            index=models.Index(fields=["project", "created_at"], name="steering_ac_project_852372_idx"),
        ),
        migrations.AddIndex(
            model_name="agentevent",
            index=models.Index(fields=["project", "period_start"], name="steering_ag_project_8ab95f_idx"),
        ),
        migrations.AddIndex(
            model_name="agentevent",
            index=models.Index(fields=["project", "source", "metric_key"], name="steering_ag_project_73b1e5_idx"),
        ),
        migrations.AddConstraint(
            model_name="agentevent",
            constraint=models.UniqueConstraint(
                fields=("project", "source", "metric_key", "period_start", "period_end", "campaign_id"),
                name="uniq_agent_event_idempotency_key",
            ),
        ),
        migrations.AddIndex(
            model_name="campaigncontact",
            index=models.Index(fields=["project", "email"], name="steering_ca_project_546a30_idx"),
        ),
        migrations.AlterUniqueTogether(name="campaigncontact", unique_together={("project", "email")}),
        migrations.AddIndex(
            model_name="campaigndaily",
            index=models.Index(fields=["project", "date"], name="steering_ca_project_65b6d4_idx"),
        ),
        migrations.AlterUniqueTogether(name="campaigndaily", unique_together={("project", "date")}),
        migrations.AddIndex(
            model_name="campaignthread",
            index=models.Index(fields=["project", "external_thread_id"], name="steering_ca_project_e6f6f3_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="campaignthread", unique_together={("project", "external_thread_id")}
        ),
        migrations.AddIndex(
            model_name="campaignmessage",
            index=models.Index(fields=["project", "sent_at"], name="steering_ca_project_6958f0_idx"),
        ),
        migrations.AddIndex(
            model_name="campaignmessage",
            index=models.Index(fields=["project", "received_at"], name="steering_ca_project_8f9490_idx"),
        ),
        migrations.AlterUniqueTogether(name="campaignmessage", unique_together={("project", "external_message_id")}),
        migrations.AddIndex(
            model_name="posthogdaily",
            index=models.Index(fields=["project", "date"], name="steering_po_project_cd3e67_idx"),
        ),
        migrations.AlterUniqueTogether(name="posthogdaily", unique_together={("project", "date")}),
        migrations.AddIndex(
            model_name="plausibledaily",
            index=models.Index(fields=["project", "date"], name="steering_pl_project_02fd44_idx"),
        ),
        migrations.AlterUniqueTogether(name="plausibledaily", unique_together={("project", "date")}),
        migrations.AddIndex(
            model_name="scorecarddaily",
            index=models.Index(fields=["project", "date"], name="steering_sc_project_0ed2d5_idx"),
        ),
        migrations.AlterUniqueTogether(name="scorecarddaily", unique_together={("project", "date")}),
        migrations.AddIndex(
            model_name="sourcehealth",
            index=models.Index(fields=["project", "source"], name="steering_so_project_8c0f70_idx"),
        ),
        migrations.AddIndex(
            model_name="sourcehealth",
            index=models.Index(fields=["project", "last_success_at"], name="steering_so_project_8f0877_idx"),
        ),
        migrations.AlterUniqueTogether(name="sourcehealth", unique_together={("project", "source")}),
        migrations.RunPython(seed_tuxseo_project, unseed_tuxseo_project),
    ]
