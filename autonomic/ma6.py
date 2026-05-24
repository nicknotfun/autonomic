from __future__ import annotations

import socket
from collections.abc import Iterable, Mapping
from types import TracebackType

from .amplifier import AMPLIFIER_DIAGNOSTIC_PORT, MirageAmplifier
from .exceptions import AutonomicError
from .mms import MMS_PORT, MirageMediaServer
from .models import BrowseResponse, CommandResponse
from .mrad import MRAD_PORT, MirageAudioSystem, OutputRef, SourceRef


class MA6Client:
    """Convenience client for MA6-style systems exposing MMS and MRAD control."""

    def __init__(
        self,
        host: str,
        *,
        media_port: int = MMS_PORT,
        mrad_port: int = MRAD_PORT,
        amplifier_port: int = AMPLIFIER_DIAGNOSTIC_PORT,
        timeout: float = 3.0,
        mode: str = "auto",
        amplifier_output_count: int = 8,
        amplifier_source_count: int = 8,
    ):
        self.host = host
        self.mode = mode
        self._detected_mode: str | None = None if mode == "auto" else mode
        self._selected_output: OutputRef | None = None
        self.media = MirageMediaServer(host, media_port, timeout=timeout)
        self.audio = MirageAudioSystem(host, mrad_port, timeout=timeout)
        self.amplifier = MirageAmplifier(
            host,
            amplifier_port,
            timeout=timeout,
            output_count=amplifier_output_count,
            source_count=amplifier_source_count,
        )

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

    def list_zones(self) -> BrowseResponse:
        return self.list_outputs()

    def list_outputs(self) -> BrowseResponse:
        if self._backend() == "mrad":
            return self.audio.list_outputs()
        return self.amplifier.list_outputs()

    def list_sources(self) -> BrowseResponse:
        if self._backend() == "mrad":
            return self.audio.list_sources()
        return self.amplifier.list_sources()

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
            return self.audio.set_source(source, output, include_group=include_group)
        target = output if output is not None else self._selected_output
        if target is None:
            raise ValueError("select_source requires output=... or a prior select_output() in amplifier mode")
        return self.amplifier.assign_source_to_output(int(source), target)

    def assign_source_to_output(
        self,
        source: SourceRef,
        output: OutputRef,
        *,
        include_group: bool = False,
    ) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self.audio.assign_source_to_output(source, output, include_group=include_group)
        return [self.amplifier.assign_source_to_output(int(source), output)]

    def assign_source_to_outputs(self, source: SourceRef, outputs: Iterable[OutputRef]) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self.audio.assign_source_to_outputs(source, outputs)
        return self.amplifier.assign_source_to_outputs(int(source), outputs)

    def assign_source_to_all_outputs(self, source: SourceRef) -> list[CommandResponse] | str:
        if self._backend() == "mrad":
            return self.audio.assign_source_to_all_outputs(source)
        return self.amplifier.assign_source_to_all_outputs(int(source))

    def assign_output_sources(self, assignments: Mapping[OutputRef, SourceRef]) -> list[CommandResponse] | list[str]:
        if self._backend() == "mrad":
            return self.audio.assign_output_sources(assignments)
        return self.amplifier.assign_output_sources({output: int(source) for output, source in assignments.items()})

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

    def set_output_enabled(self, output: OutputRef, enabled: bool = True) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.set_output_enabled(output, enabled)
        return self.amplifier.set_output_enabled(output, enabled)

    def enable_output(self, output: OutputRef) -> CommandResponse | str:
        return self.set_output_enabled(output, True)

    def disable_output(self, output: OutputRef) -> CommandResponse | str:
        return self.set_output_enabled(output, False)

    def enable_all_outputs(self) -> list[CommandResponse] | str:
        if self._backend() == "mrad":
            return self.audio.enable_all_outputs()
        return self.amplifier.enable_all_outputs()

    def disable_all_outputs(self) -> CommandResponse | str:
        if self._backend() == "mrad":
            return self.audio.disable_all_outputs()
        return self.amplifier.disable_all_outputs()

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

    def detect_mode(self) -> str:
        return self._backend()

    def _backend(self) -> str:
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
        return resolved

    def __enter__(self) -> "MA6Client":
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
