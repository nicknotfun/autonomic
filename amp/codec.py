"""Codec for encoding and decoding AMP operations."""

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.encoder import SubclassEncoder
from amp.toggle_bool import ToggleBool
from amp.transport import Transport

ALL_OUTPUTS = 0xFF


class Op:
    def is_write(self) -> bool:
        return True


@dataclass(kw_only=True, frozen=True)
class OutputOp(Op):
    output: int = ALL_OUTPUTS


@dataclass(kw_only=True, frozen=True)
class PowerOp(OutputOp):
    PATTERN: ClassVar[str] = "01{output:N}{is_on:power_bool?}!"

    is_on: ToggleBool | None = None

    def is_write(self) -> bool:
        return self.is_on is not None


@dataclass(kw_only=True, frozen=True)
class MuteOp(OutputOp):
    PATTERN: ClassVar[str] = "02{output:N}{is_muted:mute_bool?}!"

    is_muted: ToggleBool | None = None

    def is_write(self) -> bool:
        return self.is_muted is not None


@dataclass(kw_only=True, frozen=True)
class SourceSelectOp(OutputOp):
    PATTERN: ClassVar[str] = "03{output:N}{source:X?}{detail:N*}!"

    source: HexBytes | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.source is not None


@dataclass(kw_only=True, frozen=True)
class VolumeOp(OutputOp):
    PATTERN: ClassVar[str] = "04{output:N}{volume:float(160,0.0,1.0)?}{detail:N*}!"

    volume: float | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.volume is not None


@dataclass(kw_only=True, frozen=True)
class BassOp(OutputOp):
    PATTERN: ClassVar[str] = "05{output:N}{bass:S?}!"

    bass: int | None = None

    def is_write(self) -> bool:
        return self.bass is not None


@dataclass(kw_only=True, frozen=True)
class TrebleOp(OutputOp):
    PATTERN: ClassVar[str] = "06{output:N}{treble:S?}!"

    treble: int | None = None

    def is_write(self) -> bool:
        return self.treble is not None


@dataclass(kw_only=True, frozen=True)
class BalanceOp(OutputOp):
    PATTERN: ClassVar[str] = "07{output:N}{balance:S?}!"

    balance: int | None = None

    def is_write(self) -> bool:
        return self.balance is not None


@dataclass(kw_only=True, frozen=True)
class OutputParametersRefreshOp(OutputOp):
    PATTERN: ClassVar[str] = "09{output:N}{request:N?}!"

    request: int | None = 0

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class LoudnessOp(OutputOp):
    PATTERN: ClassVar[str] = "0C{output:N}{is_loud:bool?}{detail:N*}!"

    is_loud: bool | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.is_loud is not None


@dataclass(kw_only=True, frozen=True)
class MaxVolumeOp(OutputOp):
    PATTERN: ClassVar[str] = "0D{output:N}{max_volume:N?}{detail:N*}!"

    max_volume: int | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.max_volume is not None


@dataclass(kw_only=True, frozen=True)
class VolumeUpOp(OutputOp):
    PATTERN: ClassVar[str] = "11{output:N}!"


@dataclass(kw_only=True, frozen=True)
class VolumeDownOp(OutputOp):
    PATTERN: ClassVar[str] = "12{output:N}!"


@dataclass(kw_only=True, frozen=True)
class DeviceInfoDiscoveryOp(Op):
    PATTERN: ClassVar[str] = "14FF06!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceIdOp(Op):
    device_id: HexBytes


@dataclass(kw_only=True, frozen=True)
class DeviceInfoOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "94FF00{firmware:N}{model_id}{device_id:4X}{zones:N+}!"

    firmware: int
    model_id: HexBytes
    zones: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ExtendedDeviceInfoDiscoveryOp(Op):
    PATTERN: ClassVar[str] = "39FF{device_id:4X?}!"

    device_id: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ExtendedDeviceInfoOp(DeviceIdOp):
    PATTERN: ClassVar[str] = (
        "B9FF{prefix:4X}{device_id:4X}{model_info:18X}{mac:12X}{detail:hex}!"
    )

    prefix: HexBytes
    model_info: HexBytes
    mac: HexBytes
    detail: HexBytes = HexBytes("")

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceGuidQueryOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "3AFF{device_id:4X}85!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceGuidOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "3AFF{device_id:4X}05{guid:guid}!"

    guid: UUID

    def is_write(self) -> bool:
        return True


@dataclass(kw_only=True, frozen=True)
class DeviceSystemIdOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "3AFF{device_id:4X}06{system_id:N}!"

    system_id: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceSubInfoOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "3AFF{device_id:4X}{subtype:N}{payload:hex}!"

    subtype: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class OutputNameRefreshOp(OutputOp):
    PATTERN: ClassVar[str] = "38{output:N}!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class OutputNameOp(OutputOp):
    PATTERN: ClassVar[str] = "1C{output:N}{name:utf8}!"

    name: str = ""


@dataclass(kw_only=True, frozen=True)
class DiagnosticStatus1DOp(OutputOp):
    PATTERN: ClassVar[str] = "1D{output:N}{payload:hex}!"

    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DiagnosticStatus1EOp(OutputOp):
    PATTERN: ClassVar[str] = "1E{output:N}{payload:hex}!"

    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceIdDiscoveryOp(Op):
    PATTERN: ClassVar[str] = "2FFF!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ThisDeviceIdOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "AFFF{device_id:4X}{zones:N*}!"

    zones: tuple[int, ...]

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SourceNameDiscoveryOp(OutputOp):
    PATTERN: ClassVar[str] = "29{output:N}!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SourceNameOp(OutputOp):
    PATTERN: ClassVar[str] = (
        "29{output:N}{source_selector:N}{misc:6X?}{hidden_name:lenutf8?}{name:utf8?}!"
    )

    source_selector: int
    misc: HexBytes | None = None
    hidden_name: str | None = None
    name: str | None = None

    def is_write(self) -> bool:
        return self.name is not None or self.misc is not None or self.hidden_name is not None


@dataclass(kw_only=True, frozen=True)
class ZoneGroupOp(OutputOp):
    PATTERN: ClassVar[str] = "30{output:N}{flags:N?}{members:N*}!"

    flags: int | None = 0x20
    members: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DelayOp(OutputOp):
    PATTERN: ClassVar[str] = "31{output:N}{delay:N?}!"

    delay: int | None = None

    def is_write(self) -> bool:
        return self.delay is not None


@dataclass(kw_only=True, frozen=True)
class SourceDelayStatusOp(OutputOp):
    PATTERN: ClassVar[str] = "31{output:N}{source_delays:N+}!"

    source_delays: tuple[int, ...]

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class InputGainOp(OutputOp):
    PATTERN: ClassVar[str] = "32{output:N}{source_selector:N?}{gains:float(18,0.0,1.0)*?}!"

    source_selector: int | None = None
    gains: tuple[float, ...] | None = None

    def is_write(self) -> bool:
        return self.gains is not None


@dataclass(kw_only=True, frozen=True)
class OutputGainOp(OutputOp):
    PATTERN: ClassVar[str] = "44{output:N}{gain:S?}!"

    gain: int | None = None

    def is_write(self) -> bool:
        return self.gain is not None


@dataclass(kw_only=True, frozen=True)
class SourceMetadataOp(OutputOp):
    PATTERN: ClassVar[str] = "46{output:N}{source_selector:N}{position:N}{value:utf8}!"

    source_selector: int
    position: int
    value: str


@dataclass(kw_only=True, frozen=True)
class SourceMetadataQueryOp(OutputOp):
    PATTERN: ClassVar[str] = "47{output:N}{source_selector:N}{position:N}!"

    source_selector: int
    position: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class UnknownOutputStatusOp(OutputOp):
    PATTERN: ClassVar[str] = "48{output:N}{payload:hex}!"

    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceStateOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "4AFF{device_id:4X}{state:hex?}!"

    state: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceLinkQueryOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "4DFF{device_id:4X}{linked:bool?}!"

    linked: bool | None = None

    def is_write(self) -> bool:
        return self.linked is not None


@dataclass(kw_only=True, frozen=True)
class DeviceLinkOp(DeviceIdOp):
    PATTERN: ClassVar[str] = "CDFF{device_id:4X}{linked:bool?}!"

    linked: bool | None = None

    def is_write(self) -> bool:
        return self.linked is not None


@dataclass(kw_only=True, frozen=True)
class PresetGroupOp(Op):
    PATTERN: ClassVar[str] = "4EFF{slot_id:4N}{payload:hex?}!"

    slot_id: int = 0
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RemoteSourceDiscoveryOp(Op):
    PATTERN: ClassVar[str] = "4FFF{slot_id:N?}!"

    slot_id: int | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RemoteSourceInfoOp(Op):
    PATTERN: ClassVar[str] = "4FFF{slot_id:N}{backing_device_guid:guid}{source_index:N}{name:utf8}!"

    slot_id: int
    backing_device_guid: UUID
    source_index: int
    name: str

    def is_write(self) -> bool:
        return True


@dataclass(kw_only=True, frozen=True)
class RemoteSourceDeleteOp(Op):
    PATTERN: ClassVar[str] = "4FFF{slot_id:N}00!"

    slot_id: int


class OpEncoder(SubclassEncoder[Op]):
    def __init__(self, read_only: bool = True) -> None:
        super().__init__(Op)
        self.read_only = read_only

    def encode(self, value: Op) -> HexBytes | None:
        if self.read_only and value.is_write():
            return None
        return super().encode(value)

    def decoder(self, value: bytes) -> Op | None:
        return super().decode(value)


def connect(
    host: str,
    port: int = 17037,
    *,
    reconnection_wait_secs: float = 5.0,
    connection_timeout_secs: float = 10.0,
    trace: bool = False,
    read_only: bool = True,
) -> Transport[Op]:
    return Transport(
        encoder=OpEncoder(read_only=read_only),
        host=host,
        port=port,
        reconnection_wait_secs=reconnection_wait_secs,
        connection_timeout_secs=connection_timeout_secs,
        trace=trace,
    )
