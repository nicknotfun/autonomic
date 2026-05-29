"""Utilities for working with bytes and hexadecimal strings."""

from uuid import UUID


def assert_is_byte(value: int) -> int:
    if not 0 <= value <= 255:
        raise ValueError("must be an integer between 0 and 255")
    return value


class HexBytes(bytes):
    def __new__(cls, value: str | bytes | int) -> "HexBytes":
        if isinstance(value, str):
            value = value.strip()
            if not all(c in "0123456789abcdefABCDEF" for c in value):
                raise ValueError("must be a hexadecimal string")
            if not len(value) % 2 == 0:
                raise ValueError("hexadecimal string must have an even length")
            value = bytes(int(value[i : i + 2], 16) for i in range(0, len(value), 2))
        elif isinstance(value, int):
            value = bytes([assert_is_byte(value)])
        return super().__new__(cls, value)

    def __str__(self) -> str:
        return "".join(f"{byte:02X}" for byte in self)

    def __repr__(self) -> str:
        return self.__str__()

    def int(self, *, signed: bool = False) -> int:
        return int.from_bytes(self, byteorder="big", signed=signed)

    @classmethod
    def from_int(
        cls, value: int, *, length: int = 1, signed: bool = False
    ) -> "HexBytes":
        if not isinstance(value, int):
            raise ValueError(f"expected int value but got {value!r}")
        if length < 1:
            raise ValueError("length must be at least 1")
        try:
            return cls(value.to_bytes(length, byteorder="big", signed=signed))
        except OverflowError as exc:
            raise ValueError(f"int value {value} is out of range for length {length}") from exc

    def uuid(self, *, little_endian_fields: bool = False) -> UUID:
        if len(self) != 16:
            raise ValueError(f"expected 16 bytes for UUID value but got {len(self)}")
        if little_endian_fields:
            return UUID(bytes_le=self)
        return UUID(bytes=self)

    @classmethod
    def from_uuid(cls, value: UUID, *, little_endian_fields: bool = False) -> "HexBytes":
        if not isinstance(value, UUID):
            raise ValueError(f"expected UUID value but got {value!r}")
        if little_endian_fields:
            return cls(value.bytes_le)
        return cls(value.bytes)

    def utf8(self) -> str:
        return self.decode("utf-8")

    @classmethod
    def from_utf8(cls, value: str) -> "HexBytes":
        if not isinstance(value, str):
            raise ValueError(f"expected str value but got {value!r}")
        return cls(value.encode("utf-8"))
