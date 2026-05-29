"""Message pattern parsing and emitting according to a custom pattern syntax.

Exists just to make codec.py much cleaner as a description of the protocol.
"""

from abc import ABC
import logging
import re
from typing import Any, Generic, TypeVar
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.exceptions import ParseUnderflowError
from amp.types import ToggleBool

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MessageParseStep(ABC, Generic[T]):
    """Represents a single step in parsing or emitting a message according to a MessagePattern."""

    def consume(self, input: HexBytes) -> tuple[T, int]: ...
    def emit(self, value: T) -> HexBytes: ...


class FixedParseStep(MessageParseStep[HexBytes]):
    """A parse step that matches a fixed sequence of bytes."""

    def __init__(self, *, expected: HexBytes) -> None:
        self.expected = expected

    def consume(self, input: HexBytes) -> tuple[HexBytes, int]:
        if not input.startswith(self.expected):
            raise ParseUnderflowError(f"expected {self.expected!r} but got {input!r}")
        return self.expected, len(self.expected)

    def emit(self, value: HexBytes | None) -> HexBytes:
        if value is not None and value != self.expected:
            raise ValueError(f"expected {self.expected!r} but got {value!r}")
        return self.expected


class HexParseStep(MessageParseStep[HexBytes]):
    """A parse step that consumes a fixed number of bytes and stores them in a field."""

    def __init__(self, *, length: int) -> None:
        self.length = length

    def consume(self, input: HexBytes) -> tuple[HexBytes, int]:
        if len(input) < self.length:
            raise ParseUnderflowError("not enough input to parse hex value")
        return HexBytes(input[: self.length]), self.length

    def emit(self, value: HexBytes) -> HexBytes:
        if not isinstance(value, bytes):
            raise ValueError(f"expected bytes value but got {value!r}")
        if len(value) != self.length:
            raise ValueError(f"expected bytes of length {self.length} but got {value!r}")
        return HexBytes(value)


class ToggleBoolParseStep(MessageParseStep[ToggleBool]):
    """A parse step that consumes a single byte and interprets it as a ToggleBool value."""

    def __init__(self, *, for_mute: bool) -> None:
        self.for_mute = for_mute

    def consume(self, input: HexBytes) -> tuple[ToggleBool, int]:
        if len(input) < 1:
            raise ParseUnderflowError("not enough input to parse toggle bool value")
        return ToggleBool.consume(input[0], for_mute=self.for_mute), 1

    def emit(self, value: ToggleBool) -> HexBytes:
        if not isinstance(value, ToggleBool):
            raise ValueError(f"expected ToggleBool value but got {value!r}")
        if self.for_mute:
            return value.as_mute()
        return value.as_power()


class BoolParseStep(MessageParseStep[bool]):
    """A parse step that consumes a single byte and interprets it as a boolean value."""

    def consume(self, input: HexBytes) -> tuple[bool, int]:
        if len(input) < 1:
            raise ParseUnderflowError("not enough input to parse bool value")
        match input[0]:
            case 0x00:
                return False, 1
            case 0x01:
                return True, 1
            case _:
                raise ValueError(f"invalid value for bool: {input[0]}")

    def emit(self, value: bool) -> HexBytes:
        if not isinstance(value, bool):
            raise ValueError(f"expected bool value but got {value!r}")
        return HexBytes.from_int(0x01 if value else 0x00)


class UUIDParseStep(MessageParseStep[UUID]):
    """A parse step that consumes 16 bytes and interprets them as a UUID value."""

    def __init__(self, *, little_endian_fields: bool = False) -> None:
        self.little_endian_fields = little_endian_fields

    def consume(self, input: HexBytes) -> tuple[UUID, int]:
        if len(input) < 16:
            raise ParseUnderflowError("not enough input to parse UUID value")
        return (
            HexBytes(input[:16]).uuid(little_endian_fields=self.little_endian_fields),
            16,
        )

    def emit(self, value: UUID) -> HexBytes:
        return HexBytes.from_uuid(value, little_endian_fields=self.little_endian_fields)


class RawParseStep(MessageParseStep[HexBytes]):
    """A parse step that consumes all remaining bytes."""

    def consume(self, input: HexBytes) -> tuple[HexBytes, int]:
        return HexBytes(input), len(input)

    def emit(self, value: HexBytes) -> HexBytes:
        if not isinstance(value, bytes):
            raise ValueError(f"expected bytes value but got {value!r}")
        return HexBytes(value)


class StringParseStep(MessageParseStep[str]):
    """A parse step that consumes a full UTF-8 string."""

    def __init__(self, *, has_length_prefix: bool = False) -> None:
        self.has_length_prefix = has_length_prefix

    def consume(self, input: HexBytes) -> tuple[str, int]:
        if self.has_length_prefix:
            if len(input) < 1:
                raise ParseUnderflowError("not enough input to parse length prefix for string")
            length = input[0]
            if length >= 32:
                raise ParseUnderflowError("string value is too long to parse with length prefix")
            if len(input) < 1 + length:
                raise ParseUnderflowError(
                    f"not enough input to parse string value, expected length prefix of {length} but got only {len(input) - 1} bytes"
                )
            return HexBytes(input[1 : 1 + length]).utf8(), 1 + length
        else:
            return HexBytes(input).utf8(), len(input)

    def emit(self, value: str) -> HexBytes:
        encoded = HexBytes.from_utf8(value)
        if self.has_length_prefix:
            if len(encoded) >= 32:
                raise ValueError("string value is too long to encode with length prefix")
            return HexBytes.from_int(len(encoded)) + encoded
        else:
            return encoded


class IntParseStep(MessageParseStep[int]):
    """A parse step that consumes a fixed number of bytes and interprets them as an integer."""

    def __init__(self, *, length: int) -> None:
        self.length = length

    def consume(self, input: HexBytes) -> tuple[int, int]:
        if len(input) < self.length:
            raise ParseUnderflowError("not enough input to parse int value")
        return HexBytes(input[: self.length]).int(), self.length

    def emit(self, value: int) -> HexBytes:
        if not isinstance(value, int):
            raise ValueError(f"expected int value but got {value!r}")
        return HexBytes.from_int(value, length=self.length)


class SignedIntParseStep(MessageParseStep[int]):
    """A parse step that consumes a fixed number of signed bytes."""

    def __init__(self, *, length: int) -> None:
        self.length = length

    def consume(self, input: HexBytes) -> tuple[int, int]:
        if len(input) < self.length:
            raise ParseUnderflowError("not enough input to parse signed int value")
        return HexBytes(input[: self.length]).int(signed=True), self.length

    def emit(self, value: int) -> HexBytes:
        if not isinstance(value, int):
            raise ValueError(f"expected int value but got {value!r}")
        return HexBytes.from_int(value, length=self.length, signed=True)


class FloatParseStep(MessageParseStep[float]):
    def __init__(self, *, length: int, min_value: float, max_value: float, int_max: int) -> None:
        self.int_parse_step = IntParseStep(length=length)
        self.min_value = min_value
        self.max_value = max_value
        self.int_max = int_max

    def consume(self, input: HexBytes) -> tuple[float, int]:
        int_value, consumed = self.int_parse_step.consume(input)
        value = self.min_value + (self.max_value - self.min_value) * int_value / self.int_max
        return value, consumed

    def emit(self, value: float) -> HexBytes:
        if isinstance(value, int):
            value = float(value)
        if not isinstance(value, float):
            raise ValueError(f"expected float value but got {value!r}")
        if value < self.min_value or value > self.max_value:
            raise ValueError(
                f"float value {value} is out of range [{self.min_value}, {self.max_value}]"
            )
        int_value = round(
            (value - self.min_value) / (self.max_value - self.min_value) * self.int_max
        )
        return self.int_parse_step.emit(int_value)


class OptionalParseStep(MessageParseStep[T | None]):
    """A parse step that wraps another step and makes it optional."""

    def __init__(self, *, sub_pattern: MessageParseStep[T]) -> None:
        self.sub_pattern = sub_pattern

    def consume(self, input: HexBytes) -> tuple[T | None, int]:
        if not input:
            return None, 0
        try:
            return self.sub_pattern.consume(input)
        except ParseUnderflowError:
            return None, 0
        except ValueError:
            raise

    def emit(self, value: T | None) -> HexBytes:
        if value is None:
            return HexBytes(b"")
        return self.sub_pattern.emit(value)


class RepeatParseStep(MessageParseStep[list[T]]):
    """A parse step that repeats a sub-pattern a variable number of times until the input is exhausted."""

    def __init__(self, *, sub_pattern: MessageParseStep[T], min_repeats: int) -> None:
        self.sub_pattern = sub_pattern
        self.min_repeats = min_repeats

    def consume(self, input: HexBytes) -> tuple[list[T], int]:
        values: list[T] = []
        total_consumed = 0
        while total_consumed < len(input):
            try:
                value, consumed = self.sub_pattern.consume(input[total_consumed:])
                if consumed == 0:
                    break
                values.append(value)
                total_consumed += consumed
            except ParseUnderflowError:
                break
        if len(values) < self.min_repeats:
            raise ParseUnderflowError(
                f"expected at least {self.min_repeats} repetitions but got {len(values)}"
            )
        return values, total_consumed

    def emit(self, values: list[T]) -> HexBytes:
        if not isinstance(values, list):
            raise ValueError(f"expected list value but got {values!r}")
        output = HexBytes(b"")
        for value in values:
            output += self.sub_pattern.emit(value)
        return output


class MessagePattern:
    """Represents a pattern for parsing and emitting messages, defined by a string with placeholders."""

    def __init__(self, pattern: str) -> None:
        if pattern.endswith("!"):
            pattern = pattern[:-1]
            self.consumes_all = True
        else:
            self.consumes_all = False
        self.raw = pattern
        self.steps = self._compile(pattern)

    def _compile_step(self, *, type_spec: str, optional: bool = False) -> MessageParseStep:
        if type_spec.startswith("="):
            return FixedParseStep(expected=HexBytes(type_spec[1:]))
        elif type_spec == "power_bool":
            return ToggleBoolParseStep(for_mute=False)
        elif type_spec == "mute_bool":
            return ToggleBoolParseStep(for_mute=True)
        elif type_spec == "bool":
            return BoolParseStep()
        elif type_spec == "float":
            return FloatParseStep(length=4, min_value=0.0, max_value=1.0, int_max=255)
        elif type_spec == "utf8":
            return StringParseStep()
        elif type_spec == "lenutf8":
            return StringParseStep(has_length_prefix=True)
        elif type_spec == "hex":
            return RawParseStep()
        elif re.match(r"^\d+N$", type_spec) or type_spec == "N":
            length = int(type_spec[:-1]) if type_spec != "N" else 2
            if length % 2 != 0:
                raise ValueError(
                    f"hex byte length must be a multiple of 2 but got {length} in type spec: {type_spec}"
                )
            length //= 2
            return IntParseStep(length=length)
        elif re.match(r"^\d+X$", type_spec) or type_spec == "X":
            length = int(type_spec[:-1]) if type_spec != "X" else 2
            if length % 2 != 0:
                raise ValueError(
                    f"hex byte length must be a multiple of 2 but got {length} in type spec: {type_spec}"
                )
            length //= 2
            return HexParseStep(length=length)
        elif re.match(r"^\d+S$", type_spec) or type_spec == "S":
            length = int(type_spec[:-1]) if type_spec != "S" else 2
            if length % 2 != 0:
                raise ValueError(
                    f"hex byte length must be a multiple of 2 but got {length} in type spec: {type_spec}"
                )
            length //= 2
            return SignedIntParseStep(length=length)
        elif type_spec == "uuid":
            return UUIDParseStep()
        elif type_spec == "guid":
            return UUIDParseStep(little_endian_fields=True)

        float_spec = re.match(
            r"^float\((?P<int_max>\d+),(?P<min_value>[-.\d]+),(?P<max_value>[-.\d]+)\)$", type_spec
        )
        if float_spec:
            int_max = int(float_spec.group("int_max"))
            min_value = float(float_spec.group("min_value"))
            max_value = float(float_spec.group("max_value"))
            length = (int_max.bit_length() + 7) // 8
            return FloatParseStep(
                length=length, min_value=min_value, max_value=max_value, int_max=int_max
            )

        raise ValueError(f"unsupported type specifier in pattern: {type_spec}")

    def _compile(self, pattern: str) -> list[MessageParseStep]:
        steps: list[MessageParseStep] = []
        i = 0
        while i < len(pattern):
            if pattern[i] == "{":
                end = pattern.find("}", i)
                if end == -1:
                    raise ValueError(f"unmatched '{{' in pattern: {pattern}")
                field_spec = pattern[i + 1 : end]

                if ":" in field_spec:
                    field_name, type_spec = field_spec.split(":", 1)
                else:
                    field_name, type_spec = field_spec, "2X"

                field_name = field_name.strip()
                type_spec = type_spec.strip()

                if not field_name:
                    field_name = None
                elif not field_name.isidentifier():
                    raise ValueError(f"invalid field name in pattern: {field_name!r}")

                if type_spec.endswith("?"):
                    optional = True
                    type_spec = type_spec[:-1]
                else:
                    optional = False

                if type_spec.endswith(("+", "*")):
                    repeat_type_spec = type_spec[-1]
                    type_spec = type_spec[:-1]
                else:
                    repeat_type_spec = None

                step = self._compile_step(type_spec=type_spec)
                match repeat_type_spec:
                    case "+":
                        step = RepeatParseStep(sub_pattern=step, min_repeats=1)
                    case "*":
                        step = RepeatParseStep(sub_pattern=step, min_repeats=0)

                if optional:
                    step = OptionalParseStep(sub_pattern=step)

                steps.append((field_name, step))
                i = end + 1
            else:
                j = pattern.find("{", i)
                if j == -1:
                    j = len(pattern)
                steps.append((None, FixedParseStep(expected=HexBytes(pattern[i:j]))))
                i = j
        return steps

    def parse(self, input: bytes) -> dict[str, Any]:
        target: dict[str, Any] = {}
        total_consumed = 0
        for field_name, step in self.steps:
            value, consumed = step.consume(input[total_consumed:])
            if field_name is not None:
                target[field_name] = value
            total_consumed += consumed
        if self.consumes_all and total_consumed != len(input):
            raise ValueError(f"extra unparsed input: {input[total_consumed:]!r}")
        return target

    def emit(self, message: object) -> HexBytes:
        output = HexBytes(b"")
        for field_name, step in self.steps:
            value = getattr(message, field_name) if field_name is not None else None
            output += step.emit(value)
        return HexBytes(output)


class PatternEncoder(Generic[T]):
    """An encoder that encodes messages of type T according to a specified MessagePattern."""

    def __init__(self, cls: type[T], pattern: str | None = None) -> None:
        self.cls = cls
        if pattern is None:
            pattern = getattr(cls, "PATTERN", None)
            if pattern is None:
                raise ValueError(
                    f"no pattern specified for class {cls.__name__} and no PATTERN class variable found"
                )
            if not isinstance(pattern, str):
                raise ValueError(
                    f"PATTERN class variable for class {cls.__name__} must be a string if present but got {pattern!r}"
                )
        try:
            self.compiled = MessagePattern(pattern)
        except ValueError:
            logger.exception(f"failed to compile pattern for class {cls.__name__}: {pattern}")
            raise

    @property
    def pattern(self) -> str:
        return self.compiled.raw

    def encode(self, message: T) -> HexBytes:
        return self.compiled.emit(message)

    def decode(self, data: bytes) -> T:
        parsed = self.compiled.parse(data)
        return self.cls(**parsed)


class SubclassEncoder(Generic[T]):
    """An encoder that encodes messages of type T by finding a subclass of Op with a matching pattern."""

    def __init__(self, target_type: type[T]) -> None:
        self.target_type = target_type
        self.encoders: list[PatternEncoder] = []
        self.discover_patterns()
        self.encoders = sorted(self.encoders, key=lambda encoder: encoder.pattern)
        for encoder in self.encoders:
            logger.debug("registered: %s -> %s", encoder.pattern, encoder.cls.__name__)

    def discover_patterns(self) -> None:
        for cls in self.target_type.__subclasses__():
            try:
                encoder = PatternEncoder(cls)
            except ValueError as e:
                logger.warning(
                    f"skipping class {cls.__name__} due to pattern compilation error: {e}"
                )
                continue
            self.encoders.append(encoder)

    def encode(self, message: T) -> HexBytes:
        for encoder in self.encoders:
            if type(message) is encoder.cls:
                return encoder.encode(message)
        raise ValueError(
            f"no matching pattern found to encode message of type {type(message).__name__}"
        )

    def decode(self, data: bytes) -> T:
        first_decoding: T | None = None
        errors: list[tuple[PatternEncoder, Exception]] = []
        for encoder in self.encoders:
            try:
                decoded = encoder.decode(data)
                if first_decoding is None:
                    first_decoding = decoded
                else:
                    logger.warning(
                        "multiple patterns matched for decoding data: %r, got %r and %r",
                        data,
                        first_decoding,
                        decoded,
                    )
            except Exception as e:
                errors.append((encoder, e))
                continue
        if first_decoding is None:
            for encoder, error in sorted(errors, key=lambda pair: pair[0].pattern):
                logger.warning(
                    "%r failed: %s",
                    encoder.pattern,
                    error,
                )
        return first_decoding
