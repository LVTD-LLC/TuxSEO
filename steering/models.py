from django.db import models
from django.utils import timezone


class Project(models.Model):
    key = models.SlugField(max_length=64, unique=True)
    display_name = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    plausible_site_id = models.CharField(max_length=255, blank=True)
    posthog_project_id = models.CharField(max_length=255, blank=True)
    campaign_source = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key",)

    def __str__(self) -> str:
        return f"{self.display_name} ({self.key})"


class SourceHealth(models.Model):
    class Source(models.TextChoices):
        PLAUSIBLE = "plausible", "Plausible"
        POSTHOG = "posthog", "PostHog"
        BEACON = "beacon", "Beacon"

    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    source = models.CharField(max_length=32, choices=Source.choices)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error_message = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("project", "source")
        indexes = [
            models.Index(fields=["project", "source"]),
            models.Index(fields=["project", "last_success_at"]),
        ]


class PlausibleDaily(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    date = models.DateField()
    visitors = models.PositiveIntegerField(default=0)
    pageviews = models.PositiveIntegerField(default=0)
    pricing_visitors = models.PositiveIntegerField(default=0)
    signup_page_visitors = models.PositiveIntegerField(default=0)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("project", "date")
        indexes = [models.Index(fields=["project", "date"])]


class PosthogDaily(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    date = models.DateField()
    signups = models.PositiveIntegerField(default=0)
    activations = models.PositiveIntegerField(default=0)
    checkout_started = models.PositiveIntegerField(default=0)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("project", "date")
        indexes = [models.Index(fields=["project", "date"])]


class CampaignDaily(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    date = models.DateField()
    sent = models.PositiveIntegerField(default=0)
    delivered = models.PositiveIntegerField(default=0)
    replied = models.PositiveIntegerField(default=0)
    positive = models.PositiveIntegerField(default=0)
    meetings_booked = models.PositiveIntegerField(default=0)
    outbound_7d = models.PositiveIntegerField(default=0)
    inbound_7d = models.PositiveIntegerField(default=0)
    median_first_reply_lag_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    class Meta:
        unique_together = ("project", "date")
        indexes = [models.Index(fields=["project", "date"])]


class CampaignContact(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=255, blank=True)
    email = models.EmailField()
    full_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "email")
        indexes = [models.Index(fields=["project", "email"])]


class CampaignThread(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    campaign_id = models.CharField(max_length=128, blank=True)
    external_thread_id = models.CharField(max_length=255)
    contact = models.ForeignKey(CampaignContact, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "external_thread_id")
        indexes = [models.Index(fields=["project", "external_thread_id"])]


class CampaignMessage(models.Model):
    class Direction(models.TextChoices):
        OUTBOUND = "outbound", "Outbound"
        INBOUND = "inbound", "Inbound"

    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    thread = models.ForeignKey(CampaignThread, on_delete=models.SET_NULL, null=True, blank=True)
    external_message_id = models.CharField(max_length=255)
    direction = models.CharField(max_length=16, choices=Direction.choices)
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    replied = models.BooleanField(default=False)
    positive = models.BooleanField(default=False)
    meeting_booked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "external_message_id")
        indexes = [
            models.Index(fields=["project", "sent_at"]),
            models.Index(fields=["project", "received_at"]),
        ]


class AgentEvent(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    source = models.CharField(max_length=64)
    metric_key = models.CharField(max_length=128)
    value = models.DecimalField(max_digits=16, decimal_places=4)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    campaign_id = models.CharField(max_length=128, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "period_start"]),
            models.Index(fields=["project", "source", "metric_key"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "source", "metric_key", "period_start", "period_end", "campaign_id"],
                name="uniq_agent_event_idempotency_key",
            )
        ]


class ScorecardDaily(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    date = models.DateField()
    payload = models.JSONField(default=dict)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("project", "date")
        indexes = [models.Index(fields=["project", "date"])]


class ActionCard(models.Model):
    class Severity(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    code = models.CharField(max_length=64)
    metric_name = models.CharField(max_length=128)
    current_value = models.CharField(max_length=128)
    threshold = models.CharField(max_length=128)
    source = models.CharField(max_length=128)
    recommendation = models.TextField()
    severity = models.CharField(max_length=16, choices=Severity.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["project", "created_at"])]
