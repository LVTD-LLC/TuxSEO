from django.conf import settings
import structlog


def get_tuxseo_logger(name):
    """This will add a `tuxseo` prefix to logger for easy configuration."""

    return structlog.get_logger(
        f"tuxseo.{name}",
        project="tuxseo",
        environment=settings.ENVIRONMENT,
        service="tuxseo-backend",
        module=name,
    )
