# Example CLI for restoring source names to model-specific low-level labels.
from __future__ import annotations

import argparse

from autonomic import AutonomicClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Rename all direct amplifier sources to their device-default labels.")
    parser.add_argument(
        "host",
        nargs="?",
        default="10.1.0.200",
        help="Autonomic device hostname or IP address. Defaults to 10.1.0.200.",
    )
    args = parser.parse_args()

    with AutonomicClient(args.host, mode="amplifier") as client:
        response = client.rename_sources_to_low_level_input_labels()

    if response:
        print(response)


if __name__ == "__main__":
    main()
