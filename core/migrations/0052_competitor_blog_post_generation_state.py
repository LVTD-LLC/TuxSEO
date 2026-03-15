from django.db import migrations, models


def mark_existing_competitor_posts_completed(apps, schema_editor):
    Competitor = apps.get_model("core", "Competitor")
    Competitor.objects.exclude(blog_post="").update(blog_post_generation_status="COMPLETED")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0051_alter_project_url_and_add_profile_url_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="competitor",
            name="blog_post_generation_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="competitor",
            name="blog_post_generation_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="competitor",
            name="blog_post_generation_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="competitor",
            name="blog_post_generation_status",
            field=models.CharField(
                choices=[
                    ("IDLE", "Idle"),
                    ("PROCESSING", "Processing"),
                    ("COMPLETED", "Completed"),
                    ("FAILED", "Failed"),
                ],
                default="IDLE",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            mark_existing_competitor_posts_completed,
            migrations.RunPython.noop,
        ),
    ]
