from __future__ import annotations

from collections.abc import Iterable, Mapping

from .base import AutonomicClient, ProtocolConnection
from .mms import MirageMediaServer
from .models import BrowseResponse, CommandResponse, StatusSnapshot
from .protocol import events_to_snapshot, format_command

MRAD_PORT = 5006
OutputRef = str | int
SourceRef = str | int


class MirageAudioSystem(AutonomicClient):
    """Client for MRAD/MAS zone, source, and zone-group control."""

    def __init__(
        self,
        host: str,
        port: int = MRAD_PORT,
        *,
        timeout: float = 3.0,
        connection: ProtocolConnection | None = None,
        mms_client: MirageMediaServer | None = None,
        single_socket: bool = False,
    ):
        super().__init__(host, port, timeout=timeout, connection=connection)
        self._mms_client = mms_client
        self.single_socket = single_socket

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
        if protocol_mode_command and not self.single_socket:
            self.command("*")
        self.command("SetClientType", client_type)
        self.command("SetEncoding", encoding)
        self.command("SetXmlMode", "Lists" if xml_lists else "None")
        if host_hint:
            self.command("SetHost", host_hint)
        self.subscribe_events(subscribe)

    def command(self, name: str, *args: object, **request_options: object) -> CommandResponse:
        command = format_command(name, *args)
        if self.single_socket:
            command = "MRAD." + command
            if self._mms_client is None:
                return self.request(command, **request_options)
            return self._mms_client.request(command, **request_options)
        return self.request(command, **request_options)

    def subscribe_events(self, events: bool | Iterable[str] = "Smart") -> CommandResponse:
        if isinstance(events, bool):
            return self.command("SubscribeEvents", events)
        if isinstance(events, str):
            return self.command("SubscribeEvents", events)
        return self.command("SubscribeEvents", ",".join(events))

    def get_status(self, *, timeout: float | None = None, idle_timeout: float = 0.15) -> StatusSnapshot:
        response = self.command("GetStatus", timeout=timeout, idle_timeout=idle_timeout, collect_events=True)
        return StatusSnapshot(events=response.events, by_source=events_to_snapshot(response.events), raw_lines=response.lines)

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

    def list_outputs(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        """Return all amplifier outputs.

        Autonomic's MRAD docs call physical outputs "zones"; this alias exposes
        the matrix vocabulary used by many control systems.
        """

        return self.browse_all_zones(start, count)

    def list_available_outputs(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse_zones(start, count)

    def list_sources(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse_all_sources(start, count)

    def list_available_sources(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse_sources(start, count)

    def _browse(self, command: str, start: int | None = None, count: int | None = None) -> BrowseResponse:
        response = self.command(command, start, count)
        if response.payload is None:
            raise ValueError(f"{command} did not return a browse response")
        return response.payload

    def set_zone(self, zone: OutputRef) -> CommandResponse:
        return self.command("SetZone", zone)

    def set_output(self, output: OutputRef) -> CommandResponse:
        return self.set_zone(output)

    def set_source(
        self,
        source_guid_or_name: SourceRef,
        output: OutputRef | None = None,
        *,
        include_group: bool = False,
    ) -> CommandResponse:
        if output is None:
            return self.command("SetSource", source_guid_or_name)
        return self.command("SetSource", source_guid_or_name, include_group, output)

    def volume(self, value: int, zone: OutputRef | None = None) -> CommandResponse:
        return self.command("Volume", value, zone)

    def volume_up(self, zone: OutputRef | None = None) -> CommandResponse:
        return self.command("VolumeUp", zone)

    def volume_down(self, zone: OutputRef | None = None) -> CommandResponse:
        return self.command("VolumeDown", zone)

    def mute(self, state: bool | str = "toggle", zone: OutputRef | None = None) -> CommandResponse:
        return self.command("Mute", state, zone)

    def set_output_volume(self, output: OutputRef, value: int) -> CommandResponse:
        return self.volume(value, output)

    def output_volume_up(self, output: OutputRef) -> CommandResponse:
        return self.volume_up(output)

    def output_volume_down(self, output: OutputRef) -> CommandResponse:
        return self.volume_down(output)

    def set_output_mute(self, output: OutputRef, state: bool | str = "toggle") -> CommandResponse:
        return self.mute(state, output)

    def set_output_enabled(self, output: OutputRef, enabled: bool = True) -> CommandResponse:
        """Power an output on or off through MRAD."""

        return self.command("Power", "On" if enabled else "Off", output)

    def enable_output(self, output: OutputRef) -> CommandResponse:
        return self.set_output_enabled(output, True)

    def disable_output(self, output: OutputRef) -> CommandResponse:
        return self.set_output_enabled(output, False)

    def assign_source_to_output(
        self,
        source: SourceRef,
        output: OutputRef,
        *,
        include_group: bool = False,
    ) -> list[CommandResponse]:
        """Select an MMS source into an amplifier output.

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
        return self.assign_source_to_outputs(source, _refs_from_browse(self.list_outputs()))

    def assign_output_sources(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse]:
        """Apply an output-to-source route table."""

        responses: list[CommandResponse] = []
        for output, source in assignments.items():
            responses.extend(self.assign_source_to_output(source, output))
        return responses

    def assign_matrix(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse]:
        return self.assign_output_sources(assignments)

    def set_all_output_volume(self, value: int) -> list[CommandResponse]:
        return [self.set_output_volume(output, value) for output in _refs_from_browse(self.list_outputs())]

    def mute_all_outputs(self, state: bool | str = True) -> list[CommandResponse]:
        return [self.command("MuteAll", _toggle_state(state))]

    def set_all_outputs_enabled(self, enabled: bool = True) -> list[CommandResponse]:
        return [self.set_output_enabled(output, enabled) for output in _refs_from_browse(self.list_outputs())]

    def enable_all_outputs(self) -> list[CommandResponse]:
        return self.set_all_outputs_enabled(True)

    def disable_all_outputs(self) -> CommandResponse:
        return self.command("AllOff")

    def party_mode(self, state: bool | str = "Toggle", output: OutputRef | None = None) -> CommandResponse:
        """Set party-host mode on the active or specified output."""

        return self.command("PartyMode", _toggle_state(state), output)

    def set_zone_group(
        self,
        group_or_first_zone_guid: str,
        member_zone_guids: Iterable[str],
        source_guid: str | None = None,
    ) -> CommandResponse:
        members = ",".join(member_zone_guids)
        return self.command("SetZoneGroup", group_or_first_zone_guid, members, source_guid)


def _refs_from_browse(response: BrowseResponse) -> list[str]:
    refs: list[str] = []
    for item in response.items:
        ref = item.guid or item.id or item.name
        if ref is None:
            raise ValueError(f"Browse item has no guid, id, or name: {item}")
        refs.append(ref)
    return refs


def _toggle_state(state: bool | str) -> str:
    if isinstance(state, bool):
        return "On" if state else "Off"
    normalized = state.lower()
    if normalized in {"true", "on", "1", "yes"}:
        return "On"
    if normalized in {"false", "off", "0", "no"}:
        return "Off"
    if normalized == "toggle":
        return "Toggle"
    raise ValueError("state must be On, Off, Toggle, true, false, or bool")
