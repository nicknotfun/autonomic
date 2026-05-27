# Attribute coercion helpers used by typed output/source models.
from __future__ import annotations

from typing import TypeAlias

AttributeValue: TypeAlias = str | int | bool | None


def first_attr(attrs: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = attrs.get(key)
        if value not in (None, ""):
            return value
    return None


def zone_child_attrs(attrs: dict[str, str]) -> dict[str, str]:
    rendered = dict(attrs)
    if "id" not in rendered and rendered.get("eventId"):
        rendered["id"] = rendered["eventId"]
    if "isOn" not in rendered and rendered.get("on") is not None:
        rendered["isOn"] = rendered["on"]
    return rendered


def disabled_attr(attrs: dict[str, str]) -> bool | None:
    disabled = first_attr(
        attrs,
        "disabled",
        "Disabled",
        "isDisabled",
        "IsDisabled",
        "hidden",
        "Hidden",
        "isHidden",
        "IsHidden",
        "invalid",
        "Invalid",
        "isInvalid",
        "IsInvalid",
    )
    if disabled is not None:
        return truthy(disabled, extra_true={"disabled", "hidden", "invalid"})

    enabled = first_attr(attrs, "enabled", "Enabled", "isEnabled", "IsEnabled")
    if enabled is not None:
        return not truthy(enabled)

    available = first_attr(
        attrs,
        "available",
        "Available",
        "isAvailable",
        "IsAvailable",
        "sourceAvailable",
        "SourceAvailable",
        "zoneAvailable",
        "ZoneAvailable",
        "avail",
        "Avail",
    )
    if available is not None:
        return not truthy(available)

    return None


def truthy(value: AttributeValue, *, extra_true: set[str] | None = None) -> bool:
    truthy_values = {"1", "true", "yes", "on", "enabled", "available", "valid"}
    if extra_true:
        truthy_values.update(extra_true)
    return str(value).strip().lower() in truthy_values


def bool_attr(attrs: dict[str, str], *keys: str) -> bool | None:
    value = first_attr(attrs, *keys)
    if value is None:
        return None
    return truthy(value)


def int_attr(attrs: dict[str, str], *keys: str) -> int | None:
    value = first_attr(attrs, *keys)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
