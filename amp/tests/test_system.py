import asyncio
from typing import AsyncGenerator
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.codec import (
    ALL_OUTPUTS,
    DeviceGuidOp,
    DeviceGuidQueryOp,
    DeviceInfoDiscoveryOp,
    DeviceInfoOp,
    ExtendedDeviceInfoDiscoveryOp,
    ExtendedDeviceInfoOp,
    InputGainOp,
    MuteOp,
    Op,
    OutputOp,
    OutputNameOp,
    OutputNameRefreshOp,
    PowerOp,
    SourceNameOp,
    SourceSelectOp,
    ThisDeviceIdOp,
    VolumeOp,
)
from amp.system import Device, Input, Output, System, UNKNOWN
from amp.toggle_bool import ToggleBool
from amp.transport import ConnectionInterrupted


GUID = UUID("674e1900-f8a9-f6be-a465-3d0fbee12977")


class FakeTransport:
    def __init__(self) -> None:
        self.host = "10.1.0.200"
        self.sent: list[tuple[Op, ...]] = []
        self.events: asyncio.Queue[Op | ConnectionInterrupted | None] = asyncio.Queue()
        self.shutdown_called = False

    def send(self, *ops: Op) -> None:
        self.sent.append(ops)

    async def recv(self) -> AsyncGenerator[Op | ConnectionInterrupted, None]:
        while True:
            op = await self.events.get()
            if op is None:
                break
            yield op

    def push(self, op: Op | ConnectionInterrupted) -> None:
        self.events.put_nowait(op)

    def shutdown(self) -> None:
        self.shutdown_called = True
        self.events.put_nowait(None)


def test_input_physical_source_id_maps_logical_selectors() -> None:
    selectors = {
        0x05: 1,
        0x06: 2,
        0x07: 3,
        0x03: 4,
        0x00: 5,
        0x01: 6,
        0x02: 7,
        0x04: 8,
        0x08: 9,
        0x09: 10,
        0x0A: 11,
        0x0B: 12,
    }

    for selector, physical_source_id in selectors.items():
        source = Input(
            device_id=HexBytes("00D4"),
            selector=selector,
            name="Input",
            hidden_name=None,
        )

        assert source.physical_source_id == physical_source_id
        assert source.qualified_name == f"00D4:{physical_source_id}"


def test_input_physical_source_id_ignores_remote_selectors() -> None:
    source = Input(
        device_id=HexBytes("00D4"),
        selector=0x20,
        name="Remote",
        hidden_name=None,
    )

    assert source.physical_source_id is None
    assert source.qualified_name == "00D4:0x20"


def test_device_tracks_missing_read_only_update_ops_and_readbacks() -> None:
    device = Device(id=HexBytes("00D4"))

    needed = device.needed_update_ops()

    assert [type(op) for op in needed] == [
        DeviceInfoDiscoveryOp,
        DeviceGuidQueryOp,
        ExtendedDeviceInfoDiscoveryOp,
    ]
    assert isinstance(needed[1], DeviceGuidQueryOp)
    assert isinstance(needed[2], ExtendedDeviceInfoDiscoveryOp)
    assert needed[1].device_id == HexBytes("00D4")
    assert needed[2].device_id == HexBytes("00D4")
    assert all(not op.is_write() for op in needed)

    device.update(
        DeviceInfoOp(
            firmware=6,
            model_id=HexBytes("B0"),
            device_id=HexBytes("00D4"),
            zones=(1, 2),
        )
    )
    device.update(DeviceGuidOp(device_id=HexBytes("00D4"), guid=GUID))
    device.update(
        ExtendedDeviceInfoOp(
            prefix=HexBytes("0000"),
            device_id=HexBytes("00D4"),
            model_info=HexBytes("0603001F260A0100C8"),
            mac=HexBytes("ACE14F0055B4"),
            detail=HexBytes("1907150002"),
        )
    )

    assert device.firmware == 6
    assert device.model_id == HexBytes("B0")
    assert device.outputs == (1, 2)
    assert device.guid == GUID
    assert device.mac == HexBytes("ACE14F0055B4")
    assert device.needed_update_ops() == []


def test_output_tracks_missing_read_only_update_ops_and_readbacks() -> None:
    output = Output(id=1)

    assert output.name is UNKNOWN
    assert output.on is UNKNOWN
    assert output.muted is UNKNOWN
    assert output.source is UNKNOWN
    assert output.volume is UNKNOWN
    assert all(not op.is_write() for op in output.needed_update_ops())

    output.update(PowerOp(output=ALL_OUTPUTS, is_on=ToggleBool.On))
    output.update(PowerOp(output=1, is_on=ToggleBool.Toggle))
    output.update(MuteOp(output=1, is_muted=ToggleBool.Off))
    output.update(SourceSelectOp(output=1, source=HexBytes("05")))
    output.update(VolumeOp(output=1, volume=0.5))
    output.update(OutputNameOp(output=1, name="Kitchen"))

    assert output.on is False
    assert output.muted is False
    assert output.source == HexBytes("05")
    assert output.volume == 0.5
    assert output.name == "Kitchen"
    assert output.needed_update_ops() == []

    unnamed = Output(id=13)
    unnamed.update(OutputNameOp(output=13, name=""))

    assert unnamed.name == ""
    assert unnamed.needed_update_ops(only_named=True) == []


def test_system_applies_transport_events_to_devices_inputs_and_outputs() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)  # type: ignore[arg-type]
        try:
            transport.push(ThisDeviceIdOp(device_id=HexBytes("00D4"), zones=(1, 2)))
            transport.push(
                DeviceInfoOp(
                    firmware=6,
                    model_id=HexBytes("B0"),
                    device_id=HexBytes("00D4"),
                    zones=(1, 2),
                )
            )
            transport.push(
                SourceNameOp(
                    output=1,
                    source_selector=0x05,
                    misc=HexBytes("000001"),
                    name="A1",
                )
            )
            transport.push(InputGainOp(output=1, source_selector=0xFF, gains=(0.0,) * 8))
            transport.push(PowerOp(output=1, is_on=ToggleBool.On))
            transport.push(OutputNameOp(output=1, name="Kitchen"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            device = system.devices[HexBytes("00D4")]
            assert device.ip == "10.1.0.200"
            assert device.outputs == (1, 2)
            assert device.input_count == 8
            assert system.inputs[(HexBytes("00D4"), 0x05)].name == "A1"
            assert system.outputs[1].on is True
            assert system.outputs[1].name == "Kitchen"
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_maps_iterate_sorted_by_key() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)  # type: ignore[arg-type]
        try:
            _ = system.devices[HexBytes("6012")]
            _ = system.devices[HexBytes("00D4")]
            _ = system.outputs[14]
            _ = system.outputs[9]
            _ = system.inputs[(HexBytes("6012"), 0x09)]
            _ = system.inputs[(HexBytes("00D4"), 0x05)]

            assert list(system.devices) == [HexBytes("00D4"), HexBytes("6012")]
            assert list(system.outputs) == [9, 14]
            assert list(system.inputs) == [
                (HexBytes("00D4"), 0x05),
                (HexBytes("6012"), 0x09),
            ]
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_refreshes_on_connection_interrupted_event() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)  # type: ignore[arg-type]
        try:
            transport.push(ConnectionInterrupted())
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            sent_ops = [op for batch in transport.sent for op in batch]
            assert sent_ops == [
                PowerOp(output=ALL_OUTPUTS),
                MuteOp(output=ALL_OUTPUTS),
                SourceSelectOp(output=ALL_OUTPUTS),
                VolumeOp(output=ALL_OUTPUTS),
            ]
            assert all(not op.is_write() for op in sent_ops)
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_system_discovery_and_refresh_emit_only_read_ops() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)  # type: ignore[arg-type]
        device = system.devices[HexBytes("00D4")]
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


def test_discover_outputs_only_named_resolves_names_then_skips_blank_names() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)  # type: ignore[arg-type]
        try:
            system.devices[HexBytes("6012")].outputs = (13, 14)

            async def complete_output_discovery() -> None:
                system.outputs[13].update(OutputNameOp(output=13, name=""))
                system.outputs[14].update(OutputNameOp(output=14, name="Pool"))
                await asyncio.sleep(0)
                output = system.outputs[14]
                output.update(PowerOp(output=14, is_on=ToggleBool.Off))
                output.update(MuteOp(output=14, is_muted=ToggleBool.Off))
                output.update(SourceSelectOp(output=14, source=HexBytes("05")))
                output.update(VolumeOp(output=14, volume=0.3125))

            asyncio.create_task(complete_output_discovery())
            await system.discover_outputs(only_named=True, time_between_probes_secs=0)

            batches = transport.sent
            assert {type(op) for op in batches[0]} == {OutputNameRefreshOp}
            name_refresh_ops = [op for op in batches[0] if isinstance(op, OutputNameRefreshOp)]
            status_ops = [op for op in batches[1] if isinstance(op, OutputOp)]
            assert {op.output for op in name_refresh_ops} == {13, 14}
            assert len(status_ops) == len(batches[1])
            assert all(op.output == 14 for op in status_ops)
            assert all(isinstance(op, (PowerOp, MuteOp, SourceSelectOp, VolumeOp)) for op in batches[1])
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discover_outputs_wakes_when_transport_events_complete_state() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        system = System(transport)  # type: ignore[arg-type]
        try:
            system.devices[HexBytes("00D4")].outputs = (1,)

            async def complete_output_discovery() -> None:
                while not transport.sent:
                    await asyncio.sleep(0)
                transport.push(OutputNameOp(output=1, name="Kitchen"))
                transport.push(PowerOp(output=1, is_on=ToggleBool.On))
                transport.push(MuteOp(output=1, is_muted=ToggleBool.Off))
                transport.push(SourceSelectOp(output=1, source=HexBytes("05")))
                transport.push(VolumeOp(output=1, volume=0.5))

            asyncio.create_task(complete_output_discovery())
            await asyncio.wait_for(
                system.discover_outputs(time_between_probes_secs=30),
                timeout=1,
            )

            output = system.outputs[1]
            assert output.name == "Kitchen"
            assert output.on is True
            assert output.muted is False
            assert output.source == HexBytes("05")
            assert output.volume == 0.5
        finally:
            system.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())
