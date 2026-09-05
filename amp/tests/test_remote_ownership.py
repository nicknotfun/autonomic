import asyncio

import pytest

from amp.byte_utils import HexBytes
from amp.codec import (
    DistributedSourceDefinitionCommand,
    DistributedSourceDefinitionUnusedCommand,
)
from amp.system import System, SystemState
from amp.tests.test_system import FakeTransport, GUID


def test_remote_slot_event_cannot_claim_unhosted_device_with_multiple_transports() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        state = SystemState()
        device = state.devices[HexBytes("00D4")]
        device.outputs = (1,)

        async with System((first, second), state) as system:
            system.update(
                DistributedSourceDefinitionCommand(
                    slot_id=0,
                    backing_device_guid=GUID,
                    source_index=6,
                    name="Unattributed source",
                ),
                transport=second,
            )

            assert device.host is None
            assert not state.remote_inputs_by_device
            assert not state.remote_inputs

            with pytest.raises(ValueError, match="owner"):
                await asyncio.wait_for(
                    system.discover_remote_inputs(slot_ids=(0,)), timeout=1
                )
            assert device.host is None
            assert first.sent == []
            assert second.sent == []

    asyncio.run(scenario())


def test_remote_slot_event_can_infer_the_only_device_on_the_only_transport() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        state = SystemState()
        device = state.devices[HexBytes("00D4")]

        async with System(transport, state) as system:
            system.update(
                DistributedSourceDefinitionCommand(
                    slot_id=0,
                    backing_device_guid=GUID,
                    source_index=6,
                    name="Known source",
                ),
                transport=transport,
            )

            assert device.host == transport.host
            slot = state.remote_input_for_device(device.id, 0)
            assert slot is not None
            assert slot.present is True
            assert slot.name == "Known source"

    asyncio.run(scenario())


def test_owned_slot_snapshot_with_stale_host_rejects_unattributed_discovery() -> None:
    async def scenario() -> None:
        transport = FakeTransport("10.1.0.200")
        state = SystemState()
        device = state.devices[HexBytes("00D4")]
        device.host = "10.1.0.199"
        state.update_remote_input(
            device.id,
            DistributedSourceDefinitionCommand(
                slot_id=0,
                backing_device_guid=GUID,
                source_index=6,
                name="Cached source",
            ),
        )

        async with System(transport, state) as system:
            snapshot_before = state.to_json()
            system.update(
                DistributedSourceDefinitionCommand(
                    slot_id=0,
                    backing_device_guid=GUID,
                    source_index=7,
                    name="Unattributed source",
                ),
                transport=transport,
            )
            assert state.to_json() == snapshot_before

            # Without an explicit owner this used to update only the legacy
            # table, then wait forever for a device-owned table to complete.
            with pytest.raises(ValueError, match="owner"):
                await asyncio.wait_for(
                    system.discover_remote_inputs(slot_ids=(0,)), timeout=1
                )

            assert transport.sent == []
            assert state.to_json() == snapshot_before

    asyncio.run(scenario())


def test_unused_scoped_slot_removes_only_its_owners_matching_remote_alias() -> None:
    async def scenario() -> None:
        first = FakeTransport("10.1.0.200")
        second = FakeTransport("10.1.0.201")
        state = SystemState()
        device_ids = (HexBytes("00D4"), HexBytes("00DC"))
        for device_id, transport in zip(device_ids, (first, second), strict=True):
            state.devices[device_id].host = transport.host
            for slot_id in (0, 1):
                state.inputs[(device_id, 0x20 + slot_id)].assigned_name = "Remote alias"
                state.update_remote_input(
                    device_id,
                    DistributedSourceDefinitionCommand(
                        slot_id=slot_id,
                        backing_device_guid=GUID,
                        source_index=6,
                        name="Defined source",
                    ),
                )

        async with System((first, second), state) as system:
            system.update(
                DistributedSourceDefinitionUnusedCommand(slot_id=0), transport=first
            )

            assert (device_ids[0], 0x20) not in state.inputs
            assert (device_ids[0], 0x21) in state.inputs
            assert (device_ids[1], 0x20) in state.inputs
            assert (device_ids[1], 0x21) in state.inputs
            assert state.remote_inputs_by_device[(device_ids[0], 0)].present is False
            assert state.remote_inputs_by_device[(device_ids[1], 0)].present is True

    asyncio.run(scenario())
