from django_q.tasks import async_task

from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


def enqueue_track_event(*, profile_id: int, event_name: str, properties: dict, source_function: str) -> None:
    """Best-effort analytics event enqueue.

    Product behavior must never fail because analytics queueing fails.
    """
    try:
        async_task(
            "core.tasks.track_event",
            profile_id=profile_id,
            event_name=event_name,
            properties=properties,
            source_function=source_function,
            group="Track Event",
        )
    except Exception as error:
        logger.warning(
            "[Analytics] Failed to enqueue tracking event",
            event_name=event_name,
            profile_id=profile_id,
            source_function=source_function,
            error=str(error),
        )
