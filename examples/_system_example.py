from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amp.codec import ALL_OUTPUTS, CommandEncoder, MuteCommand, OutputCommand
from amp.system import System
from amp.toggle_bool import ToggleBool


DEFAULT_HOST = "10.1.0.200"


def add_connection_args(parser: argparse.ArgumentParser, *, write: bool = False) -> None:
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        help=f"Amplifier hostname or IP address. Repeat for multiple transports. Defaults to {DEFAULT_HOST}.",
    )
    parser.add_argument(
        "--target-devices",
        type=int,
        default=2,
        help="Number of devices expected during discovery.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Maximum seconds to wait for discovery.",
    )
    parser.add_argument("--trace", action="store_true", help="Print raw protocol rows.")
    if write:
        parser.add_argument(
            "--settle",
            type=float,
            default=1.0,
            help="Seconds to leave the transport open after queuing writes.",
        )


def selected_hosts(args: argparse.Namespace) -> tuple[str, ...]:
    hosts = tuple(args.hosts or ())
    return hosts or (DEFAULT_HOST,)


async def discover_or_timeout(
    system: System,
    args: argparse.Namespace,
    *,
    require_complete: bool = False,
) -> None:
    try:
        await asyncio.wait_for(
            system.discover(target_devices=args.target_devices),
            timeout=args.timeout,
        )
    except asyncio.TimeoutError:
        if require_complete:
            raise TimeoutError("Discovery timed out; no configuration writes were sent")
        print("Discovery timed out; continuing with partial state.")


def send_output_plan(
    system: System,
    ops: Iterable[OutputCommand],
    *,
    unmute: bool = False,
) -> None:
    """Validate a whole output plan and mute while applying it.

    Source-only plans restore each output's previous mute state. A caller that
    explicitly requests playback can instead unmute every output at the end.
    """
    commands = tuple(ops)
    output_ids = tuple(dict.fromkeys(op.output for op in commands))
    final_mutes: list[MuteCommand] = []
    for output_id in output_ids:
        if output_id == ALL_OUTPUTS or output_id not in system.state.outputs:
            raise ValueError(f"Output {output_id} was not discovered")
        device = system.state.device_for_output(output_id)
        if device is None or system.transport_for_device_id(device.id) is None:
            raise ValueError(f"Output {output_id} has no known device transport")
        muted = system.state.outputs[output_id].muted
        if not unmute and muted is None:
            raise ValueError(f"Output {output_id} has no known mute state")
        final_mutes.append(
            MuteCommand(
                output=output_id,
                is_muted=ToggleBool.Off if unmute or not muted else ToggleBool.On,
            )
        )
    plan = (
        *(
            MuteCommand(output=output_id, is_muted=ToggleBool.On)
            for output_id in output_ids
        ),
        *commands,
        *final_mutes,
    )
    encoder = CommandEncoder(read_only=False)
    for command in plan:
        if encoder.encode(command) is None:
            raise ValueError(f"Cannot encode output command: {command!r}")
    system.send_ops(*plan)
