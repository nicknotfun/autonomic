# Parsers that fold raw direct-amplifier discovery rows into device records.
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .amplifier_codec import byte, decode_output_address, guid_from_wire, ip_from_bytes
from .amplifier_types import AmplifierDeviceInfo, AmplifierNetworkInfo, AmplifierResponse
from .hardware import model_name_for_byte


@dataclass
class MutableDeviceInfo:
    amp_id: str
    model_byte: int | None = None
    zones: tuple[int, ...] = ()
    mac: str | None = None
    raw_guid: str | None = None
    system_id: int | None = None
    network: AmplifierNetworkInfo | None = None
    raw_lines: list[str] = field(default_factory=list)


def device_info_from_rows(
    rows: Iterable[AmplifierResponse],
    *,
    existing: dict[str, MutableDeviceInfo] | None = None,
) -> dict[str, MutableDeviceInfo]:
    devices = existing or {}
    for row in rows:
        # Device discovery is pieced together from several response families:
        # AF/94 identify IDs and zones, B9 carries MAC, and 3A sub-rows carry
        # network, GUID, and system ID fields.
        if row.command == 0xAF and len(row.data) >= 2:
            amp_id = "".join(byte(value) for value in row.data[:2])
            device = devices.setdefault(amp_id, MutableDeviceInfo(amp_id=amp_id))
            if len(row.data) > 2:
                device.zones = tuple(decode_output_address(value) or value for value in row.data[2:])
            device.raw_lines.append(row.raw)
        elif row.command == 0x94 and len(row.data) >= 5:
            amp_id = "".join(byte(value) for value in row.data[3:5])
            device = devices.setdefault(amp_id, MutableDeviceInfo(amp_id=amp_id))
            device.model_byte = row.data[2]
            if len(row.data) > 5:
                device.zones = tuple(decode_output_address(value) or value for value in row.data[5:])
            device.raw_lines.append(row.raw)
        elif row.command == 0xB9:
            line = row.raw.upper()
            if len(line) >= 42:
                amp_id = line[8:12]
                device = devices.setdefault(amp_id, MutableDeviceInfo(amp_id=amp_id))
                device.mac = line[30:42]
                device.raw_lines.append(row.raw)
        elif row.command == 0x3A and len(row.data) >= 3:
            _merge_device_sub_info(row, devices)
    return devices


def finalize_device_infos(device_map: dict[str, MutableDeviceInfo]) -> list[AmplifierDeviceInfo]:
    devices: list[AmplifierDeviceInfo] = []
    for amp_id, mutable in device_map.items():
        devices.append(
            AmplifierDeviceInfo(
                amp_id=amp_id,
                model_byte=mutable.model_byte,
                model_name=model_name_for_byte(mutable.model_byte),
                zones=mutable.zones,
                mac=mutable.mac,
                guid=guid_from_wire(mutable.raw_guid) if mutable.raw_guid else None,
                raw_guid=mutable.raw_guid,
                system_id=mutable.system_id,
                network=mutable.network,
                raw_lines=tuple(mutable.raw_lines),
            )
        )
    return sorted(devices, key=lambda device: (device.zones[0] if device.zones else 999, device.amp_id))


def _merge_device_sub_info(row: AmplifierResponse, devices: dict[str, MutableDeviceInfo]) -> None:
    amp_id = "".join(byte(value) for value in row.data[:2])
    response_type = row.data[2]
    payload = row.data[3:]
    device = devices.setdefault(amp_id, MutableDeviceInfo(amp_id=amp_id))
    if response_type == 0x03 and len(payload) >= 17:
        flags = payload[0]
        device.network = AmplifierNetworkInfo(
            amp_id=amp_id,
            dhcp=bool(flags & 0x01),
            ovrc_connected=bool(flags & 0x08),
            ip_address=ip_from_bytes(payload[1:5]),
            subnet_mask=ip_from_bytes(payload[5:9]),
            dns=ip_from_bytes(payload[9:13]),
            gateway=ip_from_bytes(payload[13:17]),
        )
    elif response_type == 0x05 and len(payload) >= 16:
        device.raw_guid = "".join(byte(value) for value in payload[:16])
    elif response_type == 0x06 and payload:
        device.system_id = payload[0]
    device.raw_lines.append(row.raw)
