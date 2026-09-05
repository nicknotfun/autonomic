from __future__ import annotations

import argparse
import asyncio

from _system_example import (
    add_connection_args,
    discover_or_timeout,
    selected_hosts,
    send_output_plan,
)

from amp.codec import StandbyPowerCommand, VolumeCommand
from amp.system import System
from amp.toggle_bool import ToggleBool


def parse_volume(value: str) -> float:
    volume = float(value)
    if not 0.0 <= volume <= 1.0:
        raise argparse.ArgumentTypeError("Volume must be between 0.0 and 1.0")
    return volume


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Enable, unmute, and set every discovered output to one volume."
    )
    add_connection_args(parser, write=True)
    parser.add_argument(
        "--volume",
        type=parse_volume,
        default=0.5,
        help="Volume on the AMP 0.0-1.0 scale. Defaults to 0.5.",
    )
    args = parser.parse_args()

    with System(selected_hosts(args), read_only=False, trace=args.trace) as system:
        await discover_or_timeout(system, args, require_complete=True)
        plan = (
            *(
                VolumeCommand(output=output_id, volume=args.volume)
                for output_id in system.state.outputs
            ),
            *(
                StandbyPowerCommand(output=output_id, is_on=ToggleBool.On)
                for output_id in system.state.outputs
            ),
        )
        send_output_plan(system, plan, unmute=True)
        await asyncio.sleep(args.settle)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
