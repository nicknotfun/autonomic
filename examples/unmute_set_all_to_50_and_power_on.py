# Example CLI for unmuting every zone, setting volume to 50, and powering on.
from __future__ import annotations

import argparse

from autonomic import AutonomicClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Unmute every zone, set volume to 50 percent, and power on.")
    parser.add_argument(
        "host",
        nargs="?",
        default="10.1.0.200",
        help="Autonomic device hostname or IP address. Defaults to 10.1.0.200.",
    )
    args = parser.parse_args()

    with AutonomicClient(args.host) as client:
        client.all_on()
        client.mute_all_outputs(False)
        client.set_all_output_volume(50.0)


if __name__ == "__main__":
    main()
