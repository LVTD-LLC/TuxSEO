import json
import re
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

TAXONOMY_PATH = Path(__file__).with_name("event_taxonomy.json")
EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_event_taxonomy(taxonomy: dict) -> None:
    if not isinstance(taxonomy, dict):
        raise ValueError("Invalid analytics event taxonomy: expected object")

    events = taxonomy.get("events")
    if not isinstance(events, dict) or not events:
        raise ValueError("Invalid analytics event taxonomy: missing `events` object")

    event_names = tuple(events.keys())
    if len(event_names) != len(set(event_names)):
        raise ValueError("Invalid analytics event taxonomy: duplicate event names")

    for event_name, event_definition in events.items():
        if not EVENT_NAME_PATTERN.match(event_name):
            raise ValueError(
                f"Invalid analytics event taxonomy: malformed event name `{event_name}`"
            )
        if not isinstance(event_definition, dict):
            raise ValueError(
                f"Invalid analytics event taxonomy: event `{event_name}` definition must be object"
            )
        stage = event_definition.get("stage")
        description = event_definition.get("description")
        required_properties = event_definition.get("required_properties", [])
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError(
                f"Invalid analytics event taxonomy: event `{event_name}` missing stage"
            )
        if not isinstance(description, str) or not description.strip():
            raise ValueError(
                f"Invalid analytics event taxonomy: event `{event_name}` missing description"
            )
        if not isinstance(required_properties, list):
            raise ValueError(
                f"Invalid analytics event taxonomy: event `{event_name}` required_properties must be a list"
            )
        for property_name in required_properties:
            if not isinstance(property_name, str) or not property_name.strip():
                raise ValueError(
                    f"Invalid analytics event taxonomy: event `{event_name}` has invalid required property name"
                )

    deprecated_aliases = taxonomy.get("deprecated_aliases", {})
    if not isinstance(deprecated_aliases, dict):
        raise ValueError("Invalid analytics event taxonomy: deprecated_aliases must be object")

    for deprecated_name, canonical_name in deprecated_aliases.items():
        if not EVENT_NAME_PATTERN.match(deprecated_name):
            raise ValueError(
                f"Invalid analytics event taxonomy: malformed alias `{deprecated_name}`"
            )
        if canonical_name not in events:
            raise ValueError(
                f"Invalid analytics event taxonomy: alias `{deprecated_name}` points to unknown event `{canonical_name}`"  # noqa: E501
            )
        if deprecated_name == canonical_name:
            raise ValueError(
                f"Invalid analytics event taxonomy: alias `{deprecated_name}` cannot map to itself"
            )


@lru_cache(maxsize=1)
def _load_event_taxonomy() -> dict:
    with TAXONOMY_PATH.open(encoding="utf-8") as event_taxonomy_file:
        taxonomy = json.load(event_taxonomy_file)

    _validate_event_taxonomy(taxonomy)
    return taxonomy


EVENT_TAXONOMY = _load_event_taxonomy()
EVENT_TAXONOMY_VERSION = EVENT_TAXONOMY.get("version", 1)
ANALYTICS_EVENT_NAMES = tuple(EVENT_TAXONOMY["events"].keys())
DEPRECATED_ANALYTICS_EVENT_ALIASES = MappingProxyType(
    dict(EVENT_TAXONOMY.get("deprecated_aliases", {}))
)


def _to_constant_name(event_name: str) -> str:
    return event_name.upper().replace("-", "_")


ANALYTICS_EVENTS = SimpleNamespace(
    **{
        _to_constant_name(event_name): event_name
        for event_name in ANALYTICS_EVENT_NAMES
    }
)


def normalize_event_name(event_name: str) -> str:
    return DEPRECATED_ANALYTICS_EVENT_ALIASES.get(event_name, event_name)


def is_known_event_name(event_name: str) -> bool:
    return normalize_event_name(event_name) in ANALYTICS_EVENT_NAMES


def get_event_definition(event_name: str) -> dict | None:
    canonical_event_name = normalize_event_name(event_name)
    return EVENT_TAXONOMY["events"].get(canonical_event_name)
