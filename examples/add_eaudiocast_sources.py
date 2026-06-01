from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from uuid import UUID

from _system_example import add_connection_args, discover_or_timeout, make_system, selected_hosts

from amp.byte_utils import HexBytes
from amp.codec import RemoteSourceInfoOp


@dataclass(frozen=True)
class RemoteSourceDefinition:
    target_device_id: HexBytes
    slot_id: int
    backing_device_guid: UUID
    source_index: int
    name: str


def parse_definition(value: str) -> RemoteSourceDefinition:
    try:
        device_id, slot_id, guid, source_index, name = value.split(":", 4)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected TARGET_DEVICE:SLOT:BACKING_GUID:SOURCE_INDEX:NAME"
        ) from exc
    return RemoteSourceDefinition(
        target_device_id=HexBytes(device_id),
        slot_id=int(slot_id, 0),
        backing_device_guid=UUID(guid),
        source_index=int(source_index, 0),
        name=name,
    )


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Define one or more eAudioCast remote-source slots."
    )
    add_connection_args(parser, write=True)
    parser.add_argument(
        "definitions",
        nargs="+",
        type=parse_definition,
        metavar="TARGET:SLOT:GUID:SOURCE_INDEX:NAME",
        help="Remote source definition, for example 6012:0:674e1900-f8a9-f6be-a465-3d0fbee12977:6:.200 OPT1",
    )
    args = parser.parse_args()

    with make_system(selected_hosts(args), read_only=False, trace=args.trace) as system:
        await discover_or_timeout(system, args)
        for definition in args.definitions:
            op = RemoteSourceInfoOp(
                slot_id=definition.slot_id,
                backing_device_guid=definition.backing_device_guid,
                source_index=definition.source_index,
                name=definition.name,
            )
            transport = system.transport_for_device_id(definition.target_device_id) or system.transport
            system.send_ops(op, transport=transport)
        await asyncio.sleep(args.settle)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
