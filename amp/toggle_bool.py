from enum import Enum

from amp.byte_utils import HexBytes
from amp.exceptions import ParseUnderflowError


class ToggleBool(Enum):
    Off = 0
    On = 1
    Toggle = 2

    def __str__(self) -> str:
        match self:
            case ToggleBool.Off:
                return "Off"
            case ToggleBool.On:
                return "On"
            case ToggleBool.Toggle:
                return "Toggle"

    def __repr__(self) -> str:
        return self.__str__()

    def as_power(self) -> HexBytes:
        match self:
            case ToggleBool.Off:
                return HexBytes("00")
            case ToggleBool.On:
                return HexBytes("01")
            case ToggleBool.Toggle:
                return HexBytes("04")

    def as_mute(self) -> HexBytes:
        match self:
            case ToggleBool.Off:
                return HexBytes("01")
            case ToggleBool.On:
                return HexBytes("00")
            case ToggleBool.Toggle:
                return HexBytes("02")

    def as_bool(self, existing: bool | None = None) -> bool | None:
        match self:
            case ToggleBool.Off:
                return False
            case ToggleBool.On:
                return True
            case ToggleBool.Toggle:
                if existing is None:
                    return None
                return not existing

    @staticmethod
    def consume(value: HexBytes | int, *, for_mute: bool = False) -> "ToggleBool":
        if isinstance(value, HexBytes):
            if len(value) != 1:
                raise ParseUnderflowError(
                    f"expected a single byte for ToggleBool value but got {value!r}"
                )
            value = value.int()
        if for_mute:
            match value:
                case 0x00:
                    return ToggleBool.On
                case 0x01:
                    return ToggleBool.Off
                case 0x02:
                    return ToggleBool.Toggle
                case _:
                    raise ValueError(f"invalid mute value for ToggleBool: {value}")
        else:
            match value:
                case 0x00:
                    return ToggleBool.Off
                case 0x01:
                    return ToggleBool.On
                case 0x04:
                    return ToggleBool.Toggle
                case _:
                    raise ValueError(f"invalid power value for ToggleBool: {value}")
