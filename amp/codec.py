"""Codec for encoding and decoding AMP operations."""

from dataclasses import dataclass, field
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.encoder import SubclassEncoder
from amp.transport import Transport
from amp.types import ToggleBool

ALL_OUTPUTS = 0xFF


class Op:
    def is_write(self) -> bool:
        return True


@dataclass(kw_only=True, frozen=True)
class PowerOp(Op):
    PATTERN = "01{output:N}{is_on:power_bool?}!"

    output: int = ALL_OUTPUTS
    is_on: ToggleBool | None = None

    def is_write(self) -> bool:
        return self.is_on is not None


@dataclass(kw_only=True, frozen=True)
class MuteOp(Op):
    PATTERN = "02{output:N}{is_muted:mute_bool?}!"

    output: int = ALL_OUTPUTS
    is_muted: ToggleBool | None = None

    def is_write(self) -> bool:
        return self.is_muted is not None


@dataclass(kw_only=True, frozen=True)
class SourceSelectOp(Op):
    PATTERN = "03{output:N}{source:N?}{detail:N*}!"

    output: int = ALL_OUTPUTS
    source: int | None = None
    detail: list[int] = field(default_factory=list)

    def is_write(self) -> bool:
        return self.source is not None


@dataclass(kw_only=True, frozen=True)
class VolumeOp(Op):
    PATTERN = "04{output:N}{volume:float(160,0.0,1.0)?}{detail:N*}!"

    output: int = ALL_OUTPUTS
    volume: float | None = None
    detail: list[int] = field(default_factory=list)

    def is_write(self) -> bool:
        return self.volume is not None


@dataclass(kw_only=True, frozen=True)
class BassOp(Op):
    PATTERN = "05{output:N}{bass:S?}!"

    output: int = ALL_OUTPUTS
    bass: int | None = None

    def is_write(self) -> bool:
        return self.bass is not None


@dataclass(kw_only=True, frozen=True)
class TrebleOp(Op):
    PATTERN = "06{output:N}{treble:S?}!"

    output: int = ALL_OUTPUTS
    treble: int | None = None

    def is_write(self) -> bool:
        return self.treble is not None


@dataclass(kw_only=True, frozen=True)
class BalanceOp(Op):
    PATTERN = "07{output:N}{balance:S?}!"

    output: int = ALL_OUTPUTS
    balance: int | None = None

    def is_write(self) -> bool:
        return self.balance is not None


@dataclass(kw_only=True, frozen=True)
class OutputParametersRefreshOp(Op):
    PATTERN = "09{output:N}{request:N?}!"

    output: int = ALL_OUTPUTS
    request: int | None = 0

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class LoudnessOp(Op):
    PATTERN = "0C{output:N}{is_loud:bool?}{detail:N*}!"

    output: int = ALL_OUTPUTS
    is_loud: bool | None = None
    detail: list[int] = field(default_factory=list)

    def is_write(self) -> bool:
        return self.is_loud is not None


@dataclass(kw_only=True, frozen=True)
class MaxVolumeOp(Op):
    PATTERN = "0D{output:N}{max_volume:N?}{detail:N*}!"

    output: int = ALL_OUTPUTS
    max_volume: int | None = None
    detail: list[int] = field(default_factory=list)

    def is_write(self) -> bool:
        return self.max_volume is not None


@dataclass(kw_only=True, frozen=True)
class VolumeUpOp(Op):
    PATTERN = "11{output:N}!"

    output: int = ALL_OUTPUTS


@dataclass(kw_only=True, frozen=True)
class VolumeDownOp(Op):
    PATTERN = "12{output:N}!"

    output: int = ALL_OUTPUTS


@dataclass(kw_only=True, frozen=True)
class DeviceInfoDiscoveryOp(Op):
    PATTERN = "14FF06!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceInfoOp(Op):
    PATTERN = "94FF00{firmware:N}{model_id}{device_id:4X}{zones:N+}!"

    firmware: int
    model_id: HexBytes
    device_id: HexBytes
    zones: list[int]

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ExtendedDeviceInfoDiscovery(Op):
    PATTERN = "39FF{device_id:4X?}!"

    device_id: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ExtendedDeviceInfoOp(Op):
    PATTERN = "B9{output:N}{:4X}{device_id:4X}{:18X}{mac:12X}"

    output: int = ALL_OUTPUTS
    device_id: HexBytes
    mac: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceGuidQueryOp(Op):
    PATTERN = "3AFF{device_id:4X}85!"

    device_id: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceGuidOp(Op):
    PATTERN = "3AFF{device_id:4X}05{guid:guid}!"

    device_id: HexBytes
    guid: UUID

    def is_write(self) -> bool:
        return True


@dataclass(kw_only=True, frozen=True)
class DeviceSystemIdOp(Op):
    PATTERN = "3AFF{device_id:4X}06{system_id:N}!"

    device_id: HexBytes
    system_id: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceSubInfoOp(Op):
    PATTERN = "3AFF{device_id:4X}{subtype:N}{payload:hex}!"

    device_id: HexBytes
    subtype: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class OutputNameRefreshOp(Op):
    PATTERN = "38FF!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class OutputNameOp(Op):
    PATTERN = "1C{output:N}{name:utf8}!"

    output: int
    name: str


@dataclass(kw_only=True, frozen=True)
class DiagnosticStatus1DOp(Op):
    PATTERN = "1D{output:N}{payload:hex}!"

    output: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DiagnosticStatus1EOp(Op):
    PATTERN = "1E{output:N}{payload:hex}!"

    output: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceIdDiscoveryOp(Op):
    PATTERN = "2FFF!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceIdOp(Op):
    PATTERN = "AFFF{device_id:4X}{zones:N*}!"

    device_id: HexBytes
    zones: list[int]

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SourceNameOp(Op):
    PATTERN = "29{output:N}{source_selector:X?}{misc:6X?}{hidden_name:lenutf8?}{name:utf8?}!"

    output: int = ALL_OUTPUTS
    source_selector: HexBytes | None = None
    misc: HexBytes | None = None
    hidden_name: str | None = None
    name: str | None = None

    def is_write(self) -> bool:
        return self.name is not None or self.misc is not None or self.hidden_name is not None


@dataclass(kw_only=True, frozen=True)
class ZoneGroupOp(Op):
    PATTERN = "30{output:N}{flags:N?}{members:N*}!"

    output: int = ALL_OUTPUTS
    flags: int | None = 0x20
    members: list[int] = field(default_factory=list)

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DelayOp(Op):
    PATTERN = "31{output:N}{delay:N?}!"

    output: int = ALL_OUTPUTS
    delay: int | None = None

    def is_write(self) -> bool:
        return self.delay is not None


@dataclass(kw_only=True, frozen=True)
class SourceDelayStatusOp(Op):
    PATTERN = "31{output:N}{source_delays:N+}!"

    output: int
    source_delays: list[int]

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class InputGainOp(Op):
    PATTERN = "32{output:N}{source_selector:N?}{gain:float(18,0.0,1.0)?}{source_gains:N*}!"

    output: int = ALL_OUTPUTS
    source_selector: int | None = None
    gain: float | None = None
    source_gains: list[int] = field(default_factory=list)

    def is_write(self) -> bool:
        return self.source_selector is not None and self.gain is not None


@dataclass(kw_only=True, frozen=True)
class OutputGainOp(Op):
    PATTERN = "44{output:N}{gain:S?}!"

    output: int = ALL_OUTPUTS
    gain: int | None = None

    def is_write(self) -> bool:
        return self.gain is not None


@dataclass(kw_only=True, frozen=True)
class SourceMetadataOp(Op):
    PATTERN = "46{output:N}{source_selector:N}{position:N}{value:utf8}!"

    output: int = ALL_OUTPUTS
    source_selector: int
    position: int
    value: str


@dataclass(kw_only=True, frozen=True)
class SourceMetadataQueryOp(Op):
    PATTERN = "47{output:N}{source_selector:N}{position:N}!"

    output: int = ALL_OUTPUTS
    source_selector: int
    position: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class UnknownOutputStatusOp(Op):
    PATTERN = "48{output:N}{payload:hex}!"

    output: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceStateOp(Op):
    PATTERN = "4AFF{device_id:4X}{state:hex?}!"

    device_id: HexBytes
    state: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DeviceLinkQueryOp(Op):
    PATTERN = "4DFF{device_id:4X}{linked:bool?}!"

    device_id: HexBytes
    linked: bool | None = None

    def is_write(self) -> bool:
        return self.linked is not None


@dataclass(kw_only=True, frozen=True)
class DeviceLinkOp(Op):
    PATTERN = "CDFF{device_id:4X}{linked:bool?}!"

    device_id: HexBytes
    linked: bool | None = None

    def is_write(self) -> bool:
        return self.linked is not None


@dataclass(kw_only=True, frozen=True)
class PresetGroupOp(Op):
    PATTERN = "4EFF{slot_id:4N}{payload:hex?}!"

    slot_id: int = 0
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RemoteSourceDiscoveryOp(Op):
    PATTERN = "4FFF{slot_id:N?}!"

    slot_id: int | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RemoteSourceInfoOp(Op):
    PATTERN = "4FFF{slot_id:N}{backing_device_guid:guid}{source_index:N}{name:utf8}!"

    slot_id: int
    backing_device_guid: UUID
    source_index: int
    name: str

    def is_write(self) -> bool:
        return True


@dataclass(kw_only=True, frozen=True)
class RemoteSourceDeleteOp(Op):
    PATTERN = "4FFF{slot_id:N}00!"

    slot_id: int


class OpEncoder(SubclassEncoder[Op]):
    def __init__(self, read_only: bool = True) -> None:
        super().__init__(Op)
        self.read_only = read_only

    def encode(self, value: Op) -> bytes | None:
        if self.read_only and value.is_write():
            return None
        return super().encode(value)

    def decoder(self, value: bytes) -> Op:
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
