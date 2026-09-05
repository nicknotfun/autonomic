from __future__ import annotations

import argparse
import asyncio

from _system_example import (
    add_connection_args,
    discover_or_timeout,
    selected_hosts,
    send_output_plan,
)

from amp.codec import ALL_OUTPUTS
from amp.system import InputSelector, System


def select_input(system: System, source_name: str | None) -> InputSelector:
    if source_name is not None:
        return system.input_by_name(source_name)
    try:
        first_input = next(iter(system.state.inputs.values()))
    except StopIteration as exc:
        raise ValueError("No inputs were discovered") from exc
    return system.input_by_id(first_input.device_id, first_input.selector)


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Set every discovered output to one input by name."
    )
    add_connection_args(parser, write=True)
    parser.add_argument(
        "source_name",
        nargs="?",
        help="Input name. Defaults to the first discovered input.",
    )
    args = parser.parse_args()

    with System(selected_hosts(args), read_only=False, trace=args.trace) as system:
        await discover_or_timeout(system, args, require_complete=True)
        source = select_input(system, args.source_name)
        plan = system.state.source_selection_commands_for_input(ALL_OUTPUTS, source.input)
        send_output_plan(system, plan)
        await asyncio.sleep(args.settle)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
