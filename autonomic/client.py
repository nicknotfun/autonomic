from __future__ import annotations

import socket
from collections.abc import Iterable, Mapping
from types import TracebackType
from typing import Literal

from .amplifier import AMPLIFIER_DIAGNOSTIC_PORT, MirageAmplifier
from .exceptions import AutonomicError
from .mms import MMS_PORT, MirageMediaServer
from .models import AutonomicOutput, AutonomicOutputGroup, AutonomicSource, CommandResponse, output_ref, source_id
from .mrad import MRAD_PORT, MirageAudioSystem, OutputRef, SourceRef

ClientMode = Literal["auto", "mrad", "mas", "amplifier", "amp", "direct"]
DetectedMode = Literal["mrad", "amplifier"]
DEFAULT_SOURCE_ALIASES: dict[str, str] = {
    "000027fb-f8a9-f6be-a465-3d0fbee12977": "Alpha",
    "000027fc-f8a9-f6be-a465-3d0fbee12977": "Beta",
    "0000008a-f8a9-f6be-a465-3d0fbee12977": "Gamma",
    "0000008b-f8a9-f6be-a465-3d0fbee12977": "Delta",
}


class AutonomicClient:
    """Convenience client for Autonomic systems with auto-detected control mode."""

    def __init__(
        self,
        host: str,
        *,
        media_port: int = MMS_PORT,
        mrad_port: int = MRAD_PORT,
        amplifier_port: int = AMPLIFIER_DIAGNOSTIC_PORT,
        timeout: float = 5.0,
        mode: ClientMode = "auto",
        amplifier_output_count: int = 8,
        amplifier_source_count: int = 8,
        auto_initialize: bool = True,
        host_hint: str | None = None,
        instance: str | None = None,
        source_aliases: Mapping[str, str] | None = DEFAULT_SOURCE_ALIASES,
    ):
        self.host = host
        self.mode = mode
        self.source_aliases = _normalize_source_aliases(source_aliases or {})
        normalized_mode = _normalize_mode(mode)
        self._detected_mode: DetectedMode | None = None if normalized_mode == "auto" else normalized_mode
        self._selected_output: OutputRef | None = None
        self._initialized = False
        self.media = MirageMediaServer(host, media_port, timeout=timeout)
        self.audio = MirageAudioSystem(host, mrad_port, timeout=timeout)
        self.amplifier = MirageAmplifier(
            host,
            amplifier_port,
            timeout=timeout,
            output_count=amplifier_output_count,
            source_count=amplifier_source_count,
        )
        if auto_initialize:
            self.initialize(host_hint=host_hint, instance=instance)

    def connect(self) -> None:
        if self._backend() == "mrad":
            self.media.connect()
            self.audio.connect()

    def close(self) -> None:
        self.media.close()
        self.audio.close()

    def initialize(self, *, host_hint: str | None = None, instance: str | None = None) -> None:
        if self._backend() == "mrad":
            self.media.initialize(host_hint=host_hint, instance=instance)
            self.audio.initialize(host_hint=host_hint)
        else:
            self.amplifier.get_device_id()
        self._initialized = True

    def list_zones(self, *, include_disabled: bool = False, include_status: bool = True) -> list[AutonomicOutput]:
        return self.list_outputs(include_disabled=include_disabled, include_status=include_status)

    def list_outputs(self, *, include_disabled: bool = False, include_status: bool = True) -> list[AutonomicOutput]:
        if self._backend() == "mrad":
            outputs = self.audio.list_outputs(include_disabled=include_disabled)
        else:
            outputs = self.amplifier.list_outputs(include_disabled=include_disabled, include_status=include_status)
        return [self._with_output_aliases(output).bind(self) for output in outputs]

    def all_outputs(self, *, include_disabled: bool = False, include_status: bool = True) -> AutonomicOutputGroup:
        return AutonomicOutputGroup(outputs=self.list_outputs(include_disabled=include_disabled, include_status=include_status)).bind(self)

    def list_sources(self, *, include_disabled: bool = False) -> list[AutonomicSource]:
        if self._backend() == "mrad":
            sources = self.audio.list_sources(include_disabled=include_disabled)
        else:
            sources = self.amplifier.list_sources(include_disabled=include_disabled)
        return [self._with_source_alias(source).bind(self) for source in sources]

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

    def select_zone(self, zone: OutputRef) -> CommandResponse:
        return self.select_output(zone)

    def select_output(self, output: OutputRef) -> CommandResponse | None:
        self._selected_output = output
        if self._backend() == "mrad":
            return self.audio.set_output(output)
        return None

    def select_source(
        self,
        source: SourceRef,
        output: OutputRef | None = None,
        *,
        include_group: bool = False,
    ) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.set_source(self._source_alias_ref(source), output, include_group=include_group)
        target = output if output is not None else self._selected_output
        if target is None:
            raise ValueError("select_source requires output=... or a prior select_output() in amplifier mode")
        return self.amplifier.assign_source_to_output(source_id(source), target)

    def assign_source_to_output(
        self,
        source: SourceRef,
        output: OutputRef,
        *,
        include_group: bool = False,
    ) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self.audio.assign_source_to_output(self._source_alias_ref(source), output, include_group=include_group)
        return [self.amplifier.assign_source_to_output(source_id(source), output)]

    def assign_source_to_outputs(self, source: SourceRef, outputs: Iterable[OutputRef]) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self.audio.assign_source_to_outputs(self._source_alias_ref(source), outputs)
        return self.amplifier.assign_source_to_outputs(source_id(source), outputs)

    def assign_source_to_all_outputs(self, source: SourceRef) -> list[CommandResponse] | str:
        if self._backend() == "mrad":
            return self.audio.assign_source_to_all_outputs(self._source_alias_ref(source))
        return self.amplifier.assign_source_to_all_outputs(source_id(source))

    def assign_output_sources(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self.audio.assign_output_sources(
                {output: self._source_alias_ref(source) for output, source in assignments.items()}
            )
        return self.amplifier.assign_output_sources({output: source_id(source) for output, source in assignments.items()})

    def assign_matrix(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse] | list[str]:
        return self.assign_output_sources(assignments)

    def volume(self, value: int, zone: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.volume(value, zone)
        output = self._resolve_output(zone)
        return self.amplifier.set_output_volume(output, value)

    def set_output_volume(self, output: OutputRef, value: int) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.set_output_volume(output, value)
        return self.amplifier.set_output_volume(output, value)

    def set_all_output_volume(self, value: int) -> list[CommandResponse] | str:
        if self._backend() == "mrad":
            return self.audio.set_all_output_volume(value)
        return self.amplifier.set_output_volume("all", value)

    def volume_up(self, zone: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.volume_up(zone)
        return self.amplifier.output_volume_up(self._resolve_output(zone))

    def volume_down(self, zone: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.volume_down(zone)
        return self.amplifier.output_volume_down(self._resolve_output(zone))

    def mute(self, state: bool | str = "toggle", zone: OutputRef | None = None) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.mute(state, zone)
        return self.amplifier.set_output_mute(self._resolve_output(zone), state)

    def set_output_mute(self, output: OutputRef, state: bool | str = "toggle") -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.set_output_mute(output, state)
        return self.amplifier.set_output_mute(output, state)

    def mute_all_outputs(self, state: bool | str = True) -> list[CommandResponse] | str:
        if self._backend() == "mrad":
            return self.audio.mute_all_outputs(state)
        return self.amplifier.mute_all_outputs(state)

    def set_output_power(self, output: OutputRef, is_on: bool | str = True) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.set_output_power(output, is_on)
        return self.amplifier.set_output_power(output, is_on)

    def set_output_is_on(self, output: OutputRef, is_on: bool | str = True) -> CommandResponse | str:
        return self.set_output_power(output, is_on)

    def play(self) -> CommandResponse:
        if self._backend() != "mrad":
            raise AutonomicError("Media playback controls are not available in direct amplifier mode")
        return self.media.play()

    def pause(self) -> CommandResponse:
        if self._backend() != "mrad":
            raise AutonomicError("Media playback controls are not available in direct amplifier mode")
        return self.media.pause()

    def stop(self) -> CommandResponse:
        if self._backend() != "mrad":
            raise AutonomicError("Media playback controls are not available in direct amplifier mode")
        return self.media.stop()

    def skip_next(self) -> CommandResponse:
        if self._backend() != "mrad":
            raise AutonomicError("Media playback controls are not available in direct amplifier mode")
        return self.media.skip_next()

    def skip_previous(self) -> CommandResponse:
        if self._backend() != "mrad":
            raise AutonomicError("Media playback controls are not available in direct amplifier mode")
        return self.media.skip_previous()

    def detect_mode(self) -> DetectedMode:
        return self._backend()

    def _backend(self) -> DetectedMode:
        if self._detected_mode is not None:
            return self._detected_mode

        if _can_connect(self.host, self.audio.port, self.audio.timeout):
            self._detected_mode = "mrad"
            return self._detected_mode

        if _can_connect(self.host, self.amplifier.port, self.amplifier.timeout):
            self._detected_mode = "amplifier"
            return self._detected_mode

        raise AutonomicError(f"No supported Autonomic control port found on {self.host}")

    def _resolve_output(self, output: OutputRef | None) -> OutputRef:
        resolved = output if output is not None else self._selected_output
        if resolved is None:
            raise ValueError("operation requires an output or prior select_output() in amplifier mode")
        return output_ref(resolved)

    def _with_source_alias(self, source: AutonomicSource) -> AutonomicSource:
        alias = self._source_alias_for_guid(source.guid)
        if alias is None:
            return source
        return source.model_copy(update={"name": alias})

    def _with_output_aliases(self, output: AutonomicOutput) -> AutonomicOutput:
        alias = self._source_alias_for_guid(output.source_guid)
        if alias is None:
            return output
        return output.model_copy(update={"source_name": alias})

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


def _can_connect(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _normalize_mode(mode: str) -> Literal["auto", "mrad", "amplifier"]:
    normalized = str(mode).strip().lower()
    if normalized in {"auto", ""}:
        return "auto"
    if normalized in {"mrad", "mas"}:
        return "mrad"
    if normalized in {"amplifier", "amp", "direct"}:
        return "amplifier"
    raise ValueError("mode must be auto, mrad/mas, or amplifier/amp/direct")


def _name_matches(actual: str | None, expected: str) -> bool:
    return actual is not None and actual.strip().lower() == expected.strip().lower()


def _normalize_source_aliases(source_aliases: Mapping[str, str]) -> dict[str, str]:
    return {str(guid).strip().lower(): str(name).strip() for guid, name in source_aliases.items() if str(guid).strip()}
