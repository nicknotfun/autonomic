# Internal helpers for unified-client normalization, routing, and matching.
from __future__ import annotations

import socket
from collections.abc import Mapping
from typing import Literal

from .amplifier import MirageAmplifier
from .client_types import DirectEndpoint
from .config import DirectRemoteSourceConfig
from .hardware import model_for_byte
from .models import AutonomicSource, SourceRef


def can_connect(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def normalize_mode(mode: str) -> Literal["auto", "mrad", "amplifier"]:
    normalized = str(mode).strip().lower()
    if normalized in {"auto", ""}:
        return "auto"
    if normalized in {"mrad", "mas"}:
        return "mrad"
    if normalized in {"amplifier", "amp", "direct"}:
        return "amplifier"
    raise ValueError("mode must be auto, mrad/mas, or amplifier/amp/direct")


def name_matches(actual: str | None, expected: str) -> bool:
    return actual is not None and actual.strip().lower() == expected.strip().lower()


def percent(value: float) -> float:
    number = float(value)
    if not 0 <= number <= 100:
        raise ValueError("volume percent must be between 0.0 and 100.0")
    return number


def native_source_ref(source_id_value: str | None) -> str:
    if source_id_value is None:
        return ""
    value = str(source_id_value).strip()
    if ":" in value:
        value = value.rsplit(":", 1)[1]
    return value


def synthetic_source_id(device_id: str | None, source_id_value: str | None) -> str | None:
    if source_id_value is None:
        return None
    native_id = native_source_ref(source_id_value)
    if device_id:
        return f"{device_id.upper()}:{native_id}"
    return native_id


def source_device_id(source: SourceRef | None) -> str | None:
    if isinstance(source, AutonomicSource):
        attr_device = source.attributes.get("deviceId")
        if attr_device:
            return attr_device.upper()
        return source_device_id_from_id(source.id)
    if isinstance(source, str):
        return source_device_id_from_id(source)
    return None


def source_device_id_from_id(source_id_value: str | None) -> str | None:
    if source_id_value is None:
        return None
    value = str(source_id_value).strip()
    if ":" not in value:
        return None
    device_id = value.split(":", 1)[0].strip()
    return device_id.upper() or None


def source_id_from_slot(slot: int, endpoint: DirectEndpoint) -> int:
    return int(slot) - 1 if endpoint.source_base == 0 else int(slot)


def slot_from_source_id(source_id_value: int, endpoint: DirectEndpoint) -> int:
    if int(source_id_value) >= 0x20:
        raise ValueError("remote sources do not have local slots")
    return int(source_id_value) + 1 if endpoint.source_base == 0 else int(source_id_value)


def hardware_local_source_labels(endpoint: DirectEndpoint) -> dict[int, str]:
    model = model_for_byte(endpoint.model_byte)
    if model is None or not model.source_labels_by_data:
        return {}

    labels: dict[int, str] = {}
    for source_data, label in model.source_labels_by_data:
        source_id_value = MirageAmplifier.decode_matrix_source(source_data)
        if endpoint.source_base != 0 and int(source_data) < 0x20:
            source_id_value += 1
        try:
            slot = slot_from_source_id(source_id_value, endpoint)
        except ValueError:
            continue
        if 1 <= slot <= endpoint.source_count:
            labels[slot] = label
    return labels


def device_attr(remote_source: DirectRemoteSourceConfig) -> dict[str, str]:
    if remote_source.target_device_id is None:
        return {}
    return {"deviceId": remote_source.target_device_id}


def normalize_source_aliases(source_aliases: Mapping[str, str] | None) -> dict[str, str]:
    if source_aliases is None:
        return {}
    return {str(guid).strip().lower(): str(name).strip() for guid, name in source_aliases.items() if str(guid).strip()}
