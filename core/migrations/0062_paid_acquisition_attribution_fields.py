from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0061_custom_post_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="first_touch_attribution",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="profile",
            name="latest_touch_attribution",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="project",
            name="first_touch_attribution",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="project",
            name="latest_touch_attribution",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
