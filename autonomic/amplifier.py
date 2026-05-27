# Low-level direct amplifier client for diagnostic-port control and discovery.
from __future__ import annotations

import re
import socket
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import TypeAlias

from .amplifier_codec import (
    ALL_OUTPUTS,
    AMPLIFIER_DIAGNOSTIC_PORT,
    AMPLIFIER_HTTP_PORT,
    DEVICE_ID_COMMAND,
    REMOTE_SOURCE_COUNT,
    REMOTE_SOURCE_START,
    SOURCE_TO_DATA as _SOURCE_TO_DATA,
    amp_id as _amp_id,
    bool_data as _bool_data,
    byte as _byte,
    data_bytes as _data_bytes,
    decode_matrix_source as _decode_matrix_source,
    decode_output_address,
    decode_text as _decode_text,
    decoded_output_ref as _decoded_output_ref,
    delay_step as _delay_step,
    delay_to_raw as _delay_to_raw,
    encode_matrix_source as _encode_matrix_source,
    encode_output_address,
    guid_from_wire as _guid_from_wire,
    guid_to_wire as _guid_to_wire,
    input_gain_percent_from_raw as _input_gain_percent_from_raw,
    input_gain_to_raw as _input_gain_to_raw,
    input_gain_to_raw_percent as _input_gain_to_raw_percent,
    logical_to_physical_source as _logical_to_physical_source,
    metadata_position as _metadata_position,
    output_address as _output_address,
    output_or_none as _output_or_none,
    parse_response as _parse_response,
    power_data as _power_data,
    signed_range_from_raw as _signed_range_from_raw,
    signed_range_to_raw as _signed_range_to_raw,
    slots_from_bitmap as _slots_from_bitmap,
    source_data as _source_data,
    text_to_hex as _text_to_hex,
    validate_range as _validate_range,
    volume_from_raw as _volume_from_raw,
    volume_to_raw as _volume_to_raw,
    zones_from_member_masks as _zones_from_member_masks,
)
from .amplifier_discovery import MutableDeviceInfo as _MutableDeviceInfo
from .amplifier_discovery import device_info_from_rows as _device_info_from_rows
from .amplifier_discovery import finalize_device_infos as _finalize_device_infos
from .amplifier_types import (
    AmplifierDeviceInfo,
    AmplifierDeviceLinkInfo,
    AmplifierDeviceStateInfo,
    AmplifierDeviceSubInfo,
    AmplifierDiscovery,
    AmplifierInputGain,
    AmplifierLayout,
    AmplifierNetworkInfo,
    AmplifierOutputName,
    AmplifierOutputRef,
    AmplifierPresetGroup,
    AmplifierPresetGroupMap,
    AmplifierRemoteSource,
    AmplifierResetDefaults,
    AmplifierResponse,
    AmplifierSourceDelay,
    AmplifierSourceDetails,
    AmplifierSourceMetadata,
    AmplifierSourceName,
    AmplifierSourceRef,
    AmplifierZoneGroup,
)
from .exceptions import AutonomicError, ProtocolError
from .hardware import model_for_byte
from .models import AutonomicOutput, AutonomicSource, omit_disabled, output_ref, source_id
from .protocol_types import BrowseItem, BrowseResponse
from .protocol import CRLF

_DEVICE_ID_RE = re.compile(r"AFFF(?P<id>[0-9A-Fa-f]{4})")
_ALL_OUTPUT_STATUS_COMMANDS = {
    0x01,
    0x02,
    0x03,
    0x04,
    0x05,
    0x06,
    0x07,
    0x0C,
    0x0D,
    0x1C,
    0x31,
    0x32,
    0x44,
}
_ALL_OUTPUT_RESPONSE_COMMANDS = {
    0x38: 0x1C,
    0x11: 0x04,
    0x12: 0x04,
}
OutputStatusUpdateValue: TypeAlias = str | int | float | bool | None | dict[str, str]


def _preferred_source_name(
    names: Iterable[AmplifierSourceName],
    *,
    source_id: int,
    output: int | None,
) -> AmplifierSourceName | None:
    matches = [name for name in names if name.source_id == source_id]
    if output is not None:
        for name in matches:
            if name.output == output:
                return name
    for name in matches:
        if name.output is None:
            return name
    return matches[0] if matches else None


def _preferred_source_names_by_id(
    names: Iterable[AmplifierSourceName],
    *,
    native_outputs: Iterable[int],
) -> dict[int, AmplifierSourceName]:
    native_output_set = set(native_outputs)
    selected: dict[int, tuple[int, AmplifierSourceName]] = {}
    for name in names:
        if name.output in native_output_set:
            priority = 0
        elif name.output is None:
            priority = 1
        else:
            priority = 2

        current = selected.get(name.source_id)
        if current is None or priority < current[0]:
            selected[name.source_id] = (priority, name)
    return {source_id_value: item for source_id_value, (_priority, item) in selected.items()}


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
        output_count: int | None = None,
        source_count: int | None = None,
        native_output_start: int = 1,
        transport: str = "tcp",
        http_path: str = "/poll.cgi",
        source_base: int | None = None,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.transport = transport.lower()
        self.http_path = http_path if http_path.startswith("/") else f"/{http_path}"
        self._configured_output_count = output_count
        self._configured_source_count = source_count
        self._configured_native_output_start = native_output_start
        self._configured_source_base = source_base
        self._layout_cache: AmplifierLayout | None = None
        self._devices_cache: tuple[AmplifierDeviceInfo, ...] | None = None
        self._selected_output: AmplifierOutputRef | None = None

    @property
    def output_count(self) -> int:
        if self._layout_cache is not None:
            return self._layout_cache.output_count
        return int(self._configured_output_count if self._configured_output_count is not None else 8)

    @property
    def source_count(self) -> int:
        if self._layout_cache is not None:
            return self._layout_cache.source_count
        return int(self._configured_source_count if self._configured_source_count is not None else 8)

    @property
    def source_base(self) -> int:
        if self._layout_cache is not None:
            return self._layout_cache.source_base
        return int(self._configured_source_base if self._configured_source_base is not None else 1)

    @property
    def native_output_start(self) -> int:
        if self._layout_cache is not None:
            return self._layout_cache.native_output_start
        return int(self._configured_native_output_start)

    @property
    def native_output_ids(self) -> range:
        return range(self.native_output_start, self.native_output_start + self.output_count)

    def clear_cache(self) -> None:
        self._layout_cache = None
        self._devices_cache = None

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
        return _source_data(source)

    @staticmethod
    def encode_matrix_source(source: int) -> str:
        return _encode_matrix_source(source)

    @staticmethod
    def decode_matrix_source(source_data: int | str) -> int:
        return _decode_matrix_source(source_data)

    @staticmethod
    def guid_from_wire(raw_guid: str) -> str:
        return _guid_from_wire(raw_guid)

    @staticmethod
    def guid_to_wire(guid: str) -> str:
        return _guid_to_wire(guid)

    @staticmethod
    def encode_output(output: int | str) -> str:
        return _output_address(output)

    @staticmethod
    def decode_output(output_data: int | str) -> int | None:
        return decode_output_address(int(output_data))

    @staticmethod
    def parse_response(text: str) -> list[AmplifierResponse]:
        return _parse_response(text)

    @staticmethod
    def parse_source_name(row: AmplifierResponse) -> AmplifierSourceName | None:
        if row.command != 0x29 or len(row.data) < 4:
            return None

        logical_source = row.data[0]
        source_id_value = _logical_to_physical_source(logical_source)
        short_name = _decode_text(row.data[1:4]) or None
        hidden_name: str | None = None
        name_bytes = row.data[4:]

        if name_bytes and 0 < name_bytes[0] < len(name_bytes):
            hidden_end = 1 + name_bytes[0]
            hidden_name = _decode_text(name_bytes[1:hidden_end]) or None
            display_name = _decode_text(name_bytes[hidden_end:])
            name = display_name or hidden_name or f"S{source_id_value}"
        else:
            name = _decode_text(name_bytes) or f"S{source_id_value}"

        return AmplifierSourceName(
            source_id=source_id_value,
            logical_source=logical_source,
            output=_output_or_none(row.output),
            name=name,
            short_name=short_name,
            hidden_name=hidden_name,
            raw=row.raw,
        )

    @staticmethod
    def parse_output_name(row: AmplifierResponse) -> AmplifierOutputName | None:
        if row.command != 0x1C or row.output in (None, 0xFF):
            return None
        output = row.output
        if output is None:
            return None
        return AmplifierOutputName(
            output=output,
            name=_decode_text(row.data) or f"Zone {output}",
            raw=row.raw,
        )

    @staticmethod
    def parse_remote_source(row: AmplifierResponse) -> AmplifierRemoteSource | None:
        if row.command != 0x4F or len(row.data) < 18:
            return None
        slot = row.data[0]
        if not 0 <= slot < REMOTE_SOURCE_COUNT:
            return None
        raw_guid = "".join(_byte(value) for value in row.data[1:17])
        source_player_id = row.data[17]
        name = _decode_text(row.data[18:]) or f"Remote Source {slot + 1}"
        return AmplifierRemoteSource(
            slot=slot,
            source_id=REMOTE_SOURCE_START + slot,
            guid=_guid_from_wire(raw_guid),
            raw_guid=raw_guid,
            source_player_id=source_player_id,
            name=name,
            raw=row.raw,
        )

    @staticmethod
    def parse_source_metadata(row: AmplifierResponse) -> AmplifierSourceMetadata | None:
        if row.command != 0x46 or len(row.data) < 2:
            return None
        logical_source = row.data[0]
        position = row.data[1]
        return AmplifierSourceMetadata(
            source_id=_logical_to_physical_source(logical_source),
            logical_source=logical_source,
            position=position,
            value=_decode_text(row.data[2:]),
            raw=row.raw,
            output=_output_or_none(row.output),
        )

    def parse_input_gains(
        self,
        row: AmplifierResponse,
        *,
        source_names: Iterable[AmplifierSourceName] | None = None,
    ) -> list[AmplifierInputGain]:
        if row.command != 0x32 or row.output in (None, 0xFF) or not row.data:
            return []

        if row.data[0] != 0xFF:
            logical_source = row.data[0]
            raw_gain = row.data[1] if len(row.data) > 1 else 0
            return [
                AmplifierInputGain(
                    output=row.output,
                    source_id=self._source_id_for_instance(logical_source),
                    logical_source=logical_source,
                    gain_percent=_input_gain_percent_from_raw(raw_gain),
                    raw_gain=raw_gain,
                    raw=row.raw,
                )
            ]

        names = sorted(source_names or (), key=lambda source_name: source_name.source_id)
        gains: list[AmplifierInputGain] = []
        for index, raw_gain in enumerate(row.data[1:]):
            if index < len(names):
                source_id_value = names[index].source_id
                logical_source = names[index].logical_source
            else:
                source_id_value = index if self.source_base == 0 else index + 1
                try:
                    logical_source = int(self._source_data_for_instance(source_id_value), 16)
                except ValueError:
                    logical_source = source_id_value
            gains.append(
                AmplifierInputGain(
                    output=row.output,
                    source_id=source_id_value,
                    logical_source=logical_source,
                    gain_percent=_input_gain_percent_from_raw(raw_gain),
                    raw_gain=raw_gain,
                    raw=row.raw,
                )
            )
        return gains

    def parse_source_delays(self, row: AmplifierResponse) -> list[AmplifierSourceDelay]:
        if row.command != 0x31 or row.output in (None, 0xFF) or len(row.data) <= 1:
            return []

        delays: list[AmplifierSourceDelay] = []
        for logical_source, raw_delay in enumerate(row.data):
            delays.append(
                AmplifierSourceDelay(
                    output=row.output,
                    source_id=self._source_id_for_instance(logical_source),
                    logical_source=logical_source,
                    delay_ms=raw_delay * 5,
                    raw_delay=raw_delay,
                    raw=row.raw,
                )
            )
        return delays

    @staticmethod
    def parse_zone_group(row: AmplifierResponse) -> AmplifierZoneGroup | None:
        if row.command != 0x30 or not row.data:
            return None
        flags = row.data[0]
        zones = tuple(decode_output_address(zone) or zone for zone in row.data[1:])
        if not zones:
            return None
        return AmplifierZoneGroup(
            zones=zones,
            source_linked=bool(flags & 0x01),
            volume_linked=bool(flags & 0x02),
            power_linked=bool(flags & 0x04),
            raw=row.raw,
        )

    @staticmethod
    def parse_preset_group_map(row: AmplifierResponse) -> AmplifierPresetGroupMap | None:
        if row.command != 0x4E or len(row.data) < 2:
            return None
        slot_id = (row.data[0] << 8) | row.data[1]
        if slot_id & 0x7FFF:
            return None
        signature_bytes = row.data[2:4]
        bitmap = row.data[4:] if len(row.data) > 4 else []
        return AmplifierPresetGroupMap(
            available=not bool(slot_id & 0x8000),
            map_data="".join(_byte(value) for value in bitmap),
            signature=_decode_text(signature_bytes) or None,
            available_slots=_slots_from_bitmap(bitmap),
            raw=row.raw,
        )

    @staticmethod
    def parse_preset_group(row: AmplifierResponse) -> AmplifierPresetGroup | None:
        if row.command != 0x4E or len(row.data) < 2:
            return None

        slot_id = (row.data[0] << 8) | row.data[1]
        slot = slot_id & 0x7FFF
        if not 1 <= slot <= 100:
            return None

        available = not bool(slot_id & 0x8000)
        payload = row.data[4:] if len(row.data) > 4 else []
        if not available or not payload:
            return AmplifierPresetGroup(
                slot=slot,
                available=available,
                empty=True,
                read_only=None,
                preset_id=None,
                name=None,
                raw_name=None,
                member_zones=(),
                raw=row.raw,
            )

        read_write_id = payload[0]
        if read_write_id <= 0:
            return AmplifierPresetGroup(
                slot=slot,
                available=True,
                empty=True,
                read_only=False,
                preset_id=None,
                name=None,
                raw_name=None,
                member_zones=(),
                raw=row.raw,
            )

        name_length = payload[1] if len(payload) > 1 else 0
        name_bytes = payload[2 : 2 + name_length]
        member_mask_bytes = payload[2 + name_length :]
        return AmplifierPresetGroup(
            slot=slot,
            available=True,
            empty=False,
            read_only=bool(read_write_id & 0x80),
            preset_id=read_write_id & 0x7F,
            name=_decode_text(name_bytes) or None,
            raw_name="".join(_byte(value) for value in name_bytes) if name_bytes else None,
            member_zones=_zones_from_member_masks(member_mask_bytes),
            raw=row.raw,
        )

    @staticmethod
    def parse_device_sub_info(row: AmplifierResponse) -> AmplifierDeviceSubInfo | None:
        if row.command != 0x3A or len(row.data) < 3:
            return None
        payload = tuple(row.data[3:])
        return AmplifierDeviceSubInfo(
            amp_id="".join(_byte(value) for value in row.data[:2]),
            response_type=row.data[2],
            payload=payload,
            payload_hex="".join(_byte(value) for value in payload),
            value=int.from_bytes(bytes(payload), byteorder="big") if payload else None,
            raw=row.raw,
        )

    @staticmethod
    def parse_device_link_info(row: AmplifierResponse) -> AmplifierDeviceLinkInfo | None:
        if row.command not in {0x4D, 0xCD} or len(row.data) < 2:
            return None
        return AmplifierDeviceLinkInfo(
            command=row.command,
            amp_id="".join(_byte(value) for value in row.data[:2]),
            status=row.data[2] if len(row.data) > 2 else None,
            raw=row.raw,
        )

    @staticmethod
    def parse_device_state_info(row: AmplifierResponse) -> AmplifierDeviceStateInfo | None:
        if row.command != 0x4A or len(row.data) < 2:
            return None
        payload = tuple(row.data[2:])
        return AmplifierDeviceStateInfo(
            amp_id="".join(_byte(value) for value in row.data[:2]),
            payload=payload,
            payload_hex="".join(_byte(value) for value in payload),
            raw=row.raw,
        )

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
                command_byte = int(line[0:2], 16)
                keys.add((_ALL_OUTPUT_RESPONSE_COMMANDS.get(command_byte, command_byte), decode_output_address(raw_output)))
            except ValueError:
                continue
        return keys

    def send_ascii(self, command: str) -> str:
        return self.send_commands([command]).strip()

    def get_device_id(self) -> str:
        response = self.send_ascii(DEVICE_ID_COMMAND)
        return self.parse_device_id(response)

    def infer_layout(self, *, refresh: bool = False) -> AmplifierLayout:
        if self._layout_cache is not None and not refresh:
            return self._layout_cache

        devices = self.discover_devices(refresh=refresh)
        try:
            primary_id = self.get_device_id()
        except (OSError, ProtocolError):
            primary_id = None
        layout = self._layout_from_devices(devices, primary_id=primary_id)
        self._layout_cache = layout
        return layout

    def _layout_from_devices(
        self,
        devices: Iterable[AmplifierDeviceInfo],
        *,
        primary_id: str | None = None,
    ) -> AmplifierLayout:
        device_list = list(devices)
        primary = self._primary_device_from_devices(device_list, primary_id=primary_id)
        primary_model = model_for_byte(primary.model_byte if primary is not None else None)
        model_candidates = [model for device in device_list if (model := model_for_byte(device.model_byte)) is not None]
        zones = [zone for device in device_list for zone in device.zones if zone > 0]
        primary_zones = tuple(zone for zone in (primary.zones if primary is not None else ()) if zone > 0)

        inferred_output_count = (
            max(zones)
            if zones
            else primary_model.output_count
            if primary_model is not None and primary_model.output_count is not None
            else max((model.output_count or 0 for model in model_candidates), default=0)
            or 8
        )
        inferred_source_count = (
            primary_model.source_count
            if primary_model is not None and primary_model.source_count is not None
            else max((model.source_count or 0 for model in model_candidates), default=0)
            or 8
        )
        inferred_source_base = primary_model.source_base if primary_model is not None else 1
        inferred_native_output_start = min(primary_zones) if primary_zones else self._configured_native_output_start

        return AmplifierLayout(
            output_count=int(self._configured_output_count if self._configured_output_count is not None else inferred_output_count),
            source_count=int(self._configured_source_count if self._configured_source_count is not None else inferred_source_count),
            source_base=int(self._configured_source_base if self._configured_source_base is not None else inferred_source_base),
            native_output_start=int(inferred_native_output_start),
            device_id=primary.amp_id if primary is not None else primary_id,
            model_byte=primary.model_byte if primary is not None else None,
        )

    @staticmethod
    def _primary_device_from_devices(
        devices: Iterable[AmplifierDeviceInfo],
        *,
        primary_id: str | None = None,
    ) -> AmplifierDeviceInfo | None:
        device_list = list(devices)
        if primary_id:
            normalized = _amp_id(primary_id)
            for device in device_list:
                if device.amp_id == normalized:
                    return device
        return device_list[0] if device_list else None

    def _layout_for_dynamic_queries(self) -> AmplifierLayout:
        if self._layout_cache is not None:
            return self._layout_cache
        if (
            self._configured_output_count is not None
            or self._configured_source_count is not None
            or self._configured_source_base is not None
        ):
            return AmplifierLayout(
                output_count=self.output_count,
                source_count=self.source_count,
                source_base=self.source_base,
                native_output_start=self.native_output_start,
                device_id=None,
                model_byte=None,
            )
        try:
            return self.infer_layout()
        except (OSError, ProtocolError):
            return AmplifierLayout(
                output_count=self.output_count,
                source_count=self.source_count,
                source_base=self.source_base,
                native_output_start=self.native_output_start,
                device_id=None,
                model_byte=None,
            )

    def discover_devices(self, *, refresh: bool = False) -> list[AmplifierDeviceInfo]:
        if self._devices_cache is not None and not refresh:
            return list(self._devices_cache)

        rows = self.poll(["14FF06"], timeout=max(self.timeout, 2.0))
        device_map = _device_info_from_rows(rows)

        if not device_map:
            try:
                device_id = self.get_device_id()
            except ProtocolError:
                device_id = ""
            if device_id:
                device_map[device_id] = _MutableDeviceInfo(amp_id=device_id)

        if device_map:
            commands: list[str] = []
            for amp_id in sorted(device_map):
                commands.extend(
                    [
                        f"39FF{amp_id}",
                        f"3AFF{amp_id}83",
                        f"3AFF{amp_id}86",
                        f"3AFF{amp_id}85",
                    ]
                )
            rows.extend(self.poll(commands, timeout=max(self.timeout, 3.0)))
            device_map = _device_info_from_rows(rows, existing=device_map)

        devices = tuple(_finalize_device_infos(device_map))
        self._devices_cache = devices
        return list(devices)

    def get_device_info(self, *, refresh: bool = False) -> AmplifierDeviceInfo:
        devices = self.discover_devices(refresh=refresh)
        if not devices:
            raise ProtocolError("Could not discover amplifier device information")
        try:
            device_id = self.get_device_id()
        except ProtocolError:
            device_id = None
        if device_id:
            for device in devices:
                if device.amp_id == device_id:
                    return device
        return devices[0]

    def request_device_model_info(self) -> list[AmplifierResponse]:
        return self.poll("14FF06", timeout=max(self.timeout, 2.0))

    def request_extended_device_info(self, amp_id: str) -> list[AmplifierResponse]:
        return self.poll(f"39FF{_amp_id(amp_id)}", timeout=max(self.timeout, 2.0))

    def request_network_info(self, amp_id: str) -> list[AmplifierResponse]:
        return self.poll(f"3AFF{_amp_id(amp_id)}83", timeout=max(self.timeout, 2.0))

    def request_system_id(self, amp_id: str) -> list[AmplifierResponse]:
        return self.poll(f"3AFF{_amp_id(amp_id)}86", timeout=max(self.timeout, 2.0))

    def request_device_guid(self, amp_id: str) -> list[AmplifierResponse]:
        return self.poll(f"3AFF{_amp_id(amp_id)}85", timeout=max(self.timeout, 2.0))

    @staticmethod
    def build_device_sub_info_command(amp_id: str, subop: int | str) -> str:
        return f"3AFF{_amp_id(amp_id)}{_byte(subop)}"

    def request_device_sub_info(self, amp_id: str, subop: int | str) -> list[AmplifierResponse]:
        return self.poll(self.build_device_sub_info_command(amp_id, subop), timeout=max(self.timeout, 2.0))

    def query_device_sub_info(self, amp_id: str, subop: int | str) -> list[AmplifierDeviceSubInfo]:
        return [
            item
            for row in self.request_device_sub_info(amp_id, subop)
            if (item := self.parse_device_sub_info(row)) is not None
        ]

    def query_device_status_info(self, amp_id: str) -> list[AmplifierDeviceSubInfo]:
        return self.query_device_sub_info(amp_id, 0x87)

    @staticmethod
    def build_device_link_query_command(amp_id: str, opcode: int | str) -> str:
        command = int(_byte(opcode), 16)
        if command not in {0x4D, 0xCD}:
            raise ValueError("device link opcode must be 0x4D or 0xCD")
        return f"{command:02X}{ALL_OUTPUTS}{_amp_id(amp_id)}"

    def query_device_link_info(self, amp_id: str, opcode: int | str) -> list[AmplifierDeviceLinkInfo]:
        return [
            item
            for row in self.poll(self.build_device_link_query_command(amp_id, opcode), timeout=max(self.timeout, 2.0))
            if (item := self.parse_device_link_info(row)) is not None
        ]

    def query_device_links(self, amp_id: str) -> list[AmplifierDeviceLinkInfo]:
        rows: list[AmplifierDeviceLinkInfo] = []
        rows.extend(self.query_device_link_info(amp_id, 0x4D))
        rows.extend(self.query_device_link_info(amp_id, 0xCD))
        return rows

    @staticmethod
    def build_device_state_query_command(amp_id: str) -> str:
        return f"4A{ALL_OUTPUTS}{_amp_id(amp_id)}"

    def query_device_state_info(self, amp_id: str) -> list[AmplifierDeviceStateInfo]:
        return [
            item
            for row in self.poll(self.build_device_state_query_command(amp_id), timeout=max(self.timeout, 2.0))
            if (item := self.parse_device_state_info(row)) is not None
        ]

    def ping_device(self, output: AmplifierOutputRef | None = None) -> str:
        target = ALL_OUTPUTS if output is None else output_ref(output)
        return self.send_data_command("14", target, "06")

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
                    if expected_keys and time.monotonic() < deadline:
                        continue
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if expected_keys:
                    text = b"".join(chunks).decode("ascii", errors="ignore")
                    seen = {(row.command, row.output) for row in self.parse_response(text)}
                    if expected_keys.issubset(seen):
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
            return str(response.read().decode("ascii", errors="replace"))

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
            response_command = _ALL_OUTPUT_RESPONSE_COMMANDS.get(command_byte, command_byte)
            if (
                raw_output != 0xFF
                or command_byte not in _ALL_OUTPUT_STATUS_COMMANDS
                and command_byte not in _ALL_OUTPUT_RESPONSE_COMMANDS
            ):
                continue
            keys.update((response_command, output) for output in self.native_output_ids)
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
        if source_id_value >= 0x20:
            return f"Remote {source_id_value - 0x20 + 1}"
        if self.source_base == 0:
            return f"S{source_id_value + 1}"
        return f"S{source_id_value}"

    def send_data_command(self, command: int | str, output: int | str, data: int | str | Iterable[int | str] = 0) -> str:
        return self.send_ascii(self.build_data_command(command, output, data))

    def send_query_command(self, command: int | str, output: int | str) -> str:
        return self.send_ascii(self.build_query_command(command, output))

    def browse_outputs(self, *, include_names: bool = False) -> BrowseResponse:
        layout = self._layout_for_dynamic_queries()
        discovered_names = self.discover_output_names() if include_names else {}
        items = [
            BrowseItem(
                kind="Output",
                attributes={
                    "id": str(index),
                    "name": discovered_names.get(index, f"Output {index}"),
                    "address": _output_address(index),
                },
            )
            for index in range(layout.native_output_start, layout.native_output_start + layout.output_count)
        ]
        return BrowseResponse(
            kind="Outputs",
            attributes={"total": str(len(items)), "start": "1", "more": "false"},
            items=items,
            raw="",
        )

    def list_outputs(
        self,
        *,
        include_disabled: bool = False,
        include_status: bool = True,
        include_names: bool = False,
        include_source_names: bool = True,
        source_names: Mapping[int, str] | None = None,
    ) -> list[AutonomicOutput]:
        outputs = [AutonomicOutput.from_browse_item(item, client=self) for item in self.browse_outputs(include_names=include_names).items]
        if include_status:
            names = source_names if source_names is not None else self._source_name_lookup() if include_source_names else None
            outputs = self._with_status(outputs, source_names=names)
        return omit_disabled(outputs, include_disabled=include_disabled)

    def query_output_names(self) -> list[AmplifierOutputName]:
        names: list[AmplifierOutputName] = []
        for row in self.poll("38FF", timeout=max(self.timeout, 2.0)):
            output_name = self.parse_output_name(row)
            if output_name is not None:
                names.append(output_name)
        return names

    def discover_output_names(self) -> dict[int, str]:
        return {output_name.output: output_name.name for output_name in self.query_output_names()}

    def discover_source_names(self, output: AmplifierOutputRef | None = None) -> list[AmplifierSourceName]:
        target = ALL_OUTPUTS if output is None else output_ref(output)
        rows = self.poll(self.build_query_command("29", target), timeout=max(self.timeout, 2.0))
        names: list[AmplifierSourceName] = []
        for row in rows:
            source_name = self.parse_source_name(row)
            if source_name is not None:
                names.append(replace(source_name, source_id=self._source_id_for_instance(source_name.logical_source)))
        return names

    @staticmethod
    def build_source_name_query_command(source_data: int | str, *, output: AmplifierOutputRef = ALL_OUTPUTS) -> str:
        return f"29{_output_address(output_ref(output))}{_byte(source_data)}"

    def query_source_name(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> list[AmplifierSourceName]:
        logical_source = int(self._source_data_for_instance(source_id(source)), 16)
        output_value = output_ref(output)
        rows = self.poll(self.build_source_name_query_command(logical_source, output=output_value), timeout=max(self.timeout, 2.0))
        names: list[AmplifierSourceName] = []
        for row in rows:
            source_name = self.parse_source_name(row)
            if source_name is not None:
                names.append(replace(source_name, source_id=self._source_id_for_instance(source_name.logical_source)))
        normalized_source_id = self._source_id_for_instance(logical_source)
        names = [name for name in names if name.source_id == normalized_source_id]
        target_output = _output_or_none(_decoded_output_ref(output_value))
        if target_output is None:
            return names
        output_matches = [name for name in names if name.output == target_output]
        return output_matches or names

    def refresh_source_name(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> list[AmplifierSourceName]:
        return self.query_source_name(source, output=output)

    def discover_remote_sources(self) -> list[AmplifierRemoteSource]:
        rows = self.poll("4FFF", timeout=max(self.timeout, 2.0))
        sources: list[AmplifierRemoteSource] = []
        for row in rows:
            remote_source = self.parse_remote_source(row)
            if remote_source is not None:
                sources.append(remote_source)
        return sources

    def discover_zone_groups(self) -> list[AmplifierZoneGroup]:
        rows = self.poll("30FF20", timeout=max(self.timeout, 2.0))
        groups: list[AmplifierZoneGroup] = []
        seen: set[tuple[int, ...]] = set()
        for row in rows:
            group = self.parse_zone_group(row)
            if group is None or group.zones in seen:
                continue
            groups.append(group)
            seen.add(group.zones)
        return groups

    @staticmethod
    def build_preset_group_query_command(slot: int = 0) -> str:
        _validate_range(slot, lower=0, upper=100, name="preset group slot")
        return f"4E{ALL_OUTPUTS}{slot:04X}"

    def query_preset_group_map(self) -> AmplifierPresetGroupMap | None:
        for row in self.poll(self.build_preset_group_query_command(0), timeout=max(self.timeout, 2.0)):
            preset_map = self.parse_preset_group_map(row)
            if preset_map is not None:
                return preset_map
        return None

    def query_preset_group(self, slot: int) -> AmplifierPresetGroup | None:
        for row in self.poll(self.build_preset_group_query_command(slot), timeout=max(self.timeout, 2.0)):
            group = self.parse_preset_group(row)
            if group is not None:
                return group
        return None

    def discover_preset_groups(
        self,
        slots: Iterable[int] | None = None,
        *,
        include_empty: bool = False,
        include_unavailable: bool = False,
    ) -> list[AmplifierPresetGroup]:
        groups: list[AmplifierPresetGroup] = []
        for slot in slots or range(1, 101):
            group = self.query_preset_group(slot)
            if group is None:
                continue
            if group.empty and not include_empty:
                continue
            if not group.available and not include_unavailable:
                continue
            groups.append(group)
        return groups

    def discover_source_metadata(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> list[AmplifierSourceMetadata]:
        logical_source = int(self._source_data_for_instance(source_id(source)), 16)
        commands = [
            self.build_source_metadata_query_command(logical_source, position, output=output_ref(output))
            for position in range(4)
        ]
        rows = self.poll(commands, timeout=max(self.timeout, 2.0))
        metadata: list[AmplifierSourceMetadata] = []
        for row in rows:
            item = self.parse_source_metadata(row)
            if item is not None:
                metadata.append(replace(item, source_id=self._source_id_for_instance(item.logical_source)))
        return metadata

    def refresh_source_metadata(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> list[AmplifierSourceMetadata]:
        return self.discover_source_metadata(source, output=output)

    def discover_all_source_metadata(
        self,
        output: AmplifierOutputRef = ALL_OUTPUTS,
        *,
        sources: Iterable[AmplifierSourceRef] | None = None,
    ) -> list[AmplifierSourceMetadata]:
        layout = self._layout_for_dynamic_queries()
        first_source = 0 if layout.source_base == 0 else 1
        source_ids = (
            list(sources)
            if sources is not None
            else list(range(first_source, first_source + layout.source_count))
        )
        commands: list[str] = []
        for source in source_ids:
            logical_source = int(self._source_data_for_instance(source_id(source)), 16)
            commands.extend(
                self.build_source_metadata_query_command(logical_source, position, output=output_ref(output))
                for position in range(4)
            )

        metadata: list[AmplifierSourceMetadata] = []
        for row in self.poll(commands, timeout=max(self.timeout, 3.0)):
            item = self.parse_source_metadata(row)
            if item is not None:
                metadata.append(replace(item, source_id=self._source_id_for_instance(item.logical_source)))
        return metadata

    def refresh_all_source_metadata(
        self,
        output: AmplifierOutputRef = ALL_OUTPUTS,
        *,
        sources: Iterable[AmplifierSourceRef] | None = None,
    ) -> list[AmplifierSourceMetadata]:
        return self.discover_all_source_metadata(output, sources=sources)

    def discover_source_details(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> AmplifierSourceDetails:
        source_id_value = source_id(source)
        logical_source = int(self._source_data_for_instance(source_id_value), 16)
        output_value = output_ref(output)
        commands = [self.build_source_name_query_command(logical_source, output=output_value)]
        commands.extend(
            self.build_source_metadata_query_command(logical_source, position, output=output_value)
            for position in range(4)
        )

        source_names: list[AmplifierSourceName] = []
        metadata: list[AmplifierSourceMetadata] = []
        for row in self.poll(commands, timeout=max(self.timeout, 3.0)):
            if row.command == 0x29:
                item = self.parse_source_name(row)
                if item is not None:
                    source_names.append(replace(item, source_id=self._source_id_for_instance(item.logical_source)))
            elif row.command == 0x46:
                metadata_item = self.parse_source_metadata(row)
                if metadata_item is not None:
                    metadata.append(
                        replace(metadata_item, source_id=self._source_id_for_instance(metadata_item.logical_source))
                    )

        normalized_source_id = self._source_id_for_instance(logical_source)
        target_output = _output_or_none(_decoded_output_ref(output_value))
        source_name = _preferred_source_name(source_names, source_id=normalized_source_id, output=target_output)
        detail_output = source_name.output if source_name is not None else target_output
        return AmplifierSourceDetails(
            source_id=normalized_source_id,
            logical_source=logical_source,
            output=detail_output,
            name=source_name,
            metadata=tuple(
                item
                for item in metadata
                if item.source_id == normalized_source_id and item.output in (None, target_output)
            ),
        )

    def refresh_source_details(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> AmplifierSourceDetails:
        return self.discover_source_details(source, output=output)

    def query_input_gains(
        self,
        output: AmplifierOutputRef | None = None,
        *,
        include_source_names: bool = True,
    ) -> list[AmplifierInputGain]:
        target = ALL_OUTPUTS if output is None else output_ref(output)
        source_names_by_output: dict[int | None, list[AmplifierSourceName]] = {}
        if include_source_names:
            try:
                for source_name in self.discover_source_names(None if output is None else target):
                    source_names_by_output.setdefault(source_name.output, []).append(source_name)
            except OSError:
                source_names_by_output = {}

        gains: list[AmplifierInputGain] = []
        for row in self.poll(self.build_query_command("32", target), timeout=max(self.timeout, 2.0)):
            gains.extend(self.parse_input_gains(row, source_names=source_names_by_output.get(row.output)))
        return gains

    def discover(self, *, include_preset_groups: bool = True) -> AmplifierDiscovery:
        raw_lines: list[str] = []
        devices = tuple(self.discover_devices())
        source_names = tuple(self.discover_source_names())
        remote_sources = tuple(self.discover_remote_sources())
        zone_groups = tuple(self.discover_zone_groups())
        preset_group_map = self.query_preset_group_map()
        preset_groups: tuple[AmplifierPresetGroup, ...] = ()
        if include_preset_groups and preset_group_map is not None:
            preset_groups = tuple(
                group
                for slot in preset_group_map.available_slots
                if (group := self.query_preset_group(slot)) is not None
            )
        sources = tuple(self.list_sources(include_disabled=True))
        raw_lines.extend(item.raw for item in source_names)
        raw_lines.extend(item.raw for item in remote_sources)
        raw_lines.extend(item.raw for item in zone_groups)
        if preset_group_map is not None:
            raw_lines.append(preset_group_map.raw)
        raw_lines.extend(group.raw for group in preset_groups)
        for device in devices:
            raw_lines.extend(device.raw_lines)
        return AmplifierDiscovery(
            devices=devices,
            sources=sources,
            remote_sources=remote_sources,
            source_names=source_names,
            zone_groups=zone_groups,
            raw_lines=tuple(raw_lines),
            preset_group_map=preset_group_map,
            preset_groups=preset_groups,
        )

    def browse_sources(self, *, dynamic: bool = True) -> BrowseResponse:
        layout = self._layout_for_dynamic_queries()
        first_source = 0 if layout.source_base == 0 else 1
        discovered: dict[int, AmplifierSourceName] = {}
        remote_sources: dict[int, AmplifierRemoteSource] = {}
        if dynamic:
            try:
                discovered = _preferred_source_names_by_id(
                    self.discover_source_names(),
                    native_outputs=range(layout.native_output_start, layout.native_output_start + layout.output_count),
                )
                for remote_source in self.discover_remote_sources():
                    remote_sources[remote_source.source_id] = remote_source
            except OSError:
                discovered = {}
                remote_sources = {}

        discovered_local_sources = [
            source_id_value
            for source_id_value in discovered
            if first_source <= source_id_value < REMOTE_SOURCE_START and source_id_value in _SOURCE_TO_DATA
        ]
        max_local_source = max([first_source + layout.source_count - 1, *discovered_local_sources])
        items = [
            BrowseItem(
                kind="Source",
                attributes={
                    "id": str(index),
                    "name": discovered[index].name if index in discovered else f"S{index + 1 if layout.source_base == 0 else index}",
                    "address": self._source_data_for_instance(index),
                    **_source_name_attrs(discovered.get(index)),
                },
            )
            for index in range(first_source, max_local_source + 1)
        ]
        for source_id_value in sorted(remote_sources):
            remote_source = remote_sources[source_id_value]
            address = self._source_data_for_instance(source_id_value)
            items.append(
                BrowseItem(
                    kind="Source",
                    attributes={
                        "id": str(source_id_value),
                        "guid": remote_source.guid,
                        "rawGuid": remote_source.raw_guid,
                        "name": remote_source.name,
                        "address": address,
                        "remoteSlot": str(remote_source.slot),
                        "sourcePlayerId": str(remote_source.source_player_id),
                        "discovered": "true",
                    },
                )
            )
        return BrowseResponse(
            kind="Sources",
            attributes={"total": str(len(items)), "start": "1", "more": "false"},
            items=items,
            raw="",
        )

    def list_sources(self, *, include_disabled: bool = False, dynamic: bool = True) -> list[AutonomicSource]:
        sources = [AutonomicSource.from_browse_item(item, client=self) for item in self.browse_sources(dynamic=dynamic).items]
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

    def query_output_bass(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("05", output_ref(output)))

    def query_output_treble(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("06", output_ref(output)))

    def query_output_balance(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("07", output_ref(output)))

    def query_output_loudness(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("0C", output_ref(output)))

    def query_output_max_volume(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("0D", output_ref(output)))

    def query_output_delay(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("31", output_ref(output)))

    def query_output_gain(self, output: AmplifierOutputRef) -> list[AmplifierResponse]:
        return self.poll(self.build_query_command("44", output_ref(output)))

    def query_output_source_delays(self, output: AmplifierOutputRef) -> list[AmplifierSourceDelay]:
        delays: list[AmplifierSourceDelay] = []
        for row in self.query_output_delay(output):
            delays.extend(self.parse_source_delays(row))
        return delays

    def get_output_status(
        self,
        output: AmplifierOutputRef,
        *,
        source_names: Mapping[int, str] | None = None,
    ) -> AutonomicOutput:
        output_value = output_ref(output)
        output_id = str(_decoded_output_ref(output_value) or output_value)
        base = AutonomicOutput(
            id=output_id,
            name=f"Output {output_id}",
            kind="Output",
            address=_output_address(output_value),
        )
        names = source_names if source_names is not None else self._source_name_lookup()
        return self._with_status([base], source_names=names)[0].bind(self)

    def read_output_power(self, output: AmplifierOutputRef) -> bool | None:
        return self.get_output_status(output).is_on

    def read_output_mute(self, output: AmplifierOutputRef) -> bool | None:
        return self.get_output_status(output).muted

    def read_output_volume(self, output: AmplifierOutputRef) -> float | None:
        return self.get_output_status(output).volume

    def read_output_max_volume(self, output: AmplifierOutputRef) -> float | None:
        return self.get_output_status(output).max_volume

    def read_output_bass(self, output: AmplifierOutputRef) -> int | None:
        return self.get_output_status(output).bass

    def read_output_treble(self, output: AmplifierOutputRef) -> int | None:
        return self.get_output_status(output).treble

    def read_output_balance(self, output: AmplifierOutputRef) -> int | None:
        return self.get_output_status(output).balance

    def read_output_gain(self, output: AmplifierOutputRef) -> int | None:
        return self.get_output_status(output).gain

    def read_output_delay(self, output: AmplifierOutputRef) -> int | None:
        return self.get_output_status(output).delay_ms

    def read_output_loudness(self, output: AmplifierOutputRef) -> bool | None:
        return self.get_output_status(output).loudness

    def read_output_mono_downmix(self, output: AmplifierOutputRef) -> bool | None:
        return None

    def read_output_power_on_volume(self, output: AmplifierOutputRef) -> float | None:
        return None

    def read_output_source_id(self, output: AmplifierOutputRef) -> str | None:
        return self.get_output_status(output).source_id

    def read_output_source_name(self, output: AmplifierOutputRef) -> str | None:
        return self.get_output_status(output).source_name

    def get_output_statuses(
        self,
        outputs: Iterable[AmplifierOutputRef] | None = None,
        *,
        source_names: Mapping[int, str] | None = None,
    ) -> list[AutonomicOutput]:
        if outputs is None:
            return self.list_outputs(include_status=True, source_names=source_names)
        base_outputs = [
            AutonomicOutput(
                id=str(_decoded_output_ref(output_ref(output)) or output_ref(output)),
                name=getattr(output, "name", None) or f"Output {_decoded_output_ref(output_ref(output)) or output_ref(output)}",
                kind="Output",
                address=_output_address(output_ref(output)),
            )
            for output in outputs
        ]
        names = source_names if source_names is not None else self._source_name_lookup()
        return [output.bind(self) for output in self._with_status(base_outputs, source_names=names)]

    def _resolve_selected_output(self, output: AmplifierOutputRef | None) -> AmplifierOutputRef:
        if output is not None:
            return output
        if self._selected_output is None:
            raise AutonomicError("select an output first or pass output explicitly")
        return self._selected_output

    def select_output(self, output: AmplifierOutputRef) -> None:
        self._selected_output = output

    def select_source(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef | None = None,
        *,
        include_group: bool = False,
    ) -> str:
        return self.assign_source_to_output(source, self._resolve_selected_output(output), include_group=include_group)

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

    def toggle_output_power(self, output: AmplifierOutputRef) -> str:
        return self.set_output_power(output, "toggle")

    def set_all_output_power(self, is_on: bool | str = True) -> str:
        return self.set_output_power(ALL_OUTPUTS, is_on)

    def set_all_outputs_on(self, is_on: bool | str = True) -> str:
        return self.set_all_output_power(is_on)

    def all_off(self) -> str:
        return self.set_all_output_power(False)

    def all_on(self) -> str:
        return self.set_all_output_power(True)

    def turn_on_output(self, output: AmplifierOutputRef) -> str:
        return self.set_output_power(output, True)

    def turn_off_output(self, output: AmplifierOutputRef) -> str:
        return self.set_output_power(output, False)

    def set_output_volume(self, output: AmplifierOutputRef, volume: float) -> str:
        return self.send_data_command("04", output_ref(output), _volume_to_raw(volume))

    def set_output_max_volume(self, output: AmplifierOutputRef, volume: float) -> str:
        return self.send_data_command("0D", output_ref(output), _volume_to_raw(volume))

    def output_max_volume_up(self, output: AmplifierOutputRef, step: float = 1.0) -> str:
        current = self._read_required_output_max_volume(output)
        return self.set_output_max_volume(output, min(100.0, current + float(step)))

    def output_max_volume_down(self, output: AmplifierOutputRef, step: float = 1.0) -> str:
        current = self._read_required_output_max_volume(output)
        return self.set_output_max_volume(output, max(0.0, current - float(step)))

    def set_output_bass(self, output: AmplifierOutputRef, value: int) -> str:
        return self.send_data_command("05", output_ref(output), _signed_range_to_raw(value, lower=-12, upper=12))

    def output_bass_up(self, output: AmplifierOutputRef | None = None, step: int = 1) -> str:
        target = self._resolve_selected_output(output)
        current = self._read_required_signed_output_value(target, "05", lower=-12, upper=12, name="Bass")
        return self.set_output_bass(target, min(12, current + int(step)))

    def output_bass_down(self, output: AmplifierOutputRef | None = None, step: int = 1) -> str:
        target = self._resolve_selected_output(output)
        current = self._read_required_signed_output_value(target, "05", lower=-12, upper=12, name="Bass")
        return self.set_output_bass(target, max(-12, current - int(step)))

    def set_output_treble(self, output: AmplifierOutputRef, value: int) -> str:
        return self.send_data_command("06", output_ref(output), _signed_range_to_raw(value, lower=-12, upper=12))

    def output_treble_up(self, output: AmplifierOutputRef | None = None, step: int = 1) -> str:
        target = self._resolve_selected_output(output)
        current = self._read_required_signed_output_value(target, "06", lower=-12, upper=12, name="Treble")
        return self.set_output_treble(target, min(12, current + int(step)))

    def output_treble_down(self, output: AmplifierOutputRef | None = None, step: int = 1) -> str:
        target = self._resolve_selected_output(output)
        current = self._read_required_signed_output_value(target, "06", lower=-12, upper=12, name="Treble")
        return self.set_output_treble(target, max(-12, current - int(step)))

    def set_output_balance(self, output: AmplifierOutputRef, value: int) -> str:
        return self.send_data_command("07", output_ref(output), _signed_range_to_raw(value, lower=-20, upper=20))

    def output_balance_left(self, output: AmplifierOutputRef | None = None, step: int = 1) -> str:
        target = self._resolve_selected_output(output)
        current = self._read_required_signed_output_value(target, "07", lower=-20, upper=20, name="Balance")
        return self.set_output_balance(target, max(-20, current - int(step)))

    def output_balance_right(self, output: AmplifierOutputRef | None = None, step: int = 1) -> str:
        target = self._resolve_selected_output(output)
        current = self._read_required_signed_output_value(target, "07", lower=-20, upper=20, name="Balance")
        return self.set_output_balance(target, min(20, current + int(step)))

    def set_output_gain(self, output: AmplifierOutputRef, value: int) -> str:
        return self.send_data_command("44", output_ref(output), _signed_range_to_raw(value, lower=-12, upper=12))

    def output_gain_up(self, output: AmplifierOutputRef | None = None, step: int = 1) -> str:
        target = self._resolve_selected_output(output)
        current = self._read_required_signed_output_value(target, "44", lower=-12, upper=12, name="Gain")
        return self.set_output_gain(target, min(12, current + int(step)))

    def output_gain_down(self, output: AmplifierOutputRef | None = None, step: int = 1) -> str:
        target = self._resolve_selected_output(output)
        current = self._read_required_signed_output_value(target, "44", lower=-12, upper=12, name="Gain")
        return self.set_output_gain(target, max(-12, current - int(step)))

    def set_output_delay(self, output: AmplifierOutputRef, delay_ms: int) -> str:
        return self.send_data_command("31", output_ref(output), _delay_to_raw(delay_ms))

    def output_delay_up(self, output: AmplifierOutputRef | None = None, step_ms: int = 5) -> str:
        target = self._resolve_selected_output(output)
        step = _delay_step(step_ms)
        current = self._read_required_output_delay(target)
        return self.set_output_delay(target, min(600, current + step))

    def output_delay_down(self, output: AmplifierOutputRef | None = None, step_ms: int = 5) -> str:
        target = self._resolve_selected_output(output)
        step = _delay_step(step_ms)
        current = self._read_required_output_delay(target)
        return self.set_output_delay(target, max(0, current - step))

    def set_output_loudness(self, output: AmplifierOutputRef, enabled: bool | str) -> str:
        return self.send_data_command("0C", output_ref(output), _bool_data(enabled))

    def set_output_mono_downmix(self, output: AmplifierOutputRef, enabled: bool | str) -> str:
        raise AutonomicError("Direct amplifier mono-downmix writes are not available")

    def set_output_power_on_volume(self, output: AmplifierOutputRef, value: float) -> str:
        raise AutonomicError("Direct amplifier power-on volume writes are not available")

    @staticmethod
    def build_output_name_command(output: AmplifierOutputRef, name: str) -> str:
        name_bytes = str(name).encode("utf-8")
        if len(name_bytes) > 25:
            raise ValueError("output name must be 25 UTF-8 bytes or fewer")
        return f"1C{_output_address(output_ref(output))}{name_bytes.hex().upper()}"

    def set_output_name(self, output: AmplifierOutputRef, name: str) -> str:
        return self.send_ascii(self.build_output_name_command(output, name))

    def set_output_icon(self, output: AmplifierOutputRef, icon: str) -> str:
        raise AutonomicError("Direct amplifier output icons are not available")

    def set_all_output_max_volume(self, volume: float) -> str:
        return self.set_output_max_volume(ALL_OUTPUTS, volume)

    def set_all_output_bass(self, value: int) -> str:
        return self.set_output_bass(ALL_OUTPUTS, value)

    def set_all_output_treble(self, value: int) -> str:
        return self.set_output_treble(ALL_OUTPUTS, value)

    def set_all_output_balance(self, value: int) -> str:
        return self.set_output_balance(ALL_OUTPUTS, value)

    def set_all_output_gain(self, value: int) -> str:
        return self.set_output_gain(ALL_OUTPUTS, value)

    def set_all_output_delay(self, delay_ms: int) -> str:
        return self.set_output_delay(ALL_OUTPUTS, delay_ms)

    def set_all_output_loudness(self, enabled: bool | str) -> str:
        return self.set_output_loudness(ALL_OUTPUTS, enabled)

    def output_volume_up(self, output: AmplifierOutputRef | None = None) -> str:
        return self.send_query_command("11", output_ref(self._resolve_selected_output(output)))

    def output_volume_down(self, output: AmplifierOutputRef | None = None) -> str:
        return self.send_query_command("12", output_ref(self._resolve_selected_output(output)))

    def volume_up(self, zone: AmplifierOutputRef | None = None) -> str:
        return self.output_volume_up(zone)

    def volume_down(self, zone: AmplifierOutputRef | None = None) -> str:
        return self.output_volume_down(zone)

    def assign_source_to_output(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef,
        *,
        include_group: bool = False,
    ) -> str:
        return self.send_data_command("03", output_ref(output), self._source_data_for_instance(source_id(source)))

    @staticmethod
    def build_input_gain_command(output: AmplifierOutputRef, source_data: int | str, raw_gain: int) -> str:
        return MirageAmplifier.build_data_command(
            "32",
            output_ref(output),
            [_byte(source_data), _input_gain_to_raw(raw_gain)],
        )

    def set_input_gain(
        self,
        source: AmplifierSourceRef,
        output: AmplifierOutputRef,
        gain: int,
        *,
        refresh: bool = True,
    ) -> str:
        source_data_value = int(self._source_data_for_instance(source_id(source)), 16)
        commands = [self.build_input_gain_command(output, source_data_value, _input_gain_to_raw_percent(gain))]
        if refresh:
            commands.append(self.build_query_command("32", ALL_OUTPUTS))
        return self.send_commands(commands, timeout=max(self.timeout, 2.0)).strip()

    def define_remote_source(self, slot: int, guid: str, source_position: int, name: str = "") -> str:
        _validate_range(slot, lower=0, upper=REMOTE_SOURCE_COUNT - 1, name="remote source slot")
        _validate_range(source_position, lower=0, upper=0xFF, name="source position")
        data = f"{slot:02X}{_guid_to_wire(guid)}{source_position:02X}{_text_to_hex(name)}"
        return self.send_data_command("4F", ALL_OUTPUTS, data)

    def delete_remote_source(self, slot: int) -> str:
        _validate_range(slot, lower=0, upper=REMOTE_SOURCE_COUNT - 1, name="remote source slot")
        return self.send_data_command("4F", ALL_OUTPUTS, [slot, 0])

    @staticmethod
    def build_source_name_command(output: AmplifierOutputRef, source_data: int | str, name: str) -> str:
        name_bytes = str(name).encode("utf-8")
        if len(name_bytes) > 25:
            raise ValueError("source name must be 25 UTF-8 bytes or fewer")
        return f"29{_output_address(output_ref(output))}{_byte(source_data)}000001{name_bytes.hex().upper()}"

    def set_source_name(self, source: AmplifierSourceRef, name: str, output: AmplifierOutputRef = ALL_OUTPUTS) -> str:
        return self.send_ascii(self.build_source_name_command(output, self._source_data_for_instance(source_id(source)), name))

    def set_source_icon(self, source: AmplifierSourceRef, icon: str) -> str:
        raise AutonomicError("Direct amplifier source icons are not available")

    @staticmethod
    def build_source_metadata_command(
        source_data: int | str,
        position: int,
        value: str,
        *,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> str:
        position = _metadata_position(position)
        value_bytes = str(value).encode("utf-8")
        if len(value_bytes) > 100:
            raise ValueError("source metadata value must be 100 UTF-8 bytes or fewer")
        return f"46{_output_address(output_ref(output))}{_byte(source_data)}{_byte(position)}{value_bytes.hex().upper()}"

    @staticmethod
    def build_source_metadata_query_command(
        source_data: int | str,
        position: int,
        *,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> str:
        return f"47{_output_address(output_ref(output))}{_byte(source_data)}{_byte(_metadata_position(position))}"

    def set_source_metadata(
        self,
        source: AmplifierSourceRef,
        position: int,
        value: str,
        *,
        refresh: bool = True,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> str:
        source_data_value = int(self._source_data_for_instance(source_id(source)), 16)
        commands = [self.build_source_metadata_command(source_data_value, position, value, output=output)]
        if refresh:
            commands.append(self.build_source_metadata_query_command(source_data_value, position, output=output))
        return self.send_commands(commands, timeout=max(self.timeout, 2.0)).strip()

    def set_source_metadata_fields(
        self,
        source: AmplifierSourceRef,
        values: Mapping[int, str] | Iterable[str],
        *,
        refresh: bool = True,
        output: AmplifierOutputRef = ALL_OUTPUTS,
    ) -> str:
        source_data_value = int(self._source_data_for_instance(source_id(source)), 16)
        if isinstance(values, Mapping):
            fields = sorted((int(position), str(value)) for position, value in values.items())
        else:
            fields = list(enumerate(str(value) for value in values))

        commands: list[str] = []
        for position, value in fields:
            commands.append(self.build_source_metadata_command(source_data_value, position, value, output=output))
            if refresh:
                commands.append(self.build_source_metadata_query_command(source_data_value, position, output=output))
        return self.send_commands(commands, timeout=max(self.timeout, 2.0)).strip()

    def rename_sources_to_low_level_input_labels(
        self,
        *,
        devices: Iterable[AmplifierDeviceInfo] | None = None,
    ) -> str:
        """Rename discovered source labels back to model-specific low-level input labels."""

        commands = self.low_level_source_label_commands(devices=devices)
        return self.send_commands(commands, timeout=max(self.timeout, 3.0)).strip()

    def low_level_source_label_commands(
        self,
        *,
        devices: Iterable[AmplifierDeviceInfo] | None = None,
    ) -> list[str]:
        discovered_devices = list(devices) if devices is not None else self.discover_devices()
        layout = self._layout_for_dynamic_queries()
        if not discovered_devices:
            discovered_devices = [
                AmplifierDeviceInfo(
                    amp_id="",
                    zones=tuple(range(layout.native_output_start, layout.native_output_start + layout.output_count)),
                )
            ]

        commands: list[str] = []
        for device in discovered_devices:
            labels = self._low_level_source_labels_for_device(device)
            zones = device.zones or tuple(range(layout.native_output_start, layout.native_output_start + layout.output_count))
            for zone in zones:
                commands.extend(self.build_source_name_command(zone, source_data_value, label) for source_data_value, label in labels)
        return commands

    def default_output_name_commands(self, outputs: Iterable[AmplifierOutputRef] | None = None) -> list[str]:
        layout = self._layout_for_dynamic_queries()
        target_outputs = list(outputs) if outputs is not None else list(range(layout.native_output_start, layout.native_output_start + layout.output_count))
        return [self.build_output_name_command(output, f"Zone {_decoded_output_ref(output_ref(output)) or output_ref(output)}") for output in target_outputs]

    def reset_output_names(self, outputs: Iterable[AmplifierOutputRef] | None = None) -> str:
        return self.send_commands(self.default_output_name_commands(outputs), timeout=max(self.timeout, 2.0)).strip()

    def assign_source_to_outputs(self, source: AmplifierSourceRef, outputs: Iterable[AmplifierOutputRef]) -> list[str]:
        return [self.assign_source_to_output(source, output) for output in outputs]

    def assign_source_to_all_outputs(self, source: AmplifierSourceRef) -> str:
        return self.assign_source_to_output(source, ALL_OUTPUTS)

    def assign_output_sources(self, assignments: Mapping[AmplifierOutputRef, AmplifierSourceRef]) -> list[str]:
        return [self.assign_source_to_output(source, output) for output, source in assignments.items()]

    def assign_matrix(self, assignments: Mapping[AmplifierOutputRef, AmplifierSourceRef]) -> list[str]:
        return self.assign_output_sources(assignments)

    def reset_all_to_defaults(
        self,
        defaults: AmplifierResetDefaults | None = None,
        *,
        safety_mute: bool = True,
        clear_remote_sources: bool = False,
        reset_output_names: bool = True,
    ) -> str:
        return self.send_commands(
            self.reset_all_to_defaults_commands(
                defaults,
                safety_mute=safety_mute,
                clear_remote_sources=clear_remote_sources,
                reset_output_names=reset_output_names,
            )
        ).strip()

    def reset_all_to_defaults_commands(
        self,
        defaults: AmplifierResetDefaults | None = None,
        *,
        safety_mute: bool = True,
        clear_remote_sources: bool = False,
        reset_output_names: bool = True,
    ) -> list[str]:
        values = defaults or AmplifierResetDefaults()
        source_id_value = source_id(values.source)
        if values.volume > values.max_volume:
            raise ValueError("default volume must be less than or equal to default max_volume")
        if clear_remote_sources and source_id_value >= REMOTE_SOURCE_START:
            raise ValueError("clear_remote_sources requires a local default source")

        commands: list[str] = []
        if safety_mute:
            commands.append(self.build_data_command("02", ALL_OUTPUTS, "00"))
        commands.extend(
            [
                self.build_data_command("0D", ALL_OUTPUTS, _volume_to_raw(values.max_volume)),
                self.build_data_command("04", ALL_OUTPUTS, _volume_to_raw(values.volume)),
                self.build_data_command("03", ALL_OUTPUTS, self._source_data_for_instance(source_id_value)),
                self.build_data_command("05", ALL_OUTPUTS, _signed_range_to_raw(values.bass, lower=-12, upper=12)),
                self.build_data_command("06", ALL_OUTPUTS, _signed_range_to_raw(values.treble, lower=-12, upper=12)),
                self.build_data_command("07", ALL_OUTPUTS, _signed_range_to_raw(values.balance, lower=-20, upper=20)),
                self.build_data_command("44", ALL_OUTPUTS, _signed_range_to_raw(values.gain, lower=-12, upper=12)),
                self.build_data_command("31", ALL_OUTPUTS, _delay_to_raw(values.delay_ms)),
                self.build_data_command("0C", ALL_OUTPUTS, "01" if values.loudness else "00"),
            ]
        )
        if reset_output_names:
            commands.extend(self.default_output_name_commands())
        layout = self._layout_for_dynamic_queries()
        input_gain_raw = _input_gain_to_raw_percent(values.input_gain)
        first_source = 0 if layout.source_base == 0 else 1
        commands.extend(
            self.build_input_gain_command(ALL_OUTPUTS, self._source_data_for_instance(source), input_gain_raw)
            for source in range(first_source, first_source + layout.source_count)
        )
        if clear_remote_sources:
            commands.extend(
                self.build_data_command("4F", ALL_OUTPUTS, [slot, 0])
                for slot in range(REMOTE_SOURCE_COUNT)
            )
        if not safety_mute or not values.muted:
            commands.append(self.build_data_command("02", ALL_OUTPUTS, "00" if values.muted else "01"))
        commands.append(self.build_data_command("01", ALL_OUTPUTS, _power_data(values.is_on)))
        return commands

    def _low_level_source_labels_for_device(self, device: AmplifierDeviceInfo) -> tuple[tuple[int, str], ...]:
        model = model_for_byte(device.model_byte)
        if model is not None and model.source_labels_by_data:
            return model.source_labels_by_data
        layout = self._layout_for_dynamic_queries()
        first_source = 0 if layout.source_base == 0 else 1
        return tuple(
            (int(self._source_data_for_instance(source), 16), self._source_name_for_instance(source))
            for source in range(first_source, first_source + layout.source_count)
        )

    def _source_name_lookup(self) -> dict[int, str]:
        lookup: dict[int, str] = {}
        try:
            for source_id_value, source_name in _preferred_source_names_by_id(
                self.discover_source_names(),
                native_outputs=self.native_output_ids,
            ).items():
                lookup[source_id_value] = source_name.name
            for remote_source in self.discover_remote_sources():
                lookup[remote_source.source_id] = remote_source.name
        except OSError:
            return {}
        return lookup

    def _with_status(self, outputs: list[AutonomicOutput], *, source_names: Mapping[int, str] | None = None) -> list[AutonomicOutput]:
        if not outputs:
            return []

        by_id = {str(output.id): output for output in outputs if output.id is not None}
        if not by_id:
            return outputs

        rows = self._poll_status_rows()
        updates: dict[str, dict[str, OutputStatusUpdateValue]] = {output_id: {} for output_id in by_id}
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
                source_byte = row.data[0]
                source_data_value = source_byte & 0x7F
                source_id_value = self._source_id_for_instance(source_data_value)
                source_name = (source_names or {}).get(source_id_value, self._source_name_for_instance(source_id_value))
                update["source_id"] = str(source_id_value)
                update["source_name"] = source_name
                attrs["sourceId"] = str(source_id_value)
                attrs["sourceName"] = source_name
                attrs["sourceAddress"] = _byte(source_data_value)
                if len(row.data) > 1:
                    attrs["sourceStatusData"] = "".join(_byte(value) for value in row.data)
                    attrs["reportedSourcePlayerId"] = str(row.data[-1] & 0x7F)
                if row.data[0] & 0x80:
                    update.setdefault("is_on", True)
                    attrs.setdefault("PowerOn", "true")
            elif row.command == 0x04:
                raw_volume = row.data[0]
                volume = _volume_from_raw(raw_volume)
                update["volume"] = volume
                attrs["Volume"] = str(volume)
                attrs["rawVolume"] = str(raw_volume)
            elif row.command == 0x05:
                bass = _signed_range_from_raw(row.data[0], lower=-12, upper=12)
                update["bass"] = bass
                attrs["Bass"] = str(bass)
            elif row.command == 0x06:
                treble = _signed_range_from_raw(row.data[0], lower=-12, upper=12)
                update["treble"] = treble
                attrs["Treble"] = str(treble)
            elif row.command == 0x07:
                balance = _signed_range_from_raw(row.data[0], lower=-20, upper=20)
                update["balance"] = balance
                attrs["Balance"] = str(balance)
            elif row.command == 0x0C:
                loudness = row.data[0] == 0x01
                update["loudness"] = loudness
                attrs["Loudness"] = "true" if loudness else "false"
            elif row.command == 0x0D:
                max_volume = _volume_from_raw(row.data[0])
                update["max_volume"] = max_volume
                attrs["MaxVolume"] = str(max_volume)
                attrs["rawMaxVolume"] = str(row.data[0])
                if len(row.data) > 1:
                    attrs["maxVolumeStatusData"] = "".join(_byte(value) for value in row.data)
            elif row.command == 0x31:
                delay_ms = row.data[0] * 5
                update["delay_ms"] = delay_ms
                attrs["DelayMs"] = str(delay_ms)
                if len(row.data) > 1:
                    source_delays = self.parse_source_delays(row)
                    attrs["sourceDelayData"] = "".join(_byte(value) for value in row.data)
                    attrs["sourceDelayMsBySource"] = ",".join(
                        f"{delay.source_id}:{delay.delay_ms}" for delay in source_delays
                    )
            elif row.command == 0x44:
                gain = _signed_range_from_raw(row.data[0], lower=-12, upper=12)
                update["gain"] = gain
                attrs["Gain"] = str(gain)

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
            self.build_query_command("05", ALL_OUTPUTS),
            self.build_query_command("06", ALL_OUTPUTS),
            self.build_query_command("07", ALL_OUTPUTS),
            self.build_query_command("0C", ALL_OUTPUTS),
            self.build_query_command("0D", ALL_OUTPUTS),
            self.build_query_command("31", ALL_OUTPUTS),
            self.build_query_command("44", ALL_OUTPUTS),
        ]
        return self.poll(commands)

    def _read_required_output_max_volume(self, output: AmplifierOutputRef) -> float:
        for row in self.query_output_max_volume(output):
            if row.output not in (None, 0xFF) and row.data:
                return _volume_from_raw(row.data[0])
        raise ProtocolError(f"No max-volume response for output {output_ref(output)!r}")

    def _read_required_signed_output_value(
        self,
        output: AmplifierOutputRef,
        command: int | str,
        *,
        lower: int,
        upper: int,
        name: str,
    ) -> int:
        for row in self.poll(self.build_query_command(command, output_ref(output))):
            if row.output not in (None, 0xFF) and row.data:
                return _signed_range_from_raw(row.data[0], lower=lower, upper=upper)
        raise ProtocolError(f"No {name.lower()} response for output {output_ref(output)!r}")

    def _read_required_output_delay(self, output: AmplifierOutputRef) -> int:
        for row in self.query_output_delay(output):
            if row.output not in (None, 0xFF) and row.data:
                return row.data[0] * 5
        raise ProtocolError(f"No delay response for output {output_ref(output)!r}")


def _source_name_attrs(source_name: AmplifierSourceName | None) -> dict[str, str]:
    if source_name is None:
        return {}
    attrs = {
        "discovered": "true",
        "logicalAddress": f"{source_name.logical_source:02X}",
    }
    if source_name.output is not None:
        attrs["sourceOutput"] = str(source_name.output)
    if source_name.short_name:
        attrs["shortName"] = source_name.short_name
    if source_name.hidden_name:
        attrs["hiddenName"] = source_name.hidden_name
    return attrs
