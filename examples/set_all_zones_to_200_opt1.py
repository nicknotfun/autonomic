from __future__ import annotations

import argparse
import asyncio

from _system_example import (
    add_connection_args,
    discover_or_timeout,
    selected_hosts,
    send_output_plan,
)

from amp.byte_utils import HexBytes
from amp.codec import SourceSelectionCommand
from amp.system import InputSelector, System


M6250_DEVICE = HexBytes("00D4")
MA6_DEVICE = HexBytes("6012")


def normalize_name(name: str) -> str:
    return "".join(name.split()).casefold()


def input_by_device_and_name(
    system: System,
    device_id: HexBytes,
    name: str,
) -> InputSelector:
    normalized_name = normalize_name(name)
    for input in system.state.inputs_by_device(device_id):
        if normalize_name(input.name) == normalized_name:
            return system.input_by_id(input.device_id, input.selector)
    raise ValueError(f"Input {name!r} was not discovered on device {device_id}")


def device_output_plan(
    system: System,
    device_id: HexBytes,
    input: InputSelector,
) -> tuple[SourceSelectionCommand, ...]:
    device = system.state.devices[device_id]
    commands: list[SourceSelectionCommand] = []
    for output_id in device.outputs or ():
        commands.extend(
            system.state.source_selection_commands_for_input(output_id, input.input)
        )
    return tuple(commands)


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Route M6250 and MA6 outputs to named inputs discovered through System."
    )
    add_connection_args(parser, write=True)
    parser.add_argument(
        "--m6250-source-name",
        default="W1",
        help="Input name to use for M6250 outputs. Defaults to W1.",
    )
    parser.add_argument(
        "--ma6-source-name",
        default=".200 OPT1",
        help="Input name to use for MA6 outputs. Defaults to .200 OPT1.",
    )
    args = parser.parse_args()

    with System(selected_hosts(args), read_only=False, trace=args.trace) as system:
        await discover_or_timeout(system, args, require_complete=True)
        plan = (
            *device_output_plan(
                system,
                M6250_DEVICE,
                input_by_device_and_name(system, M6250_DEVICE, args.m6250_source_name),
            ),
            *device_output_plan(
                system,
                MA6_DEVICE,
                input_by_device_and_name(system, MA6_DEVICE, args.ma6_source_name),
            ),
        )
        send_output_plan(system, plan)
        await asyncio.sleep(args.settle)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
