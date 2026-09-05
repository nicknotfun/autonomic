from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from uuid import UUID

from _system_example import add_connection_args, discover_or_timeout, selected_hosts

from amp.byte_utils import HexBytes
from amp.codec import Command, CommandEncoder, DistributedSourceDefinitionCommand
from amp.system import REMOTE_INPUT_SLOT_IDS, System
from amp.transport import BaseTransport


@dataclass(frozen=True)
class DistributedSourceDefinition:
    target_device_id: HexBytes
    slot_id: int
    backing_device_guid: UUID
    source_index: int
    name: str


def parse_definition(value: str) -> DistributedSourceDefinition:
    try:
        device_id, slot_id, guid, source_index, name = value.split(":", 4)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected TARGET_DEVICE:SLOT:BACKING_GUID:SOURCE_INDEX:NAME"
        ) from exc
    try:
        definition = DistributedSourceDefinition(
            target_device_id=HexBytes(device_id),
            slot_id=int(slot_id, 0),
            backing_device_guid=UUID(guid),
            source_index=int(source_index, 0),
            name=name,
        )
        definition_command(definition)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return definition


def definition_command(
    definition: DistributedSourceDefinition,
) -> DistributedSourceDefinitionCommand:
    if len(definition.target_device_id) != 2:
        raise ValueError("Target device must be a two-byte device id")
    if definition.slot_id not in REMOTE_INPUT_SLOT_IDS:
        raise ValueError("Distributed source slot must be between 0 and 31")
    command = DistributedSourceDefinitionCommand(
        slot_id=definition.slot_id,
        backing_device_guid=definition.backing_device_guid,
        source_index=definition.source_index,
        name=definition.name,
    )
    if CommandEncoder(read_only=False).encode(command) is None:
        raise ValueError("Cannot encode distributed source definition")
    return command


def prepare_definitions(
    system: System,
    definitions: list[DistributedSourceDefinition],
) -> tuple[tuple[DistributedSourceDefinitionCommand, BaseTransport[Command]], ...]:
    """Resolve and encode every definition before any slot is written."""
    plan: list[tuple[DistributedSourceDefinitionCommand, BaseTransport[Command]]] = []
    for definition in definitions:
        command = definition_command(definition)
        device_id = definition.target_device_id
        if device_id not in system.state.devices:
            raise ValueError(f"Target device {device_id} was not discovered")
        transport = system.transport_for_device_id(device_id)
        if transport is None:
            raise ValueError(f"Target device {device_id} has no known transport")
        transport.validate_send(command)
        plan.append((command, transport))
    return tuple(plan)


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Define one or more eAudioCast distributed-source slots."
    )
    add_connection_args(parser, write=True)
    parser.add_argument(
        "definitions",
        nargs="+",
        type=parse_definition,
        metavar="TARGET:SLOT:GUID:SOURCE_INDEX:NAME",
        help="Distributed Source Definition, for example 6012:0:674e1900-f8a9-f6be-a465-3d0fbee12977:6:.200 OPT1",
    )
    args = parser.parse_args()

    with System(selected_hosts(args), read_only=False, trace=args.trace) as system:
        await discover_or_timeout(system, args, require_complete=True)
        plan = prepare_definitions(system, args.definitions)
        for command, transport in plan:
            system.send_ops(command, transport=transport)
        await asyncio.sleep(args.settle)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
