# Base connection wrapper for line-oriented Autonomic protocol clients.
from __future__ import annotations

import time
from types import TracebackType
from typing import Callable, Protocol

from .connection import LineConnection
from .exceptions import AutonomicTimeoutError, CommandError
from .protocol_types import CommandResponse, Event
from .protocol_types import BrowseResponse
from .protocol import (
    is_legacy_list_end,
    is_legacy_list_start,
    is_banner_line,
    is_error_response,
    is_xml_response,
    parse_event,
    parse_legacy_list,
    parse_xml_list,
)


class ProtocolConnection(Protocol):
    connected: bool

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def send_line(self, command: str) -> None: ...

    def read_line(self, timeout: float | None = None) -> str: ...

    def set_response_delimiter(self, delimiter: bytes) -> None: ...


EventCallback = Callable[[Event], None]


class ProtocolClient:
    """Base synchronous command client for Autonomic line-protocol sockets."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = 5.0,
        connection: ProtocolConnection | None = None,
        on_event: EventCallback | None = None,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.on_event = on_event
        self._connection = connection or LineConnection(host, port, timeout=timeout)

    @property
    def connected(self) -> bool:
        return self._connection.connected

    def connect(self) -> None:
        self._connection.connect()

    def close(self) -> None:
        self._connection.close()

    def send(self, command: str) -> None:
        if not self.connected:
            self.connect()
        self._connection.send_line(command)

    def request(
        self,
        command: str,
        *,
        timeout: float | None = None,
        idle_timeout: float = 0.15,
        collect_events: bool = False,
        collect_until_idle: bool = False,
        include_banners: bool = False,
        expect_response: bool = True,
    ) -> CommandResponse:
        self.send(command)
        if not expect_response:
            return CommandResponse(command=command, lines=[])
        return self.read_response(
            command,
            timeout=timeout,
            idle_timeout=idle_timeout,
            collect_events=collect_events,
            collect_until_idle=collect_until_idle,
            include_banners=include_banners,
        )

    def read_response(
        self,
        command: str = "",
        *,
        timeout: float | None = None,
        idle_timeout: float = 0.15,
        collect_events: bool = False,
        collect_until_idle: bool = False,
        include_banners: bool = False,
    ) -> CommandResponse:
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        events: list[Event] = []

        while True:
            remaining = max(0.001, deadline - time.monotonic())
            try:
                line = self._connection.read_line(timeout=remaining).rstrip()
            except AutonomicTimeoutError:
                if collect_events and events:
                    return CommandResponse(command=command, lines=lines, events=events)
                raise

            event = parse_event(line)
            if event is not None:
                events.append(event)
                if self.on_event:
                    self.on_event(event)
                if collect_events:
                    continue
                continue

            if not line:
                continue

            if is_banner_line(line) and not include_banners:
                continue

            if is_error_response(line):
                raise CommandError(line)

            lines.append(line)

            if collect_events and not collect_until_idle:
                events.extend(self._read_idle_events(idle_timeout))
                return CommandResponse(command=command, lines=lines, events=events, payload=_parse_payload(lines))

            if is_legacy_list_start(line):
                while True:
                    next_line = self._connection.read_line(timeout=max(0.001, deadline - time.monotonic()))
                    lines.append(next_line)
                    if is_legacy_list_end(next_line):
                        tail_lines, tail_events = self._read_idle_tail(idle_timeout, include_banners=include_banners)
                        lines.extend(tail_lines)
                        events.extend(tail_events)
                        return CommandResponse(command=command, lines=lines, events=events, payload=parse_legacy_list(lines))

            if is_xml_response(line):
                tail_lines, tail_events = self._read_idle_tail(idle_timeout, include_banners=include_banners)
                lines.extend(tail_lines)
                events.extend(tail_events)

            elif collect_until_idle:
                tail_lines, tail_events = self._read_idle_tail(idle_timeout, include_banners=include_banners)
                lines.extend(tail_lines)
                events.extend(tail_events)

            return CommandResponse(command=command, lines=lines, events=events, payload=_parse_payload(lines))

    def _read_idle_events(self, idle_timeout: float) -> list[Event]:
        events: list[Event] = []
        while True:
            try:
                line = self._connection.read_line(timeout=idle_timeout).rstrip()
            except AutonomicTimeoutError:
                return events
            event = parse_event(line)
            if event is None:
                return events
            events.append(event)
            if self.on_event:
                self.on_event(event)

    def _read_idle_tail(self, idle_timeout: float, *, include_banners: bool = False) -> tuple[list[str], list[Event]]:
        lines: list[str] = []
        events: list[Event] = []
        while True:
            try:
                line = self._connection.read_line(timeout=idle_timeout).rstrip()
            except AutonomicTimeoutError:
                return lines, events

            event = parse_event(line)
            if event is not None:
                events.append(event)
                if self.on_event:
                    self.on_event(event)
                continue

            if not line or (is_banner_line(line) and not include_banners):
                continue
            if is_error_response(line):
                raise CommandError(line)
            lines.append(line)

    def __enter__(self) -> "ProtocolClient":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

def _parse_payload(lines: list[str]) -> BrowseResponse | None:
    if not lines:
        return None
    first = lines[0].strip()
    if is_xml_response(first):
        return parse_xml_list(first)
    if is_legacy_list_start(first):
        return parse_legacy_list(lines)
    return None
