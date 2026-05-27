# JSON configuration loader for static names, aliases, and direct amp stacks.
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import TypeAlias, cast

from .hardware import model_for_byte

ConfigScalar: TypeAlias = str | int | float | bool | None
ConfigValue: TypeAlias = ConfigScalar | list["ConfigValue"] | tuple["ConfigValue", ...] | dict[str, "ConfigValue"]
ConfigMapping: TypeAlias = Mapping[str, ConfigValue]


@dataclass(frozen=True)
class DirectLocalSourceConfig:
    slot: int
    name: str
    guid: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class DirectRemoteSourceConfig:
    source_id: int
    name: str
    guid: str
    target_device_id: str | None = None
    source_device_id: str | None = None
    device_id: str | None = None


@dataclass(frozen=True)
class DirectAmplifierDeviceConfig:
    device_id: str | None
    host: str
    output_start: int = 1
    native_output_start: int = 1
    output_count: int = 8
    source_count: int = 8
    source_base: int = 0
    model_byte: int | None = None


@dataclass(frozen=True)
class DirectAmplifierConfig:
    devices: tuple[DirectAmplifierDeviceConfig, ...] = ()
    output_names: dict[str, str] = field(default_factory=dict)
    source_name_aliases: dict[str, str] = field(default_factory=dict)
    local_sources_by_device_id: dict[str, tuple[DirectLocalSourceConfig, ...]] = field(default_factory=dict)
    remote_sources: tuple[DirectRemoteSourceConfig, ...] = ()

    @property
    def remote_sources_by_id(self) -> dict[str, DirectRemoteSourceConfig]:
        return {str(source.source_id): source for source in self.remote_sources if source.target_device_id is None}

    def remote_source_for(self, *, target_device_id: str | None, source_id: str) -> DirectRemoteSourceConfig | None:
        normalized_target = target_device_id.upper() if target_device_id else None
        fallback: DirectRemoteSourceConfig | None = None
        for source in self.remote_sources:
            if str(source.source_id) != str(source_id):
                continue
            if source.target_device_id is None:
                fallback = source
            elif normalized_target is not None and source.target_device_id == normalized_target:
                return source
        return fallback


@dataclass(frozen=True)
class AutonomicConfig:
    source_aliases: dict[str, str] = field(default_factory=dict)
    direct_amplifier: DirectAmplifierConfig = field(default_factory=DirectAmplifierConfig)


def load_config(path: str | Path | None = None) -> AutonomicConfig:
    if path is None:
        text = resources.files("autonomic").joinpath("default_config.json").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    data = cast(ConfigValue, json.loads(text))
    return config_from_mapping(_mapping(data))


def config_from_mapping(data: ConfigMapping | AutonomicConfig) -> AutonomicConfig:
    if isinstance(data, AutonomicConfig):
        return data

    direct_data = _mapping(data.get("direct_amplifier", {}))
    devices_list: list[DirectAmplifierDeviceConfig] = []
    next_output_start = 1
    for entry in _sequence_of_mappings(direct_data.get("devices", ())):
        model_byte = _optional_int(entry.get("model_byte"))
        model = model_for_byte(model_byte)
        output_count = _int(
            entry.get(
                "output_count",
                model.output_count if model is not None and model.output_count is not None else 8,
            )
        )
        source_count = _int(
            entry.get(
                "source_count",
                model.source_count if model is not None and model.source_count is not None else 8,
            )
        )
        source_base = _int(entry.get("source_base", model.source_base if model is not None else 0))
        output_start = _int(entry.get("output_start", next_output_start))
        native_output_start = _int(entry.get("native_output_start", output_start))
        devices_list.append(
            DirectAmplifierDeviceConfig(
                device_id=str(entry["device_id"]).upper() if entry.get("device_id") else None,
                host=str(entry["host"]),
                output_start=output_start,
                native_output_start=native_output_start,
                output_count=output_count,
                source_count=source_count,
                source_base=source_base,
                model_byte=model_byte,
            )
        )
        next_output_start = output_start + output_count
    devices = tuple(devices_list)

    local_sources: dict[str, tuple[DirectLocalSourceConfig, ...]] = {}
    for device_id, entries in _mapping(direct_data.get("local_sources_by_device_id", {})).items():
        local_sources[str(device_id).upper()] = tuple(
            DirectLocalSourceConfig(
                slot=_int(entry["slot"]),
                name=str(entry["name"]),
                guid=str(entry["guid"]) if entry.get("guid") else None,
                aliases=tuple(str(alias) for alias in _sequence(entry.get("aliases", ()))),
            )
            for entry in _sequence_of_mappings(entries)
        )

    remote_sources = tuple(
        DirectRemoteSourceConfig(
            source_id=_int(entry["source_id"]),
            name=str(entry["name"]),
            guid=str(entry["guid"]),
            target_device_id=_optional_upper_string(entry.get("target_device_id", entry.get("device_id"))),
            source_device_id=_optional_upper_string(entry.get("source_device_id")),
            device_id=_optional_upper_string(entry.get("target_device_id", entry.get("device_id"))),
        )
        for entry in _sequence_of_mappings(direct_data.get("remote_sources", ()))
    )

    return AutonomicConfig(
        source_aliases=_string_mapping(data.get("source_aliases", {}), lower_keys=True),
        direct_amplifier=DirectAmplifierConfig(
            devices=devices,
            output_names=_string_mapping(direct_data.get("output_names", {})),
            source_name_aliases=_string_mapping(direct_data.get("source_name_aliases", {}), lower_keys=True),
            local_sources_by_device_id=local_sources,
            remote_sources=remote_sources,
        ),
    )


def _mapping(value: ConfigValue) -> ConfigMapping:
    if isinstance(value, Mapping):
        return cast(ConfigMapping, value)
    raise TypeError("expected a JSON mapping")


def _sequence(value: ConfigValue) -> tuple[ConfigValue, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise TypeError("expected a JSON array")
    return tuple(value)


def _sequence_of_mappings(value: ConfigValue) -> tuple[ConfigMapping, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise TypeError("expected a JSON array")
    return tuple(_mapping(item) for item in value)


def _string_mapping(value: ConfigValue, *, lower_keys: bool = False) -> dict[str, str]:
    mapping = _mapping(value)
    result: dict[str, str] = {}
    for key, item in mapping.items():
        rendered_key = str(key).strip()
        if lower_keys:
            rendered_key = rendered_key.lower()
        if rendered_key:
            result[rendered_key] = str(item).strip()
    return result


def _optional_upper_string(value: ConfigValue) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def _optional_int(value: ConfigValue) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip()
        base = 16 if text.lower().startswith("0x") else 10
        return int(text, base)
    return _int(value)


def _int(value: ConfigValue) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"expected an integer-compatible value, got {type(value).__name__}")
