from dataclasses import fields, is_dataclass
from uuid import UUID

import pytest

import amp.codec as codec
from amp.byte_utils import HexBytes
from amp.codec import (
    ALL_OUTPUTS,
    BalanceCommand,
    BassCommand,
    AudioDelayCommand,
    NetworkSettingsDeviceGuidCommand,
    NetworkSettingsDeviceGuidRequestCommand,
    UndocumentedHostIdentityCommand,
    UndocumentedHostIdentityCommandResponse,
    RequestZoneAssignmentsCommand,
    DeviceIdCommand,
    RequestDeviceInformationCommand,
    RequestDeviceInformationCommandResponse,
    KeypadPortOccupancyCommandResponse,
    KeypadPortOccupancyCommand,
    KeypadPortZoneMappingCommand,
    NetworkSettingsCommandResponse,
    NetworkSettingsAmplifierStackAssignmentCommandResponse,
    PreampVolumeModeCommand,
    PresetSelectionStatusCommand,
    RequestExtendedDeviceInformationCommand,
    RequestExtendedDeviceInformationCommandResponse,
    SourceGainCommand,
    AmplifierSpecialFeaturesCommand,
    MaximumVolumeCommand,
    MuteCommand,
    Command,
    CommandEncoder,
    ZoneGainCommand,
    ZoneNameCommand,
    ZoneNameRequestCommand,
    SendAllParametersCommand,
    StandbyPowerCommand,
    ArbitraryDataStorageCommand,
    DistributedSourceDefinitionUnusedCommand,
    DistributedSourceDefinitionRequestCommand,
    DistributedSourceDefinitionCommand,
    DistributedSourceDefinitionSlotCommand,
    AudioDelayCommandResponse,
    SourceSpecificMetadataCommand,
    SourceSpecificMetadataRequestCommand,
    SourceNameOptionsRequestCommand,
    SourceNameOptionsCommand,
    SourceSelectionCommand,
    RequestZoneAssignmentsCommandResponse,
    TrebleCommand,
    PowerOnVolumeLevelCommand,
    VolumeDownCommand,
    VolumeCommand,
    VolumeUpCommand,
    LinkZonesCommand,
    connect,
)
from amp.encoder import PatternEncoder, SubclassEncoder
from amp.transport import Transport
from amp.toggle_bool import ToggleBool


GUID = UUID("674e1900-f8a9-f6be-a465-3d0fbee12977")
GUID_WIRE = "00194E67A9F8BEF6A4653D0FBEE12977"


def _command_classes(cls: type[Command] = codec.Command) -> tuple[type[Command], ...]:
    found: list[type[Command]] = []
    for subclass in cls.__subclasses__():
        found.extend(_command_classes(subclass))
        found.append(subclass)
    return tuple(found)


def test_codec_parameter_comments_cover_all_command_fields() -> None:
    field_names = {
        field.name
        for cls in _command_classes()
        if is_dataclass(cls)
        for field in fields(cls)
    }

    assert field_names <= set(codec.PARAMETER_COMMENTS)


def test_non_active_commands_have_status_notes() -> None:
    non_active_classes = [
        cls
        for cls in _command_classes()
        if getattr(cls, "COMMAND_STATUS", "active") != "active"
    ]

    assert non_active_classes
    assert all(cls.COMMAND_NOTE != codec.Command.COMMAND_NOTE for cls in non_active_classes)


@pytest.mark.parametrize(
    ("command", "encoded"),
    [
        (codec.NoOperationCommand(output=1, payload=None), "0001"),
        (codec.StandbyPowerCommand(output=1, is_on=ToggleBool.On), "010101"),
        (codec.MuteCommand(output=1, is_muted=ToggleBool.On), "020100"),
        (codec.SourceSelectionCommand(output=1, source=0x05), "030105"),
        (codec.VolumeCommand(output=1, volume=0.5), "040150"),
        (codec.BassCommand(output=1, bass=-3), "0501FD"),
        (codec.TrebleCommand(output=1, treble=4), "060104"),
        (codec.BalanceCommand(output=1, balance=-10), "0701F6"),
        (codec.RequestProtocolVersionCommand(), "08FF"),
        (codec.RequestProtocolVersionCommandResponse(version=1), "88FF01"),
        (codec.SendAllParametersCommand(output=1), "0901"),
        (codec.ReportErrorCommand(payload=HexBytes("00")), "0AFF00"),
        (codec.EmulateKeyPressOnKeypadCommand(key=1), "0BFF01"),
        (codec.AmplifierSpecialFeaturesCommand(output=1, is_loud=True), "0C0101"),
        (codec.MaximumVolumeCommand(output=1, max_volume=1.0), "0D01A0"),
        (codec.ObsoletePresetSelectionStatusCommand(payload=HexBytes("00")), "0EFF00"),
        (codec.LinkZonePairCommand(linked_output=1, options=1), "0FFF0101"),
        (codec.MediaFavouritesCommand(device_id=HexBytes("00D4"), favorite_index=1), "10FF00D401"),
        (codec.VolumeUpCommand(output=1), "1101"),
        (codec.VolumeDownCommand(output=1), "1201"),
        (codec.AutoDistributedSourceAssignmentAdvisoryCommand(payload=HexBytes("01")), "13FF01"),
        (codec.RequestDeviceInformationCommand(), "14FF06"),
        (
            codec.RequestDeviceInformationCommandResponse(
                firmware=6,
                model_id=HexBytes("B0"),
                device_id=HexBytes("00D4"),
                zones=(1, 2, 3, 4, 5, 6, 7, 8),
            ),
            "94FF0006B000D40102030405060708",
        ),
        (codec.FirmwareUpdateCommand(device_id=HexBytes("00D4"), status=1), "15FF00D401"),
        (codec.AutoPowerCommand(device_id=HexBytes("00D4")), "16FF00D4"),
        (codec.DigitalInputOutputOptionsCommand(device_id=HexBytes("00D4")), "17FF00D4"),
        (codec.DynamicZoneLinkingCommand(operation=1, zones=(2, 3)), "18FF010203"),
        (codec.MasterVolumeCommand(volume=0.5), "19FF50"),
        (codec.Unused1ACommand(), "1AFF"),
        (codec.PresetParametersCommand(payload=HexBytes("01")), "1BFF01"),
        (codec.ZoneNameCommand(output=1, name="Kitchen"), "1C014B69746368656E"),
        (codec.PreampVolumeModeCommand(output=1, preamp_volume_mode=-128), "1D0180"),
        (codec.PresetSelectionStatusCommand(preset=0), "1EFF00"),
        (codec.NoLongerInUse1FCommand(), "1FFF"),
        (codec.PresetSoundSetupCommand(payload=HexBytes("01")), "20FF01"),
        (codec.EqualizationCommand(payload=HexBytes("01")), "21FF01"),
        (codec.RequestDeviceLogEntryCommand(device_id=HexBytes("00D4")), "22FF00D4"),
        (
            codec.RequestDeviceLogEntryCommandResponse(
                device_id=HexBytes("00D4"),
                payload=HexBytes("01"),
            ),
            "A2FF00D401",
        ),
        (codec.PresetAlarmControlCommand(preset=1, action=2), "23FF0102"),
        (codec.RequestPcmCapabilitiesCommand(device_id=HexBytes("00D4"), purpose=1), "24FF00D401"),
        (
            codec.RequestPcmCapabilitiesCommandResponse(
                device_id=HexBytes("00D4"),
                purpose=1,
                payload=HexBytes("010203"),
            ),
            "A4FF00D401010203",
        ),
        (
            codec.PcmStreamCommand(
                device_id=HexBytes("00D4"),
                purpose=1,
                stream_format=2,
                position_or_length=3,
                payload=HexBytes("AA"),
            ),
            "25FF00D4010200000003AA",
        ),
        (
            codec.PcmStreamCommandResponse(
                device_id=HexBytes("00D4"),
                purpose=1,
                position=3,
            ),
            "A5FF00D40100000003",
        ),
        (codec.KeypadPortOptionsCommand(device_id=HexBytes("00D4")), "26FF00D4"),
        (codec.SetTimeZoneDateTimeCommand(payload=HexBytes("010203")), "27FF010203"),
        (codec.VideoSourceSelectionCommand(source=2), "28FF02"),
        (codec.SourceNameOptionsRequestCommand(output=9), "2909"),
        (codec.SourceNameOptionsCommand(output=9, source_selector=5), "290905"),
        (codec.PresetNameCommand(preset=1, name="P1"), "2AFF015031"),
        (codec.RequestPresetNameCommand(preset=1), "2BFF01"),
        (codec.SourceUpCommand(), "2CFF00"),
        (codec.SourceDownCommand(), "2DFF00"),
        (codec.ZoneAssignmentCommand(device_id=HexBytes("00D4"), zones=(1, 2)), "2EFF00D40102"),
        (codec.RequestZoneAssignmentsCommand(), "2FFF"),
        (
            codec.RequestZoneAssignmentsCommandResponse(
                device_id=HexBytes("00D4"),
                zones=(1, 2),
            ),
            "AFFF00D40102",
        ),
        (codec.LinkZonesCommand(), "30FF20"),
        (codec.AudioDelayCommand(output=1, delay=0x14), "310114"),
        (codec.AudioDelayCommandResponse(output=1, source_delays=(1, 2)), "31010102"),
        (codec.SourceGainCommand(output=1, source_selector=2, gains=(0.5,)), "32010209"),
        (codec.PagePreset2SelectionCommand(preset=1), "33FF01"),
        (codec.ClippingNotificationCommand(event=1, info=2), "34FF0102"),
        (codec.IrRoutingAssignmentsCommand(device_id=HexBytes("00D4"), payload=HexBytes("0102")), "35FF00D40102"),
        (codec.PartyModeSelectionCommand(is_selected=True), "36FF01"),
        (codec.PartyModeConfigurationCommand(device_id=HexBytes("00D4"), source=5), "37FF00D405"),
        (codec.ZoneNameRequestCommand(), "38FF"),
        (codec.RequestExtendedDeviceInformationCommand(device_id=HexBytes("00D4")), "39FF00D4"),
        (
            codec.RequestExtendedDeviceInformationCommandResponse(
                prefix=HexBytes("0000"),
                device_id=HexBytes("00D4"),
                model_info=HexBytes("0603001F260A0100C8"),
                mac=HexBytes("ACE14F0055B4"),
                detail=HexBytes("1907150002"),
            ),
            "B9FF000000D40603001F260A0100C8ACE14F0055B41907150002",
        ),
        (codec.NetworkSettingsDeviceGuidRequestCommand(device_id=HexBytes("00D4")), "3AFF00D485"),
        (codec.NetworkSettingsDeviceGuidCommand(device_id=HexBytes("00D4"), guid=GUID), f"3AFF00D405{GUID_WIRE}"),
        (
            codec.NetworkSettingsAmplifierStackAssignmentCommandResponse(
                device_id=HexBytes("00D4"),
                system_id=1,
            ),
            "3AFF00D40601",
        ),
        (
            codec.NetworkSettingsCommandResponse(
                device_id=HexBytes("00D4"),
                setting_id=3,
                payload=HexBytes("01"),
            ),
            "3AFF00D40301",
        ),
        (
            codec.NetworkSettingsCommand(
                output=1,
                device_id=HexBytes("00D4"),
                setting_id=3,
                payload=HexBytes("01"),
            ),
            "3A0100D40301",
        ),
        (codec.MediaServersCommand(device_id=HexBytes("00D4"), entry_index=1), "3BFF00D401"),
        (codec.ListSourcesCommand(payload=HexBytes("01")), "3CFF01"),
        (codec.MediaPlayerPlayControlCommand(source=1, payload=HexBytes("02")), "3DFF0102"),
        (codec.PlayStatusNotificationCommand(source=1, parameter=2, payload=HexBytes("03")), "3EFF010203"),
        (codec.PlayStatusRequestCommand(source=1, payload=HexBytes("02")), "3FFF0102"),
        (codec.ReportMessageCommand(message_type=1, message="Hi"), "40FF014869"),
        (codec.RequestTimeCommand(), "41FF00"),
        (
            codec.RequestTimeCommandResponse(
                hour=1,
                minute=2,
                second=3,
                weekday=4,
                day=5,
                month=6,
                year=7,
            ),
            "C1FF01020304050607",
        ),
        (codec.SettingsManagementCommand(device_id=HexBytes("00D4"), instruction=0), "42FF00D400"),
        (
            codec.MiscellaneousDeviceSettingsCommand(
                device_id=HexBytes("00D4"),
                option=1,
                command=2,
                payload=HexBytes("03"),
            ),
            "43FF00D4010203",
        ),
        (codec.ZoneGainCommand(output=1, gain=2), "440102"),
        (codec.UserAccountsCommand(device_id=HexBytes("00D4"), entry_index=1), "45FF00D401"),
        (codec.SourceSpecificMetadataCommand(source_selector=1, position=3, value="Title"), "46FF01035469746C65"),
        (codec.SourceSpecificMetadataRequestCommand(source_selector=1, position=3), "47FF0103"),
        (codec.PowerOnVolumeLevelCommand(output=1, power_on_volume=0.0, detail=(0,)), "48010000"),
        (codec.RequestKeypadZoneAssignmentCommand(keypad_id=HexBytes("01020304")), "49FF01020304"),
        (
            codec.RequestKeypadZoneAssignmentCommandResponse(
                keypad_id=HexBytes("01020304"),
                assigned_output=1,
            ),
            "C9FF0102030401",
        ),
        (codec.KeypadPortZoneMappingCommand(device_id=HexBytes("00D4")), "4AFF00D4"),
        (codec.KpeKeyEventCommand(key_code=0x1234), "4BFF1234"),
        (codec.KpeLedControlCommand(payload=HexBytes("01")), "4CFF01"),
        (codec.KeypadPortOccupancyCommand(device_id=HexBytes("00D4")), "4DFF00D4"),
        (codec.KeypadPortOccupancyCommandResponse(device_id=HexBytes("00D4"), occupancy=0), "CDFF00D400"),
        (codec.ArbitraryDataStorageCommand(), "4EFF0000"),
        (codec.DistributedSourceDefinitionRequestCommand(slot_id=0), "4FFF00"),
        (
            codec.DistributedSourceDefinitionCommand(
                slot_id=0,
                backing_device_guid=GUID,
                source_index=6,
                name="M6250 OPT1",
            ),
            f"4FFF00{GUID_WIRE}064D36323530204F505431",
        ),
        (codec.DistributedSourceDefinitionUnusedCommand(slot_id=0), "4FFF0000"),
        (codec.DistributedSourceAudioDelayCommand(payload=HexBytes("01")), "50FF01"),
        (codec.RegisterServiceCommand(source=1, flags=2, payload=HexBytes("03")), "51FF010203"),
        (codec.ExtendedPlayControlCommand(service_id=1, command=2, payload=HexBytes("03")), "52FF010203"),
        (codec.ExtendedPlayStatusCommand(service_id=1, parameter=2, payload=HexBytes("03")), "53FF010203"),
        (codec.ExtendedPlayStatusRequestCommand(service_id=1, payload=HexBytes("02")), "54FF0102"),
        (codec.ServiceStatusCommand(service_id=1, flags=2, zones=(3, 4)), "55FF01020304"),
        (
            codec.SourceMappingCommand(
                device_id=HexBytes("00D4"),
                digital_output=1,
                source=2,
                zones=(3,),
            ),
            "56FF00D4010203",
        ),
        (codec.ExtensibleCommandHandlingCommand(purpose=1, command=2, payload=HexBytes("03")), "57FF010203"),
        (codec.UndocumentedHostIdentityCommand(), "58FF00"),
        (
            codec.UndocumentedHostIdentityCommandResponse(
                guid=UUID("6c126887-df88-bd41-abbd-079c4e743694"),
                mac=HexBytes("ACE14F006012"),
                detail=HexBytes("00"),
            ),
            "58FF006C126887DF88BD41ABBD079C4E743694ACE14F00601200",
        ),
    ],
)
def test_documented_command_patterns_round_trip(command: Command, encoded: str) -> None:
    encoder = CommandEncoder(read_only=False)

    assert str(encoder.encode(command)) == encoded
    assert encoder.decoder(bytes.fromhex(encoded)) == command


def test_pattern_encoder_uses_compiled_pattern_for_power_command_round_trip() -> None:
    encoder = PatternEncoder(StandbyPowerCommand)

    assert str(encoder.encode(StandbyPowerCommand())) == "01FF"
    assert encoder.decode(bytes.fromhex("01FF")) == StandbyPowerCommand()


def test_subclass_encoder_encodes_power_command_registered_under_command() -> None:
    encoder = SubclassEncoder(Command)

    assert str(encoder.encode(StandbyPowerCommand())) == "01FF"
    assert str(encoder.encode(StandbyPowerCommand(output=1, is_on=ToggleBool.On))) == "010101"


def test_subclass_encoder_preserves_matched_encoder_validation_error() -> None:
    encoder = SubclassEncoder(Command)

    with pytest.raises(ValueError, match="out of range"):
        encoder.encode(StandbyPowerCommand(output=256))


def test_command_encoder_filters_writes_only_in_read_only_mode() -> None:
    read_only_encoder = CommandEncoder()

    assert str(read_only_encoder.encode(StandbyPowerCommand())) == "01FF"
    assert read_only_encoder.encode(StandbyPowerCommand(is_on=ToggleBool.On)) is None
    assert read_only_encoder.encode(VolumeUpCommand(output=1)) is None
    assert str(CommandEncoder(read_only=False).encode(StandbyPowerCommand(is_on=ToggleBool.On))) == "01FF01"


def test_command_encoder_decoder_delegates_to_subclass_decode() -> None:
    assert CommandEncoder().decoder(bytes.fromhex("01FF")) == StandbyPowerCommand()
    assert CommandEncoder().decoder(bytes.fromhex("FFFF")) is None


@pytest.mark.parametrize(
    ("op", "encoded"),
    [
        (StandbyPowerCommand(), "01FF"),
        (MuteCommand(), "02FF"),
        (SourceSelectionCommand(), "03FF"),
        (VolumeCommand(), "04FF"),
        (BassCommand(), "05FF"),
        (TrebleCommand(), "06FF"),
        (BalanceCommand(), "07FF"),
        (SendAllParametersCommand(output=1), "0901"),
        (AmplifierSpecialFeaturesCommand(), "0CFF"),
        (MaximumVolumeCommand(), "0DFF"),
        (RequestDeviceInformationCommand(), "14FF06"),
        (RequestExtendedDeviceInformationCommand(), "39FF"),
        (RequestExtendedDeviceInformationCommand(device_id=HexBytes("00D4")), "39FF00D4"),
        (NetworkSettingsDeviceGuidRequestCommand(device_id=HexBytes("00D4")), "3AFF00D485"),
        (ZoneNameRequestCommand(), "38FF"),
        (RequestZoneAssignmentsCommand(), "2FFF"),
        (UndocumentedHostIdentityCommand(), "58FF00"),
        (SourceNameOptionsRequestCommand(output=9), "2909"),
        (SourceNameOptionsCommand(output=9, source_selector=0x05), "290905"),
        (LinkZonesCommand(), "30FF20"),
        (AudioDelayCommand(), "31FF"),
        (SourceSpecificMetadataRequestCommand(source_selector=1, position=3), "47FF0103"),
        (KeypadPortZoneMappingCommand(device_id=HexBytes("00D4")), "4AFF00D4"),
        (KeypadPortOccupancyCommand(device_id=HexBytes("00D4")), "4DFF00D4"),
        (ArbitraryDataStorageCommand(), "4EFF0000"),
        (DistributedSourceDefinitionRequestCommand(), "4FFF"),
        (DistributedSourceDefinitionRequestCommand(slot_id=0), "4FFF00"),
    ],
)
def test_read_patterns_encode_in_read_only_mode(op: Command, encoded: str) -> None:
    assert str(CommandEncoder().encode(op)) == encoded


@pytest.mark.parametrize(
    ("op", "encoded"),
    [
        (StandbyPowerCommand(output=1, is_on=ToggleBool.On), "010101"),
        (MuteCommand(output=1, is_muted=ToggleBool.On), "020100"),
        (SourceSelectionCommand(output=1, source=0x05), "030105"),
        (VolumeCommand(output=1, volume=0.5), "040150"),
        (BassCommand(output=1, bass=-3), "0501FD"),
        (TrebleCommand(output=1, treble=4), "060104"),
        (BalanceCommand(output=1, balance=-10), "0701F6"),
        (AmplifierSpecialFeaturesCommand(output=1, is_loud=True), "0C0101"),
        (MaximumVolumeCommand(output=1, max_volume=1.0), "0D01A0"),
        (VolumeUpCommand(output=1), "1101"),
        (VolumeDownCommand(output=1), "1201"),
        (NetworkSettingsDeviceGuidCommand(device_id=HexBytes("00D4"), guid=GUID), f"3AFF00D405{GUID_WIRE}"),
        (ZoneNameCommand(output=1, name="Kitchen"), "1C014B69746368656E"),
        (
            SourceNameOptionsCommand(
                output=1,
                source_selector=0x05,
                options=HexBytes("000001"),
                name="A1",
            ),
            "2901050000014131",
        ),
        (AudioDelayCommand(output=1, delay=0x14), "310114"),
        (SourceGainCommand(output=1, source_selector=2, gains=(0.5,)), "32010209"),
        (ZoneGainCommand(output=1, gain=2), "440102"),
        (
            SourceSpecificMetadataCommand(source_selector=1, position=3, value="Title"),
            "46FF01035469746C65",
        ),
        (
            DistributedSourceDefinitionCommand(
                slot_id=0,
                backing_device_guid=GUID,
                source_index=6,
                name="M6250 OPT1",
            ),
            f"4FFF00{GUID_WIRE}064D36323530204F505431",
        ),
        (DistributedSourceDefinitionUnusedCommand(slot_id=0), "4FFF0000"),
    ],
)
def test_write_patterns_encode_when_enabled(op: Command, encoded: str) -> None:
    assert str(CommandEncoder(read_only=False).encode(op)) == encoded


def test_distributed_source_definition_commands_share_slot_base() -> None:
    ops = [
        DistributedSourceDefinitionRequestCommand(slot_id=3),
        DistributedSourceDefinitionCommand(
            slot_id=3,
            backing_device_guid=GUID,
            source_index=6,
            name="M6250 OPT1",
        ),
        DistributedSourceDefinitionUnusedCommand(slot_id=3),
    ]

    assert all(isinstance(op, DistributedSourceDefinitionSlotCommand) for op in ops)
    assert [op.slot_id for op in ops] == [3, 3, 3]


def test_device_host_info_exposes_both_observed_guid_orders() -> None:
    op = UndocumentedHostIdentityCommandResponse(
        guid=UUID("8768126c-88df-41bd-abbd-079c4e743694"),
        mac=HexBytes("ACE14F006012"),
        detail=HexBytes("00"),
    )

    assert op.wire_guid == UUID("6c126887-df88-bd41-abbd-079c4e743694")
    assert op.candidate_guids == (
        UUID("8768126c-88df-41bd-abbd-079c4e743694"),
        UUID("6c126887-df88-bd41-abbd-079c4e743694"),
    )


@pytest.mark.parametrize(
    ("encoded", "op"),
    [
        ("010104", StandbyPowerCommand(output=1, is_on=ToggleBool.Toggle)),
        ("020100", MuteCommand(output=1, is_muted=ToggleBool.On)),
        ("020101", MuteCommand(output=1, is_muted=ToggleBool.Off)),
        ("03010580", SourceSelectionCommand(output=1, source=0x05, detail=(0x80,))),
        ("04011428", VolumeCommand(output=1, volume=0.125, detail=(0x28,))),
        ("0501FD", BassCommand(output=1, bass=-3)),
        ("060104", TrebleCommand(output=1, treble=4)),
        ("0701F6", BalanceCommand(output=1, balance=-10)),
        ("090100", SendAllParametersCommand(output=1, request=0)),
        ("0C01000E", AmplifierSpecialFeaturesCommand(output=1, is_loud=False, detail=(0x0E,))),
        ("0D01A0C8", MaximumVolumeCommand(output=1, max_volume=1.0, detail=(0xC8,))),
        ("1101", VolumeUpCommand(output=1)),
        ("1201", VolumeDownCommand(output=1)),
        ("14FF06", RequestDeviceInformationCommand()),
        (
            "94FF0006B000D40102030405060708",
            RequestDeviceInformationCommandResponse(
                firmware=6,
                model_id=HexBytes("B0"),
                device_id=HexBytes("00D4"),
                zones=(1, 2, 3, 4, 5, 6, 7, 8),
            ),
        ),
        ("39FF00D4", RequestExtendedDeviceInformationCommand(device_id=HexBytes("00D4"))),
        (
            "B9FF000000D40603001F260A0100C8ACE14F0055B41907150002",
            RequestExtendedDeviceInformationCommandResponse(
                prefix=HexBytes("0000"),
                device_id=HexBytes("00D4"),
                model_info=HexBytes("0603001F260A0100C8"),
                mac=HexBytes("ACE14F0055B4"),
                detail=HexBytes("1907150002"),
            ),
        ),
        (f"3AFF00D405{GUID_WIRE}", NetworkSettingsDeviceGuidCommand(device_id=HexBytes("00D4"), guid=GUID)),
        ("3AFF00D40601", NetworkSettingsAmplifierStackAssignmentCommandResponse(device_id=HexBytes("00D4"), system_id=1)),
        (
            "3AFF00D403030A0100C8FFFF00000A0100010A010001",
            NetworkSettingsCommandResponse(
                device_id=HexBytes("00D4"),
                setting_id=3,
                payload=HexBytes("030A0100C8FFFF00000A0100010A010001"),
            ),
        ),
        ("1C014B69746368656E", ZoneNameCommand(output=1, name="Kitchen")),
        ("1C0D", ZoneNameCommand(output=13, name="")),
        ("1D0180", PreampVolumeModeCommand(output=1, preamp_volume_mode=-128)),
        ("1EFF00", PresetSelectionStatusCommand(output=ALL_OUTPUTS, preset=0)),
        ("2FFF", RequestZoneAssignmentsCommand()),
        (
            "AFFF00D40102030405060708",
            RequestZoneAssignmentsCommandResponse(
                device_id=HexBytes("00D4"),
                zones=(1, 2, 3, 4, 5, 6, 7, 8),
            ),
        ),
        (
            "29FF054900004D4D532D3541204D",
            SourceNameOptionsCommand(
                output=ALL_OUTPUTS,
                source_selector=0x05,
                options=HexBytes("490000"),
                name="MMS-5A M",
            ),
        ),
        (
            "29090500000115506C617965725F4140414345313446303036303132506C617965725F41",
            SourceNameOptionsCommand(
                output=9,
                source_selector=0x05,
                options=HexBytes("000001"),
                hidden_name="Player_A@ACE14F006012",
                name="Player_A",
            ),
        ),
        (
            "2901000000025335",
            SourceNameOptionsCommand(
                output=1,
                source_selector=0x00,
                options=HexBytes("000002"),
                name="S5",
            ),
        ),
        ("29FF05", SourceNameOptionsCommand(output=ALL_OUTPUTS, source_selector=0x05)),
        ("29FF", SourceNameOptionsRequestCommand(output=ALL_OUTPUTS)),
        ("3001030203", LinkZonesCommand(output=1, flags=3, members=(2, 3))),
        ("310114", AudioDelayCommand(output=1, delay=0x14)),
        (
            "31011900000000000000",
            AudioDelayCommandResponse(output=1, source_delays=(0x19, 0, 0, 0, 0, 0, 0, 0)),
        ),
        (
            "3201FF0000000000000000",
            SourceGainCommand(
                output=1,
                source_selector=0xFF,
                gains=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            ),
        ),
        ("440102", ZoneGainCommand(output=1, gain=2)),
        (
            "46FF01034469737472616374696F6E73",
            SourceSpecificMetadataCommand(
                source_selector=1,
                position=3,
                value="Distractions",
            ),
        ),
        ("47FF0103", SourceSpecificMetadataRequestCommand(source_selector=1, position=3)),
        ("48010000", PowerOnVolumeLevelCommand(output=1, power_on_volume=0.0, detail=(0,))),
        ("58FF00", UndocumentedHostIdentityCommand()),
        (
            "58FF006C126887DF88BD41ABBD079C4E743694ACE14F00601200",
            UndocumentedHostIdentityCommandResponse(
                guid=UUID("6c126887-df88-bd41-abbd-079c4e743694"),
                mac=HexBytes("ACE14F006012"),
                detail=HexBytes("00"),
            ),
        ),
        (
            "4AFF00D40102",
            KeypadPortZoneMappingCommand(device_id=HexBytes("00D4"), payload=HexBytes("0102")),
        ),
        ("4DFF00D4", KeypadPortOccupancyCommand(device_id=HexBytes("00D4"))),
        ("CDFF00D400", KeypadPortOccupancyCommandResponse(device_id=HexBytes("00D4"), occupancy=0)),
        (
            "4EFF800000010203",
            ArbitraryDataStorageCommand(slot_id=0x8000, payload=HexBytes("00010203")),
        ),
        ("4FFF", DistributedSourceDefinitionRequestCommand()),
        ("4FFF00", DistributedSourceDefinitionRequestCommand(slot_id=0)),
        (
            f"4FFF00{GUID_WIRE}064D36323530204F505431",
            DistributedSourceDefinitionCommand(
                slot_id=0,
                backing_device_guid=GUID,
                source_index=6,
                name="M6250 OPT1",
            ),
        ),
        ("4FFF0000", DistributedSourceDefinitionUnusedCommand(slot_id=0)),
    ],
)
def test_protocol_rows_decode(encoded: str, op: Command) -> None:
    assert CommandEncoder().decoder(bytes.fromhex(encoded)) == op


def test_connect_builds_transport_with_command_encoder() -> None:
    transport = connect(
        "127.0.0.1",
        port=12345,
        reconnection_wait_secs=0.1,
        connection_timeout_secs=0.2,
        trace=True,
        read_only=False,
    )

    assert isinstance(transport, Transport)
    assert transport.host == "127.0.0.1"
    assert transport.port == 12345
    assert transport.reconnection_wait_secs == 0.1
    assert transport.connection_timeout_secs == 0.2
    assert transport.trace is True
    assert isinstance(transport.encoder, CommandEncoder)
    assert transport.encoder.read_only is False
