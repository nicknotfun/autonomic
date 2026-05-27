# Protocol-agnostic high-level client that chooses MRAD or direct amp control.
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Literal, TypeAlias

from .amplifier import MirageAmplifier
from .amplifier_codec import AMPLIFIER_DIAGNOSTIC_PORT, REMOTE_SOURCE_START
from .amplifier_types import (
    AmplifierInputGain,
    AmplifierResetDefaults,
    AmplifierSourceDetails,
    AmplifierSourceMetadata,
    AmplifierSourceName,
    AmplifierZoneGroup,
)
from .config import (
    AutonomicConfig,
    ConfigMapping,
    DirectAmplifierDeviceConfig,
    DirectLocalSourceConfig,
    DirectRemoteSourceConfig,
    config_from_mapping,
    load_config,
)
from .client_types import DirectEndpoint as _DirectEndpoint
from .client_utils import can_connect as _can_connect
from .client_utils import device_attr as _device_attr
from .client_utils import hardware_local_source_labels as _hardware_local_source_labels
from .client_utils import name_matches as _name_matches
from .client_utils import native_source_ref as _native_source_ref
from .client_utils import normalize_mode as _normalize_mode
from .client_utils import normalize_source_aliases as _normalize_source_aliases
from .client_utils import percent as _percent
from .client_utils import slot_from_source_id as _slot_from_source_id
from .client_utils import source_device_id as _source_device_id
from .client_utils import source_device_id_from_id as _source_device_id_from_id
from .client_utils import source_id_from_slot as _source_id_from_slot
from .client_utils import synthetic_source_id as _synthetic_source_id
from .exceptions import AutonomicError, ProtocolError
from .hardware import model_for_byte
from .models import (
    AutonomicOutput,
    AutonomicOutputGroup,
    AutonomicPartyModeInfo,
    AutonomicSource,
    AutonomicZoneGroup,
    output_ref,
    source_id,
)
from .mrad import MirageAudioSystem
from .mrad_types import MRAD_PORT, MRADCommandHelp, MRADVersion, OutputRef, SourceRef, XmlMode
from .protocol_types import BrowseResponse, CommandResponse

ClientMode = Literal["auto", "mrad", "mas", "amplifier", "amp", "direct"]
DetectedMode = Literal["mrad", "amplifier"]
ModelUpdateValue: TypeAlias = str | int | float | bool | None | dict[str, str]
DEFAULT_HOST = "10.1.0.200"
DEFAULT_CONFIG = load_config()
DEFAULT_SOURCE_ALIASES = DEFAULT_CONFIG.source_aliases


class _ConfigSourceAliases:
    __slots__ = ()


_CONFIG_SOURCE_ALIASES = _ConfigSourceAliases()


class AutonomicClient:
    """Convenience client for Autonomic systems with auto-detected control mode."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        *,
        mrad_port: int = MRAD_PORT,
        amplifier_port: int = AMPLIFIER_DIAGNOSTIC_PORT,
        timeout: float = 5.0,
        mode: ClientMode = "auto",
        auto_initialize: bool = True,
        host_hint: str | None = None,
        config_path: str | Path | None = None,
        config: AutonomicConfig | ConfigMapping | None = None,
        source_aliases: Mapping[str, str] | None | _ConfigSourceAliases = _CONFIG_SOURCE_ALIASES,
    ):
        self.host = host
        self.mode = mode
        self.mrad_port = mrad_port
        self.amplifier_port = amplifier_port
        self.timeout = timeout
        if config is not None and config_path is not None:
            raise ValueError("config and config_path are mutually exclusive")
        self.config = config_from_mapping(config) if config is not None else load_config(config_path)
        direct_output_count, direct_source_count, direct_source_base, direct_native_output_start = self._direct_amplifier_defaults()
        if isinstance(source_aliases, _ConfigSourceAliases):
            self.source_aliases = dict(self.config.source_aliases)
        else:
            self.source_aliases = _normalize_source_aliases(source_aliases)
        normalized_mode = _normalize_mode(mode)
        self._detected_mode: DetectedMode | None = None if normalized_mode == "auto" else normalized_mode
        self._selected_output: OutputRef | None = None
        self._initialized = False
        self._amplifier_device_id: str | None = None
        self._direct_endpoints_cache: list[_DirectEndpoint] | None = None
        self._mrad_client_cache: MirageAudioSystem | None = None
        self._initialized_mrad_clients: set[int] = set()
        self._source_cache: list[AutonomicSource] | None = None
        self.audio = MirageAudioSystem(host, mrad_port, timeout=timeout)
        self.amplifier = MirageAmplifier(
            host,
            amplifier_port,
            timeout=timeout,
            output_count=direct_output_count,
            source_count=direct_source_count,
            native_output_start=direct_native_output_start,
            source_base=direct_source_base,
        )
        if auto_initialize:
            self.initialize(host_hint=host_hint)

    def connect(self) -> None:
        if self._backend() == "mrad":
            self._mrad_client().connect()

    def close(self) -> None:
        self.audio.close()
        if self._mrad_client_cache is not None:
            self._mrad_client_cache.close()

    def initialize(self, *, host_hint: str | None = None) -> None:
        if self._backend() == "mrad":
            client = self._mrad_client()
            client.initialize(host_hint=host_hint)
            self._initialized_mrad_clients.add(id(client))
        else:
            if self._configured_direct_device_for_host() is None:
                try:
                    layout = self.amplifier.infer_layout()
                except (OSError, ProtocolError):
                    self._amplifier_device_id = self.amplifier.get_device_id()
                else:
                    self._amplifier_device_id = layout.device_id or self.amplifier.get_device_id()
                self._direct_endpoints_cache = None
            else:
                endpoint = self._primary_direct_endpoint()
                self._amplifier_device_id = endpoint.device_id or endpoint.amplifier.get_device_id()
        self.clear_source_cache()
        self._initialized = True

    def list_zones(self, *, include_disabled: bool = False, include_status: bool = True) -> list[AutonomicOutput]:
        return self.list_outputs(include_disabled=include_disabled, include_status=include_status)

    def list_outputs(self, *, include_disabled: bool = False, include_status: bool = True) -> list[AutonomicOutput]:
        if self._backend() == "mrad":
            outputs = self._mrad_client().list_outputs(include_disabled=include_disabled, include_status=include_status)
            return [self._with_output_aliases(output).bind(self) for output in outputs]
        return self._list_direct_outputs(include_disabled=include_disabled, include_status=include_status)

    def all_outputs(self, *, include_disabled: bool = False, include_status: bool = True) -> AutonomicOutputGroup:
        return AutonomicOutputGroup(outputs=self.list_outputs(include_disabled=include_disabled, include_status=include_status)).bind(self)

    def list_sources(self, *, include_disabled: bool = False) -> list[AutonomicSource]:
        return self._cached_sources(include_disabled=include_disabled)

    def refresh_sources(self, *, include_disabled: bool = False) -> list[AutonomicSource]:
        """Reload the lifetime source cache from the active backend."""

        self._source_cache = self._read_sources(include_disabled=True)
        return self._source_cache_items(include_disabled=include_disabled)

    def clear_source_cache(self) -> None:
        """Forget cached sources so the next source read probes the backend."""

        self._source_cache = None

    def _read_sources(self, *, include_disabled: bool = False) -> list[AutonomicSource]:
        if self._backend() == "mrad":
            sources = self._mrad_client().list_sources(include_disabled=include_disabled)
            return [self._with_source_alias(source).bind(self) for source in sources]
        return self._list_direct_sources(include_disabled=include_disabled)

    def _cached_sources(self, *, include_disabled: bool = False) -> list[AutonomicSource]:
        if self._source_cache is None:
            self._source_cache = self._read_sources(include_disabled=True)
        return self._source_cache_items(include_disabled=include_disabled)

    def _source_cache_items(self, *, include_disabled: bool) -> list[AutonomicSource]:
        if self._source_cache is None:
            return []
        return [
            self._source_cache_item(source)
            for source in self._source_cache
            if include_disabled or not source.disabled
        ]

    def _source_cache_item(self, source: AutonomicSource) -> AutonomicSource:
        return source.model_copy(deep=True).bind(self)

    def source_by_name(self, name: str, *, include_disabled: bool = False) -> AutonomicSource:
        for source in self.list_sources(include_disabled=include_disabled):
            if _name_matches(source.name, name):
                return source
        raise LookupError(f"No Autonomic source named {name!r}")

    def output_by_name(
        self,
        name: str,
        *,
        include_disabled: bool = False,
        include_status: bool = True,
    ) -> AutonomicOutput:
        for output in self.list_outputs(include_disabled=include_disabled, include_status=include_status):
            if _name_matches(output.name, name):
                return output
        raise LookupError(f"No Autonomic output named {name!r}")

    def get_versions(self) -> list[MRADVersion]:
        return self._mrad_client().get_versions()

    def ping(self) -> CommandResponse | str:
        if self._backend() == "amplifier":
            return self._primary_direct_endpoint().amplifier.ping_device()
        return self._mrad_client().ping()

    def ping_ok(self) -> bool:
        if self._backend() == "amplifier":
            return bool(self._primary_direct_endpoint().amplifier.ping_device())
        return self._mrad_client().ping_ok()

    def echo(self, text: str) -> CommandResponse:
        return self._mrad_client().echo(text)

    def echo_text(self, text: str) -> str:
        return self._mrad_client().echo_text(text)

    def uptime(self) -> CommandResponse:
        return self._mrad_client().uptime()

    def uptime_text(self) -> str:
        return self._mrad_client().uptime_text()

    def time(self, fmt: str = "U") -> CommandResponse:
        return self._mrad_client().time(fmt)

    def time_text(self, fmt: str = "U") -> str:
        return self._mrad_client().time_text(fmt)

    def sync_time(self) -> CommandResponse:
        return self._mrad_client().sync_time()

    def log_comment(self, text: str) -> CommandResponse:
        return self._mrad_client().log_comment(text)

    def enter_command_mode(self) -> CommandResponse:
        return self._mrad_client().enter_command_mode()

    def toggle_passthrough_mode(self) -> CommandResponse:
        return self._mrad_client().toggle_passthrough_mode()

    def enter_passthrough_mode(self) -> CommandResponse:
        return self._mrad_client().enter_passthrough_mode()

    def clear_terminal(self) -> CommandResponse:
        return self._mrad_client().clear_terminal()

    def exit_session(self) -> CommandResponse:
        return self._mrad_client().exit_session()

    def set_client_version(self, version: str) -> CommandResponse:
        return self._mrad_client().set_client_version(version)

    def set_client_type(self, client_type: str) -> CommandResponse:
        return self._mrad_client().set_client_type(client_type)

    def set_encoding(self, encoding: int) -> CommandResponse:
        return self._mrad_client().set_encoding(encoding)

    def set_host(self, host: str) -> CommandResponse:
        return self._mrad_client().set_host(host)

    def set_xml_mode(self, mode: bool | str) -> CommandResponse:
        normalized_mode: bool | XmlMode
        if isinstance(mode, bool):
            normalized_mode = mode
        else:
            text = mode.strip().lower()
            if text == "lists":
                normalized_mode = "Lists"
            elif text == "none":
                normalized_mode = "None"
            else:
                raise ValueError("XML mode must be Lists, None, or bool")
        return self._mrad_client().set_xml_mode(normalized_mode)

    def set_response_eol_zero(self, *, expect_response: bool = False) -> CommandResponse:
        return self._mrad_client().set_response_eol_zero(expect_response=expect_response)

    def banner(self, *, timeout: float | None = None, idle_timeout: float = 0.2) -> list[str]:
        return self._mrad_client().banner(timeout=timeout, idle_timeout=idle_timeout)

    def mrad_help(self, command: str | None = None, *, timeout: float | None = None, idle_timeout: float = 0.2) -> list[str]:
        return self._mrad_client().help(command, timeout=timeout, idle_timeout=idle_timeout)

    def mrad_help_text(self, command: str | None = None, *, timeout: float | None = None, idle_timeout: float = 0.2) -> str:
        return self._mrad_client().help_text(command, timeout=timeout, idle_timeout=idle_timeout)

    def command_catalog(self, *, timeout: float | None = None, idle_timeout: float = 0.2) -> list[MRADCommandHelp]:
        return self._mrad_client().command_catalog(timeout=timeout, idle_timeout=idle_timeout)

    def command_help(self, command: str, *, timeout: float | None = None, idle_timeout: float = 0.2) -> MRADCommandHelp:
        return self._mrad_client().command_help(command, timeout=timeout, idle_timeout=idle_timeout)

    def list_zone_groups(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicZoneGroup]:
        if self._backend() == "amplifier":
            return self._list_direct_zone_groups(start, count, include_disabled=include_disabled)
        return self._mrad_client().list_zone_groups(start, count, include_disabled=include_disabled)

    def list_zone_group(
        self,
        group_or_zone: str | AutonomicZoneGroup | AutonomicOutput | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicZoneGroup]:
        if self._backend() == "amplifier":
            groups = self._list_direct_zone_groups(include_disabled=include_disabled)
            if group_or_zone is None:
                return groups
            return [group for group in groups if self._direct_zone_group_matches(group, group_or_zone)]
        return self._mrad_client().list_zone_group(group_or_zone, include_disabled=include_disabled)

    def list_zones_for_group(
        self,
        group: str | AutonomicZoneGroup,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicOutput]:
        if self._backend() == "amplifier":
            matches = self.list_zone_group(group, include_disabled=include_disabled)
            if not matches:
                return []
            zone_ids = self._direct_zone_ids_for_group(matches[0])
            by_id = {output.id: output for output in self.list_outputs(include_disabled=include_disabled, include_status=False)}
            return [by_id[str(zone_id)] for zone_id in zone_ids if str(zone_id) in by_id]
        return [self._with_output_aliases(output).bind(self) for output in self._mrad_client().list_zones_for_group(group, include_disabled=include_disabled)]

    def list_party_mode_include(self, *, include_disabled: bool = False) -> list[AutonomicPartyModeInfo]:
        return [item.bind(self) for item in self._mrad_client().list_party_mode_include(include_disabled=include_disabled)]

    def get_output_status(self, output: OutputRef) -> AutonomicOutput:
        if self._backend() == "mrad":
            return self._with_output_aliases(self._mrad_client().get_output_status(self._mrad_output_ref(output))).bind(self)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return self._with_direct_output_endpoint(
            endpoint.amplifier.get_output_status(
                local_output,
                source_names=self._direct_source_names_for_endpoint(endpoint),
            ),
            endpoint,
        ).bind(self)

    def read_output_volume(self, output: OutputRef) -> float | None:
        if self._backend() == "mrad":
            return self.get_output_status(output).volume
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.read_output_volume(local_output)

    def read_output_max_volume(self, output: OutputRef) -> float | None:
        if self._backend() == "mrad":
            return self.get_output_status(output).max_volume
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.read_output_max_volume(local_output)

    def read_output_mute(self, output: OutputRef) -> bool | None:
        if self._backend() == "mrad":
            return self.get_output_status(output).muted
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.read_output_mute(local_output)

    def read_output_power(self, output: OutputRef) -> bool | None:
        if self._backend() == "mrad":
            return self.get_output_status(output).is_on
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.read_output_power(local_output)

    def read_output_source_id(self, output: OutputRef) -> str | None:
        return self.get_output_status(output).source_id

    def read_output_source_name(self, output: OutputRef) -> str | None:
        return self.get_output_status(output).source_name

    def read_output_bass(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).bass

    def read_output_treble(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).treble

    def read_output_balance(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).balance

    def read_output_gain(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).gain

    def read_output_delay(self, output: OutputRef) -> int | None:
        if self._backend() == "mrad":
            return self.get_output_status(output).delay_ms
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.read_output_delay(local_output)

    def read_output_loudness(self, output: OutputRef) -> bool | None:
        return self.get_output_status(output).loudness

    def read_output_mono_downmix(self, output: OutputRef) -> bool | None:
        if self._backend() == "amplifier":
            return self._mrad_output_status(output).mono_downmix
        return self.get_output_status(output).mono_downmix

    def read_output_power_on_volume(self, output: OutputRef) -> float | None:
        if self._backend() == "amplifier":
            return self._mrad_output_status(output).power_on_volume
        return self.get_output_status(output).power_on_volume

    def browse_page_up(self) -> BrowseResponse:
        return self._mrad_client().browse_page_up()

    def browse_page_down(self) -> BrowseResponse:
        return self._mrad_client().browse_page_down()

    def identify_zone(self, zone: OutputRef) -> CommandResponse:
        return self._mrad_client().identify_zone(self._mrad_output_ref(zone))

    def identify_output(self, output: OutputRef) -> CommandResponse:
        return self.identify_zone(output)

    def set_party_mode(self, state: bool | str = True, zone: OutputRef | None = None) -> CommandResponse:
        mrad_zone = None if zone is None else self._mrad_output_ref(zone)
        return self._mrad_client().set_party_mode(state, mrad_zone)

    def party_mode(self, state: bool | str = "toggle", zone: OutputRef | None = None) -> CommandResponse:
        return self.set_party_mode(state, zone)

    def set_party_mode_include(
        self,
        state: bool | str,
        zone_or_group: OutputRef | AutonomicZoneGroup | None = None,
    ) -> CommandResponse:
        target = self._mrad_output_ref(zone_or_group) if zone_or_group is not None and not isinstance(zone_or_group, AutonomicZoneGroup) else zone_or_group
        return self._mrad_client().set_party_mode_include(state, target)

    def set_source_for_group(self, source: SourceRef) -> CommandResponse:
        return self._mrad_client().set_source_for_group(self._source_alias_ref(source))

    def set_zone_group(
        self,
        zone_or_group: OutputRef | AutonomicZoneGroup,
        selected_zones: Iterable[OutputRef] | str,
        target_source: SourceRef | None = None,
    ) -> CommandResponse:
        target = self._mrad_output_ref(zone_or_group) if not isinstance(zone_or_group, AutonomicZoneGroup) else zone_or_group
        selected = selected_zones if isinstance(selected_zones, str) else [self._mrad_output_ref(zone) for zone in selected_zones]
        source = self._source_alias_ref(target_source) if target_source is not None else None
        return self._mrad_client().set_zone_group(target, selected, source)

    def set_zone_group_timer(
        self,
        target_time: str | int,
        zone_or_group: OutputRef | AutonomicZoneGroup | None = None,
    ) -> CommandResponse:
        target = self._mrad_output_ref(zone_or_group) if zone_or_group is not None and not isinstance(zone_or_group, AutonomicZoneGroup) else zone_or_group
        return self._mrad_client().set_zone_group_timer(target_time, target)

    def set_output_name(self, output: OutputRef, name: str) -> CommandResponse | str:
        if self._backend() == "amplifier":
            endpoint, native_output = self._direct_endpoint_for_output(output)
            return endpoint.amplifier.set_output_name(native_output, name)
        return self._mrad_client().set_output_name(self._mrad_output_ref(output), name)

    def set_zone_name(self, zone: OutputRef, name: str) -> CommandResponse | str:
        return self.set_output_name(zone, name)

    def set_output_icon(self, output: OutputRef, icon: str) -> CommandResponse:
        return self._mrad_client().set_output_icon(self._mrad_output_ref(output), icon)

    def set_zone_icon(self, zone: OutputRef, icon: str) -> CommandResponse:
        return self.set_output_icon(zone, icon)

    def set_source_by_name(self, name: str) -> CommandResponse:
        return self._mrad_client().set_source_by_name(name)

    def set_source_name(self, source: SourceRef, name: str) -> CommandResponse | str:
        if self._backend() == "amplifier":
            endpoint = self._direct_endpoint_for_source(source)
            native_id = self._direct_source_id(source, endpoint=endpoint)
            direct_response = endpoint.amplifier.set_source_name(native_id, name)
            self._update_cached_source_name(source, name, endpoint=endpoint, native_id=native_id)
            return direct_response
        resolved_source = self._source_alias_ref(source)
        mrad_response = self._mrad_client().set_source_name(resolved_source, name)
        self._update_cached_source_name(resolved_source, name)
        return mrad_response

    def set_source_icon(self, source: SourceRef, icon: str) -> CommandResponse:
        resolved_source = self._source_alias_ref(source)
        response = self._mrad_client().set_source_icon(resolved_source, icon)
        self._update_cached_source_icon(resolved_source, icon)
        return response

    def select_zone(self, zone: OutputRef) -> CommandResponse | None:
        return self.select_output(zone)

    def select_output(self, output: OutputRef) -> CommandResponse | None:
        self._selected_output = output
        if self._backend() == "mrad":
            return self._mrad_client().set_output(output)
        return None

    def select_source(
        self,
        source: SourceRef,
        output: OutputRef | None = None,
        *,
        include_group: bool = False,
    ) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_source(self._source_alias_ref(source), output, include_group=include_group)
        target = output if output is not None else self._selected_output
        if target is None:
            raise ValueError("select_source requires output=... or a prior select_output() in amplifier mode")
        endpoint, local_output = self._direct_endpoint_for_output(target)
        return endpoint.amplifier.assign_source_to_output(self._direct_source_id(source, endpoint=endpoint), local_output)

    def assign_source_to_output(
        self,
        source: SourceRef,
        output: OutputRef,
        *,
        include_group: bool = False,
    ) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self._mrad_client().assign_source_to_output(self._source_alias_ref(source), output, include_group=include_group)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return [endpoint.amplifier.assign_source_to_output(self._direct_source_id(source, endpoint=endpoint), local_output)]

    def assign_source_to_outputs(self, source: SourceRef, outputs: Iterable[OutputRef]) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self._mrad_client().assign_source_to_outputs(self._source_alias_ref(source), outputs)
        grouped: list[tuple[_DirectEndpoint, list[str | int]]] = []
        for output in outputs:
            endpoint, local_output = self._direct_endpoint_for_output(output)
            for existing_endpoint, local_outputs in grouped:
                if existing_endpoint == endpoint:
                    local_outputs.append(local_output)
                    break
            else:
                grouped.append((endpoint, [local_output]))

        responses: list[str] = []
        for endpoint, local_outputs in grouped:
            direct_source = self._direct_source_id(source, endpoint=endpoint)
            if self._covers_all_direct_outputs(local_outputs, endpoint):
                responses.append(endpoint.amplifier.assign_source_to_output(direct_source, "all"))
            else:
                responses.extend(endpoint.amplifier.assign_source_to_outputs(direct_source, local_outputs))
        return responses

    def assign_source_to_all_outputs(self, source: SourceRef) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self._mrad_client().assign_source_to_all_outputs(self._source_alias_ref(source))
        return self.assign_source_to_outputs(source, self.list_outputs(include_status=False))

    def assign_output_sources(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self._mrad_client().assign_output_sources(
                {output: self._source_alias_ref(source) for output, source in assignments.items()}
            )
        responses: list[str] = []
        for output, source in assignments.items():
            endpoint, local_output = self._direct_endpoint_for_output(output)
            responses.append(endpoint.amplifier.assign_source_to_output(self._direct_source_id(source, endpoint=endpoint), local_output))
        return responses

    def assign_matrix(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse] | list[str]:
        return self.assign_output_sources(assignments)

    def volume(self, value: float, zone: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            target = zone if zone is not None else self._selected_output
            if target is None:
                return self._mrad_client().volume(self._mrad_scaled_volume(value))
            return self._mrad_client().volume(self._mrad_scaled_volume(value, target), target)
        output = self._resolve_output(zone)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_volume(local_output, _percent(value))

    def set_output_volume(self, output: OutputRef, value: float) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_volume(output, self._mrad_scaled_volume(value, output))
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_volume(local_output, _percent(value))

    def set_output_max_volume(self, output: OutputRef, value: float) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_max_volume(output, self._mrad_scaled_volume(value, output))
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_max_volume(local_output, _percent(value))

    def output_max_volume_up(self, output: OutputRef, step: float = 1.0) -> CommandResponse | str:
        if self._backend() == "amplifier":
            endpoint, local_output = self._direct_endpoint_for_output(output)
            return endpoint.amplifier.output_max_volume_up(local_output, step)
        current = self.read_output_max_volume(output)
        if current is None:
            raise AutonomicError(f"No max-volume status reported for output {output_ref(output)!r}")
        return self.set_output_max_volume(output, min(100.0, float(current) + float(step)))

    def output_max_volume_down(self, output: OutputRef, step: float = 1.0) -> CommandResponse | str:
        if self._backend() == "amplifier":
            endpoint, local_output = self._direct_endpoint_for_output(output)
            return endpoint.amplifier.output_max_volume_down(local_output, step)
        current = self.read_output_max_volume(output)
        if current is None:
            raise AutonomicError(f"No max-volume status reported for output {output_ref(output)!r}")
        return self.set_output_max_volume(output, max(0.0, float(current) - float(step)))

    def set_output_bass(self, output: OutputRef, value: int) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_bass(output, value)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_bass(local_output, value)

    def output_bass_up(self, output: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().output_bass_up(output)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_bass_up(local_output)

    def output_bass_down(self, output: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().output_bass_down(output)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_bass_down(local_output)

    def set_output_treble(self, output: OutputRef, value: int) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_treble(output, value)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_treble(local_output, value)

    def output_treble_up(self, output: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().output_treble_up(output)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_treble_up(local_output)

    def output_treble_down(self, output: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().output_treble_down(output)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_treble_down(local_output)

    def set_output_balance(self, output: OutputRef, value: int) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_balance(output, value)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_balance(local_output, value)

    def output_balance_left(self, output: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().output_balance_left(output)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_balance_left(local_output)

    def output_balance_right(self, output: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().output_balance_right(output)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_balance_right(local_output)

    def set_output_gain(self, output: OutputRef, value: int) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_gain(output, value)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_gain(local_output, value)

    def set_input_gain(
        self,
        source: SourceRef,
        output: OutputRef,
        gain: int,
        *,
        refresh: bool = True,
    ) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier input-gain controls are not available in MRAD mode")
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_input_gain(
            self._direct_source_id(source, endpoint=endpoint),
            local_output,
            gain,
            refresh=refresh,
        )

    def query_input_gains(
        self,
        output: OutputRef | None = None,
        *,
        include_source_names: bool = True,
    ) -> list[AmplifierInputGain]:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier input-gain controls are not available in MRAD mode")
        if output is None:
            gains: list[AmplifierInputGain] = []
            for endpoint in self._direct_endpoints():
                gains.extend(endpoint.amplifier.query_input_gains(None, include_source_names=include_source_names))
            return gains
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.query_input_gains(local_output, include_source_names=include_source_names)

    def set_output_delay(self, output: OutputRef, value_ms: int) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier delay controls are not available in MRAD mode")
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_delay(local_output, value_ms)

    def output_delay_up(self, output: OutputRef | None = None, step_ms: int = 5) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier delay controls are not available in MRAD mode")
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_delay_up(local_output, step_ms)

    def output_delay_down(self, output: OutputRef | None = None, step_ms: int = 5) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier delay controls are not available in MRAD mode")
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_delay_down(local_output, step_ms)

    def set_output_loudness(self, output: OutputRef, enabled: bool | str) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_loudness(output, enabled)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_loudness(local_output, enabled)

    def set_output_mono_downmix(self, output: OutputRef, enabled: bool | str) -> CommandResponse | str:
        client = self._mrad_client() if self._backend() == "mrad" else self._mrad_control_client()
        return client.set_output_mono_downmix(self._mrad_output_ref(output), enabled)

    def set_output_power_on_volume(self, output: OutputRef, value: float) -> CommandResponse | str:
        mrad_output = self._mrad_output_ref(output)
        client = self._mrad_client() if self._backend() == "mrad" else self._mrad_control_client()
        return client.set_output_power_on_volume(mrad_output, self._mrad_scaled_volume(value, mrad_output))

    def set_all_output_volume(self, value: float) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return [self.set_output_volume(output, value) for output in self.list_outputs(include_status=False)]
        return [endpoint.amplifier.set_output_volume("all", _percent(value)) for endpoint in self._direct_endpoints()]

    def set_all_output_max_volume(self, value: float) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return [self.set_output_max_volume(output, value) for output in self.list_outputs(include_status=False)]
        return [endpoint.amplifier.set_all_output_max_volume(_percent(value)) for endpoint in self._direct_endpoints()]

    def set_all_output_bass(self, value: int) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_all_output_bass(value)
        return [endpoint.amplifier.set_all_output_bass(value) for endpoint in self._direct_endpoints()]

    def set_all_output_treble(self, value: int) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_all_output_treble(value)
        return [endpoint.amplifier.set_all_output_treble(value) for endpoint in self._direct_endpoints()]

    def set_all_output_balance(self, value: int) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_all_output_balance(value)
        return [endpoint.amplifier.set_all_output_balance(value) for endpoint in self._direct_endpoints()]

    def set_all_output_gain(self, value: int) -> list[CommandResponse] | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_all_output_gain(value)
        return "\n".join(endpoint.amplifier.set_all_output_gain(value) for endpoint in self._direct_endpoints())

    def set_all_output_delay(self, value_ms: int) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier delay controls are not available in MRAD mode")
        return "\n".join(endpoint.amplifier.set_all_output_delay(value_ms) for endpoint in self._direct_endpoints())

    def set_all_output_loudness(self, enabled: bool | str) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_all_output_loudness(enabled)
        return [endpoint.amplifier.set_all_output_loudness(enabled) for endpoint in self._direct_endpoints()]

    def set_all_output_mono_downmix(self, enabled: bool | str) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_all_output_mono_downmix(enabled)
        return [self.set_output_mono_downmix(output, enabled) for output in self.list_outputs(include_status=False)]

    def set_all_output_power_on_volume(self, value: float) -> Sequence[CommandResponse | str] | str:
        return [self.set_output_power_on_volume(output, value) for output in self.list_outputs(include_status=True)]

    def volume_up(self, zone: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().volume_up(zone)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(zone))
        return endpoint.amplifier.output_volume_up(local_output)

    def volume_down(self, zone: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().volume_down(zone)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(zone))
        return endpoint.amplifier.output_volume_down(local_output)

    def output_gain_up(self, output: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().output_gain_up(output)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_gain_up(local_output)

    def output_gain_down(self, output: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().output_gain_down(output)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(output))
        return endpoint.amplifier.output_gain_down(local_output)

    def mute(self, state: bool | str = "toggle", zone: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().mute(state, zone)
        endpoint, local_output = self._direct_endpoint_for_output(self._resolve_output(zone))
        return endpoint.amplifier.set_output_mute(local_output, state)

    def set_output_mute(self, output: OutputRef, state: bool | str = "toggle") -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_mute(output, state)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_mute(local_output, state)

    def mute_all_outputs(self, state: bool | str = True) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return self._mrad_client().mute_all_outputs(state)
        return [endpoint.amplifier.mute_all_outputs(state) for endpoint in self._direct_endpoints()]

    def set_output_power(self, output: OutputRef, is_on: bool | str = True) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_output_power(output, is_on)
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.set_output_power(local_output, is_on)

    def set_output_is_on(self, output: OutputRef, is_on: bool | str = True) -> CommandResponse | str:
        return self.set_output_power(output, is_on)

    def set_all_output_power(self, is_on: bool | str = True) -> Sequence[CommandResponse | str] | str:
        if self._backend() == "mrad":
            return self._mrad_client().set_all_output_power(is_on)
        return [endpoint.amplifier.set_all_output_power(is_on) for endpoint in self._direct_endpoints()]

    def set_all_outputs_on(self, is_on: bool | str = True) -> Sequence[CommandResponse | str] | str:
        return self.set_all_output_power(is_on)

    def all_off(self) -> Sequence[CommandResponse | str] | str:
        return self.set_all_output_power(False)

    def all_on(self) -> Sequence[CommandResponse | str] | str:
        return self.set_all_output_power(True)

    def reset_all_to_defaults(
        self,
        defaults: AmplifierResetDefaults | None = None,
        *,
        safety_mute: bool = True,
        clear_remote_sources: bool = False,
    ) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier reset is not available in MRAD mode")
        responses = [
            endpoint.amplifier.reset_all_to_defaults(
                defaults,
                safety_mute=safety_mute,
                clear_remote_sources=clear_remote_sources,
            )
            for endpoint in self._direct_endpoints()
        ]
        if clear_remote_sources:
            self._remove_cached_remote_sources()
        values = defaults or AmplifierResetDefaults()
        try:
            mrad_client = self._mrad_control_client()
        except (AutonomicError, OSError):
            return "\n".join(responses)

        for output in self.list_outputs(include_status=False):
            mrad_output = self._mrad_output_ref(output)
            responses.append(mrad_client.set_output_mono_downmix(mrad_output, values.mono_downmix).text)
            responses.append(
                mrad_client.set_output_power_on_volume(
                    mrad_output,
                    self._mrad_scaled_volume(values.power_on_volume, mrad_output),
                ).text
            )
        return "\n".join(responses)

    def reset_all_outputs_to_defaults(
        self,
        defaults: AmplifierResetDefaults | None = None,
        *,
        safety_mute: bool = True,
        clear_remote_sources: bool = False,
    ) -> str:
        return self.reset_all_to_defaults(
            defaults,
            safety_mute=safety_mute,
            clear_remote_sources=clear_remote_sources,
        )

    def rename_sources_to_low_level_input_labels(self) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier source-name reset is not available in MRAD mode")
        response = "\n".join(endpoint.amplifier.rename_sources_to_low_level_input_labels() for endpoint in self._direct_endpoints())
        self._update_cached_low_level_source_names()
        return response

    def refresh_source_name(
        self,
        source: SourceRef,
        output: OutputRef | None = None,
    ) -> list[AmplifierSourceName]:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier source details are not available in MRAD mode")
        if output is None:
            endpoint = self._direct_endpoint_for_source(source)
            return endpoint.amplifier.refresh_source_name(self._direct_source_id(source, endpoint=endpoint))
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.refresh_source_name(
            self._direct_source_id(source, endpoint=endpoint),
            output=local_output,
        )

    def refresh_source_details(
        self,
        source: SourceRef,
        output: OutputRef | None = None,
    ) -> AmplifierSourceDetails:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier source details are not available in MRAD mode")
        if output is None:
            endpoint = self._direct_endpoint_for_source(source)
            return endpoint.amplifier.refresh_source_details(self._direct_source_id(source, endpoint=endpoint))
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.refresh_source_details(
            self._direct_source_id(source, endpoint=endpoint),
            output=local_output,
        )

    def refresh_source_metadata(
        self,
        source: SourceRef,
        output: OutputRef | None = None,
    ) -> list[AmplifierSourceMetadata]:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier source metadata is not available in MRAD mode")
        if output is None:
            endpoint = self._direct_endpoint_for_source(source)
            return endpoint.amplifier.refresh_source_metadata(self._direct_source_id(source, endpoint=endpoint))
        endpoint, local_output = self._direct_endpoint_for_output(output)
        return endpoint.amplifier.refresh_source_metadata(
            self._direct_source_id(source, endpoint=endpoint),
            output=local_output,
        )

    def refresh_all_source_metadata(
        self,
        output: OutputRef | None = None,
        *,
        sources: Iterable[SourceRef] | None = None,
    ) -> list[AmplifierSourceMetadata]:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier source metadata is not available in MRAD mode")
        if output is not None:
            endpoint, local_output = self._direct_endpoint_for_output(output)
            local_sources = None if sources is None else [self._direct_source_id(source, endpoint=endpoint) for source in sources]
            return endpoint.amplifier.refresh_all_source_metadata(local_output, sources=local_sources)

        metadata: list[AmplifierSourceMetadata] = []
        for endpoint in self._direct_endpoints():
            local_sources = None if sources is None else [self._direct_source_id(source, endpoint=endpoint) for source in sources]
            metadata.extend(endpoint.amplifier.refresh_all_source_metadata(sources=local_sources))
        return metadata

    def set_source_metadata(
        self,
        source: SourceRef,
        position: int,
        value: str,
        *,
        refresh: bool = True,
        output: OutputRef | None = None,
    ) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier source metadata is not available in MRAD mode")
        if output is None:
            endpoint = self._direct_endpoint_for_source(source)
            metadata_output: OutputRef = "all"
        else:
            endpoint, local_output = self._direct_endpoint_for_output(output)
            metadata_output = local_output
        return endpoint.amplifier.set_source_metadata(
            self._direct_source_id(source, endpoint=endpoint),
            position,
            value,
            refresh=refresh,
            output=metadata_output,
        )

    def set_source_metadata_fields(
        self,
        source: SourceRef,
        values: Mapping[int, str] | Iterable[str],
        *,
        refresh: bool = True,
        output: OutputRef | None = None,
    ) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier source metadata is not available in MRAD mode")
        if output is None:
            endpoint = self._direct_endpoint_for_source(source)
            metadata_output: OutputRef = "all"
        else:
            endpoint, local_output = self._direct_endpoint_for_output(output)
            metadata_output = local_output
        return endpoint.amplifier.set_source_metadata_fields(
            self._direct_source_id(source, endpoint=endpoint),
            values,
            refresh=refresh,
            output=metadata_output,
        )

    def define_eaudiocast_source(
        self,
        *,
        target_device_id: str,
        slot: int,
        source: SourceRef,
        name: str | None = None,
    ) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier eAudioCast source setup is not available in MRAD mode")
        target = self._direct_endpoint_for_device(target_device_id)
        source_endpoint = self._direct_endpoint_for_source(source)
        source_position = self._direct_source_id(source, endpoint=source_endpoint)
        source_guid = self._direct_endpoint_guid(source_endpoint)
        source_name = name if name is not None else self._direct_source_display_name(source, endpoint=source_endpoint)
        response = target.amplifier.define_remote_source(slot, source_guid, source_position, source_name)
        self._upsert_cached_remote_source(
            target=target,
            slot=slot,
            source_endpoint=source_endpoint,
            source_guid=source_guid,
            source_position=source_position,
            source_name=source_name,
        )
        return response

    def delete_eaudiocast_source(self, *, target_device_id: str, slot: int) -> str:
        if self._backend() != "amplifier":
            raise AutonomicError("Direct amplifier eAudioCast source setup is not available in MRAD mode")
        endpoint = self._direct_endpoint_for_device(target_device_id)
        response = endpoint.amplifier.delete_remote_source(slot)
        self._remove_cached_remote_source(endpoint=endpoint, slot=slot)
        return response

    def detect_mode(self) -> DetectedMode:
        return self._backend()

    def _backend(self) -> DetectedMode:
        if self._detected_mode is not None:
            return self._detected_mode

        if _can_connect(self.host, self.amplifier.port, self.amplifier.timeout):
            self._detected_mode = "amplifier"
            return self._detected_mode

        for device in self._configured_direct_devices():
            if device.host != self.host and _can_connect(device.host, self.amplifier_port, self.timeout):
                self._detected_mode = "amplifier"
                return self._detected_mode

        if _can_connect(self.host, self.audio.port, self.audio.timeout):
            self._detected_mode = "mrad"
            return self._detected_mode

        for device in self._configured_direct_devices():
            if device.host != self.host and _can_connect(device.host, self.mrad_port, self.timeout):
                self._detected_mode = "mrad"
                return self._detected_mode

        raise AutonomicError(f"No supported Autonomic control port found on {self.host}")

    def _direct_amplifier_defaults(self) -> tuple[int | None, int | None, int | None, int]:
        configured = self._configured_direct_device_for_host()
        if configured is not None:
            return (
                int(configured.output_count),
                int(configured.source_count),
                int(configured.source_base),
                int(configured.native_output_start),
            )
        return (None, None, None, 1)

    def _configured_direct_device_for_host(self) -> DirectAmplifierDeviceConfig | None:
        for device in self.config.direct_amplifier.devices:
            if device.host == self.host:
                return device
        return None

    def _resolve_output(self, output: OutputRef | None) -> OutputRef:
        resolved = output if output is not None else self._selected_output
        if resolved is None:
            raise ValueError("operation requires an output or prior select_output() in amplifier mode")
        return output_ref(resolved)

    def _configured_direct_devices(self) -> tuple[DirectAmplifierDeviceConfig, ...]:
        devices = self.config.direct_amplifier.devices
        if not devices:
            return ()
        configured_hosts = {device.host for device in devices}
        if self.host in configured_hosts:
            return devices
        return ()

    def _direct_endpoints(self) -> list[_DirectEndpoint]:
        if self._direct_endpoints_cache is not None:
            return self._direct_endpoints_cache

        configured_devices = self._configured_direct_devices()
        if configured_devices:
            endpoints = []
            for device in configured_devices:
                amplifier = (
                    self.amplifier
                    if device.host == self.host
                    else MirageAmplifier(
                        device.host,
                        self.amplifier_port,
                        timeout=self.timeout,
                        output_count=device.output_count,
                        source_count=device.source_count,
                        native_output_start=device.native_output_start,
                        source_base=device.source_base,
                    )
                )
                endpoints.append(
                    _DirectEndpoint(
                        amplifier=amplifier,
                        host=device.host,
                        device_id=device.device_id,
                        output_start=device.output_start,
                        native_output_start=device.native_output_start,
                        output_count=device.output_count,
                        source_count=device.source_count,
                        source_base=device.source_base,
                        model_byte=device.model_byte,
                    )
                )
        else:
            endpoints = [
                _DirectEndpoint(
                    amplifier=self.amplifier,
                    host=self.host,
                    device_id=self._amplifier_device_id,
                    output_start=1,
                    native_output_start=1,
                    output_count=self.amplifier.output_count,
                    source_count=self.amplifier.source_count,
                    source_base=self.amplifier.source_base,
                )
            ]
        self._direct_endpoints_cache = endpoints
        return endpoints

    def _primary_direct_endpoint(self) -> _DirectEndpoint:
        endpoints = self._direct_endpoints()
        for endpoint in endpoints:
            if endpoint.host == self.host:
                return endpoint
        return endpoints[0]

    def _direct_endpoint_for_device(self, device_id: str) -> _DirectEndpoint:
        normalized = str(device_id).strip().upper()
        for endpoint in self._direct_endpoints():
            if endpoint.device_id == normalized:
                return endpoint
        raise ValueError(f"No configured direct amplifier device {device_id!r}")

    def _direct_endpoint_for_output(self, output: OutputRef) -> tuple[_DirectEndpoint, str | int]:
        global_output = self._global_output_id(output)
        if global_output is not None:
            for endpoint in self._direct_endpoints():
                if endpoint.owns_global_output(global_output):
                    # Public output IDs are global across the configured stack;
                    # each direct socket still expects its device-local address.
                    return endpoint, endpoint.local_output(global_output)
            if not self._configured_direct_devices():
                return self._primary_direct_endpoint(), global_output
        endpoint = self._primary_direct_endpoint()
        return endpoint, self._direct_output_ref(output, endpoint=endpoint)

    def _direct_endpoint_for_source(self, source: SourceRef) -> _DirectEndpoint:
        device_id = _source_device_id(source)
        if device_id is not None:
            for endpoint in self._direct_endpoints():
                if endpoint.device_id == device_id:
                    # Device-qualified synthetic source IDs keep local inputs
                    # on their owning amp even when a different amp is primary.
                    return endpoint
        return self._primary_direct_endpoint()

    def _list_direct_zone_groups(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicZoneGroup]:
        groups: list[AutonomicZoneGroup] = []
        seen: set[tuple[tuple[int, ...], bool, bool, bool]] = set()
        for endpoint in self._direct_endpoints():
            for group in endpoint.amplifier.discover_zone_groups():
                key = (group.zones, group.source_linked, group.volume_linked, group.power_linked)
                if key in seen:
                    continue
                seen.add(key)
                groups.append(self._direct_zone_group_model(group).bind(self))

        if include_disabled:
            rendered = groups
        else:
            rendered = [group for group in groups if group.attributes.get("disabled", "false").lower() != "true"]
        if start is not None or count is not None:
            start_index = max(0, int(start or 1) - 1)
            end_index = None if count is None else start_index + int(count)
            return rendered[start_index:end_index]
        return rendered

    def _direct_zone_group_model(self, group: AmplifierZoneGroup) -> AutonomicZoneGroup:
        zone_ids = ",".join(str(zone) for zone in group.zones)
        outputs = [self._direct_group_output(zone) for zone in group.zones]
        attributes = {
            "id": f"DirectGroup_{zone_ids.replace(',', '_')}",
            "name": f"Direct Group {zone_ids}",
            "zones": zone_ids,
            "sourceLinked": "true" if group.source_linked else "false",
            "volumeLinked": "true" if group.volume_linked else "false",
            "powerLinked": "true" if group.power_linked else "false",
            "raw": group.raw,
        }
        return AutonomicZoneGroup(
            id=attributes["id"],
            name=attributes["name"],
            attributes=attributes,
            volume_outputs=outputs if group.volume_linked else [],
            source_outputs=outputs if group.source_linked else [],
        )

    def _direct_group_output(self, global_output: int) -> AutonomicOutput:
        endpoint = self._direct_endpoint_for_global_output(global_output)
        native_output = endpoint.local_output(global_output) if endpoint is not None else global_output
        attrs = {
            "id": str(global_output),
            "eventId": f"Zone_{global_output}",
            "globalId": str(global_output),
            "nativeId": str(native_output),
        }
        if endpoint is not None:
            attrs["deviceHost"] = endpoint.host
            if endpoint.device_id:
                attrs["deviceId"] = endpoint.device_id
        name = self.config.direct_amplifier.output_names.get(str(global_output), f"Zone {global_output}")
        return AutonomicOutput(
            id=str(global_output),
            name=name,
            address=MirageAmplifier.encode_output(native_output),
            attributes=attrs,
        ).bind(self)

    def _direct_endpoint_for_global_output(self, global_output: int) -> _DirectEndpoint | None:
        for endpoint in self._direct_endpoints():
            if endpoint.owns_global_output(global_output):
                return endpoint
        return None

    def _direct_zone_group_matches(
        self,
        group: AutonomicZoneGroup,
        group_or_zone: str | AutonomicZoneGroup | AutonomicOutput,
    ) -> bool:
        if isinstance(group_or_zone, AutonomicZoneGroup):
            return group_or_zone.id == group.id or group_or_zone.name == group.name or group_or_zone.guid == group.guid
        if isinstance(group_or_zone, AutonomicOutput):
            global_output = self._global_output_id(group_or_zone)
            return global_output in self._direct_zone_ids_for_group(group) if global_output is not None else False

        ref = str(group_or_zone).strip()
        if _name_matches(group.id, ref) or _name_matches(group.name, ref) or _name_matches(group.guid, ref):
            return True
        global_output = self._global_output_id(ref)
        return global_output in self._direct_zone_ids_for_group(group) if global_output is not None else False

    @staticmethod
    def _direct_zone_ids_for_group(group: AutonomicZoneGroup) -> tuple[int, ...]:
        zones = group.attributes.get("zones", "")
        output_ids: list[int] = []
        for item in zones.split(","):
            item = item.strip()
            if item.isdigit():
                output_ids.append(int(item))
        return tuple(output_ids)

    def _global_output_id(self, output: OutputRef) -> int | None:
        if isinstance(output, AutonomicOutput):
            native_id = output.attributes.get("globalId") or output.id
            try:
                return int(str(native_id).removeprefix("Zone_"))
            except (TypeError, ValueError):
                return None

        value = output_ref(output)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            for output_id, name in self.config.direct_amplifier.output_names.items():
                if _name_matches(name, stripped):
                    return int(output_id)
            if stripped.lower().startswith("zone_"):
                stripped = stripped.split("_", 1)[1]
            if stripped.isdigit():
                return int(stripped)
        return None

    def _direct_output_ref(self, output: OutputRef, *, endpoint: _DirectEndpoint | None = None) -> str | int:
        value = output_ref(output)
        if isinstance(output, AutonomicOutput):
            native_id = output.attributes.get("nativeId")
            if native_id is not None and native_id != "":
                try:
                    return int(native_id)
                except ValueError:
                    return native_id

        global_output = self._global_output_id(output)
        target = endpoint or self._primary_direct_endpoint()
        if global_output is not None and target.owns_global_output(global_output):
            return target.local_output(global_output)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return value

    @staticmethod
    def _covers_all_direct_outputs(outputs: Iterable[str | int], endpoint: _DirectEndpoint) -> bool:
        output_ids: set[int] = set()
        for output in outputs:
            try:
                output_ids.add(int(output))
            except (TypeError, ValueError):
                return False
        return output_ids == set(range(endpoint.native_output_start, endpoint.native_output_end + 1))

    def _list_direct_outputs(self, *, include_disabled: bool, include_status: bool) -> list[AutonomicOutput]:
        outputs: list[AutonomicOutput] = []
        for endpoint in self._direct_endpoints():
            source_names = self._direct_source_names_for_endpoint(endpoint) if include_status else None
            if endpoint.native_output_start == 1 and endpoint.amplifier.output_count == endpoint.output_count:
                endpoint_outputs = endpoint.amplifier.list_outputs(
                    include_disabled=include_disabled,
                    include_status=include_status,
                    source_names=source_names,
                )
            elif include_status:
                endpoint_outputs = endpoint.amplifier.get_output_statuses(
                    range(endpoint.native_output_start, endpoint.native_output_end + 1),
                    source_names=source_names,
                )
            else:
                endpoint_outputs = [
                    AutonomicOutput(
                        id=str(native_output),
                        name=f"Output {native_output}",
                        kind="Output",
                        address=MirageAmplifier.encode_output(native_output),
                    )
                    for native_output in range(endpoint.native_output_start, endpoint.native_output_end + 1)
                ]
            for output in endpoint_outputs:
                outputs.append(self._with_direct_output_endpoint(output, endpoint).bind(self))
        return outputs

    def _with_direct_output_endpoint(self, output: AutonomicOutput, endpoint: _DirectEndpoint) -> AutonomicOutput:
        local_id = int(output.id or 0)
        global_id = endpoint.global_output(local_id) if local_id else local_id
        attrs = dict(output.attributes)
        attrs["nativeId"] = str(local_id)
        attrs["globalId"] = str(global_id)
        attrs["deviceHost"] = endpoint.host
        if endpoint.device_id:
            attrs["deviceId"] = endpoint.device_id

        update: dict[str, ModelUpdateValue] = {
            "id": str(global_id),
            "attributes": attrs,
        }
        output_alias = self.config.direct_amplifier.output_names.get(str(global_id))
        if output_alias is not None:
            update["name"] = output_alias

        source_alias = self._direct_source_alias(output.source_name, output.source_id, endpoint=endpoint)
        if source_alias is not None:
            update["source_name"] = source_alias
        if output.source_id is not None:
            update["source_id"] = _synthetic_source_id(endpoint.device_id, output.source_id)
        return output.model_copy(update=update)

    def _list_direct_sources(self, *, include_disabled: bool) -> list[AutonomicSource]:
        rendered: list[AutonomicSource] = []
        seen_ids: set[str | None] = set()
        for endpoint in self._direct_endpoints():
            endpoint_sources = endpoint.amplifier.list_sources(include_disabled=include_disabled)
            endpoint_native_ids: set[str | None] = set()
            for source in endpoint_sources:
                if not self._direct_source_belongs_to_endpoint(source, endpoint):
                    continue
                item = self._with_direct_source_endpoint(source, endpoint)
                endpoint_native_ids.add(_native_source_ref(item.id))
                if item.id not in seen_ids:
                    rendered.append(item.bind(self))
                    seen_ids.add(item.id)
            for source in self._direct_remote_sources(endpoint=endpoint, existing_ids=endpoint_native_ids):
                if source.id not in seen_ids:
                    rendered.append(source.bind(self))
                    seen_ids.add(source.id)
        return rendered

    def _direct_source_names_for_endpoint(self, endpoint: _DirectEndpoint) -> dict[int, str]:
        try:
            sources = self._source_cache if self._source_cache is not None else self._read_sources(include_disabled=True)
        except (OSError, ProtocolError):
            return {}
        if self._source_cache is None:
            self._source_cache = sources

        names: dict[int, str] = {}
        for source in sources:
            if not self._cached_source_on_endpoint(source, endpoint):
                continue
            native_id = _native_source_ref(source.id) or source.attributes.get("nativeId", "")
            try:
                source_id_value = int(native_id)
            except ValueError:
                continue
            if source.name:
                names[source_id_value] = source.name
        return names

    @staticmethod
    def _direct_source_belongs_to_endpoint(source: AutonomicSource, endpoint: _DirectEndpoint) -> bool:
        native_id = _native_source_ref(source.id)
        if native_id in (None, ""):
            return True
        try:
            numeric_id = int(native_id)
        except ValueError:
            return True
        if numeric_id >= 0x20:
            return True
        try:
            slot = _slot_from_source_id(numeric_id, endpoint)
        except ValueError:
            return False
        return 1 <= slot <= endpoint.source_count

    @staticmethod
    def _cached_source_on_endpoint(source: AutonomicSource, endpoint: _DirectEndpoint) -> bool:
        source_device_id = source.attributes.get("deviceId") or _source_device_id_from_id(source.id)
        if endpoint.device_id and source_device_id and endpoint.device_id != source_device_id.upper():
            return False
        return True

    def _with_direct_source_endpoint(self, source: AutonomicSource, endpoint: _DirectEndpoint) -> AutonomicSource:
        definition = self._direct_source_definition(source.name, source.id, endpoint=endpoint)
        attrs = dict(source.attributes)
        attrs.setdefault("rawName", source.name or "")
        if source.id is not None:
            attrs.setdefault("nativeId", _native_source_ref(source.id))
        attrs["deviceHost"] = endpoint.host
        if endpoint.device_id:
            attrs["deviceId"] = endpoint.device_id

        update: dict[str, ModelUpdateValue] = {
            "id": _synthetic_source_id(endpoint.device_id, source.id),
            "attributes": attrs,
        }
        if definition is not None:
            name, guid, device_id = definition
            update["name"] = name
            update["id"] = _synthetic_source_id(device_id or endpoint.device_id, source.id)
            if guid is not None:
                update["guid"] = guid
            if device_id:
                attrs["deviceId"] = device_id
        return source.model_copy(update=update)

    def _with_source_alias(self, source: AutonomicSource) -> AutonomicSource:
        if self._backend() == "amplifier":
            endpoint = self._direct_endpoint_for_source(source)
            definition = self._direct_source_definition(source.name, source.id, endpoint=endpoint)
            if definition is None:
                return source
            name, guid, device_id = definition
            attrs = dict(source.attributes)
            attrs.setdefault("rawName", source.name or "")
            if source.id is not None:
                attrs.setdefault("nativeId", _native_source_ref(source.id))
            if device_id:
                attrs["deviceId"] = device_id
            attrs["name"] = name
            if guid is not None:
                attrs["guid"] = guid
            synthetic_id = _synthetic_source_id(device_id, source.id)
            return source.model_copy(update={"id": synthetic_id, "name": name, "guid": guid, "attributes": attrs})

        alias = self._source_alias_for_guid(source.guid)
        if alias is None:
            return source
        return source.model_copy(update={"name": alias})

    def _with_output_aliases(self, output: AutonomicOutput) -> AutonomicOutput:
        if self._backend() == "amplifier":
            update: dict[str, str] = {}
            output_alias = self.config.direct_amplifier.output_names.get(str(output.id))
            if output_alias is not None:
                update["name"] = output_alias
            endpoint = self._direct_endpoint_for_source_id(output.source_id)
            source_alias = self._direct_source_alias(output.source_name, output.source_id, endpoint=endpoint)
            if source_alias is not None:
                update["source_name"] = source_alias
            if not update:
                return output
            return output.model_copy(update=update)

        output = self._normalize_mrad_output_volume(output)
        alias = self._source_alias_for_guid(output.source_guid)
        if alias is None:
            return output
        return output.model_copy(update={"source_name": alias})

    def _direct_source_id(self, source: SourceRef, *, endpoint: _DirectEndpoint | None = None) -> int:
        target = endpoint or self._direct_endpoint_for_source(source)
        explicit_device_id = _source_device_id(source)
        try:
            native = source_id(source)
            if explicit_device_id in (None, target.device_id) or native >= 0x20:
                return native
        except (TypeError, ValueError):
            if not isinstance(source, str):
                raise

        local_source = self._local_source_match(source, endpoint=target)
        if local_source is not None:
            return _source_id_from_slot(local_source.slot, target)

        remote_source = self._remote_source_match(source, endpoint=target)
        if remote_source is not None:
            return remote_source.source_id

        raise ValueError(f"No direct amplifier source named {source!r}")

    def _direct_endpoint_for_source_id(self, source_id_value: str | None) -> _DirectEndpoint:
        device_id = _source_device_id_from_id(source_id_value)
        if device_id is not None:
            for endpoint in self._direct_endpoints():
                if endpoint.device_id == device_id:
                    return endpoint
        return self._primary_direct_endpoint()

    def _direct_source_alias(self, source_name: str | None, source_id_value: str | None, *, endpoint: _DirectEndpoint) -> str | None:
        definition = self._direct_source_definition(source_name, source_id_value, endpoint=endpoint)
        if definition is not None:
            return definition[0]

        if source_name:
            alias = self.config.direct_amplifier.source_name_aliases.get(source_name.strip().lower())
            if alias is not None:
                return alias
        slot_name = self._direct_source_slot_name(source_id_value, endpoint=endpoint)
        if slot_name is None:
            return None
        return self.config.direct_amplifier.source_name_aliases.get(slot_name.lower())

    def _direct_source_slot_name(self, source_id_value: str | None, *, endpoint: _DirectEndpoint | None = None) -> str | None:
        if source_id_value in (None, ""):
            return None
        try:
            numeric_id = int(_native_source_ref(source_id_value))
        except (TypeError, ValueError):
            return None
        if numeric_id >= 0x20:
            return None
        target = endpoint or self._primary_direct_endpoint()
        if target.source_base == 0:
            return f"S{numeric_id + 1}"
        return f"S{numeric_id}"

    def _direct_source_definition(
        self,
        source_name: str | None,
        source_id_value: str | None,
        *,
        endpoint: _DirectEndpoint,
    ) -> tuple[str, str | None, str | None] | None:
        if source_id_value:
            remote_definition = self.config.direct_amplifier.remote_source_for(
                target_device_id=endpoint.device_id,
                source_id=_native_source_ref(source_id_value),
            )
            if remote_definition is not None:
                return (remote_definition.name, remote_definition.guid, remote_definition.target_device_id)

        slot_names = [source_name, self._direct_source_slot_name(source_id_value, endpoint=endpoint)]
        for slot_name in slot_names:
            if not slot_name:
                continue
            local_definition = self._direct_local_source_by_slot(endpoint).get(slot_name.strip().lower())
            if local_definition is not None:
                return local_definition
        return None

    def _direct_local_source_by_slot(self, endpoint: _DirectEndpoint) -> dict[str, tuple[str, str | None, str | None]]:
        device_id = endpoint.device_id or self._direct_device_id()
        if not device_id:
            return {}
        definitions = self.config.direct_amplifier.local_sources_by_device_id.get(device_id.upper(), ())
        lookup: dict[str, tuple[str, str | None, str | None]] = {}
        for source in definitions:
            value = (source.name, source.guid, device_id.upper())
            lookup[f"s{source.slot}"] = value
            lookup[source.name.strip().lower()] = value
            for alias in source.aliases:
                lookup[alias.strip().lower()] = value
        return lookup

    def _local_source_match(self, source: SourceRef, *, endpoint: _DirectEndpoint) -> DirectLocalSourceConfig | None:
        source_device_id = _source_device_id(source)
        if source_device_id and endpoint.device_id and source_device_id != endpoint.device_id:
            return None
        if isinstance(source, AutonomicSource):
            native_id = _native_source_ref(source.id)
            if native_id:
                try:
                    slot = _slot_from_source_id(int(native_id), endpoint)
                except ValueError:
                    slot = -1
                if slot > 0:
                    for item in self._local_sources_for_endpoint(endpoint):
                        if item.slot == slot:
                            return item
        if not isinstance(source, str):
            return None
        normalized = _native_source_ref(source).strip().lower() if source_device_id else source.strip().lower()
        for item in self._local_sources_for_endpoint(endpoint):
            names = {item.name.strip().lower(), f"s{item.slot}"}
            names.update(alias.strip().lower() for alias in item.aliases)
            if normalized in names:
                return item
        return None

    def _local_sources_for_endpoint(self, endpoint: _DirectEndpoint) -> tuple[DirectLocalSourceConfig, ...]:
        device_id = endpoint.device_id or self._direct_device_id()
        configured_by_slot: dict[int, DirectLocalSourceConfig] = {}
        if device_id:
            configured = self.config.direct_amplifier.local_sources_by_device_id.get(device_id.upper())
            if configured:
                configured_by_slot = {source.slot: source for source in configured}

        hardware_by_slot = _hardware_local_source_labels(endpoint)
        sources: list[DirectLocalSourceConfig] = []
        for slot in range(1, endpoint.source_count + 1):
            configured_source = configured_by_slot.get(slot)
            hardware_name = hardware_by_slot.get(slot)
            if configured_source is None:
                sources.append(DirectLocalSourceConfig(slot=slot, name=hardware_name or f"S{slot}"))
                continue

            aliases = list(configured_source.aliases)
            for alias in (hardware_name, f"S{slot}"):
                if alias and alias.strip().lower() not in {item.strip().lower() for item in aliases} and alias.strip().lower() != configured_source.name.strip().lower():
                    aliases.append(alias)
            sources.append(
                DirectLocalSourceConfig(
                    slot=configured_source.slot,
                    name=configured_source.name,
                    guid=configured_source.guid,
                    aliases=tuple(aliases),
                )
            )
        return tuple(sources)

    def _remote_source_match(self, source: SourceRef, *, endpoint: _DirectEndpoint) -> DirectRemoteSourceConfig | None:
        source_device_id = _source_device_id(source)
        source_guid = source.guid if isinstance(source, AutonomicSource) else None
        source_name = source.name if isinstance(source, AutonomicSource) else str(source) if isinstance(source, str) else None
        endpoint_device_id = endpoint.device_id or self._direct_device_id()
        for remote_source in self.config.direct_amplifier.remote_sources:
            if endpoint_device_id and remote_source.target_device_id not in (None, endpoint_device_id):
                continue
            if source_device_id and remote_source.source_device_id not in (None, source_device_id):
                continue
            if source_guid and _name_matches(remote_source.guid, source_guid):
                return remote_source
            if source_name and _name_matches(remote_source.name, source_name):
                return remote_source
        return None

    def _direct_remote_sources(self, *, endpoint: _DirectEndpoint, existing_ids: Iterable[str | None]) -> list[AutonomicSource]:
        sources: list[AutonomicSource] = []
        existing_native_ids = {_native_source_ref(source_id_value) for source_id_value in existing_ids if source_id_value}
        endpoint_device_id = endpoint.device_id or self._direct_device_id()
        for remote_source in self.config.direct_amplifier.remote_sources:
            source_id_value = str(remote_source.source_id)
            if endpoint_device_id is not None and remote_source.target_device_id not in (None, endpoint_device_id):
                continue
            if source_id_value in existing_native_ids:
                continue
            address = MirageAmplifier.encode_matrix_source(remote_source.source_id)
            synthetic_id = _synthetic_source_id(endpoint_device_id, source_id_value)
            if synthetic_id is None:
                raise ValueError(f"remote source {remote_source.source_id} produced no synthetic id")
            sources.append(
                AutonomicSource(
                    id=synthetic_id,
                    guid=remote_source.guid,
                    name=remote_source.name,
                    address=address,
                    attributes={
                        "id": synthetic_id,
                        "nativeId": source_id_value,
                        "guid": remote_source.guid,
                        "name": remote_source.name,
                        "address": address,
                        "remoteSlot": f"{remote_source.source_id - 0x20:02X}",
                        **_device_attr(remote_source),
                    },
                )
            )
        return sources

    def _direct_endpoint_guid(self, endpoint: _DirectEndpoint) -> str:
        device_id = endpoint.device_id or endpoint.amplifier.infer_layout().device_id
        for device in endpoint.amplifier.discover_devices():
            if device_id is None or device.amp_id == device_id:
                if device.guid:
                    return device.guid
        raise AutonomicError(f"No device GUID discovered for direct amplifier {endpoint.host}")

    def _direct_source_display_name(self, source: SourceRef, *, endpoint: _DirectEndpoint) -> str:
        if isinstance(source, AutonomicSource) and source.name:
            return source.name
        if isinstance(source, str) and _source_device_id_from_id(source) is None:
            try:
                source_id(source)
            except ValueError:
                return source
            except TypeError:
                pass
        if isinstance(source, str) and not source.strip():
            return source
        native_id = str(self._direct_source_id(source, endpoint=endpoint))
        synthetic_id = _synthetic_source_id(endpoint.device_id, native_id)
        for item in self.list_sources(include_disabled=True):
            if item.id == synthetic_id or _native_source_ref(item.id) == native_id:
                return item.name or f"Source {native_id}"
        return f"Source {native_id}"

    def _update_cached_source_name(
        self,
        source: SourceRef,
        name: str,
        *,
        endpoint: _DirectEndpoint | None = None,
        native_id: int | None = None,
    ) -> None:
        if self._source_cache is None:
            return

        native_id_text = None if native_id is None else str(native_id)
        updated: list[AutonomicSource] = []
        for cached in self._source_cache:
            if not self._cached_source_matches_ref(cached, source, endpoint=endpoint, native_id=native_id_text):
                updated.append(cached)
                continue

            attrs = dict(cached.attributes)
            attrs["name"] = name
            attrs["rawName"] = name
            display_name = self._cached_source_display_name(cached, name, endpoint=endpoint)
            updated.append(cached.model_copy(update={"name": display_name, "attributes": attrs}))
        self._source_cache = updated

    def _update_cached_source_icon(self, source: SourceRef, icon: str) -> None:
        if self._source_cache is None:
            return

        updated: list[AutonomicSource] = []
        for cached in self._source_cache:
            if not self._cached_source_matches_ref(cached, source):
                updated.append(cached)
                continue
            attrs = dict(cached.attributes)
            attrs["icon"] = icon
            attrs["iconId"] = icon
            updated.append(cached.model_copy(update={"attributes": attrs}))
        self._source_cache = updated

    def _update_cached_low_level_source_names(self) -> None:
        if self._source_cache is None:
            return

        updated: list[AutonomicSource] = []
        for cached in self._source_cache:
            replacement = cached
            for endpoint in self._direct_endpoints():
                if not self._cached_source_on_endpoint(cached, endpoint):
                    continue
                label = self._low_level_source_label(cached, endpoint=endpoint)
                if label is None:
                    continue
                attrs = dict(cached.attributes)
                attrs["name"] = label
                attrs["rawName"] = label
                replacement = cached.model_copy(update={"name": label, "attributes": attrs})
                break
            updated.append(replacement)
        self._source_cache = updated

    def _low_level_source_label(self, source: AutonomicSource, *, endpoint: _DirectEndpoint) -> str | None:
        native_id = _native_source_ref(source.id) or source.attributes.get("nativeId", "")
        try:
            source_id_value = int(native_id)
        except ValueError:
            return None
        if source_id_value >= REMOTE_SOURCE_START:
            return None
        try:
            slot = _slot_from_source_id(source_id_value, endpoint)
        except ValueError:
            return None
        if not 1 <= slot <= endpoint.source_count:
            return None
        return _hardware_local_source_labels(endpoint).get(slot, f"S{slot}")

    def _upsert_cached_remote_source(
        self,
        *,
        target: _DirectEndpoint,
        slot: int,
        source_endpoint: _DirectEndpoint,
        source_guid: str,
        source_position: int,
        source_name: str,
    ) -> None:
        if self._source_cache is None:
            return

        remote_source_id = REMOTE_SOURCE_START + int(slot)
        target_device_id = target.device_id or self._direct_device_id()
        synthetic_id = _synthetic_source_id(target_device_id, str(remote_source_id))
        if synthetic_id is None:
            return
        address = MirageAmplifier.encode_matrix_source(remote_source_id)
        attrs = {
            "id": synthetic_id,
            "nativeId": str(remote_source_id),
            "guid": source_guid,
            "name": source_name,
            "address": address,
            "remoteSlot": f"{int(slot):02X}",
            "sourceNativeId": str(source_position),
            "sourceDeviceHost": source_endpoint.host,
        }
        if target_device_id:
            attrs["deviceId"] = target_device_id
        if source_endpoint.device_id:
            attrs["sourceDeviceId"] = source_endpoint.device_id

        source = AutonomicSource(
            id=synthetic_id,
            guid=source_guid,
            name=source_name,
            address=address,
            attributes=attrs,
        )
        self._source_cache = [
            cached
            for cached in self._source_cache
            if not self._cached_source_matches_ref(cached, synthetic_id, endpoint=target, native_id=str(remote_source_id))
        ]
        self._source_cache.append(source)

    def _remove_cached_remote_source(self, *, endpoint: _DirectEndpoint, slot: int) -> None:
        if self._source_cache is None:
            return
        remote_source_id = str(REMOTE_SOURCE_START + int(slot))
        self._source_cache = [
            source
            for source in self._source_cache
            if not self._cached_source_matches_ref(source, remote_source_id, endpoint=endpoint, native_id=remote_source_id)
        ]

    def _remove_cached_remote_sources(self) -> None:
        if self._source_cache is None:
            return
        self._source_cache = [source for source in self._source_cache if not self._cached_source_is_remote(source)]

    @staticmethod
    def _cached_source_is_remote(source: AutonomicSource) -> bool:
        native_id = _native_source_ref(source.id) or source.attributes.get("nativeId", "")
        try:
            return int(native_id) >= REMOTE_SOURCE_START
        except ValueError:
            return False

    def _cached_source_display_name(
        self,
        source: AutonomicSource,
        name: str,
        *,
        endpoint: _DirectEndpoint | None = None,
    ) -> str:
        if endpoint is not None:
            definition = self._direct_source_definition(name, source.id, endpoint=endpoint)
            if definition is not None:
                return definition[0]
            return name
        return self._source_alias_for_guid(source.guid) or name

    def _cached_source_matches_ref(
        self,
        source: AutonomicSource,
        reference: SourceRef,
        *,
        endpoint: _DirectEndpoint | None = None,
        native_id: str | None = None,
    ) -> bool:
        if endpoint is not None and not self._cached_source_on_endpoint(source, endpoint):
            return False

        source_native_id = _native_source_ref(source.id) or source.attributes.get("nativeId", "")
        if native_id is not None and self._same_source_ref(source_native_id, native_id):
            return True

        if isinstance(reference, AutonomicSource):
            return (
                self._same_optional_ref(source.id, reference.id)
                or self._same_optional_ref(source.guid, reference.guid)
                or (
                    reference.name is not None
                    and (_name_matches(source.name, reference.name) or _name_matches(source.attributes.get("rawName"), reference.name))
                )
            )

        if isinstance(reference, int):
            return self._same_source_ref(source_native_id, str(reference)) or self._same_optional_ref(source.id, str(reference))

        ref = reference.strip()
        return (
            self._same_optional_ref(source.id, ref)
            or self._same_optional_ref(source.guid, ref)
            or self._same_source_ref(source_native_id, _native_source_ref(ref))
            or _name_matches(source.name, ref)
            or _name_matches(source.attributes.get("rawName"), ref)
            or _name_matches(source.attributes.get("name"), ref)
        )

    @staticmethod
    def _same_optional_ref(left: str | None, right: str | None) -> bool:
        if left is None or right is None:
            return False
        return left.strip().lower() == right.strip().lower()

    @staticmethod
    def _same_source_ref(left: str, right: str) -> bool:
        if left.strip().lower() == right.strip().lower():
            return True
        try:
            return int(left) == int(right)
        except ValueError:
            return False

    def _direct_device_id(self) -> str | None:
        if self._amplifier_device_id:
            return self._amplifier_device_id.upper()
        try:
            self._amplifier_device_id = self.amplifier.get_device_id()
        except Exception:
            return None
        return self._amplifier_device_id.upper()

    def _mrad_client(self) -> MirageAudioSystem:
        if self._detected_mode == "mrad":
            return self.audio
        if self.audio.host == self.host and _can_connect(self.audio.host, self.audio.port, self.audio.timeout):
            return self.audio
        cached = self._mrad_client_cache
        if cached is not None:
            return cached
        for device in self._configured_direct_devices():
            if _can_connect(device.host, self.mrad_port, self.timeout):
                self._mrad_client_cache = MirageAudioSystem(device.host, self.mrad_port, timeout=self.timeout)
                return self._mrad_client_cache
        if self.audio.host != self.host:
            return self.audio
        raise AutonomicError("No MRAD bridge is available for this client")

    def _mrad_control_client(self) -> MirageAudioSystem:
        client = self._mrad_client()
        if id(client) not in self._initialized_mrad_clients:
            client.initialize(host_hint=client.host, subscribe=False)
            self._initialized_mrad_clients.add(id(client))
        return client

    def _mrad_output_status(self, output: OutputRef) -> AutonomicOutput:
        return self._with_output_aliases(
            self._mrad_control_client().get_output_status(self._mrad_output_ref(output))
        ).bind(self)

    def _mrad_output_ref(self, output: OutputRef) -> OutputRef:
        value = output_ref(output)
        global_output = self._global_output_id(output)
        if global_output is not None:
            return f"Zone_{global_output}"
        return value

    def _mrad_scaled_volume(self, value: float, output: OutputRef | None = None) -> int:
        return int(round((_percent(value) * self._mrad_volume_scale(output)) / 100))

    def _mrad_volume_scale(self, output: OutputRef | None = None) -> int:
        if isinstance(output, AutonomicOutput):
            for key in ("MaxMaxVolume", "maxMaxVolume", "VolumeMax", "volumeMax"):
                raw = output.attributes.get(key)
                if raw is None or raw == "":
                    continue
                try:
                    return int(raw)
                except ValueError:
                    pass

        global_output = self._global_output_id(output) if output is not None else None
        if global_output is not None:
            for endpoint in self._direct_endpoints():
                if endpoint.owns_global_output(global_output):
                    model = model_for_byte(endpoint.model_byte)
                    return model.mrad_volume_max if model is not None else 100
        return 100

    def _normalize_mrad_output_volume(self, output: AutonomicOutput) -> AutonomicOutput:
        scale = self._mrad_volume_scale(output)
        if scale == 100:
            return output
        attrs = dict(output.attributes)
        update: dict[str, ModelUpdateValue] = {"attributes": attrs}
        for field_name in (
            "volume",
            "min_volume",
            "min_min_volume",
            "max_volume",
            "max_max_volume",
            "power_on_volume",
        ):
            value = getattr(output, field_name)
            if value is None:
                continue
            attrs[f"raw_{field_name}"] = str(value)
            update[field_name] = round((float(value) * 100) / scale, 3)
        return output.model_copy(update=update)

    def _source_alias_for_guid(self, guid: str | None) -> str | None:
        if not guid:
            return None
        return self.source_aliases.get(guid.strip().lower())

    def _source_alias_ref(self, source: SourceRef) -> SourceRef:
        if not isinstance(source, str):
            return source
        normalized = source.strip().lower()
        for guid, alias in self.source_aliases.items():
            if alias.strip().lower() == normalized:
                return guid
        return source

    def __enter__(self) -> "AutonomicClient":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
