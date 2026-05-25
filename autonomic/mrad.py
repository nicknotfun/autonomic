from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from .base import ProtocolClient, ProtocolConnection
from .exceptions import CommandError
from .mms import MirageMediaServer
from .models import (
    AutonomicOutput,
    AutonomicSource,
    BrowseResponse,
    CommandResponse,
    StatusSnapshot,
    omit_disabled,
    output_ref,
    source_ref,
)
from .protocol import events_to_snapshot, format_command

MRAD_PORT = 5006
OutputRef = str | int | AutonomicOutput
SourceRef = str | int | AutonomicSource


class MirageAudioSystem(ProtocolClient):
    """Client for MRAD/MAS zone, source, and zone-group control."""

    def __init__(
        self,
        host: str,
        port: int = MRAD_PORT,
        *,
        timeout: float = 5.0,
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

    def list_outputs(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        include_disabled: bool = False,
    ) -> list[AutonomicOutput]:
        """Return all amplifier outputs.

        Autonomic's MRAD docs call physical outputs "zones"; this alias exposes
        the matrix vocabulary used by many control systems.
        """

        outputs = [AutonomicOutput.from_browse_item(item, client=self) for item in self.browse_all_zones(start, count).items]
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

    def set_zone(self, zone: OutputRef) -> CommandResponse:
        return self.command("SetZone", output_ref(zone))

    def set_output(self, output: OutputRef) -> CommandResponse:
        return self.set_zone(output)

    def set_source(
        self,
        source_guid_or_name: SourceRef,
        output: OutputRef | None = None,
        *,
        include_group: bool = False,
    ) -> CommandResponse:
        resolved_source = source_ref(source_guid_or_name)
        if output is None:
            return self.command("SetSource", resolved_source)
        return self._with_output_power(output, lambda: self.command("SetSource", resolved_source, include_group, output_ref(output)))

    def volume(self, value: int, zone: OutputRef | None = None) -> CommandResponse:
        if zone is None:
            return self.command("Volume", value)
        return self._with_output_power(zone, lambda: self.command("Volume", value, output_ref(zone)))

    def volume_up(self, zone: OutputRef | None = None) -> CommandResponse:
        if zone is None:
            return self.command("VolumeUp")
        return self._with_output_power(zone, lambda: self.command("VolumeUp", output_ref(zone)))

    def volume_down(self, zone: OutputRef | None = None) -> CommandResponse:
        if zone is None:
            return self.command("VolumeDown")
        return self._with_output_power(zone, lambda: self.command("VolumeDown", output_ref(zone)))

    def mute(self, state: bool | str = "toggle", zone: OutputRef | None = None) -> CommandResponse:
        if zone is None:
            return self.command("Mute", state)
        return self._with_output_power(zone, lambda: self.command("Mute", state, output_ref(zone)))

    def set_output_volume(self, output: OutputRef, value: int) -> CommandResponse:
        return self.volume(value, output)

    def output_volume_up(self, output: OutputRef) -> CommandResponse:
        return self.volume_up(output)

    def output_volume_down(self, output: OutputRef) -> CommandResponse:
        return self.volume_down(output)

    def set_output_mute(self, output: OutputRef, state: bool | str = "toggle") -> CommandResponse:
        return self.mute(state, output)

    def set_output_power(self, output: OutputRef, is_on: bool | str = True) -> CommandResponse:
        """Set the runtime power state for a zone/output.

        This is the MRAD zone `Power` command and is distinct from output
        enablement or configuration-plane changes.
        """

        return self.command("Power", _toggle_state(is_on), output_ref(output))

    def set_output_is_on(self, output: OutputRef, is_on: bool | str = True) -> CommandResponse:
        return self.set_output_power(output, is_on)

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

    def _power_on_for_control(self, output: OutputRef) -> None:
        if isinstance(output, AutonomicOutput) and output.is_on is True:
            return
        self.set_output_power(output, True)


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


def _is_power_off_error(error: CommandError) -> bool:
    return " is off" in str(error).lower()
