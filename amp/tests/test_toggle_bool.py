import pytest

from amp.byte_utils import HexBytes
from amp.exceptions import ParseUnderflowError
from amp.toggle_bool import ToggleBool


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (ToggleBool.Off, "Off"),
        (ToggleBool.On, "On"),
        (ToggleBool.Toggle, "Toggle"),
    ],
)
def test_toggle_bool_string_forms(value: ToggleBool, text: str) -> None:
    assert str(value) == text
    assert repr(value) == text


@pytest.mark.parametrize(
    ("value", "power", "mute"),
    [
        (ToggleBool.Off, "00", "01"),
        (ToggleBool.On, "01", "00"),
        (ToggleBool.Toggle, "04", "02"),
    ],
)
def test_toggle_bool_wire_encodings(value: ToggleBool, power: str, mute: str) -> None:
    assert value.as_power() == HexBytes(power)
    assert value.as_mute() == HexBytes(mute)


@pytest.mark.parametrize(
    ("wire_value", "expected"),
    [
        (0x00, ToggleBool.Off),
        (0x01, ToggleBool.On),
        (0x04, ToggleBool.Toggle),
        (HexBytes("00"), ToggleBool.Off),
        (HexBytes("01"), ToggleBool.On),
        (HexBytes("04"), ToggleBool.Toggle),
    ],
)
def test_toggle_bool_consumes_ints_and_single_byte_hex(
    wire_value: int | HexBytes, expected: ToggleBool
) -> None:
    assert ToggleBool.consume(wire_value) is expected


@pytest.mark.parametrize(
    ("wire_value", "expected"),
    [
        (0x00, ToggleBool.On),
        (0x01, ToggleBool.Off),
        (0x02, ToggleBool.Toggle),
        (HexBytes("00"), ToggleBool.On),
        (HexBytes("01"), ToggleBool.Off),
        (HexBytes("02"), ToggleBool.Toggle),
    ],
)
def test_toggle_bool_consumes_mute_values_with_inverted_polarity(
    wire_value: int | HexBytes, expected: ToggleBool
) -> None:
    assert ToggleBool.consume(wire_value, for_mute=True) is expected


def test_toggle_bool_rejects_multi_byte_hex() -> None:
    with pytest.raises(ParseUnderflowError, match="single byte"):
        ToggleBool.consume(HexBytes("0001"))


def test_toggle_bool_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="invalid power value"):
        ToggleBool.consume(0x02)
    with pytest.raises(ValueError, match="invalid mute value"):
        ToggleBool.consume(0x04, for_mute=True)
