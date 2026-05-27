# Typed containers for direct amplifier commands, discovery, and status rows.
from __future__ import annotations

from dataclasses import dataclass

from .models import AutonomicOutput, AutonomicSource

AmplifierOutputRef = int | str | AutonomicOutput
AmplifierSourceRef = int | str | AutonomicSource


@dataclass(frozen=True)
class AmplifierResponse:
    command: int
    output: int | None
    raw_output: int
    data: list[int]
    raw: str


@dataclass(frozen=True)
class AmplifierSourceName:
    source_id: int
    logical_source: int
    output: int | None
    name: str
    short_name: str | None
    hidden_name: str | None
    raw: str


@dataclass(frozen=True)
class AmplifierOutputName:
    output: int
    name: str
    raw: str


@dataclass(frozen=True)
class AmplifierRemoteSource:
    slot: int
    source_id: int
    guid: str
    raw_guid: str
    source_player_id: int
    name: str
    raw: str


@dataclass(frozen=True)
class AmplifierSourceMetadata:
    source_id: int
    logical_source: int
    position: int
    value: str
    raw: str
    output: int | None = None


@dataclass(frozen=True)
class AmplifierSourceDetails:
    source_id: int
    logical_source: int
    output: int | None
    name: AmplifierSourceName | None
    metadata: tuple[AmplifierSourceMetadata, ...]


@dataclass(frozen=True)
class AmplifierInputGain:
    output: int | None
    source_id: int
    logical_source: int
    gain_percent: int
    raw_gain: int
    raw: str


@dataclass(frozen=True)
class AmplifierSourceDelay:
    output: int | None
    source_id: int
    logical_source: int
    delay_ms: int
    raw_delay: int
    raw: str


@dataclass(frozen=True)
class AmplifierZoneGroup:
    zones: tuple[int, ...]
    source_linked: bool
    volume_linked: bool
    power_linked: bool
    raw: str


@dataclass(frozen=True)
class AmplifierPresetGroupMap:
    available: bool
    map_data: str
    signature: str | None
    available_slots: tuple[int, ...]
    raw: str


@dataclass(frozen=True)
class AmplifierPresetGroup:
    slot: int
    available: bool
    empty: bool
    read_only: bool | None
    preset_id: int | None
    name: str | None
    raw_name: str | None
    member_zones: tuple[int, ...]
    raw: str


@dataclass(frozen=True)
class AmplifierResetDefaults:
    source: AmplifierSourceRef = 1
    volume: int = 0
    max_volume: int = 100
    power_on_volume: float = 0.0
    bass: int = 0
    treble: int = 0
    balance: int = 0
    gain: int = 0
    input_gain: int = 0
    delay_ms: int = 0
    loudness: bool = False
    mono_downmix: bool = False
    muted: bool = False
    is_on: bool = False


@dataclass(frozen=True)
class AmplifierLayout:
    output_count: int
    source_count: int
    source_base: int
    native_output_start: int = 1
    device_id: str | None = None
    model_byte: int | None = None


@dataclass(frozen=True)
class AmplifierNetworkInfo:
    amp_id: str
    dhcp: bool
    ovrc_connected: bool
    ip_address: str
    subnet_mask: str
    dns: str
    gateway: str


@dataclass(frozen=True)
class AmplifierDeviceSubInfo:
    amp_id: str
    response_type: int
    payload: tuple[int, ...]
    payload_hex: str
    value: int | None
    raw: str


@dataclass(frozen=True)
class AmplifierDeviceLinkInfo:
    command: int
    amp_id: str
    status: int | None
    raw: str


@dataclass(frozen=True)
class AmplifierDeviceStateInfo:
    amp_id: str
    payload: tuple[int, ...]
    payload_hex: str
    raw: str


@dataclass(frozen=True)
class AmplifierDeviceInfo:
    amp_id: str
    model_byte: int | None = None
    model_name: str | None = None
    zones: tuple[int, ...] = ()
    mac: str | None = None
    guid: str | None = None
    raw_guid: str | None = None
    system_id: int | None = None
    network: AmplifierNetworkInfo | None = None
    raw_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class AmplifierDiscovery:
    devices: tuple[AmplifierDeviceInfo, ...]
    sources: tuple[AutonomicSource, ...]
    remote_sources: tuple[AmplifierRemoteSource, ...]
    source_names: tuple[AmplifierSourceName, ...]
    zone_groups: tuple[AmplifierZoneGroup, ...]
    raw_lines: tuple[str, ...]
    preset_group_map: AmplifierPresetGroupMap | None = None
    preset_groups: tuple[AmplifierPresetGroup, ...] = ()
