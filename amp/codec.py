"""Codec for encoding and decoding AMP commands."""

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.encoder import SubclassEncoder
from amp.toggle_bool import ToggleBool
from amp.transport import Transport

ALL_OUTPUTS = 0xFF
ALL_LOCAL_OUTPUTS = 0xFE
INTERFACE_OUTPUT = 0xFD
UNASSIGNED_OUTPUT = 0xFC
DISABLED_OUTPUT = 0xFB
ALL_USED_OUTPUTS = 0xFA

PARAMETER_COMMENTS: dict[str, str] = {
    "action": "Preset alarm action byte; not obsolete except when carried by an obsolete command.",
    "amount": "Optional volume step encoded on the same 00h-A0h scale as volume; not obsolete.",
    "assigned_output": "Zone/output byte assigned to a keypad; response parameter, not obsolete.",
    "backing_device_guid": "GUID of the source-owning device in Autonomic/Windows wire order; not obsolete.",
    "balance": "Signed balance value in the manufacturer range ECh-14h (-20..20); not obsolete.",
    "bass": "Signed bass value in the manufacturer range F4h-0Ch (-12..12); not obsolete.",
    "command": "Command-specific subcommand byte; not obsolete except when carried by an obsolete command.",
    "day": "Day-of-month byte in a time response; not obsolete.",
    "delay": "Audio delay byte; not obsolete.",
    "detail": "Observed trailing command-specific status bytes preserved losslessly; not obsolete unless noted on the command.",
    "device_id": "Two-byte amplifier/unit ID; not obsolete except when carried by an obsolete command.",
    "digital_output": "Digital output selector for source mapping; not obsolete.",
    "entry_index": "List entry index for media-server or account-like tables; obsolete only on UserAccountsCommand.",
    "event": "Clipping event byte; notification parameter, not obsolete.",
    "favorite_index": "Media favourite list index; not obsolete.",
    "firmware": "Firmware version byte returned by device information; response parameter, not obsolete.",
    "flags": "Command-specific bit flags; not obsolete unless noted on the command.",
    "format_or_zero": "PCM stream format in the first packet, otherwise 00h; obsolete with PCM Stream.",
    "gain": "Signed zone-gain value in the manufacturer range F4h-0Ch (-12 dB..+12 dB); not obsolete.",
    "gains": "Per-source gain values encoded on the 00h-12h scale; not obsolete.",
    "guid": "Device GUID in Autonomic/Windows wire order for network settings or RFC order for observed host identity; not obsolete.",
    "hidden_name": "Manufacturer length-prefixed internal source name observed in source-name rows; not obsolete.",
    "hour": "Hour byte in a time response; not obsolete.",
    "info": "Clipping notification detail byte; not obsolete.",
    "instruction": "Settings-management instruction byte; not obsolete.",
    "is_loud": "Amplifier-special-features loudness bit; not obsolete, though some adjacent feature bits are obsolete in the PDF.",
    "is_muted": "Mute state byte using manufacturer mute polarity; not obsolete.",
    "is_on": "Power state byte using manufacturer power polarity; not obsolete.",
    "is_selected": "Party-mode selection flag; obsolete with PartyModeSelectionCommand.",
    "key": "Keypad key/repeat byte; obsolete with EmulateKeyPressOnKeypadCommand.",
    "key_code": "KPE key code; not obsolete.",
    "keypad_id": "Four-byte keypad ID used by keypad zone assignment; not obsolete.",
    "linked_output": "Zone linked to this command's zone; obsolete with LinkZonePairCommand.",
    "mac": "Six-byte MAC address; response parameter, not obsolete.",
    "max_packet_size": "Maximum PCM packet size; obsolete with RequestPcmCapabilitiesCommandResponse.",
    "max_volume": "Maximum-volume byte encoded on the 00h-A0h scale; not obsolete.",
    "members": "Linked member zone bytes for Link zones; not obsolete.",
    "message": "UTF-8 report-message body; not obsolete.",
    "message_type": "Report-message type byte; not obsolete.",
    "minute": "Minute byte in a time response; not obsolete.",
    "mode": "Command-specific mode byte for source up/down or time requests; not obsolete.",
    "model_id": "Model identifier byte returned by device information; response parameter, not obsolete.",
    "model_info": "Device-specific model/manufacture bytes in extended information; response parameter, not obsolete.",
    "month": "Month byte in a time response; not obsolete.",
    "name": "UTF-8 display name; not obsolete unless carried by an obsolete command.",
    "occupancy": "Keypad port occupancy byte; response parameter, not obsolete.",
    "operation": "Dynamic-zone-linking operation byte; not obsolete.",
    "option": "Miscellaneous-device-settings option byte; not obsolete.",
    "options": "Command-specific options/flags bytes; obsolete only when carried by an obsolete command.",
    "output": "Manufacturer zone/output selector byte; not obsolete.",
    "parameter": "Status parameter selector byte; not obsolete.",
    "payload": "Opaque command-specific bytes retained losslessly; obsolete only when carried by an obsolete command.",
    "position": "Metadata position or PCM stream position depending on command; obsolete only for PCM stream fields.",
    "position_or_length": "PCM stream length in the first packet, otherwise stream position; obsolete with PCM Stream.",
    "power_on_volume": "Power-on volume byte encoded on the 00h-A0h scale; not obsolete.",
    "preamp_volume_mode": "Preamp volume-mode signed byte; not obsolete.",
    "prefix": "Extended device-information response prefix bytes; not obsolete.",
    "preset": "Preset selector/index byte; obsolete only on ObsoletePresetSelectionStatusCommand.",
    "purpose": "PCM purpose byte; obsolete with PCM capability/stream commands.",
    "request": "Optional Send All Parameters request byte; not obsolete, but the command is manufacturer-deprecated.",
    "second": "Second byte in a time response; not obsolete.",
    "service_id": "Extended media/service identifier byte; not obsolete.",
    "setting_id": "Network settings selector byte; not obsolete.",
    "slot_id": "Arbitrary-data or distributed-source slot ID; not obsolete.",
    "source": "Source selector byte; not obsolete.",
    "source_delays": "Per-source audio-delay table bytes; response parameter, not obsolete.",
    "source_index": "Backing source index for a distributed source definition; not obsolete.",
    "source_selector": "Logical source selector byte; not obsolete.",
    "status": "Command-specific status byte; obsolete only when carried by an obsolete command.",
    "stream_format": "PCM stream format byte; obsolete with PCM Stream.",
    "supported_formats": "PCM supported-format bytes; obsolete with RequestPcmCapabilitiesCommandResponse.",
    "system_id": "Amplifier stack/system assignment byte; response parameter, not obsolete.",
    "treble": "Signed treble value in the manufacturer range F4h-0Ch (-12..12); not obsolete.",
    "value": "UTF-8 metadata value; not obsolete.",
    "version": "Protocol version byte; response parameter, not obsolete.",
    "volume": "Volume byte encoded on the 00h-A0h scale; not obsolete.",
    "weekday": "Weekday byte in a time response; not obsolete.",
    "year": "Year byte in a time response; not obsolete.",
    "zones": "Repeated zone/output bytes; not obsolete.",
}


class Command:
    COMMAND_STATUS: ClassVar[str] = "active"
    COMMAND_NOTE: ClassVar[str] = "Documented manufacturer command."

    def is_write(self) -> bool:
        return True


@dataclass(kw_only=True, frozen=True)
class OutputCommand(Command):
    output: int = ALL_OUTPUTS


@dataclass(kw_only=True, frozen=True)
class DeviceIdCommand(Command):
    device_id: HexBytes


@dataclass(kw_only=True, frozen=True)
class NoOperationCommand(OutputCommand):
    PATTERN: ClassVar[str] = "00{output:N}{payload:hex?}!"

    output: int = 0
    payload: HexBytes | None = HexBytes("00")

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class StandbyPowerCommand(OutputCommand):
    PATTERN: ClassVar[str] = "01{output:N}{is_on:power_bool?}!"

    is_on: ToggleBool | None = None

    def is_write(self) -> bool:
        return self.is_on is not None


@dataclass(kw_only=True, frozen=True)
class MuteCommand(OutputCommand):
    PATTERN: ClassVar[str] = "02{output:N}{is_muted:mute_bool?}!"

    is_muted: ToggleBool | None = None

    def is_write(self) -> bool:
        return self.is_muted is not None


@dataclass(kw_only=True, frozen=True)
class SourceSelectionCommand(OutputCommand):
    PATTERN: ClassVar[str] = "03{output:N}{source:N?}{detail:N*}!"

    source: int | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.source is not None


@dataclass(kw_only=True, frozen=True)
class VolumeCommand(OutputCommand):
    PATTERN: ClassVar[str] = "04{output:N}{volume:float(160,0.0,1.0)?}{detail:N*}!"

    volume: float | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.volume is not None


@dataclass(kw_only=True, frozen=True)
class BassCommand(OutputCommand):
    PATTERN: ClassVar[str] = "05{output:N}{bass:S?}!"

    bass: int | None = None

    def is_write(self) -> bool:
        return self.bass is not None


@dataclass(kw_only=True, frozen=True)
class TrebleCommand(OutputCommand):
    PATTERN: ClassVar[str] = "06{output:N}{treble:S?}!"

    treble: int | None = None

    def is_write(self) -> bool:
        return self.treble is not None


@dataclass(kw_only=True, frozen=True)
class BalanceCommand(OutputCommand):
    PATTERN: ClassVar[str] = "07{output:N}{balance:S?}!"

    balance: int | None = None

    def is_write(self) -> bool:
        return self.balance is not None


@dataclass(kw_only=True, frozen=True)
class RequestProtocolVersionCommand(OutputCommand):
    PATTERN: ClassVar[str] = "08{output:N}!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestProtocolVersionCommandResponse(OutputCommand):
    PATTERN: ClassVar[str] = "88{output:N}{version:N}!"

    version: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SendAllParametersCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "deprecated"
    COMMAND_NOTE: ClassVar[str] = (
        "Manufacturer-deprecated broad refresh; prefer requests for specific parameters."
    )
    PATTERN: ClassVar[str] = "09{output:N}{request:N?}!"

    request: int | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ReportErrorCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer marks Report Error (0A) obsolete."
    PATTERN: ClassVar[str] = "0A{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class EmulateKeyPressOnKeypadCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = (
        "Manufacturer marks Emulate key press on Keypad (0B) obsolete."
    )
    PATTERN: ClassVar[str] = "0B{output:N}{key:N?}!"

    key: int | None = None


@dataclass(kw_only=True, frozen=True)
class AmplifierSpecialFeaturesCommand(OutputCommand):
    PATTERN: ClassVar[str] = "0C{output:N}{is_loud:bool?}{detail:N*}!"

    is_loud: bool | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.is_loud is not None


@dataclass(kw_only=True, frozen=True)
class MaximumVolumeCommand(OutputCommand):
    PATTERN: ClassVar[str] = "0D{output:N}{max_volume:float(160,0.0,1.0)?}{detail:N*}!"

    max_volume: float | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.max_volume is not None


@dataclass(kw_only=True, frozen=True)
class ObsoletePresetSelectionStatusCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = (
        "Manufacturer marks the 0E Preset Selection / Status form obsolete; use 1E."
    )
    PATTERN: ClassVar[str] = "0E{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class LinkZonePairCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer marks Link zone pair (0F) obsolete; use 30."
    PATTERN: ClassVar[str] = "0F{output:N}{linked_output:N?}{options:N?}!"

    linked_output: int | None = None
    options: int | None = None

    def is_write(self) -> bool:
        return self.linked_output is not None


@dataclass(kw_only=True, frozen=True)
class MediaFavouritesCommand(OutputCommand):
    PATTERN: ClassVar[str] = "10{output:N}{device_id:4X}{favorite_index:N}{payload:hex?}!"

    device_id: HexBytes
    favorite_index: int
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class VolumeUpCommand(OutputCommand):
    PATTERN: ClassVar[str] = "11{output:N}{amount:float(160,0.0,1.0)?}!"

    amount: float | None = None


@dataclass(kw_only=True, frozen=True)
class VolumeDownCommand(OutputCommand):
    PATTERN: ClassVar[str] = "12{output:N}{amount:float(160,0.0,1.0)?}!"

    amount: float | None = None


@dataclass(kw_only=True, frozen=True)
class AutoDistributedSourceAssignmentAdvisoryCommand(OutputCommand):
    PATTERN: ClassVar[str] = "13{output:N}{payload:hex}!"

    payload: HexBytes


@dataclass(kw_only=True, frozen=True)
class RequestDeviceInformationCommand(OutputCommand):
    PATTERN: ClassVar[str] = "14{output:N}{options:N?}!"

    options: int | None = 0x06

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestDeviceInformationCommandResponse(DeviceIdCommand):
    PATTERN: ClassVar[str] = "94FF00{firmware:N}{model_id}{device_id:4X}{zones:N*}!"

    firmware: int
    model_id: HexBytes
    zones: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class FirmwareUpdateCommand(OutputCommand):
    PATTERN: ClassVar[str] = "15{output:N}{device_id:4X}{status:N}{payload:hex?}!"

    device_id: HexBytes
    status: int
    payload: HexBytes | None = None


@dataclass(kw_only=True, frozen=True)
class AutoPowerCommand(OutputCommand):
    PATTERN: ClassVar[str] = "16{output:N}{device_id:4X}{payload:hex?}!"

    device_id: HexBytes
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class DigitalInputOutputOptionsCommand(OutputCommand):
    PATTERN: ClassVar[str] = "17{output:N}{device_id:4X}{payload:hex?}!"

    device_id: HexBytes
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class DynamicZoneLinkingCommand(OutputCommand):
    PATTERN: ClassVar[str] = "18{output:N}{operation:N}{zones:N*}!"

    operation: int
    zones: tuple[int, ...] = ()


@dataclass(kw_only=True, frozen=True)
class MasterVolumeCommand(OutputCommand):
    PATTERN: ClassVar[str] = "19{output:N}{volume:float(160,0.0,1.0)?}!"

    volume: float | None = None

    def is_write(self) -> bool:
        return self.volume is not None


@dataclass(kw_only=True, frozen=True)
class Unused1ACommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "unused"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer documents command byte 1A as unused."
    PATTERN: ClassVar[str] = "1A{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class PresetParametersCommand(OutputCommand):
    PATTERN: ClassVar[str] = "1B{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class ZoneNameCommand(OutputCommand):
    PATTERN: ClassVar[str] = "1C{output:N}{name:utf8}!"

    name: str = ""


@dataclass(kw_only=True, frozen=True)
class PreampVolumeModeCommand(OutputCommand):
    PATTERN: ClassVar[str] = "1D{output:N}{preamp_volume_mode:S?}!"

    preamp_volume_mode: int | None = None

    def is_write(self) -> bool:
        return self.preamp_volume_mode is not None


@dataclass(kw_only=True, frozen=True)
class PresetSelectionStatusCommand(OutputCommand):
    PATTERN: ClassVar[str] = "1E{output:N}{preset:N?}{status:N?}!"

    preset: int | None = None
    status: int | None = None

    def is_write(self) -> bool:
        return self.preset is not None


@dataclass(kw_only=True, frozen=True)
class NoLongerInUse1FCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "unused"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer documents command byte 1F as no longer in use."
    PATTERN: ClassVar[str] = "1F{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class PresetSoundSetupCommand(OutputCommand):
    PATTERN: ClassVar[str] = "20{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class EqualizationCommand(OutputCommand):
    PATTERN: ClassVar[str] = "21{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class RequestDeviceLogEntryCommand(OutputCommand):
    PATTERN: ClassVar[str] = "22{output:N}{device_id:4X}{payload:hex?}!"

    device_id: HexBytes
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestDeviceLogEntryCommandResponse(OutputCommand):
    PATTERN: ClassVar[str] = "A2{output:N}{device_id:4X}{payload:hex}!"

    device_id: HexBytes
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class PresetAlarmControlCommand(OutputCommand):
    PATTERN: ClassVar[str] = "23{output:N}{preset:N}{action:N}!"

    preset: int
    action: int


@dataclass(kw_only=True, frozen=True)
class RequestPcmCapabilitiesCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer marks Request PCM capabilities (24) obsolete."
    PATTERN: ClassVar[str] = "24{output:N}{device_id:4X}{purpose:N}!"

    device_id: HexBytes
    purpose: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestPcmCapabilitiesCommandResponse(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = (
        "Manufacturer marks Request PCM capabilities response (A4) obsolete."
    )
    PATTERN: ClassVar[str] = "A4{output:N}{device_id:4X}{purpose:N}{payload:hex}!"

    device_id: HexBytes
    purpose: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class PcmStreamCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer marks PCM Stream (25) obsolete."
    PATTERN: ClassVar[str] = (
        "25{output:N}{device_id:4X}{purpose:N}{stream_format:N}{position_or_length:8N}{payload:hex?}!"
    )

    device_id: HexBytes
    purpose: int
    stream_format: int
    position_or_length: int
    payload: HexBytes | None = None


@dataclass(kw_only=True, frozen=True)
class PcmStreamCommandResponse(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer marks PCM Stream response (A5) obsolete."
    PATTERN: ClassVar[str] = "A5{output:N}{device_id:4X}{purpose:N}{position:8N}!"

    device_id: HexBytes
    purpose: int
    position: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class KeypadPortOptionsCommand(OutputCommand):
    PATTERN: ClassVar[str] = "26{output:N}{device_id:4X}{options:hex?}!"

    device_id: HexBytes
    options: HexBytes | None = None

    def is_write(self) -> bool:
        return self.options is not None


@dataclass(kw_only=True, frozen=True)
class SetTimeZoneDateTimeCommand(OutputCommand):
    PATTERN: ClassVar[str] = "27{output:N}{payload:hex}!"

    payload: HexBytes


@dataclass(kw_only=True, frozen=True)
class VideoSourceSelectionCommand(OutputCommand):
    PATTERN: ClassVar[str] = "28{output:N}{source:N?}!"

    source: int | None = None

    def is_write(self) -> bool:
        return self.source is not None


@dataclass(kw_only=True, frozen=True)
class SourceNameOptionsRequestCommand(OutputCommand):
    PATTERN: ClassVar[str] = "29{output:N}!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SourceNameOptionsCommand(OutputCommand):
    PATTERN: ClassVar[str] = (
        "29{output:N}{source_selector:N}{options:6X?}{hidden_name:lenutf8?}{name:utf8?}!"
    )

    source_selector: int
    options: HexBytes | None = None
    hidden_name: str | None = None
    name: str | None = None

    def is_write(self) -> bool:
        return self.name is not None or self.options is not None or self.hidden_name is not None


@dataclass(kw_only=True, frozen=True)
class PresetNameCommand(OutputCommand):
    PATTERN: ClassVar[str] = "2A{output:N}{preset:N}{name:utf8?}!"

    preset: int
    name: str | None = None

    def is_write(self) -> bool:
        return self.name is not None


@dataclass(kw_only=True, frozen=True)
class RequestPresetNameCommand(OutputCommand):
    PATTERN: ClassVar[str] = "2B{output:N}{preset:N}!"

    preset: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SourceUpCommand(OutputCommand):
    PATTERN: ClassVar[str] = "2C{output:N}{mode:N}!"

    mode: int = 0


@dataclass(kw_only=True, frozen=True)
class SourceDownCommand(OutputCommand):
    PATTERN: ClassVar[str] = "2D{output:N}{mode:N}!"

    mode: int = 0


@dataclass(kw_only=True, frozen=True)
class ZoneAssignmentCommand(OutputCommand):
    PATTERN: ClassVar[str] = "2E{output:N}{device_id:4X}{zones:N*}!"

    device_id: HexBytes
    zones: tuple[int, ...] = ()


@dataclass(kw_only=True, frozen=True)
class RequestZoneAssignmentsCommand(OutputCommand):
    PATTERN: ClassVar[str] = "2F{output:N}{device_id:4X?}!"

    device_id: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestZoneAssignmentsCommandResponse(DeviceIdCommand):
    PATTERN: ClassVar[str] = "AF{output:N}{device_id:4X}{zones:N*}!"

    output: int = ALL_OUTPUTS
    zones: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class LinkZonesCommand(OutputCommand):
    PATTERN: ClassVar[str] = "30{output:N}{flags:N?}{members:N*}!"

    flags: int | None = 0x20
    members: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class AudioDelayCommand(OutputCommand):
    PATTERN: ClassVar[str] = "31{output:N}{delay:N?}!"

    delay: int | None = None

    def is_write(self) -> bool:
        return self.delay is not None


@dataclass(kw_only=True, frozen=True)
class AudioDelayCommandResponse(OutputCommand):
    PATTERN: ClassVar[str] = "31{output:N}{source_delays:N+}!"

    source_delays: tuple[int, ...]

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SourceGainCommand(OutputCommand):
    PATTERN: ClassVar[str] = "32{output:N}{source_selector:N?}{gains:float(18,0.0,1.0)*?}!"

    source_selector: int | None = None
    gains: tuple[float, ...] | None = None

    def is_write(self) -> bool:
        return self.gains is not None


@dataclass(kw_only=True, frozen=True)
class PagePreset2SelectionCommand(OutputCommand):
    PATTERN: ClassVar[str] = "33{output:N}{preset:N}!"

    preset: int


@dataclass(kw_only=True, frozen=True)
class ClippingNotificationCommand(OutputCommand):
    PATTERN: ClassVar[str] = "34{output:N}{event:N}{info:N}!"

    event: int
    info: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class IrRoutingAssignmentsCommand(OutputCommand):
    PATTERN: ClassVar[str] = "35{output:N}{device_id:4X}{payload:hex?}!"

    device_id: HexBytes
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None and len(self.payload) > 1


@dataclass(kw_only=True, frozen=True)
class PartyModeSelectionCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = (
        "Manufacturer marks Party mode select/deselect (36) obsolete."
    )
    PATTERN: ClassVar[str] = "36{output:N}{is_selected:bool}!"

    is_selected: bool


@dataclass(kw_only=True, frozen=True)
class PartyModeConfigurationCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer marks Party mode configuration (37) obsolete."
    PATTERN: ClassVar[str] = "37{output:N}{device_id:4X}{source:N}!"

    device_id: HexBytes
    source: int


@dataclass(kw_only=True, frozen=True)
class ZoneNameRequestCommand(OutputCommand):
    PATTERN: ClassVar[str] = "38{output:N}!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestExtendedDeviceInformationCommand(OutputCommand):
    PATTERN: ClassVar[str] = "39{output:N}{device_id:4X?}!"

    device_id: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestExtendedDeviceInformationCommandResponse(DeviceIdCommand):
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
class NetworkSettingsDeviceGuidRequestCommand(DeviceIdCommand):
    PATTERN: ClassVar[str] = "3AFF{device_id:4X}85!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class NetworkSettingsDeviceGuidCommand(DeviceIdCommand):
    PATTERN: ClassVar[str] = "3AFF{device_id:4X}05{guid:guid}!"

    guid: UUID

    def is_write(self) -> bool:
        return True


@dataclass(kw_only=True, frozen=True)
class NetworkSettingsAmplifierStackAssignmentCommandResponse(DeviceIdCommand):
    PATTERN: ClassVar[str] = "3AFF{device_id:4X}06{system_id:N}!"

    system_id: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class NetworkSettingsCommandResponse(DeviceIdCommand):
    PATTERN: ClassVar[str] = "3AFF{device_id:4X}{setting_id:N}{payload:hex}!"

    setting_id: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class NetworkSettingsCommand(OutputCommand):
    PATTERN: ClassVar[str] = "3A{output:N}{device_id:4X}{setting_id:N}{payload:hex?}!"

    device_id: HexBytes
    setting_id: int
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class MediaServersCommand(OutputCommand):
    PATTERN: ClassVar[str] = "3B{output:N}{device_id:4X}{entry_index:N}{payload:hex?}!"

    device_id: HexBytes
    entry_index: int
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class ListSourcesCommand(OutputCommand):
    PATTERN: ClassVar[str] = "3C{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class MediaPlayerPlayControlCommand(OutputCommand):
    PATTERN: ClassVar[str] = "3D{output:N}{source:N}{payload:hex}!"

    source: int
    payload: HexBytes


@dataclass(kw_only=True, frozen=True)
class PlayStatusNotificationCommand(OutputCommand):
    PATTERN: ClassVar[str] = "3E{output:N}{source:N}{parameter:N}{payload:hex?}!"

    source: int
    parameter: int
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class PlayStatusRequestCommand(OutputCommand):
    PATTERN: ClassVar[str] = "3F{output:N}{source:N}{payload:hex}!"

    source: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ReportMessageCommand(OutputCommand):
    PATTERN: ClassVar[str] = "40{output:N}{message_type:N}{message:utf8?}!"

    message_type: int
    message: str | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestTimeCommand(OutputCommand):
    PATTERN: ClassVar[str] = "41{output:N}{mode:N}!"

    mode: int = 0

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestTimeCommandResponse(OutputCommand):
    PATTERN: ClassVar[str] = (
        "C1{output:N}{hour:N}{minute:N}{second:N}{weekday:N}{day:N}{month:N}{year:N}!"
    )

    hour: int
    minute: int
    second: int
    weekday: int
    day: int
    month: int
    year: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SettingsManagementCommand(OutputCommand):
    PATTERN: ClassVar[str] = "42{output:N}{device_id:4X}{instruction:N}{payload:hex?}!"

    device_id: HexBytes
    instruction: int
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.instruction not in (0x00,)


@dataclass(kw_only=True, frozen=True)
class MiscellaneousDeviceSettingsCommand(OutputCommand):
    PATTERN: ClassVar[str] = "43{output:N}{device_id:4X}{option:N}{command:N}{payload:hex?}!"

    device_id: HexBytes
    option: int
    command: int
    payload: HexBytes | None = None


@dataclass(kw_only=True, frozen=True)
class ZoneGainCommand(OutputCommand):
    PATTERN: ClassVar[str] = "44{output:N}{gain:S?}!"

    gain: int | None = None

    def is_write(self) -> bool:
        return self.gain is not None


@dataclass(kw_only=True, frozen=True)
class UserAccountsCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "obsolete"
    COMMAND_NOTE: ClassVar[str] = "Manufacturer marks User accounts (45) obsolete."
    PATTERN: ClassVar[str] = "45{output:N}{device_id:4X}{entry_index:N}{payload:hex?}!"

    device_id: HexBytes
    entry_index: int
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class SourceSpecificMetadataCommand(OutputCommand):
    PATTERN: ClassVar[str] = "46{output:N}{source_selector:N}{position:N}{value:utf8}!"

    source_selector: int
    position: int
    value: str


@dataclass(kw_only=True, frozen=True)
class SourceSpecificMetadataRequestCommand(OutputCommand):
    PATTERN: ClassVar[str] = "47{output:N}{source_selector:N}{position:N}!"

    source_selector: int
    position: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class PowerOnVolumeLevelCommand(OutputCommand):
    PATTERN: ClassVar[str] = "48{output:N}{power_on_volume:float(160,0.0,1.0)?}{detail:N*}!"

    power_on_volume: float | None = None
    detail: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return self.power_on_volume is not None


@dataclass(kw_only=True, frozen=True)
class RequestKeypadZoneAssignmentCommand(OutputCommand):
    PATTERN: ClassVar[str] = "49{output:N}{keypad_id:8X}!"

    keypad_id: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class RequestKeypadZoneAssignmentCommandResponse(OutputCommand):
    PATTERN: ClassVar[str] = "C9{output:N}{keypad_id:8X}{assigned_output:N}!"

    keypad_id: HexBytes
    assigned_output: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class KeypadPortZoneMappingCommand(OutputCommand):
    PATTERN: ClassVar[str] = "4A{output:N}{device_id:4X}{payload:hex?}!"

    device_id: HexBytes
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class KpeKeyEventCommand(OutputCommand):
    PATTERN: ClassVar[str] = "4B{output:N}{key_code:4N?}!"

    key_code: int | None = None

    def is_write(self) -> bool:
        return self.key_code is not None


@dataclass(kw_only=True, frozen=True)
class KpeLedControlCommand(OutputCommand):
    PATTERN: ClassVar[str] = "4C{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class KeypadPortOccupancyCommand(OutputCommand):
    PATTERN: ClassVar[str] = "4D{output:N}{device_id:4X}!"

    device_id: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class KeypadPortOccupancyCommandResponse(OutputCommand):
    PATTERN: ClassVar[str] = "CD{output:N}{device_id:4X}{occupancy:N}!"

    device_id: HexBytes
    occupancy: int

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ArbitraryDataStorageCommand(OutputCommand):
    PATTERN: ClassVar[str] = "4E{output:N}{slot_id:4N}{payload:hex?}!"

    slot_id: int = 0
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class DistributedSourceDefinitionSlotCommand(OutputCommand):
    slot_id: int | None = None


@dataclass(kw_only=True, frozen=True)
class DistributedSourceDefinitionRequestCommand(DistributedSourceDefinitionSlotCommand):
    PATTERN: ClassVar[str] = "4F{output:N}{slot_id:N?}!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class DistributedSourceDefinitionCommand(DistributedSourceDefinitionSlotCommand):
    PATTERN: ClassVar[str] = "4F{output:N}{slot_id:N}{backing_device_guid:guid}{source_index:N}{name:utf8}!"

    slot_id: int
    backing_device_guid: UUID
    source_index: int
    name: str


@dataclass(kw_only=True, frozen=True)
class DistributedSourceDefinitionUnusedCommand(DistributedSourceDefinitionSlotCommand):
    COMMAND_STATUS: ClassVar[str] = "unused"
    COMMAND_NOTE: ClassVar[str] = "Marks a Distributed Source Definition slot as unused."
    PATTERN: ClassVar[str] = "4F{output:N}{slot_id:N}00!"

    slot_id: int


@dataclass(kw_only=True, frozen=True)
class DistributedSourceAudioDelayCommand(OutputCommand):
    PATTERN: ClassVar[str] = "50{output:N}{payload:hex?}!"

    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.payload is not None


@dataclass(kw_only=True, frozen=True)
class RegisterServiceCommand(OutputCommand):
    PATTERN: ClassVar[str] = "51{output:N}{source:N}{flags:N?}{payload:hex?}!"

    source: int
    flags: int | None = None
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return self.flags is not None


@dataclass(kw_only=True, frozen=True)
class ExtendedPlayControlCommand(OutputCommand):
    PATTERN: ClassVar[str] = "52{output:N}{service_id:N}{command:N}{payload:hex?}!"

    service_id: int
    command: int
    payload: HexBytes | None = None


@dataclass(kw_only=True, frozen=True)
class ExtendedPlayStatusCommand(OutputCommand):
    PATTERN: ClassVar[str] = "53{output:N}{service_id:N}{parameter:N}{payload:hex?}!"

    service_id: int
    parameter: int
    payload: HexBytes | None = None

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ExtendedPlayStatusRequestCommand(OutputCommand):
    PATTERN: ClassVar[str] = "54{output:N}{service_id:N}{payload:hex}!"

    service_id: int
    payload: HexBytes

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class ServiceStatusCommand(OutputCommand):
    PATTERN: ClassVar[str] = "55{output:N}{service_id:N}{flags:N}{zones:N*}!"

    service_id: int
    flags: int
    zones: tuple[int, ...] = ()

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class SourceMappingCommand(OutputCommand):
    PATTERN: ClassVar[str] = "56{output:N}{device_id:4X}{digital_output:N}{source:N}{zones:N*}!"

    device_id: HexBytes
    digital_output: int
    source: int
    zones: tuple[int, ...] = ()


@dataclass(kw_only=True, frozen=True)
class ExtensibleCommandHandlingCommand(OutputCommand):
    PATTERN: ClassVar[str] = "57{output:N}{purpose:N}{command:N}{payload:hex?}!"

    purpose: int
    command: int
    payload: HexBytes | None = None


@dataclass(kw_only=True, frozen=True)
class UndocumentedHostIdentityCommand(OutputCommand):
    COMMAND_STATUS: ClassVar[str] = "undocumented"
    COMMAND_NOTE: ClassVar[str] = "Observed live-device host identity request."
    PATTERN: ClassVar[str] = "58FF00!"

    def is_write(self) -> bool:
        return False


@dataclass(kw_only=True, frozen=True)
class UndocumentedHostIdentityCommandResponse(Command):
    COMMAND_STATUS: ClassVar[str] = "undocumented"
    COMMAND_NOTE: ClassVar[str] = "Observed live-device host identity response."
    PATTERN: ClassVar[str] = "58FF00{guid:uuid}{mac:12X}{detail:hex}!"

    guid: UUID
    mac: HexBytes
    detail: HexBytes = HexBytes("")

    @property
    def wire_guid(self) -> UUID:
        return UUID(bytes_le=self.guid.bytes)

    @property
    def candidate_guids(self) -> tuple[UUID, ...]:
        if self.wire_guid == self.guid:
            return (self.guid,)
        return (self.guid, self.wire_guid)

    def is_write(self) -> bool:
        return False


class CommandEncoder(SubclassEncoder[Command]):
    def __init__(self, read_only: bool = True) -> None:
        super().__init__(Command)
        self.read_only = read_only

    def encode(self, value: Command) -> HexBytes | None:
        if self.read_only and value.is_write():
            return None
        return super().encode(value)

    def decoder(self, value: bytes) -> Command | None:
        return super().decode(value)


def connect(
    host: str,
    port: int = 17037,
    *,
    reconnection_wait_secs: float = 5.0,
    connection_timeout_secs: float = 10.0,
    trace: bool = False,
    read_only: bool = True,
) -> Transport[Command]:
    return Transport(
        encoder=CommandEncoder(read_only=read_only),
        host=host,
        port=port,
        reconnection_wait_secs=reconnection_wait_secs,
        connection_timeout_secs=connection_timeout_secs,
        trace=trace,
    )
