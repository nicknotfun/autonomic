from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amp.codec import Op, connect
from amp.system import System
from amp.transport import Transport


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


def make_system(
    hosts: Sequence[str],
    *,
    read_only: bool,
    trace: bool = False,
) -> System:
    transports: tuple[Transport[Op], ...] = tuple(
        connect(host, trace=trace, read_only=read_only) for host in hosts
    )
    if len(transports) == 1:
        return System(transports[0])
    return System(transports)


async def discover_or_timeout(system: System, args: argparse.Namespace) -> None:
    try:
        await asyncio.wait_for(
            system.discover(target_devices=args.target_devices),
            timeout=args.timeout,
        )
    except asyncio.TimeoutError:
        print("Discovery timed out; continuing with partial state.")
