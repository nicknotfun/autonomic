import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.codec import (
    ALL_OUTPUTS,
    NetworkSettingsDeviceGuidCommand,
    NetworkSettingsDeviceGuidRequestCommand,
    UndocumentedHostIdentityCommand,
    UndocumentedHostIdentityCommandResponse,
    RequestZoneAssignmentsCommand,
    RequestDeviceInformationCommand,
    RequestDeviceInformationCommandResponse,
    RequestExtendedDeviceInformationCommand,
    RequestExtendedDeviceInformationCommandResponse,
    SourceGainCommand,
    MaximumVolumeCommand,
    MuteCommand,
    Command,
    CommandEncoder,
    OutputCommand,
    ZoneNameCommand,
    ZoneNameRequestCommand,
    StandbyPowerCommand,
    DistributedSourceDefinitionUnusedCommand,
    DistributedSourceDefinitionRequestCommand,
    DistributedSourceDefinitionCommand,
    SourceNameOptionsRequestCommand,
    SourceNameOptionsCommand,
    SourceSelectionCommand,
    RequestZoneAssignmentsCommandResponse,
    VolumeCommand,
)
from amp.hardware import MA6
from amp.system import (
    PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR,
    REMOTE_INPUT_SLOT_IDS,
    DeviceState,
    InputState,
    OutputState,
    RemoteInput,
    System,
    SystemState,
)
from amp.toggle_bool import ToggleBool
from amp.transport import BaseTransport, ConnectionInterrupted, Transport


GUID = UUID("674e1900-f8a9-f6be-a465-3d0fbee12977")
OTHER_GUID = UUID("11111111-2222-3333-4444-555555555555")


class FakeTransport(BaseTransport[Command]):
    def __init__(self, host: str = "10.1.0.200") -> None:
        self.host = host
        self.sent: list[tuple[Command, ...]] = []
        self.events: asyncio.Queue[Command | ConnectionInterrupted | None] = asyncio.Queue()
        self.shutdown_called = False
        self.aclose_called = False

    def send(self, *ops: Command) -> None:
        self.sent.append(ops)

    async def recv(self) -> AsyncGenerator[Command | ConnectionInterrupted, None]:
        while True:
            op = await self.events.get()
            if op is None:
                break
            yield op

    def push(self, op: Command | ConnectionInterrupted) -> None:
        self.events.put_nowait(op)

    def shutdown(self) -> None:
        self.shutdown_called = True
        self.events.put_nowait(None)

    async def aclose(self) -> None:
        self.aclose_called = True
        self.shutdown()
        await asyncio.sleep(0)


def test_input_physical_source_id_maps_logical_selectors() -> None:
    for selector, physical_source_id in PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR.items():
        source = InputState(
            device_id=HexBytes("00D4"),
            selector=selector,
            assigned_name="Input",
            hidden_name=None,
        )

        assert source.physical_source_id == physical_source_id
        assert source.qualified_name == f"00D4:{physical_source_id}"


def test_input_qualified_names_round_trip_physical_and_logical_selectors() -> None:
    for selector, physical_source_id in PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR.items():
        assert InputState.parse_qualified_name(f"00D4:{physical_source_id}") == (
            HexBytes("00D4"),
            selector,
        )

    assert InputState.parse_qualified_name("00D4:0x01") == (HexBytes("00D4"), 0x01)
    assert InputState.parse_qualified_name("00D4:0X20") == (HexBytes("00D4"), 0x20)


def test_input_physical_source_id_ignores_remote_selectors() -> None:
    source = InputState(
        device_id=HexBytes("00D4"),
        selector=0x20,
        assigned_name="Remote",
        hidden_name=None,
    )

    assert source.physical_source_id is None
    assert source.qualified_name == "00D4:0x20"


def test_input_remote_selector_range_stops_before_casting_selectors() -> None:
    assert InputState(
        device_id=HexBytes("00D4"),
        selector=0x20,
        assigned_name="Remote 20",
        hidden_name=None,
    ).remote
    assert InputState(
        device_id=HexBytes("00D4"),
        selector=0x3F,
        assigned_name="Remote 3F",
        hidden_name=None,
    ).remote
    assert not InputState(
        device_id=HexBytes("00D4"),
        selector=0x40,
        assigned_name="Audio-only S5",
        hidden_name=None,
    ).remote
    assert not InputState(
        device_id=HexBytes("6012"),
        selector=0x50,
        assigned_name="Casting",
        hidden_name=None,
    ).remote


def test_model_hardware_source_metadata_controls_physical_source_ids() -> None:
    local = InputState(device_id=HexBytes("6012"), selector=0x03)
    local.apply_hardware_source(MA6.local_sources[3])

    assert local.hardware_kind == "local"
    assert local.physical_source_id == 4
    assert local.qualified_name == "6012:4"

    casting = InputState(device_id=HexBytes("6012"), selector=0x0B)
    casting.apply_hardware_source(MA6.casting_sources[0])

    assert casting.hardware_kind == "casting"
    assert casting.physical_source_id is None
    assert casting.qualified_name == "6012:0x0B"


def test_input_name_prefers_assigned_name_over_hardware_name() -> None:
    source = InputState(device_id=HexBytes("00D4"), selector=0x02)

    assert source.name == "Input 02"
    assert source.name_discovered is False

    source.apply_hardware_name("OPT1")

    assert source.hardware_name == "OPT1"
    assert source.name == "OPT1"
    assert source.name_discovered is False

    source.name = "W1"
    source.apply_hardware_name("Optical 1")

    assert source.assigned_name == "W1"
    assert source.hardware_name == "Optical 1"
    assert source.name == "W1"
    assert source.name_discovered is True

    source.name = None

    assert source.assigned_name is None
    assert source.name == "Optical 1"
    assert source.name_discovered is False


def test_device_tracks_missing_read_only_update_ops_and_readbacks() -> None:
    device = DeviceState(id=HexBytes("00D4"))

    needed = device.needed_update_ops()

    assert [type(op) for op in needed] == [
        RequestDeviceInformationCommand,
        NetworkSettingsDeviceGuidRequestCommand,
        RequestExtendedDeviceInformationCommand,
    ]
    assert isinstance(needed[1], NetworkSettingsDeviceGuidRequestCommand)
    assert isinstance(needed[2], RequestExtendedDeviceInformationCommand)
    assert needed[1].device_id == HexBytes("00D4")
    assert needed[2].device_id == HexBytes("00D4")
    assert all(not op.is_write() for op in needed)

    device.update(
        RequestDeviceInformationCommandResponse(
            firmware=6,
            model_id=HexBytes("B0"),
            device_id=HexBytes("00D4"),
            zones=(1, 2),
        )
    )
    device.update(NetworkSettingsDeviceGuidCommand(device_id=HexBytes("00D4"), guid=GUID))
    device.update(
        RequestExtendedDeviceInformationCommandResponse(
            prefix=HexBytes("0000"),
            device_id=HexBytes("00D4"),
            model_info=HexBytes("0603001F260A0100C8"),
            mac=HexBytes("ACE14F0055B4"),
            detail=HexBytes("1907150002"),
        )
    )

    assert device.firmware == 6
    assert device.model_id == HexBytes("B0")
    assert device.input_count == 8
    assert device.output_count == 8
    assert device.outputs == (1, 2)
    assert device.guid == GUID
    assert device.mac == HexBytes("ACE14F0055B4")
    assert device.needed_update_ops() == []


def test_hardware_model_input_count_overrides_inferred_input_count() -> None:
    device = DeviceState(id=HexBytes("00D4"))
    device.input_count = 4

    device.update(
        RequestDeviceInformationCommandResponse(
            firmware=6,
            model_id=HexBytes("B0"),
            device_id=HexBytes("00D4"),
            zones=(1, 2),
        )
    )

    assert device.input_count == 8
    assert device.output_count == 8


def test_output_tracks_missing_read_only_update_ops_and_readbacks() -> None:
    output = OutputState(id=1)

    assert output.name is None
    assert output.on is None
    assert output.muted is None
    assert output.source_raw is None
    assert output.volume is None
    assert output.max_volume is None
    assert all(not op.is_write() for op in output.needed_update_ops())

    output.update(StandbyPowerCommand(output=ALL_OUTPUTS, is_on=ToggleBool.On))
    output.update(StandbyPowerCommand(output=1, is_on=ToggleBool.Toggle))
    output.update(MuteCommand(output=1, is_muted=ToggleBool.Off))
    output.update(SourceSelectionCommand(output=1, source=0x45))

    assert output.source_raw == 0x45
    assert output.source_detail == ()

    output.update(SourceSelectionCommand(output=1, source=0xA6))
    output.update(VolumeCommand(output=1, volume=0.5))
    output.update(MaximumVolumeCommand(output=1, max_volume=0.75))
    output.update(ZoneNameCommand(output=1, name="Kitchen"))

    assert output.on is False
    assert output.muted is False
    assert output.source_raw == 0xA6
    assert output.source_detail == ()
    assert output.volume == 0.5
    assert output.max_volume == 0.75
    assert output.name == "Kitchen"
    assert output.needed_update_ops() == []

    unnamed = OutputState(id=13)
    unnamed.update(ZoneNameCommand(output=13, name=""))

    assert unnamed.name == ""
    assert [type(op) for op in unnamed.needed_update_ops()] == [
        StandbyPowerCommand,
        MuteCommand,
        SourceSelectionCommand,
        VolumeCommand,
        MaximumVolumeCommand,
    ]


def test_output_source_selection_tracks_local_and_remote_report_bytes() -> None:
    output = OutputState(id=1)

    output.update(SourceSelectionCommand(output=1, source=0xA0, detail=(0x42,)))

    assert output.source_raw == 0xA0
    assert output.source_detail == (0x42,)


def test_remote_input_tracks_missing_read_only_update_ops_and_readbacks() -> None:
    remote_input = RemoteInput(id=3)

    assert remote_input.present is None
    assert remote_input.needed_update_ops() == [DistributedSourceDefinitionRequestCommand(slot_id=3)]

    assert remote_input.update(
        DistributedSourceDefinitionCommand(
            slot_id=3,
            backing_device_guid=GUID,
            source_index=6,
            name="M6250 OPT1",
        )
    )

    assert remote_input.present is True
    assert remote_input.device_guid == GUID
    assert remote_input.source_index == 6
    assert remote_input.name == "M6250 OPT1"
    assert remote_input.needed_update_ops() == []

    assert remote_input.update(DistributedSourceDefinitionUnusedCommand(slot_id=3))
    assert remote_input.present is False
    assert remote_input.device_guid is None
    assert remote_input.source_index is None
    assert remote_input.name is None
    assert remote_input.needed_update_ops() == []


def test_system_derives_remote_source_backing_source_when_available() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            device = system.state.devices[HexBytes("00D4")]
            device.model_id = HexBytes("B0")
            device.outputs = (1,)
            device.guid = GUID
            system.apply_hardware_defaults()

            system.update(SourceSelectionCommand(output=1, source=0x20))
            output = system.state.outputs[1]

            assert output.source_raw == 0x20
            assert output.source_detail == ()
            assert output.remote_source_selector == 0x20
            assert output.local_source_selector is None
            assert output.selected_reported_source_selector == 0x20
            assert system.state.output_remote_source(output) is None

            output.name = "Kitchen"
            output.on = True
            output.muted = False
            output.volume = 0.5
            output.max_volume = 1.0
            output_selector = system.output(1)

            assert output_selector.local_source_selector is None
            assert output_selector.remote_source_selector == 0x20
            assert output_selector.remote_source is None
            assert output_selector.active_sources == (0x20,)
            assert output_selector.source == 0x20

            system.update(
                DistributedSourceDefinitionCommand(
                    slot_id=0,
                    backing_device_guid=GUID,
                    source_index=6,
                    name="W1",
                )
            )

            assert output.source_raw == 0x20
            assert output.source_detail == ()
            remote_source = system.state.output_remote_source(output)

            assert remote_source == system.state.remote_inputs[0]
            assert output_selector.remote_source is not None
            assert output_selector.remote_source.id == 0
            assert output_selector.remote_source.selector == 0x20
            assert output_selector.remote_source.backing_device is not None
            assert output_selector.remote_source.backing_device.device == device
            assert output_selector.remote_source.backing_source_selector == 0x02
            assert output_selector.remote_source.backing_input is not None
            assert output_selector.remote_source.backing_input.selector == 0x02
            assert output_selector.local_source_selector == 0x02
            assert output_selector.active_sources == (0x02, 0x20)
            assert output_selector.source == 0x02
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_accepts_host_strings_and_builds_transports() -> None:
    async def scenario() -> None:
        single = System(
            "127.0.0.1",
            port=12345,
            reconnection_wait_secs=0.1,
            connection_timeout_secs=0.2,
            trace=True,
            read_only=False,
        )
        try:
            assert len(single.transports) == 1
            assert isinstance(single.transport, Transport)
            assert single.transport.host == "127.0.0.1"
            assert single.transport.port == 12345
            assert single.transport.reconnection_wait_secs == 0.1
            assert single.transport.connection_timeout_secs == 0.2
            assert single.transport.trace is True
            assert isinstance(single.transport.encoder, CommandEncoder)
            assert single.transport.encoder.read_only is False
        finally:
            single.shutdown()

        multiple = System(("127.0.0.1", "127.0.0.2"), port=12346)
        try:
            concrete_transports = tuple(
                transport
                for transport in multiple.transports
                if isinstance(transport, Transport)
            )
            assert len(concrete_transports) == 2
            assert [transport.host for transport in concrete_transports] == [
                "127.0.0.1",
                "127.0.0.2",
            ]
            assert [transport.port for transport in concrete_transports] == [12346, 12346]
        finally:
            multiple.shutdown()

        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_async_context_manager_awaits_transport_close_and_event_tasks() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        async with System(transport) as system:
            tasks = tuple(system.tasks)
            assert tasks
            assert not tasks[0].done()

        assert transport.aclose_called is True
        assert transport.shutdown_called is True
        assert all(task.done() for task in tasks)

    asyncio.run(scenario())


def test_system_applies_transport_events_to_devices_inputs_and_outputs() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            transport.push(RequestZoneAssignmentsCommandResponse(device_id=HexBytes("00D4"), zones=(1, 2)))
            transport.push(
                RequestDeviceInformationCommandResponse(
                    firmware=6,
                    model_id=HexBytes("B0"),
                    device_id=HexBytes("00D4"),
                    zones=(1, 2),
                )
            )
            transport.push(
                SourceNameOptionsCommand(
                    output=1,
                    source_selector=0x05,
                    options=HexBytes("000001"),
                    name="A1",
                )
            )
            transport.push(SourceGainCommand(output=1, source_selector=0xFF, gains=(0.0,) * 8))
            transport.push(StandbyPowerCommand(output=1, is_on=ToggleBool.On))
            transport.push(ZoneNameCommand(output=1, name="Kitchen"))
            transport.push(
                DistributedSourceDefinitionCommand(
                    slot_id=3,
                    backing_device_guid=GUID,
                    source_index=6,
                    name="M6250 OPT1",
                )
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            device = system.state.devices[HexBytes("00D4")]
            assert device.host == "10.1.0.200"
            assert device.outputs == (1, 2)
            assert device.input_count == 8
            assert device.output_count == 8
            input_a1 = system.state.inputs[(HexBytes("00D4"), 0x05)]
            assert input_a1.name == "A1"
            assert input_a1.assigned_name == "A1"
            assert input_a1.hardware_name == "A1"
            assert input_a1.name_discovered is True
            default_opt1 = system.state.inputs[(HexBytes("00D4"), 0x02)]
            assert default_opt1.name == "OPT1"
            assert default_opt1.assigned_name is None
            assert default_opt1.hardware_name == "OPT1"
            assert default_opt1.name_discovered is False
            assert system.state.outputs[1].on is True
            assert system.state.outputs[1].name == "Kitchen"
            assert system.state.remote_inputs[3].device_guid == GUID
            assert system.state.remote_inputs[3].source_index == 6
            assert system.state.remote_inputs[3].name == "M6250 OPT1"

            transport.push(DistributedSourceDefinitionUnusedCommand(slot_id=3))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert system.state.remote_inputs[3].present is False
            assert system.state.remote_inputs[3].device_guid is None
            assert system.state.remote_inputs[3].source_index is None
            assert system.state.remote_inputs[3].name is None
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_device_host_info_updates_matching_device_identity_without_setting_host() -> None:
    async def scenario() -> None:
        transport = FakeTransport(host="10.1.0.201")
        system = System(transport)
        try:
            device = system.state.devices[HexBytes("6012")]
            device.guid = UUID("6c126887-df88-bd41-abbd-079c4e743694")

            transport.push(
                UndocumentedHostIdentityCommandResponse(
                    guid=UUID("8768126c-88df-41bd-abbd-079c4e743694"),
                    mac=HexBytes("ACE14F006012"),
                    detail=HexBytes("00"),
                )
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert device.host is None
            assert device.mac == HexBytes("ACE14F006012")
            assert device.guid == UUID("6c126887-df88-bd41-abbd-079c4e743694")
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_generic_device_id_readbacks_do_not_assign_transport_host() -> None:
    async def scenario() -> None:
        transport = FakeTransport(host="10.1.0.201")
        system = System(transport)
        try:
            transport.push(
                RequestDeviceInformationCommandResponse(
                    firmware=8,
                    model_id=HexBytes("E9"),
                    device_id=HexBytes("6012"),
                    zones=(9, 10),
                )
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            device = system.state.devices[HexBytes("6012")]
            assert device.host is None
            assert device.outputs == (9, 10)
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_multi_transport_events_are_shared_and_writes_stay_on_device_transport() -> None:
    async def scenario() -> None:
        transport_200 = FakeTransport(host="10.1.0.200")
        transport_201 = FakeTransport(host="10.1.0.201")
        system = System((transport_200, transport_201))
        try:
            transport_200.push(RequestZoneAssignmentsCommandResponse(device_id=HexBytes("00D4"), zones=(1, 2)))
            transport_201.push(RequestZoneAssignmentsCommandResponse(device_id=HexBytes("6012"), zones=(9, 10)))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert system.state.devices[HexBytes("00D4")].host == "10.1.0.200"
            assert system.state.devices[HexBytes("6012")].host == "10.1.0.201"

            for output_id in (1, 2, 9, 10):
                output = system.state.outputs[output_id]
                output.name = f"Output {output_id}"
                output.on = True
                output.muted = False
                output.source_raw = 0x05
                output.volume = 0.5
                output.max_volume = 1.0

            transport_201.push(StandbyPowerCommand(output=ALL_OUTPUTS, is_on=ToggleBool.Off))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert system.state.outputs[1].on is True
            assert system.state.outputs[2].on is True
            assert system.state.outputs[9].on is False
            assert system.state.outputs[10].on is False

            system.all_outputs().mute()

            assert transport_200.sent == [
                (
                    MuteCommand(output=1, is_muted=ToggleBool.On),
                    MuteCommand(output=2, is_muted=ToggleBool.On),
                )
            ]
            assert transport_201.sent == [
                (
                    MuteCommand(output=9, is_muted=ToggleBool.On),
                    MuteCommand(output=10, is_muted=ToggleBool.On),
                )
            ]
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_all_outputs_event_updates_only_canonical_outputs() -> None:
    async def scenario() -> None:
        transport = FakeTransport(host="10.1.0.200")
        system = System(transport)
        try:
            device = system.state.devices[HexBytes("00D4")]
            device.host = "10.1.0.200"
            device.outputs = (1, 2)

            transport.push(StandbyPowerCommand(output=ALL_OUTPUTS, is_on=ToggleBool.Off))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert list(system.state.outputs) == []

            output = system.state.outputs[1]
            output.on = True

            transport.push(StandbyPowerCommand(output=ALL_OUTPUTS, is_on=ToggleBool.Off))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert system.state.outputs[1].on is False
            assert list(system.state.outputs) == [1]
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_targeted_output_event_requires_known_device_assignment() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            transport.push(StandbyPowerCommand(output=1, is_on=ToggleBool.On))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert list(system.state.outputs) == []

            system.state.devices[HexBytes("00D4")].outputs = (1,)
            transport.push(StandbyPowerCommand(output=1, is_on=ToggleBool.On))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert list(system.state.outputs) == [1]
            assert system.state.outputs[1].on is True
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_all_outputs_source_name_event_updates_devices_for_transport_host() -> None:
    async def scenario() -> None:
        transport_200 = FakeTransport(host="10.1.0.200")
        transport_201 = FakeTransport(host="10.1.0.201")
        system = System((transport_200, transport_201))
        try:
            device_200 = system.state.devices[HexBytes("00D4")]
            device_200.host = "10.1.0.200"
            device_200.outputs = (1, 2)
            device_201 = system.state.devices[HexBytes("6012")]
            device_201.host = "10.1.0.201"
            device_201.outputs = (9, 10)

            transport_201.push(
                SourceNameOptionsCommand(
                    output=ALL_OUTPUTS,
                    source_selector=0x05,
                    options=HexBytes("490000"),
                    name="Player_A",
                )
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert (HexBytes("00D4"), 0x05) not in system.state.inputs
            input_201 = system.state.inputs[(HexBytes("6012"), 0x05)]
            assert input_201.name == "Player_A"
            assert input_201.assigned_name == "Player_A"
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_output_events_can_identify_device_transport_when_host_is_unknown() -> None:
    async def scenario() -> None:
        transport_200 = FakeTransport(host="10.1.0.200")
        transport_201 = FakeTransport(host="10.1.0.201")
        system = System((transport_200, transport_201))
        try:
            system.state.devices[HexBytes("6012")].outputs = (9, 10)
            output = system.state.outputs[9]
            output.name = "Patio"
            output.muted = False
            output.source_raw = 0x05
            output.volume = 0.5
            output.max_volume = 1.0

            transport_201.push(StandbyPowerCommand(output=9, is_on=ToggleBool.Off))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert system.state.devices[HexBytes("6012")].host == "10.1.0.201"

            system.output(9).mute()

            assert transport_200.sent == []
            assert transport_201.sent == [(MuteCommand(output=9, is_muted=ToggleBool.On),)]
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_unknown_host_output_reads_fan_out_but_writes_do_not() -> None:
    async def scenario() -> None:
        transport_200 = FakeTransport(host="10.1.0.200")
        transport_201 = FakeTransport(host="10.1.0.201")
        system = System((transport_200, transport_201))
        try:
            system.state.devices[HexBytes("6012")].outputs = (9,)

            system.send_ops(ZoneNameRequestCommand(output=9))
            system.send_ops(MuteCommand(output=9, is_muted=ToggleBool.On))

            assert transport_200.sent == [
                (ZoneNameRequestCommand(output=9),),
                (MuteCommand(output=9, is_muted=ToggleBool.On),),
            ]
            assert transport_201.sent == [(ZoneNameRequestCommand(output=9),)]
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_selector_commands_emit_typed_codec_ops_for_all_outputs() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            source_device = system.state.devices[HexBytes("00D4")]
            source_device.outputs = (1, 2)
            source_device.guid = GUID
            source_device.model_id = HexBytes("B0")
            target_device = system.state.devices[HexBytes("6012")]
            target_device.outputs = (9, 10)
            target_device.guid = OTHER_GUID
            target_device.model_id = HexBytes("E9")
            system.apply_hardware_defaults()
            _ = system.state.outputs[2]
            _ = system.state.outputs[10]
            assert list(system.state.outputs) == [2, 10]

            remote_w1 = system.state.remote_inputs[0]
            remote_w1.present = True
            remote_w1.device_guid = GUID
            remote_w1.source_index = 6
            remote_w1.name = "W1"

            w1 = system.state.inputs[(HexBytes("00D4"), 0x02)]
            w1.name = "W1"
            all_outputs = system.all_outputs()

            all_outputs.mute()
            all_outputs.unmute()
            all_outputs.enable()
            all_outputs.disable()
            all_outputs.set_volume(0.5)
            all_outputs.set_max_volume(0.75)
            all_outputs.set_input(system.input_by_name("W1"))

            assert transport.sent == [
                (
                    MuteCommand(output=2, is_muted=ToggleBool.On),
                    MuteCommand(output=10, is_muted=ToggleBool.On),
                ),
                (
                    MuteCommand(output=2, is_muted=ToggleBool.Off),
                    MuteCommand(output=10, is_muted=ToggleBool.Off),
                ),
                (
                    StandbyPowerCommand(output=2, is_on=ToggleBool.On),
                    StandbyPowerCommand(output=10, is_on=ToggleBool.On),
                ),
                (
                    StandbyPowerCommand(output=2, is_on=ToggleBool.Off),
                    StandbyPowerCommand(output=10, is_on=ToggleBool.Off),
                ),
                (
                    VolumeCommand(output=2, volume=0.5),
                    VolumeCommand(output=10, volume=0.5),
                ),
                (
                    MaximumVolumeCommand(output=2, max_volume=0.75),
                    MaximumVolumeCommand(output=10, max_volume=0.75),
                ),
                (
                    SourceSelectionCommand(output=2, source=0x02),
                    SourceSelectionCommand(output=10, source=0x20),
                ),
            ]
            assert all(
                op.output in (2, 10)
                for batch in transport.sent
                for op in batch
                if isinstance(op, OutputCommand)
            )
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_output_selector_set_input_routes_local_and_remote_sources() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            source_device = system.state.devices[HexBytes("00D4")]
            source_device.outputs = (1,)
            source_device.guid = GUID
            source_device.model_id = HexBytes("B0")
            target_device = system.state.devices[HexBytes("6012")]
            target_device.outputs = (9,)
            target_device.guid = OTHER_GUID
            target_device.model_id = HexBytes("E9")
            system.apply_hardware_defaults()
            for output_id in (1, 9):
                output = system.state.outputs[output_id]
                output.name = f"Output {output_id}"
                output.on = True
                output.muted = False
                output.source_raw = 0x05
                output.volume = 0.5
                output.max_volume = 1.0

            remote_w1 = system.state.remote_inputs[4]
            remote_w1.present = True
            remote_w1.device_guid = GUID
            remote_w1.source_index = 6
            remote_w1.name = "W1"

            w1 = system.state.inputs[(HexBytes("00D4"), 0x02)]
            w1.name = "W1"
            input_selector = system.input_by_name("W1")

            system.output(1).set_input(input_selector)
            system.output(9).set_input(input_selector)

            assert transport.sent == [
                (SourceSelectionCommand(output=1, source=0x02),),
                (SourceSelectionCommand(output=9, source=0x24),),
            ]
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_source_selection_command_for_remote_input_uses_selector_for_target_device() -> None:
    state = SystemState()
    source_device = state.devices[HexBytes("00D4")]
    source_device.outputs = (1,)
    source_device.guid = GUID
    source_device.model_id = HexBytes("B0")
    target_device = state.devices[HexBytes("6012")]
    target_device.outputs = (9,)
    target_device.guid = OTHER_GUID
    target_device.model_id = HexBytes("E9")
    state.apply_hardware_defaults()
    _ = state.outputs[1]
    _ = state.outputs[9]

    remote_w1 = state.remote_inputs[0]
    remote_w1.present = True
    remote_w1.device_guid = GUID
    remote_w1.source_index = 6
    remote_w1.name = "W1"

    remote_input = state.inputs[(HexBytes("00D4"), 0x20)]
    remote_input.name = "W1"

    assert state.source_selection_command_for_input(
        1,
        remote_input,
    ) == SourceSelectionCommand(output=1, source=0x02)
    assert state.source_selection_command_for_input(
        9,
        remote_input,
    ) == SourceSelectionCommand(output=9, source=0x20)


def test_output_selector_set_input_rejects_missing_remote_mapping() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            source_device = system.state.devices[HexBytes("00D4")]
            source_device.outputs = (1,)
            source_device.guid = GUID
            source_device.model_id = HexBytes("B0")
            target_device = system.state.devices[HexBytes("6012")]
            target_device.outputs = (9,)
            target_device.guid = OTHER_GUID
            target_device.model_id = HexBytes("E9")
            system.apply_hardware_defaults()
            output = system.state.outputs[9]
            output.name = "Output 9"
            output.on = True
            output.muted = False
            output.source_raw = 0x05
            output.volume = 0.5
            output.max_volume = 1.0

            w1 = system.state.inputs[(HexBytes("00D4"), 0x02)]
            w1.name = "W1"

            try:
                system.output(9).set_input(system.input_by_name("W1"))
            except ValueError as exc:
                message = str(exc)
                assert "no distributed source mapping" in message
                assert "00D4" in message
                assert "6012" in message
            else:
                raise AssertionError("cross-device input should require a remote mapping")

            assert transport.sent == []
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_output_selector_resolves_device_and_input() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            device = system.state.devices[HexBytes("00D4")]
            device.firmware = 6
            device.model_id = HexBytes("B0")
            device.outputs = (1,)
            device.guid = GUID
            device.mac = HexBytes("ACE14F0055B4")
            other_device = system.state.devices[HexBytes("6012")]
            other_device.outputs = (9,)

            source = system.state.inputs[(HexBytes("00D4"), 0x26)]
            source.name = "Player_C"
            same_selector_on_other_device = system.state.inputs[(HexBytes("6012"), 0x26)]
            same_selector_on_other_device.name = "Other Player_C"

            output = system.state.outputs[1]
            output.name = "Kitchen"
            output.on = True
            output.muted = False
            output.source_raw = 0x26
            output.volume = 0.5
            output.max_volume = 1.0
            other_output = system.state.outputs[9]
            other_output.source_raw = 0x26

            output_selector = system.output(1)
            selected_input = output_selector.input

            assert output_selector.device.guid == GUID
            assert selected_input is not None
            assert selected_input.name == "Player_C"
            assert selected_input.device.device.id == HexBytes("00D4")
            assert tuple(output.output_id for output in selected_input.outputs) == (1,)

            try:
                system.all_outputs().device
            except ValueError as exc:
                assert "ALL_OUTPUTS" in str(exc)
            else:
                raise AssertionError("ALL_OUTPUTS selector should not resolve to one device")

            try:
                system.all_outputs().input
            except ValueError as exc:
                assert "ALL_OUTPUTS" in str(exc)
            else:
                raise AssertionError("ALL_OUTPUTS selector should not resolve to one input")
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_device_selector_outputs_returns_only_canonical_outputs() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            device = system.state.devices[HexBytes("00D4")]
            device.outputs = (1, 2)
            _ = system.state.outputs[2]

            selector = system.device_by_id(HexBytes("00D4"))

            assert tuple(output.output_id for output in selector.outputs) == (2,)
            assert list(system.state.outputs) == [2]

            try:
                system.device_by_id(HexBytes("6012"))
            except ValueError as exc:
                assert "6012" in str(exc)
            else:
                raise AssertionError("unknown device selector should not create state")

            input_state = system.state.inputs[(HexBytes("00D4"), 0x05)]
            input_selector = system.input_by_id(HexBytes("00D4"), 0x05)

            assert input_selector.input == input_state

            try:
                system.input_by_id(HexBytes("00D4"), 0x06)
            except ValueError as exc:
                assert "00D4:0x06" in str(exc)
            else:
                raise AssertionError("unknown input selector should not create state")
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_selector_str_prints_properties_with_compact_selector_references() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            device = system.state.devices[HexBytes("00D4")]
            device.firmware = 6
            device.model_id = HexBytes("B0")
            device.outputs = (1,)
            device.guid = GUID
            device.mac = HexBytes("ACE14F0055B4")
            system.apply_hardware_defaults()

            input = system.state.inputs[(HexBytes("00D4"), 0x05)]
            input.name = "A1"

            output = system.state.outputs[1]
            output.name = "Kitchen"
            output.on = True
            output.muted = False
            output.source_raw = 0x05
            output.volume = 0.5
            output.max_volume = 1.0

            device_text = str(system.device_by_id(HexBytes("00D4")))
            output_text = str(system.output(1))
            input_text = str(system.input_by_name("A1"))

            assert device_text.startswith("DeviceSelector(")
            assert "id=00D4" in device_text
            assert "outputs=(OutputSelector(id=1),)" in device_text
            assert (
                "inputs=(InputSelector(device_id=00D4, selector=0x05, qualified_name=00D4:1)"
                in device_text
            )

            assert output_text.startswith("OutputSelector(")
            assert "id=1" in output_text
            assert "output=OutputState(id=1)" in output_text
            assert "device=DeviceSelector(id=00D4)" in output_text
            assert (
                "input=InputSelector(device_id=00D4, selector=0x05, qualified_name=00D4:1)"
                in output_text
            )

            assert input_text.startswith("InputSelector(")
            assert "device_id=00D4" in input_text
            assert "selector=0x05" in input_text
            assert "qualified_name='00D4:1'" in input_text
            assert "device=DeviceSelector(id=00D4)" in input_text
            assert "outputs=(OutputSelector(id=1),)" in input_text

            all_outputs_text = str(system.all_outputs())

            assert "id=ALL_OUTPUTS" in all_outputs_text
            assert "is_all_outputs=True" in all_outputs_text
            assert "device=<unavailable: Cannot determine device for ALL_OUTPUTS selector>" in (
                all_outputs_text
            )
            assert "input=<unavailable: Cannot determine input for ALL_OUTPUTS selector>" in (
                all_outputs_text
            )
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_by_name_selectors_ignore_case_and_whitespace() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            remote = system.state.inputs[(HexBytes("00D4"), 0x26)]
            remote.name = "Player C"
            local_duplicate = system.state.inputs[(HexBytes("00D4"), 0x06)]
            local_duplicate.name = "Player C"
            local = system.state.inputs[(HexBytes("00D4"), 0x05)]
            local.name = "Kitchen Input"
            remote_only = system.state.inputs[(HexBytes("00D4"), 0x27)]
            remote_only.name = "Remote Only"

            output = system.state.outputs[1]
            output.name = "Patio West"
            output.on = True
            output.muted = False
            output.source_raw = 0x26
            output.volume = 0.5
            output.max_volume = 1.0

            assert system.input_by_name(" playerc ").selector == 0x06
            assert system.input_by_name(" playerc ", include_remote=True).selector == 0x06
            assert (
                system.input_by_name(
                    " playerc ",
                    include_remote=True,
                    prefer_remote=True,
                ).selector
                == 0x26
            )
            assert system.input_by_name("KITCHENinput").selector == 0x05
            try:
                system.input_by_name("remote only")
            except ValueError as exc:
                assert "remote only" in str(exc)
            else:
                raise AssertionError("remote inputs should be hidden from name lookup by default")
            assert system.input_by_name("remote only", include_remote=True).selector == 0x27
            assert system.output_by_name(" patio   west ").output_id == 1
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_all_inputs_returns_non_remote_selectors_in_physical_order() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            input_a = system.state.inputs[(HexBytes("00D4"), 0x03)]
            input_a.name = "Input A4"
            input_b = system.state.inputs[(HexBytes("00D4"), 0x05)]
            input_b.name = "Input A1"
            remote_duplicate = system.state.inputs[(HexBytes("00D4"), 0x20)]
            remote_duplicate.name = "Input A1"
            input_c = system.state.inputs[(HexBytes("6012"), 0x09)]
            input_c.name = "Input C"

            selectors = system.all_inputs()

            assert tuple(selector.name for selector in selectors) == (
                "Input A1",
                "Input A4",
                "Input C",
            )
            assert tuple(selector.selector for selector in selectors) == (0x05, 0x03, 0x09)
            assert tuple(selector.input.device_id for selector in selectors) == (
                HexBytes("00D4"),
                HexBytes("00D4"),
                HexBytes("6012"),
            )

            selectors_with_remote = system.all_inputs(include_remote=True)

            assert tuple(selector.selector for selector in selectors_with_remote) == (
                0x03,
                0x05,
                0x20,
                0x09,
            )
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_maps_iterate_sorted_by_key() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            _ = system.state.devices[HexBytes("6012")]
            _ = system.state.devices[HexBytes("00D4")]
            _ = system.state.outputs[14]
            _ = system.state.outputs[9]
            _ = system.state.inputs[(HexBytes("6012"), 0x09)]
            _ = system.state.inputs[(HexBytes("00D4"), 0x05)]
            _ = system.state.remote_inputs[3]
            _ = system.state.remote_inputs[1]

            assert list(system.state.devices) == [HexBytes("00D4"), HexBytes("6012")]
            assert list(system.state.outputs) == [9, 14]
            assert list(system.state.inputs) == [
                (HexBytes("00D4"), 0x05),
                (HexBytes("6012"), 0x09),
            ]
            assert list(system.state.remote_inputs) == [1, 3]
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_state_save_load_round_trips_json(tmp_path: Path) -> None:
    async def scenario() -> None:
        state = SystemState()
        device = state.devices[HexBytes("00D4")]
        device.firmware = 6
        device.model_id = HexBytes("B0")
        device.input_count = 8
        device.outputs = (1,)
        device.mac = HexBytes("ACE14F0055B4")
        device.guid = GUID

        source = state.inputs[(HexBytes("00D4"), 0x05)]
        source.name = "A1"
        source.hidden_name = "Analog 1"

        output = state.outputs[1]
        output.name = "Kitchen"
        output.on = True
        output.muted = False
        output.source_raw = 0x05
        output.volume = 0.5
        output.max_volume = 0.75

        remote_input = state.remote_inputs[3]
        remote_input.present = True
        remote_input.device_guid = GUID
        remote_input.source_index = 6
        remote_input.name = "M6250 OPT1"

        path = tmp_path / "system-state.json"
        await state.save_to_file(str(path))

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["devices"]["00D4"]["model_id"] == "B0"
        assert data["inputs"]["00D4:0x05"]["assigned_name"] == "A1"
        assert data["outputs"]["1"]["source_raw"] == 0x05
        assert data["outputs"]["1"]["max_volume"] == 0.75
        assert data["remote_inputs"]["3"]["present"] is True
        assert data["remote_inputs"]["3"]["source_index"] == 6

        loaded = await SystemState.load_from_file(str(path))

        assert loaded.devices[HexBytes("00D4")].guid == GUID
        assert loaded.inputs[(HexBytes("00D4"), 0x05)].name == "A1"
        assert loaded.outputs[1].source_raw == 0x05
        assert loaded.outputs[1].max_volume == 0.75
        assert loaded.remote_inputs[3].present is True
        assert loaded.remote_inputs[3].device_guid == GUID
        assert loaded.remote_inputs[3].name == "M6250 OPT1"

    asyncio.run(scenario())


def test_system_state_load_ignores_legacy_output_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "system-state.json"
        path.write_text(
            json.dumps(
                {
                    "outputs": {
                        "1": {
                            "name": "Kitchen",
                            "on": True,
                            "muted": False,
                            "source": "20",
                            "volume": 0.5,
                            "max_volume": 1.0,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        loaded = await SystemState.load_from_file(str(path))

        assert loaded.outputs[1].source_raw is None

    asyncio.run(scenario())


def test_system_state_load_preserves_explicit_output_source_report() -> None:
    state = SystemState.from_json(
        {
            "devices": {
                "00D4": {
                    "model_id": "B0",
                    "outputs": [1],
                    "guid": str(GUID),
                },
                "6012": {
                    "model_id": "E9",
                    "outputs": [9],
                    "guid": "6c126887-df88-bd41-abbd-079c4e743694",
                },
            },
            "outputs": {
                "1": {
                    "source_raw": 0xA0,
                    "source_detail": [0x42],
                },
            },
        }
    )

    local_output = state.outputs[1]
    assert local_output.source_raw == 0xA0
    assert local_output.source_detail == (0x42,)


def test_system_state_merge_clears_explicit_remote_input_nulls() -> None:
    left = SystemState()
    remote_input = left.remote_inputs[3]
    remote_input.present = True
    remote_input.device_guid = GUID
    remote_input.source_index = 6
    remote_input.name = "M6250 OPT1"

    right = SystemState()
    deleted_remote_input = right.remote_inputs[3]
    deleted_remote_input.present = False
    deleted_remote_input.device_guid = None
    deleted_remote_input.source_index = None
    deleted_remote_input.name = None

    left.merge(right)

    assert remote_input.present is False
    assert remote_input.device_guid is None
    assert remote_input.source_index is None
    assert remote_input.name is None


def test_system_wait_for_change_wakes_for_nested_state_updates() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            output = system.state.outputs[1]
            baseline_version = system.version
            waiter = asyncio.create_task(
                system.wait_for_change(since_version=baseline_version, timeout=1)
            )

            await asyncio.sleep(0)
            output.update(ZoneNameCommand(output=1, name="Kitchen"))

            assert await waiter == system.version
            assert system.version > baseline_version
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_remote_inputs_queries_all_slots_and_wakes_on_readbacks() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            async def complete_remote_input_discovery() -> None:
                while not transport.sent:
                    await asyncio.sleep(0)
                for slot_id in REMOTE_INPUT_SLOT_IDS:
                    if slot_id == 3:
                        transport.push(
                            DistributedSourceDefinitionCommand(
                                slot_id=slot_id,
                                backing_device_guid=GUID,
                                source_index=6,
                                name="M6250 OPT1",
                            )
                        )
                    else:
                        transport.push(DistributedSourceDefinitionUnusedCommand(slot_id=slot_id))

            asyncio.create_task(complete_remote_input_discovery())
            await asyncio.wait_for(
                system.discover_remote_inputs(time_between_probes_secs=30),
                timeout=1,
            )

            assert transport.sent[0] == tuple(
                DistributedSourceDefinitionRequestCommand(slot_id=slot_id) for slot_id in REMOTE_INPUT_SLOT_IDS
            )
            assert list(system.state.remote_inputs) == list(REMOTE_INPUT_SLOT_IDS)
            assert system.state.remote_inputs[0].present is False
            assert system.state.remote_inputs[0].needed_update_ops() == []
            assert system.state.remote_inputs[3].present is True
            assert system.state.remote_inputs[3].device_guid == GUID
            assert system.state.remote_inputs[3].source_index == 6
            assert system.state.remote_inputs[3].name == "M6250 OPT1"
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_refreshes_on_connection_interrupted_event() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            transport.push(ConnectionInterrupted())
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            sent_ops = [op for batch in transport.sent for op in batch]
            assert sent_ops == [
                StandbyPowerCommand(output=ALL_OUTPUTS),
                MuteCommand(output=ALL_OUTPUTS),
                SourceSelectionCommand(output=ALL_OUTPUTS),
                VolumeCommand(output=ALL_OUTPUTS),
                MaximumVolumeCommand(output=ALL_OUTPUTS),
            ]
            assert all(not op.is_write() for op in sent_ops)
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_discovery_and_refresh_emit_only_read_ops() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        device = system.state.devices[HexBytes("00D4")]
        try:
            async def complete_device_discovery() -> None:
                await asyncio.sleep(0)
                device.firmware = 6
                device.model_id = HexBytes("B0")
                device.outputs = (1,)
                device.guid = GUID
                device.mac = HexBytes("ACE14F0055B4")
                device.input_count = 8

            asyncio.create_task(complete_device_discovery())
            await system.discover_devices(target_devices=1, time_between_probes_secs=0)
            system.refresh()

            sent_ops = [op for batch in transport.sent for op in batch]
            assert sent_ops
            assert all(not op.is_write() for op in sent_ops)
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_devices_uses_this_device_id_to_map_each_transport_host() -> None:
    async def scenario() -> None:
        transport_200 = FakeTransport(host="10.1.0.200")
        transport_201 = FakeTransport(host="10.1.0.201")
        system = System((transport_200, transport_201))
        try:
            async def complete_device_discovery() -> None:
                while not transport_200.sent or not transport_201.sent:
                    await asyncio.sleep(0)

                transport_200.push(RequestZoneAssignmentsCommandResponse(device_id=HexBytes("00D4"), zones=(1, 2)))
                transport_201.push(RequestZoneAssignmentsCommandResponse(device_id=HexBytes("6012"), zones=(9, 10)))
                await asyncio.sleep(0)

                device_200 = system.state.devices[HexBytes("00D4")]
                device_200.firmware = 6
                device_200.model_id = HexBytes("B0")
                device_200.guid = GUID
                device_200.mac = HexBytes("ACE14F0055B4")

                device_201 = system.state.devices[HexBytes("6012")]
                device_201.firmware = 8
                device_201.model_id = HexBytes("E9")
                device_201.guid = UUID("6c126887-df88-bd41-abbd-079c4e743694")
                device_201.mac = HexBytes("ACE14F006012")

            asyncio.create_task(complete_device_discovery())
            await asyncio.wait_for(
                system.discover_devices(target_devices=2, time_between_probes_secs=30),
                timeout=1,
            )

            first_200 = set(transport_200.sent[0])
            first_201 = set(transport_201.sent[0])
            assert RequestZoneAssignmentsCommand() in first_200
            assert UndocumentedHostIdentityCommand() in first_200
            assert first_200 == first_201
            assert system.state.devices[HexBytes("00D4")].host == "10.1.0.200"
            assert system.state.devices[HexBytes("6012")].host == "10.1.0.201"
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_devices_continues_when_only_host_ownership_is_missing() -> None:
    async def scenario() -> None:
        transport_200 = FakeTransport(host="10.1.0.200")
        transport_201 = FakeTransport(host="10.1.0.201")
        state = SystemState()
        device_200 = state.devices[HexBytes("00D4")]
        device_200.firmware = 6
        device_200.model_id = HexBytes("B0")
        device_200.outputs = (1, 2)
        device_200.guid = GUID
        device_200.mac = HexBytes("ACE14F0055B4")
        device_201 = state.devices[HexBytes("6012")]
        device_201.firmware = 8
        device_201.model_id = HexBytes("E9")
        device_201.outputs = (9, 10)
        device_201.guid = UUID("6c126887-df88-bd41-abbd-079c4e743694")
        device_201.mac = HexBytes("ACE14F006012")
        system = System((transport_200, transport_201), state=state)
        try:
            await asyncio.wait_for(
                system.discover_devices(target_devices=2, time_between_probes_secs=0),
                timeout=1,
            )

            assert state.devices[HexBytes("00D4")].needed_update_ops() == []
            assert state.devices[HexBytes("6012")].needed_update_ops() == []
            assert any(
                RequestZoneAssignmentsCommand() in batch for batch in transport_200.sent
            )
            assert any(
                UndocumentedHostIdentityCommand() in batch for batch in transport_201.sent
            )
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_devices_waits_for_missing_multi_transport_hosts() -> None:
    async def scenario() -> None:
        transport_200 = FakeTransport(host="10.1.0.200")
        transport_201 = FakeTransport(host="10.1.0.201")
        state = SystemState()
        device_200 = state.devices[HexBytes("00D4")]
        device_200.firmware = 6
        device_200.model_id = HexBytes("B0")
        device_200.outputs = (1, 2)
        device_200.guid = GUID
        device_200.mac = HexBytes("ACE14F0055B4")
        device_201 = state.devices[HexBytes("6012")]
        device_201.firmware = 8
        device_201.model_id = HexBytes("E9")
        device_201.outputs = (9, 10)
        device_201.guid = UUID("6c126887-df88-bd41-abbd-079c4e743694")
        device_201.mac = HexBytes("ACE14F006012")
        system = System((transport_200, transport_201), state=state)
        try:
            async def complete_host_discovery() -> None:
                while not transport_200.sent or not transport_201.sent:
                    await asyncio.sleep(0)
                transport_200.push(RequestZoneAssignmentsCommandResponse(device_id=HexBytes("00D4"), zones=(1, 2)))
                transport_201.push(RequestZoneAssignmentsCommandResponse(device_id=HexBytes("6012"), zones=(9, 10)))

            asyncio.create_task(complete_host_discovery())
            await asyncio.wait_for(
                system.discover_devices(target_devices=2, time_between_probes_secs=30),
                timeout=1,
            )

            assert state.devices[HexBytes("00D4")].host == "10.1.0.200"
            assert state.devices[HexBytes("6012")].host == "10.1.0.201"
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_total_timeout_interrupts_long_probe_wait() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            try:
                await asyncio.wait_for(
                    system.discover(
                        target_devices=1,
                        time_between_probes_secs=30,
                        timeout_secs=0.01,
                    ),
                    timeout=1,
                )
            except TimeoutError:
                elapsed = loop.time() - started_at
            else:
                raise AssertionError("discovery should time out without device responses")

            assert elapsed < 0.5
            assert transport.sent
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_remains_cancellable_by_external_wait_for() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            try:
                await asyncio.wait_for(
                    system.discover(target_devices=1, time_between_probes_secs=30),
                    timeout=0.01,
                )
            except TimeoutError:
                elapsed = loop.time() - started_at
            else:
                raise AssertionError("external wait_for should cancel discovery")

            assert elapsed < 0.5
            assert transport.sent
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_inputs_scans_device_outputs_and_infers_missing_input_count() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            device = system.state.devices[HexBytes("00D4")]
            device.outputs = (1, 2, 6, 7)

            async def complete_input_discovery() -> None:
                while not any(
                    isinstance(op, SourceNameOptionsRequestCommand) and op.output == 6
                    for batch in transport.sent
                    for op in batch
                ):
                    await asyncio.sleep(0)

                transport.push(
                    SourceNameOptionsCommand(
                        output=6,
                        source_selector=0,
                        options=HexBytes("000001"),
                        name="S0",
                    )
                )
                await asyncio.sleep(0.01)
                for selector in range(8):
                    if selector == 0:
                        continue
                    transport.push(
                        SourceNameOptionsCommand(
                            output=6,
                            source_selector=selector,
                            options=HexBytes("000001"),
                            name=f"S{selector}",
                        )
                    )

            asyncio.create_task(complete_input_discovery())
            await asyncio.wait_for(
                system.discover_inputs(
                    time_between_probes_secs=30,
                    time_to_wait_for_devices_with_unknown_inputs=0.03,
                ),
                timeout=1,
            )

            source_name_probes = [
                op
                for batch in transport.sent
                for op in batch
                if isinstance(op, SourceNameOptionsRequestCommand)
            ]
            assert {op.output for op in source_name_probes} == {1, 2, 6, 7}
            assert device.input_count == 8
            assert system.state.inputs[(HexBytes("00D4"), 0)].name == "S0"
            assert system.state.inputs[(HexBytes("00D4"), 7)].name == "S7"
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_inputs_uses_hardware_defaults_but_still_reads_runtime_names() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            system.update(
                RequestDeviceInformationCommandResponse(
                    firmware=6,
                    model_id=HexBytes("B0"),
                    device_id=HexBytes("00D4"),
                    zones=(1,),
                )
            )

            device = system.state.devices[HexBytes("00D4")]
            assert device.input_count == 8
            assert device.output_count == 8
            default_input = system.state.inputs[(HexBytes("00D4"), 0x02)]
            assert default_input.name == "OPT1"
            assert default_input.assigned_name is None
            assert default_input.hardware_name == "OPT1"
            assert default_input.name_discovered is False

            async def complete_input_discovery() -> None:
                while not any(
                    isinstance(op, SourceNameOptionsRequestCommand) and op.output == 1
                    for batch in transport.sent
                    for op in batch
                ):
                    await asyncio.sleep(0)
                runtime_names = {
                    0x05: "A1",
                    0x06: "A2",
                    0x07: "A3",
                    0x03: "A4",
                    0x00: "COAX1",
                    0x01: "COAX2",
                    0x02: "W1",
                    0x04: "W2",
                }
                for selector, name in runtime_names.items():
                    transport.push(
                        SourceNameOptionsCommand(
                            output=1,
                            source_selector=selector,
                            options=HexBytes("000001"),
                            name=name,
                        )
                    )

            asyncio.create_task(complete_input_discovery())
            await asyncio.wait_for(
                system.discover_inputs(time_between_probes_secs=0.01),
                timeout=1,
            )

            assert SourceNameOptionsRequestCommand(output=1) in [
                op for batch in transport.sent for op in batch
            ]
            assert default_input.name == "W1"
            assert default_input.assigned_name == "W1"
            assert default_input.hardware_name == "OPT1"
            assert default_input.name_discovered is True
            assert system.input_by_name("OPT1").selector == 0x02
            try:
                system.input_by_name("OPT1", include_hardware_names=False)
            except ValueError as exc:
                assert "OPT1" in str(exc)
            else:
                raise AssertionError("hardware-name fallback should be optional")
            opt2_input = system.state.inputs[(HexBytes("00D4"), 0x04)]
            assert opt2_input.name == "W2"
            assert opt2_input.assigned_name == "W2"
            assert opt2_input.hardware_name == "OPT2"
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_inputs_retries_known_incomplete_hardware_tables() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            system.update(
                RequestDeviceInformationCommandResponse(
                    firmware=6,
                    model_id=HexBytes("B0"),
                    device_id=HexBytes("00D4"),
                    zones=(1,),
                )
            )

            def source_name_probe_count() -> int:
                return sum(
                    1
                    for batch in transport.sent
                    for op in batch
                    if isinstance(op, SourceNameOptionsRequestCommand) and op.output == 1
                )

            async def complete_input_discovery() -> None:
                while source_name_probe_count() < 1:
                    await asyncio.sleep(0)
                first_pass_names = {
                    0x05: "A1",
                    0x06: "A2",
                    0x07: "A3",
                    0x03: "A4",
                    0x00: "COAX1",
                    0x01: "COAX2",
                    0x02: "OPT1",
                }
                for selector, name in first_pass_names.items():
                    transport.push(
                        SourceNameOptionsCommand(
                            output=1,
                            source_selector=selector,
                            options=HexBytes("000001"),
                            name=name,
                        )
                    )

                while source_name_probe_count() < 2:
                    await asyncio.sleep(0)
                transport.push(
                    SourceNameOptionsCommand(
                        output=1,
                        source_selector=0x04,
                        options=HexBytes("000001"),
                        name="OPT2",
                    )
                )

            asyncio.create_task(complete_input_discovery())
            await asyncio.wait_for(
                system.discover_inputs(
                    time_between_probes_secs=0.01,
                    time_to_wait_for_devices_with_unknown_inputs=0.01,
                ),
                timeout=1,
            )

            assert source_name_probe_count() == 2
            assert len(system.state.discovered_inputs_by_device(HexBytes("00D4"))) == 8
            assert system.state.inputs[(HexBytes("00D4"), 0x04)].name == "OPT2"
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_outputs_resolves_names_then_refreshes_missing_status() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            system.state.devices[HexBytes("6012")].outputs = (13, 14)

            async def complete_output_discovery() -> None:
                system.state.outputs[13].update(ZoneNameCommand(output=13, name=""))
                system.state.outputs[14].update(ZoneNameCommand(output=14, name="Pool"))
                await asyncio.sleep(0)
                output = system.state.outputs[13]
                output.update(StandbyPowerCommand(output=13, is_on=ToggleBool.Off))
                output.update(MuteCommand(output=13, is_muted=ToggleBool.Off))
                output.update(SourceSelectionCommand(output=13, source=0x05))
                output.update(VolumeCommand(output=13, volume=0.3125))
                output.update(MaximumVolumeCommand(output=13, max_volume=0.75))
                output = system.state.outputs[14]
                output.update(StandbyPowerCommand(output=14, is_on=ToggleBool.Off))
                output.update(MuteCommand(output=14, is_muted=ToggleBool.Off))
                output.update(SourceSelectionCommand(output=14, source=0x05))
                output.update(VolumeCommand(output=14, volume=0.3125))
                output.update(MaximumVolumeCommand(output=14, max_volume=0.75))

            asyncio.create_task(complete_output_discovery())
            await system.discover_outputs(time_between_probes_secs=0)

            batches = transport.sent
            assert batches[0] == (
                StandbyPowerCommand(output=ALL_OUTPUTS),
                MuteCommand(output=ALL_OUTPUTS),
                SourceSelectionCommand(output=ALL_OUTPUTS),
                VolumeCommand(output=ALL_OUTPUTS),
                MaximumVolumeCommand(output=ALL_OUTPUTS),
            )
            assert batches[1] == (ZoneNameRequestCommand(output=ALL_OUTPUTS),)
            sent_ops = [op for batch in batches[2:] for op in batch]
            status_ops = [op for op in sent_ops if isinstance(op, OutputCommand)]
            assert {op.output for op in status_ops} == {13, 14}
            assert all(
                isinstance(op, (StandbyPowerCommand, MuteCommand, SourceSelectionCommand, VolumeCommand, MaximumVolumeCommand))
                for op in status_ops
            )
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_outputs_wakes_when_transport_events_complete_state() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)
        try:
            system.state.devices[HexBytes("00D4")].outputs = (1,)

            async def complete_output_discovery() -> None:
                while not transport.sent:
                    await asyncio.sleep(0)
                transport.push(ZoneNameCommand(output=1, name="Kitchen"))
                transport.push(StandbyPowerCommand(output=1, is_on=ToggleBool.On))
                transport.push(MuteCommand(output=1, is_muted=ToggleBool.Off))
                transport.push(SourceSelectionCommand(output=1, source=0x05))
                transport.push(VolumeCommand(output=1, volume=0.5))
                transport.push(MaximumVolumeCommand(output=1, max_volume=0.75))

            asyncio.create_task(complete_output_discovery())
            await asyncio.wait_for(
                system.discover_outputs(time_between_probes_secs=30),
                timeout=1,
            )

            output = system.state.outputs[1]
            assert output.name == "Kitchen"
            assert output.on is True
            assert output.muted is False
            assert output.source_raw == 0x05
            assert output.volume == 0.5
            assert output.max_volume == 0.75
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())
