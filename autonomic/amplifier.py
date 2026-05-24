from __future__ import annotations

import re
import socket
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .exceptions import ProtocolError
from .models import BrowseItem, BrowseResponse
from .protocol import CRLF

AMPLIFIER_DIAGNOSTIC_PORT = 17037
AMPLIFIER_HTTP_PORT = 80
DEVICE_ID_COMMAND = "2FFF"
ALL_OUTPUTS = "FF"
_DEVICE_ID_RE = re.compile(r"AFFF(?P<id>[0-9A-Fa-f]{4})")
_DECODE_MATRIX_SOURCE = {
    0: 4,
    1: 5,
    2: 6,
    3: 3,
    4: 7,
    5: 0,
    6: 1,
    7: 2,
    8: 8,
    9: 9,
    10: 10,
    11: 11,
}
_ENCODE_MATRIX_SOURCE = {value: key for key, value in _DECODE_MATRIX_SOURCE.items()}
_SOURCE_TO_DATA = {
    1: "05",
    2: "06",
    3: "07",
    4: "03",
    5: "00",
    6: "01",
    7: "02",
    8: "04",
}


@dataclass(frozen=True)
class AmplifierResponse:
    command: int
    output: int | None
    raw_output: int
    data: list[int]
    raw: str


class MirageAmplifierDiagnostics:
    """Small guarded helper for documented direct Mirage amplifier diagnostics.

    The normal MA6/MAS control surface is MRAD. This class only covers the
    support-note diagnostic channel used to read an amplifier ID and perform a
    factory reset. Factory reset requires explicit confirmation.
    """

    def __init__(self, host: str, port: int = AMPLIFIER_DIAGNOSTIC_PORT, *, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    @staticmethod
    def parse_device_id(response: str) -> str:
        match = _DEVICE_ID_RE.search(response)
        if not match:
            raise ProtocolError(f"Could not find amplifier ID in response: {response!r}")
        return match.group("id").upper()

    @staticmethod
    def build_factory_reset_command(device_id: str) -> str:
        normalized = device_id.upper()
        if not re.fullmatch(r"[0-9A-F]{4}", normalized):
            raise ValueError("device_id must be exactly four hexadecimal characters")
        return f"42FF{normalized}0355AA"

    def get_device_id(self) -> str:
        response = self.send_ascii(DEVICE_ID_COMMAND)
        return self.parse_device_id(response)

    def factory_reset(self, *, confirm: bool = False, device_id: str | None = None) -> str:
        if not confirm:
            raise ValueError("factory_reset requires confirm=True")
        resolved_id = device_id or self.get_device_id()
        command = self.build_factory_reset_command(resolved_id)
        return self.send_ascii(command)

    def send_ascii(self, command: str) -> str:
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall((command.strip() + CRLF).encode("ascii"))
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
            return b"".join(chunks).decode("ascii", errors="replace").strip()


class MirageAmplifier(MirageAmplifierDiagnostics):
    """Direct Mirage amplifier output/source control over TCP port 17037.

    MAS/MRAD is the preferred API for most integrations. This class covers the
    amplifier ASCII command format for installations that need direct output
    controls: standby/output enable, mute, volume, and source routing.
    """

    def __init__(
        self,
        host: str,
        port: int = AMPLIFIER_DIAGNOSTIC_PORT,
        *,
        timeout: float = 3.0,
        output_count: int = 8,
        source_count: int = 8,
        transport: str = "tcp",
        http_path: str = "/poll.cgi",
        source_base: int = 1,
    ):
        super().__init__(host, port, timeout=timeout)
        self.output_count = output_count
        self.source_count = source_count
        self.transport = transport.lower()
        self.http_path = http_path if http_path.startswith("/") else f"/{http_path}"
        self.source_base = source_base

    @staticmethod
    def build_data_command(command: int | str, output: int | str, data: int | str | Iterable[int | str] = 0) -> str:
        return f"{_byte(command)}{_output_address(output)}{_data_bytes(data)}"

    @staticmethod
    def source_data(source: int) -> str:
        """Return the one-based Mirage protocol source byte for sources S1-S8."""

        try:
            return _SOURCE_TO_DATA[int(source)]
        except ValueError as exc:
            raise ValueError("source must be an integer") from exc
        except KeyError as exc:
            raise ValueError("source must be an integer from 1 through 8") from exc

    @staticmethod
    def encode_matrix_source(source: int) -> str:
        return _byte(_ENCODE_MATRIX_SOURCE.get(int(source), int(source)))

    @staticmethod
    def decode_matrix_source(source_data: int | str) -> int:
        return _DECODE_MATRIX_SOURCE.get(int(source_data), int(source_data))

    @staticmethod
    def encode_output(output: int | str) -> str:
        return _output_address(output)

    @staticmethod
    def decode_output(output_data: int | str) -> int | None:
        return decode_output_address(int(output_data))

    @staticmethod
    def parse_response(text: str) -> list[AmplifierResponse]:
        rows: list[AmplifierResponse] = []
        for raw in text.splitlines():
            line = raw.strip()
            if len(line) < 4 or len(line) % 2:
                continue
            try:
                command = int(line[0:2], 16)
                raw_output = int(line[2:4], 16)
                output = decode_output_address(raw_output)
                data = [int(line[index : index + 2], 16) for index in range(4, len(line), 2)]
            except ValueError:
                continue
            rows.append(
                AmplifierResponse(
                    command=command,
                    output=output,
                    raw_output=raw_output,
                    data=data,
                    raw=line,
                )
            )
        return rows

    @staticmethod
    def expected_response_keys(commands: Iterable[str]) -> set[tuple[int, int | None]]:
        keys: set[tuple[int, int | None]] = set()
        for command in commands:
            line = command.upper().rstrip("\r\n")
            if len(line) < 4:
                continue
            try:
                keys.add((int(line[0:2], 16), decode_output_address(int(line[2:4], 16))))
            except ValueError:
                continue
        return keys

    def send_ascii(self, command: str) -> str:
        return self.send_commands([command]).strip()

    def send_commands(self, commands: Iterable[str], *, timeout: float | None = None) -> str:
        normalized = [command.upper().rstrip("\r\n") for command in commands]
        if not normalized:
            return ""
        timeout = self.timeout if timeout is None else timeout
        if self.transport == "http":
            return self._send_http_commands(normalized, timeout=timeout)
        if self.transport == "tcp":
            return self._send_tcp_commands(normalized, timeout=timeout)
        raise ValueError("transport must be 'tcp' or 'http'")

    def poll(self, commands: str | Iterable[str], *, timeout: float | None = None) -> list[AmplifierResponse]:
        if isinstance(commands, str):
            commands = [commands]
        return self.parse_response(self.send_commands(commands, timeout=timeout))

    def _send_tcp_commands(self, commands: list[str], *, timeout: float) -> str:
        body = "".join(f"{command}{CRLF}" for command in commands).encode("ascii")
        chunks: list[bytes] = []
        expected_keys = self.expected_response_keys(commands)
        with socket.create_connection((self.host, self.port), timeout=timeout) as sock:
            sock.sendall(body)
            sock.settimeout(min(0.75, timeout))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if expected_keys:
                    text = b"".join(chunks).decode("ascii", errors="ignore")
                    seen = {(row.command, row.output) for row in self.parse_response(text)}
                    if expected_keys.issubset(seen):
                        break
                elif b"\n" in chunk:
                    break
        return b"".join(chunks).decode("ascii", errors="replace")

    def _send_http_commands(self, commands: list[str], *, timeout: float) -> str:
        body = "".join(f"{command}{CRLF}" for command in commands).encode("ascii")
        query = urllib.parse.urlencode({"id": f"python-sdk-{uuid.uuid4()}"})
        netloc = f"{self.host}:{self.port}" if self.port != AMPLIFIER_HTTP_PORT else self.host
        request = urllib.request.Request(
            f"http://{netloc}{self.http_path}?{query}",
            data=body,
            headers={"Content-Type": "application/x-poll"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("ascii", errors="replace")

    def _source_data_for_instance(self, source: int) -> str:
        if self.source_base == 0:
            return self.encode_matrix_source(source)
        return self.source_data(source)

    def send_data_command(self, command: int | str, output: int | str, data: int | str | Iterable[int | str] = 0) -> str:
        return self.send_ascii(self.build_data_command(command, output, data))

    def list_outputs(self) -> BrowseResponse:
        items = [
            BrowseItem(
                kind="Output",
                attributes={
                    "id": str(index),
                    "name": f"Output {index}",
                    "address": _output_address(index),
                },
            )
            for index in range(1, self.output_count + 1)
        ]
        return BrowseResponse(
            kind="Outputs",
            attributes={"total": str(len(items)), "start": "1", "more": "false"},
            items=items,
            raw="",
        )

    def list_sources(self) -> BrowseResponse:
        first_source = 0 if self.source_base == 0 else 1
        items = [
            BrowseItem(
                kind="Source",
                attributes={
                    "id": str(index),
                    "name": f"S{index + 1 if self.source_base == 0 else index}",
                    "address": self._source_data_for_instance(index),
                },
            )
            for index in range(first_source, first_source + self.source_count)
        ]
        return BrowseResponse(
            kind="Sources",
            attributes={"total": str(len(items)), "start": "1", "more": "false"},
            items=items,
            raw="",
        )

    def request_all_parameters(self, output: int | str) -> str:
        return self.send_data_command("09", output, "00")

    def set_output_enabled(self, output: int | str, enabled: bool = True) -> str:
        """Enable or disable an output by controlling standby.

        In the amplifier protocol, standby off means the output is enabled.
        """

        return self.send_data_command("01", output, "00" if enabled else "01")

    def enable_output(self, output: int | str) -> str:
        return self.set_output_enabled(output, True)

    def disable_output(self, output: int | str) -> str:
        return self.set_output_enabled(output, False)

    def enable_all_outputs(self) -> str:
        return self.enable_output(ALL_OUTPUTS)

    def disable_all_outputs(self) -> str:
        return self.disable_output(ALL_OUTPUTS)

    def set_output_mute(self, output: int | str, state: bool | str = "toggle") -> str:
        if isinstance(state, str):
            normalized = state.lower()
            if normalized == "toggle":
                data = "02"
            elif normalized in {"true", "on", "mute", "muted", "1"}:
                data = "00"
            elif normalized in {"false", "off", "unmute", "unmuted", "0"}:
                data = "01"
            else:
                raise ValueError("state must be true, false, or toggle")
        else:
            data = "00" if state else "01"
        return self.send_data_command("02", output, data)

    def mute_output(self, output: int | str) -> str:
        return self.set_output_mute(output, True)

    def unmute_output(self, output: int | str) -> str:
        return self.set_output_mute(output, False)

    def toggle_output_mute(self, output: int | str) -> str:
        return self.set_output_mute(output, "toggle")

    def mute_all_outputs(self, state: bool | str = True) -> str:
        return self.set_output_mute(ALL_OUTPUTS, state)

    def set_output_volume(self, output: int | str, volume: int) -> str:
        if not 0 <= volume <= 0xA0:
            raise ValueError("volume must be between 0 and 160 (0xA0)")
        return self.send_data_command("04", output, volume)

    def output_volume_up(self, output: int | str) -> str:
        return self.send_data_command("11", output, "00")

    def output_volume_down(self, output: int | str) -> str:
        return self.send_data_command("12", output, "00")

    def assign_source_to_output(self, source: int, output: int | str) -> str:
        return self.send_data_command("03", output, self._source_data_for_instance(source))

    def assign_source_to_outputs(self, source: int, outputs: Iterable[int | str]) -> list[str]:
        return [self.assign_source_to_output(source, output) for output in outputs]

    def assign_source_to_all_outputs(self, source: int) -> str:
        return self.assign_source_to_output(source, ALL_OUTPUTS)

    def assign_output_sources(self, assignments: Mapping[int | str, int]) -> list[str]:
        return [self.assign_source_to_output(source, output) for output, source in assignments.items()]

    def assign_matrix(self, assignments: Mapping[int | str, int]) -> list[str]:
        return self.assign_output_sources(assignments)


def _byte(value: int | str) -> str:
    if isinstance(value, int):
        if not 0 <= value <= 0xFF:
            raise ValueError("byte values must be between 0 and 255")
        return f"{value:02X}"

    normalized = value.upper()
    if not re.fullmatch(r"[0-9A-F]{1,2}", normalized):
        raise ValueError(f"invalid byte value: {value!r}")
    return normalized.zfill(2)


def _data_bytes(data: int | str | Iterable[int | str]) -> str:
    if isinstance(data, str):
        normalized = data.upper()
        if re.fullmatch(r"[0-9A-F]+", normalized) and len(normalized) % 2 == 0:
            return normalized
        return _byte(normalized)
    if isinstance(data, int):
        return _byte(data)
    return "".join(_byte(value) for value in data)


def encode_output_address(output: int | str) -> int:
    if isinstance(output, str) and output.lower() == "all":
        return 0xFF
    value = int(output)
    if value == 0xFF:
        return value
    if value >= 64:
        return 192 + (value - 64)
    if value >= 32:
        return 128 + (value - 32)
    return value


def decode_output_address(output: int) -> int | None:
    value = int(output)
    if value == 0xFF:
        return value
    value = value & ~32
    if (value & 192) == 128:
        return 32 + (value & 31)
    if (value & 192) == 192:
        return 64 + (value & 31)
    if (value & 192) == 64:
        return None
    if value == 0:
        return 96
    return value


def _output_address(output: int | str) -> str:
    if isinstance(output, str) and output.lower() == "all":
        return ALL_OUTPUTS
    if isinstance(output, int):
        return _byte(encode_output_address(output))
    return _byte(output)
