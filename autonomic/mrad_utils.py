# MRAD/MAS parsing and normalization helpers used by the low-level client.
from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping
from typing import TypeAlias

from .exceptions import CommandError
from .models import AutonomicOutput, output_ref
from .protocol_types import CommandResponse, StatusSnapshot
from .mrad_types import MRADCommandHelp, MRADVersion, OutputRef, XmlMode

StatusUpdateValue: TypeAlias = str | int | bool | None | dict[str, str]


def toggle_state(state: bool | str) -> str:
    if isinstance(state, bool):
        return "On" if state else "Off"
    normalized = state.strip().lower()
    if normalized in {"true", "on", "1", "yes"}:
        return "On"
    if normalized in {"false", "off", "0", "no"}:
        return "Off"
    if normalized == "toggle":
        return "Toggle"
    raise ValueError("state must be On, Off, Toggle, true, false, or bool")


def bool_toggle_state(state: bool | str) -> str:
    if isinstance(state, bool):
        return "True" if state else "False"
    normalized = state.strip().lower()
    if normalized in {"true", "on", "1", "yes"}:
        return "True"
    if normalized in {"false", "off", "0", "no"}:
        return "False"
    if normalized == "toggle":
        return "Toggle"
    raise ValueError("state must be true, false, toggle, On, Off, or bool")


def xml_mode(mode: XmlMode | bool) -> str:
    if isinstance(mode, bool):
        return "Lists" if mode else "None"
    normalized = mode.strip().lower()
    if normalized == "lists":
        return "Lists"
    if normalized == "none":
        return "None"
    raise ValueError("xml mode must be Lists, None, true, or false")


def response_value(response: CommandResponse, command: str) -> str | None:
    line = response.first_line
    if not line:
        return None
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split(maxsplit=1)
    if not parts:
        return None
    if parts[0].lower() == command.lower():
        return " ".join(parts[1:]) if len(parts) > 1 else ""
    return line


def parse_versions(lines: Iterable[str]) -> list[MRADVersion]:
    versions: list[MRADVersion] = []
    for line in lines:
        parts = shlex.split(line)
        payload = " ".join(parts[1:]) if parts and parts[0].lower() == "versions" else line
        for item in payload.split(","):
            values = parse_version_fields(item)
            component = next(iter(values), "")
            identifier = values.get(component, "") if component else ""
            if not component or not identifier:
                continue
            versions.append(
                MRADVersion(
                    component=component,
                    identifier=identifier,
                    sku=values.get("SKU"),
                    firmware=values.get("FW"),
                    raw=item.strip(),
                )
            )
    return versions


def parse_version_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in text.strip().split():
        if ":" not in token:
            continue
        key, value = token.split(":", 1)
        if key and value:
            fields[key] = value
    return fields


HELP_ROW_RE = re.compile(r"^(?P<command>\S+)\s+-\s+(?P<description>.*)$")


def parse_command_help(lines: Iterable[str]) -> list[MRADCommandHelp]:
    catalog: list[MRADCommandHelp] = []
    command: str | None = None
    description_parts: list[str] = []
    usage: list[str] = []
    raw_lines: list[str] = []

    def flush() -> None:
        nonlocal command, description_parts, usage, raw_lines
        if command is not None:
            # MRAD wraps long help descriptions onto following lines; collect
            # them until the next "Command - description" row starts.
            catalog.append(
                MRADCommandHelp(
                    command=command,
                    description=" ".join(part for part in description_parts if part).strip(),
                    usage=tuple(usage),
                    raw_lines=tuple(raw_lines),
                )
            )
        command = None
        description_parts = []
        usage = []
        raw_lines = []

    for line in lines:
        text = line.strip()
        if not text:
            continue

        match = HELP_ROW_RE.match(text)
        if match:
            flush()
            command = match.group("command")
            description = match.group("description").strip()
            description_parts = [description] if description else []
            usage = []
            raw_lines = [line]
            continue

        if command is None:
            continue

        raw_lines.append(line)
        if is_help_usage_line(text):
            usage.append(normalize_help_usage_line(text))
        else:
            description_parts.append(text)

    flush()
    return catalog


def is_help_usage_line(line: str) -> bool:
    return line.startswith("<") or line.lower().startswith("usage:")


def normalize_help_usage_line(line: str) -> str:
    if line.lower().startswith("usage:"):
        return line.split(":", 1)[1].strip()
    return line


def comma_refs(values: Iterable[OutputRef] | str) -> str:
    if isinstance(values, str):
        return values
    return ",".join(str(output_ref(value)) for value in values)


def validate_range(value: int, *, lower: int, upper: int, name: str) -> int:
    number = int(value)
    if not lower <= number <= upper:
        raise ValueError(f"{name} must be between {lower} and {upper}")
    return number


def status_for_output(output: AutonomicOutput, snapshot: StatusSnapshot) -> dict[str, str]:
    candidates: list[str] = []
    for value in (output.id, output.name):
        if value:
            candidates.append(str(value))
            zone_id = zone_event_id(value)
            if zone_id:
                candidates.append(zone_id)

    for candidate in candidates:
        if candidate in snapshot.by_source:
            return snapshot.by_source[candidate]

    output_guid = normalize_guid(output.guid)
    if output_guid:
        for values in snapshot.by_source.values():
            if normalize_guid(values.get("ZoneGuid")) == output_guid:
                return values
    return {}


def base_output(output: OutputRef) -> AutonomicOutput:
    if isinstance(output, AutonomicOutput):
        return output

    ref = output_ref(output)
    zone_id = zone_event_id(ref)
    attrs: dict[str, str] = {}
    if zone_id:
        attrs["id"] = zone_id
        return AutonomicOutput(kind="Output", id=zone_id, name=zone_id, attributes=attrs)

    text = str(ref)
    attrs["name"] = text
    return AutonomicOutput(kind="Output", id=text, name=text, attributes=attrs)


def apply_status(output: AutonomicOutput, status: Mapping[str, str]) -> AutonomicOutput:
    if not status:
        return output

    attrs = {**output.attributes, **status}
    update: dict[str, StatusUpdateValue] = {
        "attributes": attrs,
        "is_on": bool_status(status, "PowerOn"),
        "muted": bool_status(status, "Mute"),
        "volume": int_status(status, "Volume"),
        "min_volume": int_status(status, "MinVolume"),
        "min_min_volume": int_status(status, "MinMinVolume"),
        "max_volume": int_status(status, "MaxVolume"),
        "max_max_volume": int_status(status, "MaxMaxVolume"),
        "bass": int_status(status, "Bass"),
        "treble": int_status(status, "Treble"),
        "balance": int_status(status, "Balance"),
        "gain": int_status(status, "ZoneGain"),
        "loudness": bool_status(status, "LoudnessEnabled"),
        "mono_downmix": bool_status(status, "MonoDownmix"),
        "power_on_volume": int_status(status, "PowerOnVolume"),
        "adjusting_volume": bool_status(status, "AdjustingVolume"),
        "device_type": status.get("DeviceType"),
        "do_not_disturb": bool_status(status, "DoNotDisturb"),
        "gain_mode": status.get("GainMode"),
        "icon_id": status.get("IconId"),
        "party_mode": status.get("PartyMode"),
        "source_id": status.get("SourceId") or output.source_id,
        "source_name": status.get("SourceName") or output.source_name,
        "qualified_source_name": status.get("QualifiedSourceName"),
        "zone_exclusive_source": bool_status(status, "ZoneExclusiveSource"),
        "zone_group_id": status.get("ZoneGroupId"),
        "zone_group_name": status.get("ZoneGroupName"),
        "zone_group_power": bool_status(status, "ZoneGroupPower"),
        "zone_group_source": bool_status(status, "ZoneGroupSource"),
        "zone_group_volume": bool_status(status, "ZoneGroupVolume"),
        "zone_is_locked": bool_status(status, "ZoneIsLocked"),
    }
    if status.get("ZoneName"):
        update["name"] = status["ZoneName"]
    if status.get("ZoneGuid"):
        update["guid"] = status["ZoneGuid"]
    if status.get("ZoneId"):
        update["id"] = f"Zone_{status['ZoneId']}"

    return output.model_copy(update={key: value for key, value in update.items() if value is not None})


def zone_event_id(value: OutputRef | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, AutonomicOutput):
        value = output_ref(value)
    text = str(value).strip()
    if not text:
        return None
    if text.lower().startswith("zone_"):
        suffix = text.split("_", 1)[1]
        return f"Zone_{suffix}" if suffix else None
    if text.lower().startswith("zone "):
        suffix = text.split(None, 1)[1]
        return f"Zone_{suffix}" if suffix else None
    if text.isdigit():
        return f"Zone_{text}"
    return None


def normalize_guid(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("{}").lower()


def bool_status(values: Mapping[str, str], key: str) -> bool | None:
    value = values.get(key)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def int_status(values: Mapping[str, str], key: str) -> int | None:
    value = values.get(key)
    if value in (None, ""):
        return None
    try:
        if value is None:
            return None
        return int(value)
    except ValueError:
        return None


def is_power_off_error(error: CommandError) -> bool:
    return " is off" in str(error).lower()
