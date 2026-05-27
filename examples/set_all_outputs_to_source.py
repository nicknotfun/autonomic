# Example CLI for assigning every discovered output to one source.
from __future__ import annotations

import argparse

from autonomic import AutonomicClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Set every Autonomic output to one source.")
    parser.add_argument("host", help="Autonomic device hostname or IP address")
    parser.add_argument(
        "source",
        nargs="?",
        help="Source id, GUID, or name. Defaults to the first source returned by the device.",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "mrad", "mas", "amplifier", "amp", "direct"),
        default="auto",
        help="Control mode. Defaults to auto-detection.",
    )
    args = parser.parse_args()

    with AutonomicClient(args.host, mode=args.mode) as client:
        source = args.source if args.source is not None else client.list_sources()[0]
        client.all_outputs().assign(source)


if __name__ == "__main__":
    main()
