from __future__ import annotations

import argparse
import asyncio

from _system_example import add_connection_args, discover_or_timeout, make_system, selected_hosts

from amp.system import InputSelector, System


def select_input(system: System, source_name: str | None) -> InputSelector:
    if source_name is not None:
        return system.input_by_name(source_name)
    try:
        first_input = next(iter(system.state.inputs.values()))
    except StopIteration as exc:
        raise ValueError("No inputs were discovered") from exc
    return InputSelector(system, first_input.device_id, first_input.selector)


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

    with make_system(selected_hosts(args), read_only=False, trace=args.trace) as system:
        await discover_or_timeout(system, args)
        source = select_input(system, args.source_name)
        system.all_outputs().set_input(source)
        await asyncio.sleep(args.settle)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
