from __future__ import annotations

import argparse
import asyncio

from _system_example import add_connection_args, discover_or_timeout, make_system, selected_hosts


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Enable, unmute, and set every discovered output to one volume."
    )
    add_connection_args(parser, write=True)
    parser.add_argument(
        "--volume",
        type=float,
        default=0.5,
        help="Volume on the AMP 0.0-1.0 scale. Defaults to 0.5.",
    )
    args = parser.parse_args()

    with make_system(selected_hosts(args), read_only=False, trace=args.trace) as system:
        await discover_or_timeout(system, args)
        all_outputs = system.all_outputs()
        all_outputs.enable()
        all_outputs.unmute()
        all_outputs.set_volume(args.volume)
        await asyncio.sleep(args.settle)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
