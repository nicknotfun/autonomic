# Low-level MRAD/MAS client for zones, sources, groups, and amplifier status.
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from .base import ProtocolClient, ProtocolConnection
from .exceptions import AutonomicError, CommandError
from .models import (
    AutonomicPartyModeInfo,
    AutonomicOutput,
    AutonomicSource,
    AutonomicZoneGroup,
    object_ref,
    omit_disabled,
    output_ref,
    source_ref,
)
from .protocol_types import BrowseResponse, CommandResponse, StatusSnapshot
from .protocol import CommandArg, events_to_snapshot, format_command
from .mrad_types import MRAD_PORT, MRADCommandHelp, MRADVersion, OutputRef, SourceRef, XmlMode
from .mrad_utils import apply_status as _apply_status
from .mrad_utils import base_output as _base_output
from .mrad_utils import bool_toggle_state as _bool_toggle_state
from .mrad_utils import comma_refs as _comma_refs
from .mrad_utils import is_power_off_error as _is_power_off_error
from .mrad_utils import parse_command_help as _parse_command_help
from .mrad_utils import parse_versions as _parse_versions
from .mrad_utils import response_value as _response_value
from .mrad_utils import status_for_output as _status_for_output
from .mrad_utils import toggle_state as _toggle_state
from .mrad_utils import validate_range as _validate_range
from .mrad_utils import xml_mode as _xml_mode


class MirageAudioSystem(ProtocolClient):
    """Client for MRAD/MAS zone, source, and zone-group control."""

    def __init__(
        self,
        host: str,
        port: int = MRAD_PORT,
        *,
        timeout: float = 5.0,
        connection: ProtocolConnection | None = None,
    ):
        super().__init__(host, port, timeout=timeout, connection=connection)
        self._selected_output: OutputRef | None = None

    def initialize(
        self,
        *,
        client_type: str = "PythonSDK",
        host_hint: str | None = None,
        xml_lists: bool = True,
        encoding: int = 65001,
        subscribe: bool | Iterable[str] = "Smart",
        protocol_mode_command: bool = True,
    ) -> None:
        if protocol_mode_command:
            self.command("*")
        self.command("SetClientType", client_type)
        self.command("SetEncoding", encoding)
        self.command("SetXmlMode", "Lists" if xml_lists else "None")
        if host_hint:
            self.command("SetHost", host_hint)
        self.subscribe_events(subscribe)

    def command(
        self,
        name: str,
        *args: CommandArg,
        timeout: float | None = None,
        idle_timeout: float = 0.15,
        collect_events: bool = False,
        collect_until_idle: bool = False,
        include_banners: bool = False,
        expect_response: bool = True,
    ) -> CommandResponse:
        return self.request(
            format_command(name, *args),
            timeout=timeout,
            idle_timeout=idle_timeout,
            collect_events=collect_events,
            collect_until_idle=collect_until_idle,
            include_banners=include_banners,
            expect_response=expect_response,
        )

    def subscribe_events(self, events: bool | Iterable[str] = "Smart") -> CommandResponse:
        if isinstance(events, bool):
            return self.command("SubscribeEvents", events)
        if isinstance(events, str):
            return self.command("SubscribeEvents", events)
        return self.command("SubscribeEvents", ",".join(events))

    def enter_command_mode(self) -> CommandResponse:
        return self.command("!Autonomic")

    def toggle_passthrough_mode(self) -> CommandResponse:
        return self.command("*Autonomic")

    def enter_passthrough_mode(self) -> CommandResponse:
        return self.command("@Autonomic")

    def clear_terminal(self) -> CommandResponse:
        return self.command("cls")

    def exit_session(self) -> CommandResponse:
        return self.command("Exit")

    def set_client_type(self, client_type: str) -> CommandResponse:
        return self.command("SetClientType", client_type)

    def set_encoding(self, encoding: int) -> CommandResponse:
        return self.command("SetEncoding", int(encoding))

    def set_host(self, host: str) -> CommandResponse:
        return self.command("SetHost", host)

    def set_xml_mode(self, mode: XmlMode | bool) -> CommandResponse:
        return self.command("SetXmlMode", _xml_mode(mode))

    def set_response_eol_zero(self, *, expect_response: bool = False) -> CommandResponse:
        response = self.command("SetResponseEolZero", expect_response=expect_response)
        self._connection.set_response_delimiter(b"\x00")
        return response

    def banner(self, *, timeout: float | None = None, idle_timeout: float = 0.2) -> list[str]:
        response = self.command(
            "Banner",
            timeout=timeout,
            idle_timeout=idle_timeout,
            collect_until_idle=True,
            include_banners=True,
        )
        return response.lines

    def get_status(self, *, timeout: float | None = None, idle_timeout: float = 0.15) -> StatusSnapshot:
        response = self.command("GetStatus", timeout=timeout, idle_timeout=idle_timeout, collect_events=True)
        return StatusSnapshot(events=response.events, by_source=events_to_snapshot(response.events), raw_lines=response.lines)

    def get_versions(self) -> list[MRADVersion]:
        response = self.command("GetVersions")
        return _parse_versions(response.lines)

    def ping(self) -> CommandResponse:
        return self.command("Ping")

    def ping_ok(self) -> bool:
        return _response_value(self.ping(), "Ping") == "Pong"

    def echo(self, text: str) -> CommandResponse:
        return self.command("Echo", text)

    def echo_text(self, text: str) -> str:
        return _response_value(self.echo(text), "Echo") or ""

    def uptime(self) -> CommandResponse:
        return self.command("Uptime")

    def uptime_text(self) -> str:
        return _response_value(self.uptime(), "Uptime") or ""

    def time(self, fmt: str = "U") -> CommandResponse:
        return self.command("Time", fmt)

    def time_text(self, fmt: str = "U") -> str:
        return _response_value(self.time(fmt), "Time") or ""

    def sync_time(self) -> CommandResponse:
        return self.command("SyncTime")

    def log_comment(self, text: str) -> CommandResponse:
        return self.command("LogComment", text)

    def set_client_version(self, version: str) -> CommandResponse:
        return self.command("SetClientVersion", version)

    def help(self, command: str | None = None, *, timeout: float | None = None, idle_timeout: float = 0.2) -> list[str]:
        response = self.command(
            "help",
            command,
            timeout=timeout,
            idle_timeout=idle_timeout,
            collect_until_idle=True,
        )
        return response.lines

    def help_text(self, command: str | None = None, *, timeout: float | None = None, idle_timeout: float = 0.2) -> str:
        return "\n".join(self.help(command, timeout=timeout, idle_timeout=idle_timeout))

    def command_catalog(self, *, timeout: float | None = None, idle_timeout: float = 0.2) -> list[MRADCommandHelp]:
        return _parse_command_help(self.help(timeout=timeout, idle_timeout=idle_timeout))

    def command_help(self, command: str, *, timeout: float | None = None, idle_timeout: float = 0.2) -> MRADCommandHelp:
        entries = _parse_command_help(self.help(command, timeout=timeout, idle_timeout=idle_timeout))
        normalized = command.strip().lower()
        for entry in entries:
            if entry.command.lower() == normalized:
                return entry
        if entries:
            return entries[0]
        raise LookupError(f"No MRAD help entry for {command!r}")

    def browse_zones(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self._browse("BrowseZones", start, count)

    def browse_all_zones(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self._browse("BrowseAllZones", start, count)

    def browse_sources(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self._browse("BrowseSources", start, count)

    def browse_all_sources(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self._browse("BrowseAllSources", start, count)

    def browse_zone_groups(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self._browse("BrowseZoneGroups", start, count)

    def browse_zone_group(self, group_or_zone: str | AutonomicZoneGroup | AutonomicOutput | None = None) -> BrowseResponse:
        return self._browse_ref("BrowseZoneGroup", group_or_zone)

    def browse_zones_for_group(self, group: str | AutonomicZoneGroup) -> BrowseResponse:
        return self._browse_ref("BrowseZonesForGroup", group)

    def browse_party_mode_include(self) -> BrowseResponse:
        return self._browse_ref("BrowsePartyModeInclude")

    def browse_page_up(self) -> BrowseResponse:
        return self._browse("BrowsePageUp")

    def browse_page_down(self) -> BrowseResponse:
        return self._browse("BrowsePageDown")

    def list_zone_groups(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicZoneGroup]:
        groups = [
            AutonomicZoneGroup.from_browse_item(item, client=self)
            for item in self.browse_zone_groups(start, count).items
        ]
        return omit_disabled(groups, include_disabled=include_disabled)

    def list_zone_group(
        self,
        group_or_zone: str | AutonomicZoneGroup | AutonomicOutput | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicZoneGroup]:
        groups = [
            AutonomicZoneGroup.from_browse_item(item, client=self)
            for item in self.browse_zone_group(group_or_zone).items
        ]
        return omit_disabled(groups, include_disabled=include_disabled)

    def list_zones_for_group(
        self,
        group: str | AutonomicZoneGroup,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicOutput]:
        outputs = [AutonomicOutput.from_browse_item(item, client=self) for item in self.browse_zones_for_group(group).items]
        return omit_disabled(outputs, include_disabled=include_disabled)

    def list_party_mode_include(self, *, include_disabled: bool = False) -> list[AutonomicPartyModeInfo]:
        rows = [
            AutonomicPartyModeInfo.from_browse_item(item, client=self)
            for item in self.browse_party_mode_include().items
        ]
        return omit_disabled(rows, include_disabled=include_disabled)

    def list_outputs(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        include_disabled: bool = False,
        include_status: bool = False,
    ) -> list[AutonomicOutput]:
        """Return all amplifier outputs.

        Autonomic's MRAD docs call physical outputs "zones"; this alias exposes
        the matrix vocabulary used by many control systems.
        """

        outputs = [AutonomicOutput.from_browse_item(item, client=self) for item in self.browse_all_zones(start, count).items]
        if include_status:
            outputs = self._with_status(outputs)
        return omit_disabled(outputs, include_disabled=include_disabled)

    def list_available_outputs(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicOutput]:
        outputs = [AutonomicOutput.from_browse_item(item, client=self) for item in self.browse_zones(start, count).items]
        return omit_disabled(outputs, include_disabled=include_disabled)

    def list_sources(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicSource]:
        sources = [AutonomicSource.from_browse_item(item, client=self) for item in self.browse_all_sources(start, count).items]
        return omit_disabled(sources, include_disabled=include_disabled)

    def list_available_sources(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicSource]:
        sources = [AutonomicSource.from_browse_item(item, client=self) for item in self.browse_sources(start, count).items]
        return omit_disabled(sources, include_disabled=include_disabled)

    def _browse(self, command: str, start: int | None = None, count: int | None = None) -> BrowseResponse:
        response = self.command(command, start, count)
        if response.payload is None:
            raise ValueError(f"{command} did not return a browse response")
        return response.payload

    def _browse_ref(self, command: str, ref: OutputRef | AutonomicZoneGroup | None = None) -> BrowseResponse:
        response = self.command(command, object_ref(ref))
        if response.payload is None:
            raise ValueError(f"{command} did not return a browse response")
        return response.payload

    def set_zone(self, zone: OutputRef) -> CommandResponse:
        self._selected_output = zone
        return self.command("SetZone", output_ref(zone))

    def set_output(self, output: OutputRef) -> CommandResponse:
        return self.set_zone(output)

    def select_output(self, output: OutputRef) -> CommandResponse:
        return self.set_output(output)

    def set_source(
        self,
        source_guid_or_name: SourceRef,
        output: OutputRef | None = None,
        *,
        include_group: bool = False,
    ) -> CommandResponse:
        resolved_source = source_ref(source_guid_or_name)
        if output is None:
            return self._with_active_output_power(None, lambda: self.command("SetSource", resolved_source))
        return self._with_output_power(output, lambda: self.command("SetSource", resolved_source, include_group, output_ref(output)))

    def select_source(
        self,
        source: SourceRef,
        output: OutputRef | None = None,
        *,
        include_group: bool = False,
    ) -> CommandResponse:
        return self.set_source(source, output, include_group=include_group)

    def volume(self, value: float, zone: OutputRef | None = None) -> CommandResponse:
        number = int(round(float(value)))
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("Volume", number))
        return self._with_output_power(zone, lambda: self.command("Volume", number, output_ref(zone)))

    def volume_up(self, zone: OutputRef | None = None) -> CommandResponse:
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("VolumeUp"))
        return self._with_output_power(zone, lambda: self.command("VolumeUp", output_ref(zone)))

    def volume_down(self, zone: OutputRef | None = None) -> CommandResponse:
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("VolumeDown"))
        return self._with_output_power(zone, lambda: self.command("VolumeDown", output_ref(zone)))

    def mute(self, state: bool | str = "toggle", zone: OutputRef | None = None) -> CommandResponse:
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("Mute", state))
        return self._with_output_power(zone, lambda: self.command("Mute", state, output_ref(zone)))

    def set_output_volume(self, output: OutputRef, value: float) -> CommandResponse:
        return self.volume(value, output)

    def get_output_volume(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("GetVolume")
        return self.command("GetVolume", output_ref(output))

    def get_output_mute(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("GetMute")
        return self.command("GetMute", output_ref(output))

    def get_output_status(self, output: OutputRef) -> AutonomicOutput:
        base = _base_output(output)
        status = _status_for_output(base, self.get_status(timeout=max(self.timeout, 6.0), idle_timeout=0.2))
        if not status:
            raise LookupError(f"No MRAD status reported for output {output_ref(output)!r}")
        return _apply_status(base, status).bind(self)

    def read_output_volume(self, output: OutputRef) -> float | None:
        return self.get_output_status(output).volume

    def read_output_mute(self, output: OutputRef) -> bool | None:
        return self.get_output_status(output).muted

    def read_output_power(self, output: OutputRef) -> bool | None:
        return self.get_output_status(output).is_on

    def read_output_max_volume(self, output: OutputRef) -> float | None:
        return self.get_output_status(output).max_volume

    def read_output_bass(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).bass

    def read_output_treble(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).treble

    def read_output_balance(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).balance

    def read_output_gain(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).gain

    def read_output_delay(self, output: OutputRef) -> int | None:
        return self.get_output_status(output).delay_ms

    def read_output_loudness(self, output: OutputRef) -> bool | None:
        return self.get_output_status(output).loudness

    def read_output_mono_downmix(self, output: OutputRef) -> bool | None:
        return self.get_output_status(output).mono_downmix

    def read_output_power_on_volume(self, output: OutputRef) -> float | None:
        return self.get_output_status(output).power_on_volume

    def read_output_source_id(self, output: OutputRef) -> str | None:
        return self.get_output_status(output).source_id

    def read_output_source_name(self, output: OutputRef) -> str | None:
        return self.get_output_status(output).source_name

    def output_volume_up(self, output: OutputRef) -> CommandResponse:
        return self.volume_up(output)

    def output_volume_down(self, output: OutputRef) -> CommandResponse:
        return self.volume_down(output)

    def set_output_mute(self, output: OutputRef, state: bool | str = "toggle") -> CommandResponse:
        return self.mute(state, output)

    def bass(self, value: int, zone: OutputRef | None = None) -> CommandResponse:
        number = _validate_range(value, lower=-12, upper=12, name="Bass")
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("Bass", number))
        return self._with_output_power(zone, lambda: self.command("Bass", number, output_ref(zone)))

    def set_output_bass(self, output: OutputRef, value: int) -> CommandResponse:
        return self.bass(value, output)

    def output_bass_up(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("BassUp")
        return self._with_output_power(output, lambda: self.command("BassUp", output_ref(output)))

    def output_bass_down(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("BassDown")
        return self._with_output_power(output, lambda: self.command("BassDown", output_ref(output)))

    def treble(self, value: int, zone: OutputRef | None = None) -> CommandResponse:
        number = _validate_range(value, lower=-12, upper=12, name="Treble")
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("Treble", number))
        return self._with_output_power(zone, lambda: self.command("Treble", number, output_ref(zone)))

    def set_output_treble(self, output: OutputRef, value: int) -> CommandResponse:
        return self.treble(value, output)

    def output_treble_up(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("TrebleUp")
        return self._with_output_power(output, lambda: self.command("TrebleUp", output_ref(output)))

    def output_treble_down(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("TrebleDown")
        return self._with_output_power(output, lambda: self.command("TrebleDown", output_ref(output)))

    def balance(self, value: int, zone: OutputRef | None = None) -> CommandResponse:
        number = _validate_range(value, lower=-20, upper=20, name="Balance")
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("Balance", number))
        return self._with_output_power(zone, lambda: self.command("Balance", number, output_ref(zone)))

    def set_output_balance(self, output: OutputRef, value: int) -> CommandResponse:
        return self.balance(value, output)

    def output_balance_left(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("BalanceLeft")
        return self._with_output_power(output, lambda: self.command("BalanceLeft", output_ref(output)))

    def output_balance_right(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("BalanceRight")
        return self._with_output_power(output, lambda: self.command("BalanceRight", output_ref(output)))

    def zone_gain(self, value: int, zone: OutputRef | None = None) -> CommandResponse:
        number = _validate_range(value, lower=-12, upper=12, name="ZoneGain")
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("ZoneGain", number))
        return self._with_output_power(zone, lambda: self.command("ZoneGain", number, output_ref(zone)))

    def set_output_gain(self, output: OutputRef, value: int) -> CommandResponse:
        return self.zone_gain(value, output)

    def max_volume(self, value: float, zone: OutputRef | None = None) -> CommandResponse:
        number = _validate_range(int(round(float(value))), lower=0, upper=100, name="MaxVolume")
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("MaxVolume", number))
        return self._with_output_power(zone, lambda: self.command("MaxVolume", number, output_ref(zone)))

    def set_output_max_volume(self, output: OutputRef, value: float) -> CommandResponse:
        return self.max_volume(value, output)

    def output_max_volume_up(self, output: OutputRef, step: float = 1.0) -> CommandResponse:
        current = self.read_output_max_volume(output)
        if current is None:
            raise LookupError(f"No MRAD max-volume status reported for output {output_ref(output)!r}")
        return self.set_output_max_volume(output, min(100.0, float(current) + float(step)))

    def output_max_volume_down(self, output: OutputRef, step: float = 1.0) -> CommandResponse:
        current = self.read_output_max_volume(output)
        if current is None:
            raise LookupError(f"No MRAD max-volume status reported for output {output_ref(output)!r}")
        return self.set_output_max_volume(output, max(0.0, float(current) - float(step)))

    def loudness(self, enabled: bool | str, zone: OutputRef | None = None) -> CommandResponse:
        state = _bool_toggle_state(enabled)
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("Loudness", state))
        return self._with_output_power(zone, lambda: self.command("Loudness", state, output_ref(zone)))

    def set_output_loudness(self, output: OutputRef, enabled: bool | str) -> CommandResponse:
        return self.loudness(enabled, output)

    def mono_downmix(self, enabled: bool | str, zone: OutputRef | None = None) -> CommandResponse:
        state = _bool_toggle_state(enabled)
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("MonoDownmix", state))
        return self._with_output_power(zone, lambda: self.command("MonoDownmix", state, output_ref(zone)))

    def set_output_mono_downmix(self, output: OutputRef, enabled: bool | str) -> CommandResponse:
        return self.mono_downmix(enabled, output)

    def power_on_volume(self, value: float, zone: OutputRef | None = None) -> CommandResponse:
        number = _validate_range(int(round(float(value))), lower=0, upper=100, name="PowerOnVolume")
        if zone is None:
            return self._with_active_output_power(None, lambda: self.command("PowerOnVolume", number))
        return self._with_output_power(zone, lambda: self.command("PowerOnVolume", number, output_ref(zone)))

    def set_output_power_on_volume(self, output: OutputRef, value: float) -> CommandResponse:
        return self.power_on_volume(value, output)

    def set_output_delay(self, output: OutputRef, value_ms: int) -> CommandResponse:
        raise AutonomicError("MRAD delay writes are not available through this client")

    def output_delay_up(self, output: OutputRef | None = None, step_ms: int = 5) -> CommandResponse:
        raise AutonomicError("MRAD delay writes are not available through this client")

    def output_delay_down(self, output: OutputRef | None = None, step_ms: int = 5) -> CommandResponse:
        raise AutonomicError("MRAD delay writes are not available through this client")

    def set_output_name(self, output: OutputRef, name: str) -> CommandResponse:
        return self.command("ZoneName", str(name), output_ref(output))

    def set_zone_name(self, zone: OutputRef, name: str) -> CommandResponse:
        return self.set_output_name(zone, name)

    def set_output_icon(self, output: OutputRef, icon: str) -> CommandResponse:
        return self.command("ZoneIcon", str(icon), output_ref(output))

    def set_zone_icon(self, zone: OutputRef, icon: str) -> CommandResponse:
        return self.set_output_icon(zone, icon)

    def set_source_by_name(self, name: str) -> CommandResponse:
        return self.command("SetSourceByName", str(name))

    def set_source_name(self, source: SourceRef, name: str) -> CommandResponse:
        return self.command("SourceName", str(name), source_ref(source))

    def set_active_source_name(self, name: str) -> CommandResponse:
        return self.command("SourceName", str(name))

    def set_source_icon(self, source: SourceRef, icon: str) -> CommandResponse:
        return self.command("SourceIcon", str(icon), source_ref(source))

    def set_active_source_icon(self, icon: str) -> CommandResponse:
        return self.command("SourceIcon", str(icon))

    def output_gain_up(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("ZoneGainUp")
        return self._with_output_power(output, lambda: self.command("ZoneGainUp", output_ref(output)))

    def output_gain_down(self, output: OutputRef | None = None) -> CommandResponse:
        if output is None:
            return self.command("ZoneGainDown")
        return self._with_output_power(output, lambda: self.command("ZoneGainDown", output_ref(output)))

    def set_output_power(self, output: OutputRef, is_on: bool | str = True) -> CommandResponse:
        """Set the runtime power state for a zone/output.

        This is the MRAD zone `Power` command and is distinct from output
        enablement or configuration-plane changes.
        """

        return self.command("Power", _toggle_state(is_on), output_ref(output))

    def set_output_is_on(self, output: OutputRef, is_on: bool | str = True) -> CommandResponse:
        return self.set_output_power(output, is_on)

    def identify_zone(self, output: OutputRef) -> CommandResponse:
        return self.command("IdentifyZone", output_ref(output))

    def identify_output(self, output: OutputRef) -> CommandResponse:
        return self.identify_zone(output)

    def party_mode(self, state: bool | str = "toggle", zone: OutputRef | None = None) -> CommandResponse:
        if zone is None:
            return self.command("PartyMode", _toggle_state(state))
        return self.command("PartyMode", _toggle_state(state), output_ref(zone))

    def set_party_mode(self, state: bool | str = True, zone: OutputRef | None = None) -> CommandResponse:
        return self.party_mode(state, zone)

    def set_party_mode_include(self, state: bool | str, zone_or_group: OutputRef | AutonomicZoneGroup | None = None) -> CommandResponse:
        return self.command("SetPartyModeInclude", _bool_toggle_state(state), object_ref(zone_or_group))

    def set_source_for_group(self, source: SourceRef) -> CommandResponse:
        return self.command("SetSourceForGroup", source_ref(source))

    def set_zone_group(
        self,
        zone_or_group: OutputRef | AutonomicZoneGroup,
        selected_zones: Iterable[OutputRef] | str,
        target_source: SourceRef | None = None,
    ) -> CommandResponse:
        return self.command(
            "SetZoneGroup",
            object_ref(zone_or_group),
            _comma_refs(selected_zones),
            source_ref(target_source) if target_source is not None else None,
        )

    def set_zone_group_timer(self, target_time: str | int, zone_or_group: OutputRef | AutonomicZoneGroup | None = None) -> CommandResponse:
        return self.command("SetZoneGroupTimer", target_time, object_ref(zone_or_group))

    def set_all_output_power(self, is_on: bool | str = True) -> list[CommandResponse]:
        if _toggle_state(is_on) == "Off":
            return [self.command("AllOff")]
        return [self.set_output_power(output, True) for output in self.list_outputs()]

    def set_all_outputs_on(self, is_on: bool | str = True) -> list[CommandResponse]:
        return self.set_all_output_power(is_on)

    def all_off(self) -> list[CommandResponse]:
        return self.set_all_output_power(False)

    def all_on(self) -> list[CommandResponse]:
        return self.set_all_output_power(True)

    def assign_source_to_output(
        self,
        source: SourceRef,
        output: OutputRef,
        *,
        include_group: bool = False,
    ) -> list[CommandResponse]:
        """Select a source into an amplifier output.

        On current firmware the targeted form is
        `SetSource <source> <include_group> <output>`.
        """

        return [self.set_source(source, output, include_group=include_group)]

    def assign_source_to_outputs(self, source: SourceRef, outputs: Iterable[OutputRef]) -> list[CommandResponse]:
        responses: list[CommandResponse] = []
        for output in outputs:
            responses.extend(self.assign_source_to_output(source, output))
        return responses

    def assign_source_to_all_outputs(self, source: SourceRef) -> list[CommandResponse]:
        return self.assign_source_to_outputs(source, self.list_outputs())

    def assign_output_sources(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse]:
        """Apply an output-to-source route table."""

        responses: list[CommandResponse] = []
        for output, source in assignments.items():
            responses.extend(self.assign_source_to_output(source, output))
        return responses

    def assign_matrix(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse]:
        return self.assign_output_sources(assignments)

    def set_all_output_volume(self, value: int) -> list[CommandResponse]:
        return [self.set_output_volume(output, value) for output in self.list_outputs()]

    def set_all_output_bass(self, value: int) -> list[CommandResponse]:
        return [self.set_output_bass(output, value) for output in self.list_outputs()]

    def set_all_output_treble(self, value: int) -> list[CommandResponse]:
        return [self.set_output_treble(output, value) for output in self.list_outputs()]

    def set_all_output_balance(self, value: int) -> list[CommandResponse]:
        return [self.set_output_balance(output, value) for output in self.list_outputs()]

    def set_all_output_gain(self, value: int) -> list[CommandResponse]:
        return [self.set_output_gain(output, value) for output in self.list_outputs()]

    def set_all_output_max_volume(self, value: int) -> list[CommandResponse]:
        return [self.set_output_max_volume(output, value) for output in self.list_outputs()]

    def set_all_output_loudness(self, enabled: bool | str) -> list[CommandResponse]:
        return [self.set_output_loudness(output, enabled) for output in self.list_outputs()]

    def set_all_output_mono_downmix(self, enabled: bool | str) -> list[CommandResponse]:
        return [self.set_output_mono_downmix(output, enabled) for output in self.list_outputs()]

    def set_all_output_power_on_volume(self, value: int) -> list[CommandResponse]:
        return [self.set_output_power_on_volume(output, value) for output in self.list_outputs()]

    def mute_all_outputs(self, state: bool | str = True) -> list[CommandResponse]:
        if _toggle_state(state) == "Off":
            for output in self.list_outputs():
                self._power_on_for_control(output)
        return [self.command("MuteAll", _toggle_state(state))]

    def _with_output_power(self, output: OutputRef, operation: Callable[[], CommandResponse]) -> CommandResponse:
        self._power_on_for_control(output)
        try:
            return operation()
        except CommandError as exc:
            if not _is_power_off_error(exc):
                raise
            self.set_output_power(output, True)
            return operation()

    def _with_active_output_power(self, output: OutputRef | None, operation: Callable[[], CommandResponse]) -> CommandResponse:
        target = output if output is not None else self._selected_output
        if target is None:
            return operation()
        return self._with_output_power(target, operation)

    def _set_output_range(self, command: str, output: OutputRef, value: int, *, lower: int, upper: int) -> CommandResponse:
        number = _validate_range(value, lower=lower, upper=upper, name=command)
        return self._with_output_power(output, lambda: self.command(command, number, output_ref(output)))

    def _power_on_for_control(self, output: OutputRef) -> None:
        if isinstance(output, AutonomicOutput) and output.is_on is True:
            return
        self.set_output_power(output, True)

    def _with_status(self, outputs: list[AutonomicOutput]) -> list[AutonomicOutput]:
        if not outputs:
            return outputs

        snapshot = self.get_status(timeout=max(self.timeout, 6.0), idle_timeout=0.2)
        return [_apply_status(output, _status_for_output(output, snapshot)) for output in outputs]
