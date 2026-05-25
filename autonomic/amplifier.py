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
from .models import AutonomicOutput, AutonomicSource, BrowseItem, BrowseResponse, omit_disabled, output_ref, source_id
from .protocol import CRLF

AMPLIFIER_DIAGNOSTIC_PORT = 17037
AMPLIFIER_HTTP_PORT = 80
DEVICE_ID_COMMAND = "2FFF"
ALL_OUTPUTS = "FF"
AMPLIFIER_RAW_MAX_VOLUME = 0xA0
AmplifierOutputRef = int | str | AutonomicOutput
AmplifierSourceRef = int | str | AutonomicSource
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


class MirageAmplifier:
    """Direct Mirage amplifier output/source control over TCP port 17037.

    MAS/MRAD is the preferred API for most integrations. This class covers the
    amplifier ASCII command format for installations that need direct output
    controls: mute, volume, and source routing.
    """

    def __init__(
        self,
        host: str,
        port: int = AMPLIFIER_DIAGNOSTIC_PORT,
        *,
        timeout: float = 5.0,
        output_count: int = 8,
        source_count: int = 8,
        transport: str = "tcp",
        http_path: str = "/poll.cgi",
        source_base: int = 1,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.output_count = output_count
        self.source_count = source_count
        self.transport = transport.lower()
        self.http_path = http_path if http_path.startswith("/") else f"/{http_path}"
        self.source_base = source_base

    @staticmethod
    def build_data_command(command: int | str, output: int | str, data: int | str | Iterable[int | str] = 0) -> str:
        return f"{_byte(command)}{_output_address(output)}{_data_bytes(data)}"

    @staticmethod
    def build_query_command(command: int | str, output: int | str) -> str:
        return f"{_byte(command)}{_output_address(output)}"

    @staticmethod
    def parse_device_id(response: str) -> str:
        match = _DEVICE_ID_RE.search(response)
        if not match:
            raise ProtocolError(f"Could not find amplifier ID in response: {response!r}")
        return match.group("id").upper()

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
                raw_output = int(line[2:4], 16)
                if raw_output == 0xFF:
                    continue
                keys.add((int(line[0:2], 16), decode_output_address(raw_output)))
            except ValueError:
                continue
        return keys

    def send_ascii(self, command: str) -> str:
        return self.send_commands([command]).strip()

    def get_device_id(self) -> str:
        response = self.send_ascii(DEVICE_ID_COMMAND)
        return self.parse_device_id(response)

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
        expected_keys.update(self._expected_all_output_response_keys(commands))
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

    def _expected_all_output_response_keys(self, commands: Iterable[str]) -> set[tuple[int, int]]:
        keys: set[tuple[int, int]] = set()
        for command in commands:
            line = command.upper().rstrip("\r\n")
            if len(line) < 4:
                continue
            try:
                command_byte = int(line[0:2], 16)
                raw_output = int(line[2:4], 16)
            except ValueError:
                continue
            if raw_output != 0xFF or command_byte not in {0x01, 0x02, 0x03, 0x04}:
                continue
            keys.update((command_byte, output) for output in range(1, self.output_count + 1))
        return keys

    def _source_data_for_instance(self, source: int) -> str:
        if self.source_base == 0:
            return self.encode_matrix_source(source)
        return self.source_data(source)

    def _source_id_for_instance(self, source_data_value: int) -> int:
        source = self.decode_matrix_source(source_data_value)
        if self.source_base == 0 or source_data_value >= 0x20:
            return source
        return source + 1

    def _source_name_for_instance(self, source_id_value: int) -> str:
        if self.source_base == 0:
            return f"S{source_id_value + 1}"
        if source_id_value >= 0x20:
            return f"Remote {source_id_value - 0x20 + 1}"
        return f"S{source_id_value}"

    def send_data_command(self, command: int | str, output: int | str, data: int | str | Iterable[int | str] = 0) -> str:
        return self.send_ascii(self.build_data_command(command, output, data))

    def send_query_command(self, command: int | str, output: int | str) -> str:
        return self.send_ascii(self.build_query_command(command, output))

    def browse_outputs(self) -> BrowseResponse:
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

    def list_outputs(self, *, include_disabled: bool = False, include_status: bool = True) -> list[AutonomicOutput]:
        outputs = [AutonomicOutput.from_browse_item(item, client=self) for item in self.browse_outputs().items]
        if include_status:
            outputs = self._with_status(outputs)
        return omit_disabled(outputs, include_disabled=include_disabled)

    def browse_sources(self) -> BrowseResponse:
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

    def list_sources(self, *, include_disabled: bool = False) -> list[AutonomicSource]:
        sources = [AutonomicSource.from_browse_item(item, client=self) for item in self.browse_sources().items]
        return omit_disabled(sources, include_disabled=include_disabled)

    def request_all_parameters(self, output: AmplifierOutputRef) -> str:
        return self.send_data_command("09", output_ref(output), "00")

    def query_output_power(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("01", output_ref(output)))

    def query_output_mute(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("02", output_ref(output)))

    def query_output_source(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("03", output_ref(output)))

    def query_output_volume(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("04", output_ref(output)))

    def get_output_status(self, output: AmplifierOutputRef) -> AutonomicOutput:
        output_value = output_ref(output)
        output_id = str(_decoded_output_ref(output_value) or output_value)
        base = AutonomicOutput(
            id=output_id,
            name=f"Output {output_id}",
            kind="Output",
            address=_output_address(output_value),
        )
        return self._with_status([base])[0].bind(self)

    def get_output_statuses(self, outputs: Iterable[AmplifierOutputRef] | None = None) -> list[AutonomicOutput]:
        if outputs is None:
            return self.list_outputs(include_status=True)
        base_outputs = [
            AutonomicOutput(
                id=str(_decoded_output_ref(output_ref(output)) or output_ref(output)),
                name=getattr(output, "name", None) or f"Output {_decoded_output_ref(output_ref(output)) or output_ref(output)}",
                kind="Output",
                address=_output_address(output_ref(output)),
            )
            for output in outputs
        ]
        return [output.bind(self) for output in self._with_status(base_outputs)]

    def set_output_mute(self, output: AmplifierOutputRef, state: bool | str = "toggle") -> str:
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
        return self.send_data_command("02", output_ref(output), data)

    def mute_output(self, output: AmplifierOutputRef) -> str:
        return self.set_output_mute(output, True)

    def unmute_output(self, output: AmplifierOutputRef) -> str:
        return self.set_output_mute(output, False)

    def toggle_output_mute(self, output: AmplifierOutputRef) -> str:
        return self.set_output_mute(output, "toggle")

    def mute_all_outputs(self, state: bool | str = True) -> str:
        return self.set_output_mute(ALL_OUTPUTS, state)

    def set_output_power(self, output: AmplifierOutputRef, is_on: bool | str = True) -> str:
        return self.send_data_command("01", output_ref(output), _power_data(is_on))

    def set_output_is_on(self, output: AmplifierOutputRef, is_on: bool | str = True) -> str:
        return self.set_output_power(output, is_on)

    def turn_on_output(self, output: AmplifierOutputRef) -> str:
        return self.set_output_power(output, True)

    def turn_off_output(self, output: AmplifierOutputRef) -> str:
        return self.set_output_power(output, False)

    def set_output_volume(self, output: AmplifierOutputRef, volume: int) -> str:
        return self.send_data_command("04", output_ref(output), _volume_to_raw(volume))

    def output_volume_up(self, output: AmplifierOutputRef) -> str:
        return self.send_data_command("11", output_ref(output), "00")

    def output_volume_down(self, output: AmplifierOutputRef) -> str:
        return self.send_data_command("12", output_ref(output), "00")

    def assign_source_to_output(self, source: AmplifierSourceRef, output: AmplifierOutputRef) -> str:
        return self.send_data_command("03", output_ref(output), self._source_data_for_instance(source_id(source)))

    def assign_source_to_outputs(self, source: AmplifierSourceRef, outputs: Iterable[AmplifierOutputRef]) -> list[str]:
        return [self.assign_source_to_output(source, output) for output in outputs]

    def assign_source_to_all_outputs(self, source: AmplifierSourceRef) -> str:
        return self.assign_source_to_output(source, ALL_OUTPUTS)

    def assign_output_sources(self, assignments: Mapping[AmplifierOutputRef, AmplifierSourceRef]) -> list[str]:
        return [self.assign_source_to_output(source, output) for output, source in assignments.items()]

    def assign_matrix(self, assignments: Mapping[AmplifierOutputRef, AmplifierSourceRef]) -> list[str]:
        return self.assign_output_sources(assignments)

    def _with_status(self, outputs: list[AutonomicOutput]) -> list[AutonomicOutput]:
        if not outputs:
            return []

        by_id = {str(output.id): output for output in outputs if output.id is not None}
        if not by_id:
            return outputs

        rows = self._poll_status_rows()
        updates: dict[str, dict[str, object]] = {output_id: {} for output_id in by_id}
        attr_updates: dict[str, dict[str, str]] = {output_id: {} for output_id in by_id}

        for row in rows:
            if row.output in (None, 0xFF) or not row.data:
                continue
            output_id = str(row.output)
            if output_id not in by_id:
                continue

            attrs = attr_updates[output_id]
            update = updates[output_id]

            if row.command == 0x01:
                is_on = row.data[0] == 0x01
                update["is_on"] = is_on
                attrs["PowerOn"] = "true" if is_on else "false"
            elif row.command == 0x02:
                if row.data[0] in {0x00, 0x01}:
                    muted = row.data[0] == 0x00
                    update["muted"] = muted
                    attrs["Mute"] = "true" if muted else "false"
            elif row.command == 0x03:
                source_data_value = row.data[0] & 0x7F
                source_id_value = self._source_id_for_instance(source_data_value)
                source_name = self._source_name_for_instance(source_id_value)
                update["source_id"] = str(source_id_value)
                update["source_name"] = source_name
                attrs["sourceId"] = str(source_id_value)
                attrs["sourceName"] = source_name
                attrs["sourceAddress"] = _byte(source_data_value)
                if row.data[0] & 0x80:
                    update.setdefault("is_on", True)
                    attrs.setdefault("PowerOn", "true")
            elif row.command == 0x04:
                raw_volume = row.data[-1]
                volume = _volume_from_raw(raw_volume)
                update["volume"] = volume
                attrs["Volume"] = str(volume)
                attrs["rawVolume"] = str(raw_volume)

        rendered: list[AutonomicOutput] = []
        for output in outputs:
            if output.id is None or str(output.id) not in by_id:
                rendered.append(output)
                continue
            output_id = str(output.id)
            update = updates[output_id]
            attrs = dict(output.attributes)
            attrs.update(attr_updates[output_id])
            update["attributes"] = attrs
            rendered.append(output.model_copy(update=update))
        return rendered

    def _poll_status_rows(self) -> list[AmplifierResponse]:
        commands = [
            self.build_query_command("01", ALL_OUTPUTS),
            self.build_query_command("02", ALL_OUTPUTS),
            self.build_query_command("03", ALL_OUTPUTS),
            self.build_query_command("04", ALL_OUTPUTS),
        ]
        return self.poll(commands)


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


def _volume_to_raw(volume: int) -> int:
    value = int(volume)
    if not 0 <= value <= 100:
        raise ValueError("volume must be between 0 and 100")
    return int((value * AMPLIFIER_RAW_MAX_VOLUME) / 100)


def _volume_from_raw(raw_volume: int) -> int:
    raw = max(0, min(AMPLIFIER_RAW_MAX_VOLUME, int(raw_volume)))
    return int((raw * 100) / AMPLIFIER_RAW_MAX_VOLUME)


def _power_data(state: bool | str) -> str:
    if isinstance(state, bool):
        return "01" if state else "00"
    normalized = state.strip().lower()
    if normalized in {"true", "on", "1", "yes"}:
        return "01"
    if normalized in {"false", "off", "0", "no"}:
        return "00"
    raise ValueError("power state must be true/on or false/off")


def _decoded_output_ref(output: int | str) -> int | None:
    try:
        return decode_output_address(encode_output_address(output))
    except (TypeError, ValueError):
        return None


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
