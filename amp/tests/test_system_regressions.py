import asyncio
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from amp import codec
from amp.byte_utils import HexBytes
from amp.state import SystemState
from amp.system import OutputSelector, System
from amp.tests.test_system import FakeTransport
from amp.transport import ConnectionInterrupted, Transport


A = HexBytes("00D4")
B = HexBytes("00DC")
GUID_A = UUID("674e1900-f8a9-f6be-a465-3d0fbee12977")
GUID_B = UUID("ed6cd4b9-60a8-e845-b485-ace14f0055bc")


def populate_devices(system: System) -> None:
    for device_id, host, output, guid in (
        (A, "10.1.0.200", 5, GUID_A),
        (B, "10.1.0.201", 9, GUID_B),
    ):
        device = system.state.devices[device_id]
        device.host = host
        device.outputs = (output,)
        device.guid = guid
        device.model_id = HexBytes("B0")
        _ = system.state.outputs[output]
    system.apply_hardware_defaults()


def slot(index: int, guid: UUID, source_index: int, name: str) -> codec.DistributedSourceDefinitionCommand:
    return codec.DistributedSourceDefinitionCommand(
        slot_id=index, backing_device_guid=guid, source_index=source_index, name=name
    )


def test_device_remote_tables_do_not_overwrite_another_devices_route() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        async with System((first, second)) as system:
            populate_devices(system)
            system.update(slot(0, GUID_B, 1, "W5"), transport=first)
            system.update(slot(1, GUID_B, 7, "W4"), transport=first)
            desired = system.state.inputs[(B, 0x06)]
            assert system.state.source_selection_command_for_input(5, desired).source == 0x20

            system.update(slot(0, GUID_A, 6, "W1"), transport=second)
            system.update(slot(1, GUID_B, 1, "W5"), transport=second)
            assert system.state.source_selection_command_for_input(5, desired).source == 0x20
            assert system.state.remote_inputs[0].present is None

            system.update(codec.SourceSelectionCommand(output=5, source=0xA0))
            system.update(codec.SourceSelectionCommand(output=9, source=0xA0))
            selected_a = OutputSelector(system, 5).input
            selected_b = OutputSelector(system, 9).input
            assert selected_a is not None and selected_b is not None
            assert (selected_a.device_id, selected_a.selector) == (B, 0x06)
            assert (selected_b.device_id, selected_b.selector) == (A, 0x02)

            loaded = SystemState.from_json(system.state.to_json())
            assert loaded.remote_inputs_by_device[(A, 0)].name == "W5"
            assert loaded.remote_inputs_by_device[(B, 0)].name == "W1"
            assert loaded.source_selection_command_for_input(5, loaded.inputs[(B, 0x06)]).source == 0x20

    asyncio.run(scenario())


def test_remote_alias_resolves_in_the_destination_table() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        async with System((first, second)) as system:
            populate_devices(system)
            third_guid = UUID("11111111-2222-3333-4444-555555555555")
            system.update(slot(0, third_guid, 6, "External"), transport=first)
            system.update(slot(7, third_guid, 6, "External"), transport=second)
            alias = system.state.inputs[(A, 0x20)]
            assert system.state.source_selection_command_for_input(9, alias).source == 0x27
            assert system.state.source_selection_command_for_input(5, alias).source == 0x20

    asyncio.run(scenario())


def test_all_outputs_remote_source_requires_matching_owned_definitions() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        async with System((first, second)) as system:
            populate_devices(system)
            for transport in (first, second):
                system.update(slot(0, GUID_B, 1, "W5"), transport=transport)
            for output_id in (5, 9):
                system.update(codec.SourceSelectionCommand(output=output_id, source=0xA0))
            assert system.all_outputs().remote_source is not None
            system.update(slot(0, GUID_A, 6, "W1"), transport=second)
            assert system.all_outputs().remote_source is None

    asyncio.run(scenario())


def test_unowned_all_outputs_source_name_cannot_populate_another_device() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        async with System((first, second)) as system:
            device = system.state.devices[A]
            device.host = first.host
            system.update(codec.SourceNameOptionsCommand(
                output=codec.ALL_OUTPUTS, source_selector=0x05,
                options=HexBytes("490000"), name="Not this device",
            ), transport=second)
            assert not system.state.inputs

    asyncio.run(scenario())


def test_no_cross_device_fallback_when_destination_table_is_missing() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        async with System((first, second)) as system:
            populate_devices(system)
            system.update(slot(0, GUID_B, 1, "W5"), transport=second)
            with pytest.raises(ValueError, match="no distributed source mapping"):
                system.state.source_selection_command_for_input(5, system.state.inputs[(B, 0x06)])

    asyncio.run(scenario())


def test_remote_discovery_waits_for_each_device_and_refreshes_known_slots() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        async with System((first, second)) as system:
            populate_devices(system)
            for iteration in range(2):
                first.sent.clear()
                second.sent.clear()
                task = asyncio.create_task(system.discover_remote_inputs(
                    slot_ids=(0,), time_between_probes_secs=0.01
                ))
                try:
                    while not first.sent or not second.sent:
                        await asyncio.sleep(0)
                    assert first.sent[0] == (codec.DistributedSourceDefinitionRequestCommand(slot_id=0),)
                    assert second.sent[0] == (codec.DistributedSourceDefinitionRequestCommand(slot_id=0),)
                    first.push(slot(0, GUID_B, iteration, "From A"))
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    assert not task.done()
                    second.push(slot(0, GUID_A, iteration, "From B"))
                    await asyncio.wait_for(task, 1)
                    assert system.state.remote_inputs_by_device[(A, 0)].source_index == iteration
                    assert system.state.remote_inputs_by_device[(B, 0)].source_index == iteration
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_reconnect_refreshes_known_names_and_invalidates_old_routes() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        async with System((first, second)) as system:
            populate_devices(system)
            system.update(slot(0, GUID_B, 1, "W5"), transport=first)
            system.update(slot(0, GUID_A, 6, "W1"), transport=second)
            system.update(ConnectionInterrupted(), transport=first)
            ops = [op for batch in first.sent for op in batch]
            assert codec.SourceNameOptionsRequestCommand(output=5) in ops
            assert codec.ZoneNameRequestCommand(output=codec.ALL_OUTPUTS) in ops
            assert {op.slot_id for op in ops if isinstance(op, codec.DistributedSourceDefinitionRequestCommand)} == set(range(32))
            assert all(not op.is_write() for op in ops)
            assert system.state.remote_inputs_by_device[(A, 0)].present is None
            assert system.state.remote_inputs_by_device[(B, 0)].present is True
            assert second.sent == []
            with pytest.raises(ValueError, match="no distributed source mapping"):
                system.state.source_selection_command_for_input(5, system.state.inputs[(B, 0x06)])

    asyncio.run(scenario())


def test_restore_prevalidates_late_encoding_error_before_any_send(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = FakeTransport("10.1.0.200")
        async with System(transport) as system:
            device = system.state.devices[A]
            device.host = transport.host
            device.outputs = (5,)
            _ = system.state.outputs[5]
            _ = system.state.inputs[(A, 0x05)]
            _ = system.state.inputs[(A, 0x06)]
            saved = SystemState.from_json(system.state.to_json())
            for selector in (0x05, 0x06):
                source = saved.inputs[(A, selector)]
                source.options = HexBytes("490000")
                source.assigned_name = "Player"
            saved.inputs[(A, 0x06)].hidden_name = "x" * 32
            path = tmp_path / "invalid-restore.json"
            await saved.save_to_file(path)
            with pytest.raises(ValueError):
                await system.restore_state(path)
            assert transport.sent == []

    asyncio.run(scenario())


def test_system_prevalidates_all_transports_before_enqueuing() -> None:
    async def scenario() -> None:
        first = Transport(codec.CommandEncoder(read_only=False), "10.1.0.200")
        second = Transport(codec.CommandEncoder(read_only=False), "10.1.0.201")
        # The regression uses the production queue and encoder but no sockets.
        with patch.object(first, "_maybe_start_loop"), patch.object(second, "_maybe_start_loop"):
            async with System((first, second)) as system:
                populate_devices(system)
                with pytest.raises(ValueError):
                    system.send_ops(
                        codec.VolumeCommand(output=5, volume=0.5),
                        codec.VolumeCommand(output=9, volume=2.0),
                    )
                assert first.outbound._queue.empty()
                assert second.outbound._queue.empty()

    asyncio.run(scenario())
