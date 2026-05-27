# Example CLI for routing every zone to the M-6250 OPT1 source on .200.
from __future__ import annotations

import argparse

from autonomic import AutonomicClient

M6250_DEVICE = "00D4"
MA6_DEVICE = "6012"
M6250_OPT1 = f"{M6250_DEVICE}:OPT1"
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

    with AutonomicClient(args.host, mode="amplifier") as client:
        outputs = client.list_outputs(include_status=False)
        m6250_outputs = [output for output in outputs if output.attributes.get("deviceId") == M6250_DEVICE]
        ma6_outputs = [output for output in outputs if output.attributes.get("deviceId") == MA6_DEVICE]

        client.assign_source_to_outputs(M6250_OPT1, m6250_outputs)

        if not args.skip_remote_setup:
            client.define_eaudiocast_source(
                target_device_id=MA6_DEVICE,
                slot=MA6_REMOTE_M6250_OPT1_SLOT,
                source=M6250_OPT1,
                name=".200 OPT1",
            )

        client.assign_source_to_outputs(MA6_REMOTE_M6250_OPT1, ma6_outputs)


if __name__ == "__main__":
    main()
