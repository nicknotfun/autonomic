# Example CLI for setting every zone's power-on volume to 50 percent.
from __future__ import annotations

import argparse

from autonomic import AutonomicClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Set every Autonomic zone power-on volume to 50 percent.")
    parser.add_argument(
        "host",
        nargs="?",
        default="10.1.0.200",
        help="Autonomic device hostname or IP address. Defaults to 10.1.0.200.",
    )
    args = parser.parse_args()

    with AutonomicClient(args.host) as client:
        client.set_all_output_power_on_volume(50.0)


if __name__ == "__main__":
    main()
