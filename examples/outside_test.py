# Example CLI for routing every zone to the M-6250 OPT1 source on .200.
from __future__ import annotations

import argparse

from autonomic import AutonomicClient

M6250_DEVICE = "00D4"
MA6_DEVICE = "6012"
M6250_OPT1 = f"{M6250_DEVICE}:OPT1"
MA6_A1 = f"{MA6_DEVICE}:Analog 1"
MA6_REMOTE_M6250_OPT1_SLOT = 0
MA6_REMOTE_M6250_OPT1 = f"{MA6_DEVICE}:{0x20 + MA6_REMOTE_M6250_OPT1_SLOT}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Set all zones to .200's OPT1, including .201 zones through eAudioCast.")
    parser.add_argument(
        "host",
        nargs="?",
        default="10.1.0.200",
        help="Autonomic device hostname or IP address. Defaults to 10.1.0.200.",
    )
    parser.add_argument(
        "--skip-remote-setup",
        action="store_true",
        help="Assume .201 remote slot 0 already points to .200 OPT1.",
    )
    args = parser.parse_args()

    with AutonomicClient(args.host) as client:
        outputs = client.list_outputs(include_status=False)
        m6250_outputs = [output for output in outputs if output.attributes.get("deviceId") == M6250_DEVICE]
        ma6_outputs = [output for output in outputs if output.attributes.get("deviceId") == MA6_DEVICE]

        # print("Muting non-passthrough...")
        # for output in m6250_outputs:
        #     print(f"Processing {output.name}...")
        #     if output.name == "Passthrough":
        #         output.set_power()
        #         output.unmute()
        #         output.set_volume(100.0)
        #     else:
        #         output.mute()

        # print("Enabling outside volume...")
        # for output in ma6_outputs[:4]:
        #     print(f"Processing {output.name}...")
        #     output.set_power()
        #     output.unmute()
        #     output.set_volume(100.0)

        for output in ma6_outputs[:4]:
            print(f"Assigning {MA6_REMOTE_M6250_OPT1} to {output.name}...")
            output.assign(MA6_REMOTE_M6250_OPT1)


if __name__ == "__main__":
    main()
