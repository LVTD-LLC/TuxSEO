from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

from django.utils import timezone

from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

ATTRIBUTION_SCHEMA_VERSION = 1
ATTRIBUTION_SESSION_KEY = "acquisition_attribution_v1"

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
CLICK_ID_KEYS = (
    "gclid",
    "fbclid",
    "ttclid",
    "twclid",
    "xclid",
    "msclkid",
    "li_fat_id",
    "rdt_cid",
)
CANONICAL_KEYS = (
    "channel",
    "platform",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "creative_id",
    "creative_key",
    "copy_variant",
    "landing_page",
    "offer",
    "geo",
    "device",
)

_QUERY_TO_CANONICAL_KEY = {
    "campaign_id": "campaign_id",
    "utm_id": "campaign_id",
    "campaign_name": "campaign_name",
    "adset_id": "adset_id",
    "adset_name": "adset_name",
    "ad_group_id": "adset_id",
    "adgroup_id": "adset_id",
    "ad_id": "ad_id",
    "ad_name": "ad_id",
    "creative_id": "creative_id",
    "creative_key": "creative_key",
    "copy_variant": "copy_variant",
    "variant": "copy_variant",
    "offer": "offer",
    "geo": "geo",
    "device": "device",
    "platform": "platform",
    "channel": "channel",
}

_EMAIL_LIKE_MARKER = "@"
_MAX_VALUE_LENGTH = 200


@dataclass(frozen=True)
class AttributionSnapshot:
    first_touch: dict[str, Any]
    latest_touch: dict[str, Any]


class AttributionValidationError(Exception):
    pass


def _safe_value(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if len(text) > _MAX_VALUE_LENGTH:
        text = text[:_MAX_VALUE_LENGTH]

    if _EMAIL_LIKE_MARKER in text and "." in text.split(_EMAIL_LIKE_MARKER)[-1]:
        return None

    return text


def _channel_from_utm_source(utm_source: str | None) -> str | None:
    if not utm_source:
        return None

    source = utm_source.lower().strip()
    if source in {"google", "adwords", "gads"}:
        return "google"
    if source in {"meta", "facebook", "instagram", "fb"}:
        return "meta"
    if source in {"reddit"}:
        return "reddit"
    if source in {"x", "twitter"}:
        return "x"

    return source


def _platform_from_query(params: dict[str, str], *, channel: str | None) -> str | None:
    platform = params.get("platform")
    if platform:
        return platform
    return channel


def _extract_attribution_from_query_dict(
    params: dict[str, str],
    *,
    path: str,
    timestamp: datetime,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "timestamp": timestamp.isoformat(),
        "landing_page": path,
    }

    for key in UTM_KEYS:
        value = _safe_value(params.get(key))
        if value:
            payload[key] = value

    for key in CLICK_ID_KEYS:
        value = _safe_value(params.get(key))
        if value:
            payload[key] = value

    for query_key, canonical_key in _QUERY_TO_CANONICAL_KEY.items():
        value = _safe_value(params.get(query_key))
        if value:
            payload[canonical_key] = value

    if not payload.get("campaign_name") and payload.get("utm_campaign"):
        payload["campaign_name"] = payload["utm_campaign"]

    inferred_channel = _channel_from_utm_source(payload.get("utm_source"))
    if inferred_channel and not payload.get("channel"):
        payload["channel"] = inferred_channel

    if not payload.get("platform"):
        payload["platform"] = _platform_from_query(params, channel=payload.get("channel"))

    return payload


def _extract_query_params(query_string: str) -> dict[str, str]:
    parsed = parse_qs(query_string, keep_blank_values=False)
    return {key: values[0] for key, values in parsed.items() if values}


def request_attribution_params(request) -> dict[str, Any]:
    params = _extract_query_params(request.META.get("QUERY_STRING", ""))
    if not params:
        return {}

    has_attribution_signal = any(
        key in params
        for key in (
            *UTM_KEYS,
            *CLICK_ID_KEYS,
            *_QUERY_TO_CANONICAL_KEY.keys(),
        )
    )

    if not has_attribution_signal:
        return {}

    now = timezone.now()
    return _extract_attribution_from_query_dict(params, path=request.path, timestamp=now)


def capture_request_attribution(request) -> AttributionSnapshot | None:
    current = request_attribution_params(request)
    if not current:
        return None

    existing = request.session.get(ATTRIBUTION_SESSION_KEY) or {}
    first_touch = existing.get("first_touch") or current

    payload = {
        "schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "first_touch": first_touch,
        "latest_touch": current,
    }

    request.session[ATTRIBUTION_SESSION_KEY] = payload
    request.session.modified = True

    return AttributionSnapshot(first_touch=first_touch, latest_touch=current)


def read_attribution_from_session(request) -> AttributionSnapshot | None:
    payload = request.session.get(ATTRIBUTION_SESSION_KEY) or {}
    first_touch = payload.get("first_touch")
    latest_touch = payload.get("latest_touch")

    if not isinstance(first_touch, dict) or not isinstance(latest_touch, dict):
        return None

    return AttributionSnapshot(first_touch=first_touch, latest_touch=latest_touch)


def sync_profile_attribution_from_request(*, profile, request) -> None:
    snapshot = read_attribution_from_session(request)
    if not snapshot:
        return

    update_fields: list[str] = []

    if not profile.first_touch_attribution:
        profile.first_touch_attribution = snapshot.first_touch
        update_fields.append("first_touch_attribution")

    profile.latest_touch_attribution = snapshot.latest_touch
    update_fields.append("latest_touch_attribution")

    if update_fields:
        update_fields.append("updated_at")
        profile.save(update_fields=update_fields)


def sync_project_attribution_from_profile(*, project, profile) -> None:
    update_fields: list[str] = []

    if not project.first_touch_attribution and profile.first_touch_attribution:
        project.first_touch_attribution = profile.first_touch_attribution
        update_fields.append("first_touch_attribution")

    if (
        profile.latest_touch_attribution
        and project.latest_touch_attribution != profile.latest_touch_attribution
    ):
        project.latest_touch_attribution = profile.latest_touch_attribution
        update_fields.append("latest_touch_attribution")

    if update_fields:
        update_fields.append("updated_at")
        project.save(update_fields=update_fields)


def _flatten_touch(prefix: str, touch: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}

    for key in (*CANONICAL_KEYS, *UTM_KEYS, *CLICK_ID_KEYS):
        value = touch.get(key)
        if value in (None, ""):
            continue
        flat[f"{prefix}_{key}"] = value

    if touch.get("timestamp"):
        flat[f"{prefix}_timestamp"] = touch["timestamp"]

    return flat


def validate_attribution_payload(payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str) and len(value) > _MAX_VALUE_LENGTH:
            raise AttributionValidationError(f"{key} exceeds max length")


def build_attribution_event_properties(*, profile, project=None) -> dict[str, Any]:
    source = project if project is not None else profile

    first_touch_value = getattr(source, "first_touch_attribution", None)
    latest_touch_value = getattr(source, "latest_touch_attribution", None)

    first_touch = first_touch_value if isinstance(first_touch_value, dict) else {}
    latest_touch = latest_touch_value if isinstance(latest_touch_value, dict) else {}

    if not first_touch and not latest_touch:
        return {}

    properties: dict[str, Any] = {
        "acquisition_schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "attribution_scope": "project" if project is not None else "profile",
    }

    properties.update(_flatten_touch("first_touch", first_touch))
    properties.update(_flatten_touch("latest_touch", latest_touch))

    for key in CANONICAL_KEYS:
        latest_value = latest_touch.get(key)
        if latest_value not in (None, ""):
            properties[key] = latest_value

    for key in UTM_KEYS:
        latest_value = latest_touch.get(key)
        if latest_value not in (None, ""):
            properties[key] = latest_value

    for key in CLICK_ID_KEYS:
        latest_value = latest_touch.get(key)
        if latest_value not in (None, ""):
            properties[key] = latest_value

    validate_attribution_payload(properties)
    return properties
