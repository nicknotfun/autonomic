from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest

from amp.byte_utils import HexBytes
from amp.encoder import MessagePattern, PatternEncoder, SubclassEncoder
from amp.exceptions import ParseUnderflowError
from amp.toggle_bool import ToggleBool


def test_message_pattern_parses_and_emits_core_types() -> None:
    pattern = MessagePattern(
        "AA{:=BB}{number:4N}{raw:2X}{signed:S}{enabled:bool}"
        "{power:power_bool}{mute:mute_bool}{plain:utf8}!"
    )
    message = SimpleNamespace(
        number=0x1234,
        raw=HexBytes("CC"),
        signed=-3,
        enabled=True,
        power=ToggleBool.Toggle,
        mute=ToggleBool.On,
        plain="Hi",
    )

    encoded = pattern.emit(message)

    assert str(encoded) == "AABB1234CCFD0104004869"
    assert pattern.parse(encoded) == {
        "number": 0x1234,
        "raw": HexBytes("CC"),
        "signed": -3,
        "enabled": True,
        "power": ToggleBool.Toggle,
        "mute": ToggleBool.On,
        "plain": "Hi",
    }


def test_message_pattern_parses_uuid_guid_float_lenutf8_and_repeats() -> None:
    value = UUID("674e1900-f8a9-f6be-a465-3d0fbee12977")
    pattern = MessagePattern("{normal:uuid}{wire:guid}{gain:float(160,0.0,1.0)}{name:lenutf8}{items:N*}!")
    message = SimpleNamespace(normal=value, wire=value, gain=0.5, name="Amp", items=(1, 2))

    encoded = pattern.emit(message)

    assert encoded[:16] == HexBytes(value.bytes)
    assert encoded[16:32] == HexBytes(value.bytes_le)
    assert str(HexBytes(encoded[32:])) == "5003416D700102"
    assert pattern.parse(encoded) == {
        "normal": value,
        "wire": value,
        "gain": 0.5,
        "name": "Amp",
        "items": (1, 2),
    }


def test_optional_fields_are_absent_only_on_underflow() -> None:
    pattern = MessagePattern("{enabled:bool?}!")

    assert pattern.parse(b"") == {"enabled": None}
    assert pattern.parse(HexBytes("01")) == {"enabled": True}
    with pytest.raises(ValueError, match="invalid value for bool"):
        pattern.parse(HexBytes("02"))


def test_plus_repeat_requires_at_least_one_value() -> None:
    pattern = MessagePattern("{items:N+}!")

    assert pattern.parse(HexBytes("0102")) == {"items": (1, 2)}
    with pytest.raises(ParseUnderflowError, match="at least 1"):
        pattern.parse(b"")
    with pytest.raises(ValueError, match="at least 1"):
        pattern.emit(SimpleNamespace(items=()))
    assert pattern.emit(SimpleNamespace(items=(1, 2))) == HexBytes("0102")


@pytest.mark.parametrize("encoded", ["A1", "FF"])
def test_float_decode_rejects_wire_values_above_the_declared_maximum(encoded: str) -> None:
    pattern = MessagePattern("{level:float(160,0.0,1.0)?}{detail:N*}!")

    with pytest.raises(ValueError, match="out of range"):
        pattern.parse(HexBytes(encoded))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_float_encode_rejects_nonfinite_values(value: float) -> None:
    with pytest.raises(ValueError, match="out of range"):
        MessagePattern("{level:float(160,0.0,1.0)}!").emit(SimpleNamespace(level=value))


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        ("{level:N?}{detail:N*}!", SimpleNamespace(level=None, detail=(80,))),
        ("{options:6X?}{name:utf8?}!", SimpleNamespace(options=None, name="TV")),
        ("{source:N?}{gain:N?}!", SimpleNamespace(source=None, gain=9)),
    ],
)
def test_emit_rejects_omitted_positional_fields_before_supplied_fields(
    pattern: str, message: SimpleNamespace
) -> None:
    with pytest.raises(ValueError, match="required before later fields"):
        MessagePattern(pattern).emit(message)


def test_emit_allows_only_unambiguous_omission_of_a_length_prefixed_string() -> None:
    pattern = MessagePattern("{hidden:lenutf8?}{name:utf8?}!")

    assert pattern.emit(SimpleNamespace(hidden=None, name="TV")) == HexBytes("5456")
    with pytest.raises(ValueError, match="required before later fields"):
        pattern.emit(SimpleNamespace(hidden=None, name="\x01X"))


def test_consumes_all_marker_rejects_extra_input() -> None:
    strict = MessagePattern("AA{value:N}!")
    loose = MessagePattern("AA{value:N}")

    assert loose.parse(HexBytes("AA0102")) == {"value": 1}
    with pytest.raises(ValueError, match="extra unparsed input"):
        strict.parse(HexBytes("AA0102"))


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        ("{", "unmatched"),
        ("{not-valid:N}", "invalid field name"),
        ("{value:bogus}", "unsupported type specifier"),
        ("{value:3N}", "multiple of 2"),
        ("{value:3X}", "multiple of 2"),
        ("{value:3S}", "multiple of 2"),
    ],
)
def test_message_pattern_rejects_invalid_patterns(pattern: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        MessagePattern(pattern)


@pytest.mark.parametrize(
    ("pattern", "message", "error"),
    [
        ("{value:N}!", SimpleNamespace(value=256), "out of range"),
        ("{value:S}!", SimpleNamespace(value=128), "out of range"),
        ("{value:X}!", SimpleNamespace(value=1), "expected bytes"),
        ("{value:bool}!", SimpleNamespace(value=1), "expected bool"),
        ("{value:utf8}!", SimpleNamespace(value=b"x"), "expected str"),
        ("{value:lenutf8}!", SimpleNamespace(value="x" * 32), "too long"),
        ("{value:N*}!", SimpleNamespace(value=1), "expected tuple"),
    ],
)
def test_message_pattern_validates_emitted_values(
    pattern: str, message: SimpleNamespace, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        MessagePattern(pattern).emit(message)


def test_pattern_encoder_uses_class_pattern_by_default() -> None:
    @dataclass(kw_only=True, frozen=True)
    class Example:
        PATTERN = "AA{value:N}!"

        value: int

    encoder = PatternEncoder(Example)

    assert encoder.pattern == "AA{value:N}"
    assert str(encoder.encode(Example(value=3))) == "AA03"
    assert encoder.decode(HexBytes("AA03")) == Example(value=3)


def test_pattern_encoder_accepts_explicit_pattern_and_validates_class_pattern() -> None:
    class NoPattern:
        pass

    class BadPattern:
        PATTERN = 1

    assert PatternEncoder(NoPattern, "AA").pattern == "AA"
    with pytest.raises(ValueError, match="no pattern specified"):
        PatternEncoder(NoPattern)
    with pytest.raises(ValueError, match="must be a string"):
        PatternEncoder(BadPattern)


def test_subclass_encoder_discovers_encodes_and_decodes_direct_subclasses() -> None:
    class Base:
        pass

    @dataclass(kw_only=True, frozen=True)
    class Alpha(Base):
        PATTERN = "AA{value:N}!"

        value: int

    @dataclass(kw_only=True, frozen=True)
    class Beta(Base):
        PATTERN = "BB{value:N}!"

        value: int

    encoder = SubclassEncoder(Base)

    assert str(encoder.encode(Alpha(value=1))) == "AA01"
    assert encoder.decode(HexBytes("BB02")) == Beta(value=2)
    assert encoder.decode(HexBytes("CC")) is None


def test_subclass_encoder_skips_subclasses_with_invalid_patterns() -> None:
    class Base:
        pass

    class Broken(Base):
        PATTERN = "{value:bogus}!"

    encoder = SubclassEncoder(Base)

    with pytest.raises(ValueError, match="no matching pattern"):
        encoder.encode(Broken())
