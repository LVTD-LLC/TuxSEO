from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0062_paid_acquisition_attribution_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectpage",
            name="sitemap_is_stale",
            field=models.BooleanField(
                default=False,
                help_text="True when this URL was previously discovered via sitemap but is missing in the latest sync run.",
            ),
        ),
        migrations.AddField(
            model_name="projectpage",
            name="sitemap_last_seen_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When this URL was last seen in a successful sitemap sync.",
            ),
        ),
    ]
