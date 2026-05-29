from uuid import UUID

import pytest

from amp.byte_utils import HexBytes, assert_is_byte


@pytest.mark.parametrize("value", [0, 1, 255])
def test_assert_is_byte_accepts_byte_values(value: int):
    assert assert_is_byte(value) == value


@pytest.mark.parametrize("value", [-1, 256])
def test_assert_is_byte_rejects_out_of_range_values(value: int):
    with pytest.raises(ValueError, match="between 0 and 255"):
        assert_is_byte(value)


def test_hex_bytes_accepts_hex_strings_bytes_and_ints():
    assert HexBytes(" 0aFf ") == bytes([0x0A, 0xFF])
    assert HexBytes(b"\x01\x02") == bytes([1, 2])
    assert HexBytes(0x2A) == bytes([0x2A])


def test_hex_bytes_formats_as_uppercase_hex():
    value = HexBytes("0aff")

    assert str(value) == "0AFF"
    assert repr(value) == "0AFF"


def test_hex_bytes_integer_helpers_support_signed_and_unsigned_values():
    assert HexBytes("0100").int() == 256
    assert HexBytes("FF").int(signed=True) == -1
    assert HexBytes.from_int(256, length=2) == HexBytes("0100")
    assert HexBytes.from_int(-1, length=1, signed=True) == HexBytes("FF")


def test_hex_bytes_uuid_helpers_support_rfc_and_guid_wire_order():
    value = UUID("674e1900-f8a9-f6be-a465-3d0fbee12977")

    assert HexBytes.from_uuid(value) == HexBytes(value.bytes)
    assert HexBytes.from_uuid(value, little_endian_fields=True) == HexBytes(
        value.bytes_le
    )
    assert HexBytes(value.bytes).uuid() == value
    assert HexBytes(value.bytes_le).uuid(little_endian_fields=True) == value


def test_hex_bytes_utf8_helpers_round_trip_text():
    assert HexBytes.from_utf8("Kitchen") == HexBytes("4B69746368656E")
    assert HexBytes("4B69746368656E").utf8() == "Kitchen"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("0", "even length"),
        ("zz", "hexadecimal string"),
        (256, "between 0 and 255"),
    ],
)
def test_hex_bytes_rejects_invalid_values(value: str | int, message: str):
    with pytest.raises(ValueError, match=message):
        HexBytes(value)


def test_hex_bytes_helpers_reject_invalid_conversion_values():
    with pytest.raises(ValueError, match="expected int"):
        HexBytes.from_int("1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="out of range"):
        HexBytes.from_int(256)
    with pytest.raises(ValueError, match="16 bytes"):
        HexBytes("00").uuid()
    with pytest.raises(ValueError, match="expected UUID"):
        HexBytes.from_uuid("not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="expected str"):
        HexBytes.from_utf8(b"not-text")  # type: ignore[arg-type]
