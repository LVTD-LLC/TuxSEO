from django.contrib import admin

from steering import models


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("key", "display_name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("key", "display_name")


@admin.register(models.SourceHealth)
class SourceHealthAdmin(admin.ModelAdmin):
    list_display = ("project", "source", "last_success_at", "last_error_at")
    list_filter = ("source",)


admin.site.register(models.PlausibleDaily)
admin.site.register(models.PosthogDaily)
admin.site.register(models.CampaignDaily)
admin.site.register(models.CampaignContact)
admin.site.register(models.CampaignThread)
admin.site.register(models.CampaignMessage)
admin.site.register(models.AgentEvent)
admin.site.register(models.ScorecardDaily)
admin.site.register(models.ActionCard)
