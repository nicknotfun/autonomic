# Direct amplifier wire-format encoding, decoding, and value scaling helpers.
from __future__ import annotations

import re
from collections.abc import Iterable

from .amplifier_types import AmplifierResponse

AMPLIFIER_DIAGNOSTIC_PORT = 17037
AMPLIFIER_HTTP_PORT = 80
DEVICE_ID_COMMAND = "2FFF"
ALL_OUTPUTS = "FF"
AMPLIFIER_RAW_MAX_VOLUME = 0xA0
REMOTE_SOURCE_START = 0x20
REMOTE_SOURCE_COUNT = 32

# Local physical inputs are exposed through a non-linear selector table on the
# direct amplifier wire protocol. Keep this table in one place so read/write
# paths and synthetic high-level IDs stay in lockstep.
DECODE_MATRIX_SOURCE: dict[int, int] = {
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
ENCODE_MATRIX_SOURCE: dict[int, int] = {value: key for key, value in DECODE_MATRIX_SOURCE.items()}
SOURCE_TO_DATA: dict[int, str] = {
    1: "05",
    2: "06",
    3: "07",
    4: "03",
    5: "00",
    6: "01",
    7: "02",
    8: "04",
    9: "08",
    10: "09",
    11: "0A",
    12: "0B",
    **{source: f"{source:02X}" for source in range(REMOTE_SOURCE_START, REMOTE_SOURCE_START + REMOTE_SOURCE_COUNT)},
}


def byte(value: int | str) -> str:
    if isinstance(value, int):
        if not 0 <= value <= 0xFF:
            raise ValueError("byte values must be between 0 and 255")
        return f"{value:02X}"

    normalized = value.upper()
    if not re.fullmatch(r"[0-9A-F]{1,2}", normalized):
        raise ValueError(f"invalid byte value: {value!r}")
    return normalized.zfill(2)


def data_bytes(data: int | str | Iterable[int | str]) -> str:
    if isinstance(data, str):
        normalized = data.upper()
        if re.fullmatch(r"[0-9A-F]+", normalized) and len(normalized) % 2 == 0:
            return normalized
        return byte(normalized)
    if isinstance(data, int):
        return byte(data)
    return "".join(byte(value) for value in data)


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


def source_data(source: int) -> str:
    """Return the direct amplifier source selector byte for a physical source."""

    try:
        return SOURCE_TO_DATA[int(source)]
    except ValueError as exc:
        raise ValueError("source must be an integer") from exc
    except KeyError as exc:
        raise ValueError("source must be a local source from 1-12 or remote source from 32-63") from exc


def encode_matrix_source(source: int) -> str:
    return byte(ENCODE_MATRIX_SOURCE.get(int(source), int(source)))


def decode_matrix_source(source_data: int | str) -> int:
    return DECODE_MATRIX_SOURCE.get(int(source_data), int(source_data))


def volume_to_raw(volume: float) -> int:
    value = float(volume)
    if not 0 <= value <= 100:
        raise ValueError("volume must be between 0 and 100")
    return int(round((value * AMPLIFIER_RAW_MAX_VOLUME) / 100))


def volume_from_raw(raw_volume: int) -> int:
    raw = max(0, min(AMPLIFIER_RAW_MAX_VOLUME, int(raw_volume)))
    return int((raw * 100) / AMPLIFIER_RAW_MAX_VOLUME)


def input_gain_to_raw(raw_gain: int) -> int:
    return validate_range(raw_gain, lower=0, upper=18, name="raw input gain")


def input_gain_to_raw_percent(gain_percent: int) -> int:
    percent = validate_range(gain_percent, lower=0, upper=100, name="input gain")
    return int((percent * 18) / 100)


def input_gain_percent_from_raw(raw_gain: int) -> int:
    raw = max(0, min(18, int(raw_gain)))
    return int((raw * 100) / 18)


def validate_range(value: int, *, lower: int, upper: int, name: str) -> int:
    number = int(value)
    if not lower <= number <= upper:
        raise ValueError(f"{name} must be between {lower} and {upper}")
    return number


def signed_range_to_raw(value: int, *, lower: int, upper: int) -> int:
    number = validate_range(value, lower=lower, upper=upper, name="value")
    return number if number >= 0 else 256 + number


def signed_range_from_raw(raw_value: int, *, lower: int, upper: int) -> int:
    value = int(raw_value)
    if 0 <= value <= upper:
        return value
    if value >= 256 + lower:
        return value - 256
    return max(lower, min(upper, value))


def delay_to_raw(delay_ms: int) -> int:
    value = validate_range(delay_ms, lower=0, upper=600, name="delay")
    if value % 5:
        raise ValueError("delay must be a multiple of 5 milliseconds")
    return value // 5


def delay_step(delay_ms: int) -> int:
    value = validate_range(delay_ms, lower=0, upper=600, name="delay step")
    if value % 5:
        raise ValueError("delay step must be a multiple of 5 milliseconds")
    return value


def power_data(state: bool | str) -> str:
    if isinstance(state, bool):
        return "01" if state else "00"
    normalized = state.strip().lower()
    if normalized in {"true", "on", "1", "yes"}:
        return "01"
    if normalized in {"false", "off", "0", "no"}:
        return "00"
    if normalized == "toggle":
        return "04"
    raise ValueError("power state must be true/on, false/off, or toggle")


def bool_data(state: bool | str) -> str:
    if isinstance(state, bool):
        return "01" if state else "00"
    normalized = state.strip().lower()
    if normalized in {"true", "on", "1", "yes", "enabled"}:
        return "01"
    if normalized in {"false", "off", "0", "no", "disabled"}:
        return "00"
    raise ValueError("state must be true/on or false/off")


def decoded_output_ref(output: int | str) -> int | None:
    try:
        return decode_output_address(encode_output_address(output))
    except (TypeError, ValueError):
        return None


def encode_output_address(output: int | str) -> int:
    # Stacked direct amplifiers extend outputs by setting high address bits:
    # 32-63 use 0x80..0x9F and 64-95 use 0xC0..0xDF. Output 96 is encoded as 0.
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


def output_address(output: int | str) -> str:
    if isinstance(output, str) and output.lower() == "all":
        return ALL_OUTPUTS
    if isinstance(output, int):
        return byte(encode_output_address(output))
    return byte(output)


def output_or_none(output: int | None) -> int | None:
    if output in (None, 0xFF):
        return None
    return output


def logical_to_physical_source(logical_source: int) -> int:
    if logical_source >= REMOTE_SOURCE_START:
        return logical_source
    return decode_matrix_source(logical_source) + 1


def decode_text(values: Iterable[int]) -> str:
    data = bytes(values)
    return data.decode("utf-8", errors="replace").replace("\x00", "").strip()


def text_to_hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def metadata_position(position: int) -> int:
    value = int(position)
    if not 0 <= value <= 3:
        raise ValueError("source metadata position must be between 0 and 3")
    return value


def amp_id(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    if len(normalized) != 4:
        raise ValueError("amp_id must contain 4 hexadecimal characters")
    return normalized


def guid_from_wire(raw_guid: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", raw_guid).upper()
    if len(normalized) != 32:
        raise ValueError("wire GUID must contain 32 hexadecimal characters")
    first = reverse_byte_pairs(normalized[0:8])
    second = reverse_byte_pairs(normalized[8:12])
    third = reverse_byte_pairs(normalized[12:16])
    return f"{first}-{second}-{third}-{normalized[16:20]}-{normalized[20:32]}".lower()


def guid_to_wire(guid: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", guid).upper()
    if len(normalized) != 32:
        raise ValueError("GUID must contain 32 hexadecimal characters")
    return (
        reverse_byte_pairs(normalized[0:8])
        + reverse_byte_pairs(normalized[8:12])
        + reverse_byte_pairs(normalized[12:16])
        + normalized[16:20]
        + normalized[20:32]
    )


def reverse_byte_pairs(value: str) -> str:
    return "".join(value[index : index + 2] for index in range(len(value) - 2, -1, -2))


def ip_from_bytes(values: list[int]) -> str:
    if len(values) != 4:
        return ""
    return ".".join(str(value) for value in values)


def zones_from_member_masks(mask_bytes: list[int]) -> tuple[int, ...]:
    zones: list[int] = []
    for mask_index, mask in enumerate(reversed(mask_bytes)):
        for bit in range(8):
            if mask & (1 << bit):
                zones.append(mask_index * 8 + bit + 1)
    return tuple(zones)


def slots_from_bitmap(bitmap: list[int]) -> tuple[int, ...]:
    slots: list[int] = []
    for byte_index, value in enumerate(bitmap):
        for bit in range(8):
            if value & (1 << bit):
                slots.append(byte_index * 8 + bit + 1)
    return tuple(slots)
