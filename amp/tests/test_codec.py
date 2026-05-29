from uuid import UUID

import pytest

from amp.byte_utils import HexBytes
from amp.codec import (
    ALL_OUTPUTS,
    BalanceOp,
    BassOp,
    DelayOp,
    DeviceGuidOp,
    DeviceGuidQueryOp,
    DeviceIdDiscoveryOp,
    DeviceIdOp,
    DeviceInfoDiscoveryOp,
    DeviceInfoOp,
    DeviceLinkOp,
    DeviceLinkQueryOp,
    DeviceStateOp,
    DeviceSubInfoOp,
    DeviceSystemIdOp,
    DiagnosticStatus1DOp,
    DiagnosticStatus1EOp,
    ExtendedDeviceInfoDiscovery,
    ExtendedDeviceInfoOp,
    InputGainOp,
    LoudnessOp,
    MaxVolumeOp,
    MuteOp,
    Op,
    OpEncoder,
    OutputGainOp,
    OutputNameOp,
    OutputNameRefreshOp,
    OutputParametersRefreshOp,
    PowerOp,
    PresetGroupOp,
    RemoteSourceDeleteOp,
    RemoteSourceDiscoveryOp,
    RemoteSourceInfoOp,
    SourceDelayStatusOp,
    SourceMetadataOp,
    SourceMetadataQueryOp,
    SourceNameOp,
    SourceSelectOp,
    TrebleOp,
    UnknownOutputStatusOp,
    VolumeDownOp,
    VolumeOp,
    VolumeUpOp,
    ZoneGroupOp,
    connect,
)
from amp.encoder import PatternEncoder, SubclassEncoder
from amp.transport import Transport
from amp.types import ToggleBool


GUID = UUID("674e1900-f8a9-f6be-a465-3d0fbee12977")
GUID_WIRE = "00194E67A9F8BEF6A4653D0FBEE12977"


def test_pattern_encoder_uses_compiled_pattern_for_power_op_round_trip():
    encoder = PatternEncoder(PowerOp)

    assert str(encoder.encode(PowerOp())) == "01FF"
    assert encoder.decode(bytes.fromhex("01FF")) == PowerOp()


def test_subclass_encoder_encodes_power_op_registered_under_op():
    encoder = SubclassEncoder(Op)

    assert str(encoder.encode(PowerOp())) == "01FF"
    assert str(encoder.encode(PowerOp(output=1, is_on=ToggleBool.On))) == "010101"


def test_subclass_encoder_preserves_matched_encoder_validation_error():
    encoder = SubclassEncoder(Op)

    with pytest.raises(ValueError, match="out of range"):
        encoder.encode(PowerOp(output=256))


def test_op_encoder_filters_writes_only_in_read_only_mode():
    read_only_encoder = OpEncoder()

    assert str(read_only_encoder.encode(PowerOp())) == "01FF"
    assert read_only_encoder.encode(PowerOp(is_on=ToggleBool.On)) is None
    assert read_only_encoder.encode(VolumeUpOp(output=1)) is None
    assert str(OpEncoder(read_only=False).encode(PowerOp(is_on=ToggleBool.On))) == "01FF01"


def test_op_encoder_decoder_delegates_to_subclass_decode():
    assert OpEncoder().decoder(bytes.fromhex("01FF")) == PowerOp()
    assert OpEncoder().decoder(bytes.fromhex("FFFF")) is None


@pytest.mark.parametrize(
    ("op", "encoded"),
    [
        (PowerOp(), "01FF"),
        (MuteOp(), "02FF"),
        (SourceSelectOp(), "03FF"),
        (VolumeOp(), "04FF"),
        (BassOp(), "05FF"),
        (TrebleOp(), "06FF"),
        (BalanceOp(), "07FF"),
        (OutputParametersRefreshOp(output=1), "090100"),
        (LoudnessOp(), "0CFF"),
        (MaxVolumeOp(), "0DFF"),
        (DeviceInfoDiscoveryOp(), "14FF06"),
        (ExtendedDeviceInfoDiscovery(), "39FF"),
        (ExtendedDeviceInfoDiscovery(device_id=HexBytes("00D4")), "39FF00D4"),
        (DeviceGuidQueryOp(device_id=HexBytes("00D4")), "3AFF00D485"),
        (OutputNameRefreshOp(), "38FF"),
        (DeviceIdDiscoveryOp(), "2FFF"),
        (ZoneGroupOp(), "30FF20"),
        (DelayOp(), "31FF"),
        (SourceMetadataQueryOp(source_selector=1, position=3), "47FF0103"),
        (DeviceStateOp(device_id=HexBytes("00D4")), "4AFF00D4"),
        (DeviceLinkQueryOp(device_id=HexBytes("00D4")), "4DFF00D4"),
        (DeviceLinkOp(device_id=HexBytes("00D4")), "CDFF00D4"),
        (PresetGroupOp(), "4EFF0000"),
        (RemoteSourceDiscoveryOp(), "4FFF"),
        (RemoteSourceDiscoveryOp(slot_id=0), "4FFF00"),
    ],
)
def test_read_patterns_encode_in_read_only_mode(op: Op, encoded: str):
    assert str(OpEncoder().encode(op)) == encoded


@pytest.mark.parametrize(
    ("op", "encoded"),
    [
        (PowerOp(output=1, is_on=ToggleBool.On), "010101"),
        (MuteOp(output=1, is_muted=ToggleBool.On), "020100"),
        (SourceSelectOp(output=1, source=5), "030105"),
        (VolumeOp(output=1, volume=0.5), "040150"),
        (BassOp(output=1, bass=-3), "0501FD"),
        (TrebleOp(output=1, treble=4), "060104"),
        (BalanceOp(output=1, balance=-10), "0701F6"),
        (LoudnessOp(output=1, is_loud=True), "0C0101"),
        (MaxVolumeOp(output=1, max_volume=0xA0), "0D01A0"),
        (VolumeUpOp(output=1), "1101"),
        (VolumeDownOp(output=1), "1201"),
        (DeviceGuidOp(device_id=HexBytes("00D4"), guid=GUID), f"3AFF00D405{GUID_WIRE}"),
        (OutputNameOp(output=1, name="Kitchen"), "1C014B69746368656E"),
        (
            SourceNameOp(
                output=1,
                source_selector=HexBytes("05"),
                misc=HexBytes("000001"),
                name="A1",
            ),
            "2901050000014131",
        ),
        (DelayOp(output=1, delay=0x14), "310114"),
        (InputGainOp(output=1, source_selector=2, gain=0.5), "32010209"),
        (OutputGainOp(output=1, gain=2), "440102"),
        (
            SourceMetadataOp(source_selector=1, position=3, value="Title"),
            "46FF01035469746C65",
        ),
        (DeviceLinkQueryOp(device_id=HexBytes("00D4"), linked=True), "4DFF00D401"),
        (DeviceLinkOp(device_id=HexBytes("00D4"), linked=False), "CDFF00D400"),
        (
            RemoteSourceInfoOp(
                slot_id=0,
                backing_device_guid=GUID,
                source_index=6,
                name="M6250 OPT1",
            ),
            f"4FFF00{GUID_WIRE}064D36323530204F505431",
        ),
        (RemoteSourceDeleteOp(slot_id=0), "4FFF0000"),
    ],
)
def test_write_patterns_encode_when_enabled(op: Op, encoded: str):
    assert str(OpEncoder(read_only=False).encode(op)) == encoded


@pytest.mark.parametrize(
    ("encoded", "op"),
    [
        ("010104", PowerOp(output=1, is_on=ToggleBool.Toggle)),
        ("020100", MuteOp(output=1, is_muted=ToggleBool.On)),
        ("020101", MuteOp(output=1, is_muted=ToggleBool.Off)),
        ("03010580", SourceSelectOp(output=1, source=5, detail=[0x80])),
        ("04011428", VolumeOp(output=1, volume=0.125, detail=[0x28])),
        ("0501FD", BassOp(output=1, bass=-3)),
        ("060104", TrebleOp(output=1, treble=4)),
        ("0701F6", BalanceOp(output=1, balance=-10)),
        ("090100", OutputParametersRefreshOp(output=1, request=0)),
        ("0C01000E", LoudnessOp(output=1, is_loud=False, detail=[0x0E])),
        ("0D01A0C8", MaxVolumeOp(output=1, max_volume=0xA0, detail=[0xC8])),
        ("1101", VolumeUpOp(output=1)),
        ("1201", VolumeDownOp(output=1)),
        ("14FF06", DeviceInfoDiscoveryOp()),
        (
            "94FF0006B000D40102030405060708",
            DeviceInfoOp(
                firmware=6,
                model_id=HexBytes("B0"),
                device_id=HexBytes("00D4"),
                zones=[1, 2, 3, 4, 5, 6, 7, 8],
            ),
        ),
        ("39FF00D4", ExtendedDeviceInfoDiscovery(device_id=HexBytes("00D4"))),
        (
            "B9FF000000D40603001F260A0100C8ACE14F0055B41907150002",
            ExtendedDeviceInfoOp(
                output=ALL_OUTPUTS,
                device_id=HexBytes("00D4"),
                mac=HexBytes("ACE14F0055B4"),
            ),
        ),
        (f"3AFF00D405{GUID_WIRE}", DeviceGuidOp(device_id=HexBytes("00D4"), guid=GUID)),
        ("3AFF00D40601", DeviceSystemIdOp(device_id=HexBytes("00D4"), system_id=1)),
        (
            "3AFF00D403030A0100C8FFFF00000A0100010A010001",
            DeviceSubInfoOp(
                device_id=HexBytes("00D4"),
                subtype=3,
                payload=HexBytes("030A0100C8FFFF00000A0100010A010001"),
            ),
        ),
        ("1C014B69746368656E", OutputNameOp(output=1, name="Kitchen")),
        ("1C0D", OutputNameOp(output=13, name="")),
        ("1D0180", DiagnosticStatus1DOp(output=1, payload=HexBytes("80"))),
        ("1EFF00", DiagnosticStatus1EOp(output=ALL_OUTPUTS, payload=HexBytes("00"))),
        ("2FFF", DeviceIdDiscoveryOp()),
        ("AFFF00D4010203", DeviceIdOp(device_id=HexBytes("00D4"), zones=[1, 2, 3])),
        (
            "2901050000014131",
            SourceNameOp(
                output=1,
                source_selector=HexBytes("05"),
                misc=HexBytes("000001"),
                name="A1",
            ),
        ),
        ("29FF", SourceNameOp()),
        ("3001030203", ZoneGroupOp(output=1, flags=3, members=[2, 3])),
        ("310114", DelayOp(output=1, delay=0x14)),
        (
            "31011900000000000000",
            SourceDelayStatusOp(output=1, source_delays=[0x19, 0, 0, 0, 0, 0, 0, 0]),
        ),
        (
            "3201FF0000000000000000",
            InputGainOp(
                output=1,
                source_selector=0xFF,
                gain=0.0,
                source_gains=[0, 0, 0, 0, 0, 0, 0],
            ),
        ),
        ("440102", OutputGainOp(output=1, gain=2)),
        (
            "46FF01034469737472616374696F6E73",
            SourceMetadataOp(
                source_selector=1,
                position=3,
                value="Distractions",
            ),
        ),
        ("47FF0103", SourceMetadataQueryOp(source_selector=1, position=3)),
        ("48010000", UnknownOutputStatusOp(output=1, payload=HexBytes("0000"))),
        (
            "4AFF00D40102",
            DeviceStateOp(device_id=HexBytes("00D4"), state=HexBytes("0102")),
        ),
        ("4DFF00D401", DeviceLinkQueryOp(device_id=HexBytes("00D4"), linked=True)),
        ("CDFF00D400", DeviceLinkOp(device_id=HexBytes("00D4"), linked=False)),
        (
            "4EFF800000010203",
            PresetGroupOp(slot_id=0x8000, payload=HexBytes("00010203")),
        ),
        ("4FFF", RemoteSourceDiscoveryOp()),
        ("4FFF00", RemoteSourceDiscoveryOp(slot_id=0)),
        (
            f"4FFF00{GUID_WIRE}064D36323530204F505431",
            RemoteSourceInfoOp(
                slot_id=0,
                backing_device_guid=GUID,
                source_index=6,
                name="M6250 OPT1",
            ),
        ),
        ("4FFF0000", RemoteSourceDeleteOp(slot_id=0)),
    ],
)
def test_protocol_rows_decode(encoded: str, op: Op):
    assert OpEncoder().decoder(bytes.fromhex(encoded)) == op


def test_connect_builds_transport_with_op_encoder():
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
    assert isinstance(transport.encoder, OpEncoder)
    assert transport.encoder.read_only is False
