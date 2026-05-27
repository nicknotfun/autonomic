# Example CLI for adding cross-amplifier eAudioCast source slots.
from __future__ import annotations

import argparse
from dataclasses import dataclass

from autonomic import AutonomicClient

M6250_DEVICE = "00D4"
MA6_DEVICE = "6012"

M6250_OPT1 = f"{M6250_DEVICE}:OPT1"
M6250_OPT2 = f"{M6250_DEVICE}:OPT2"
MA6_ANALOG1 = f"{MA6_DEVICE}:Analog 1"
MA6_OPT1 = f"{MA6_DEVICE}:Optical 1"
MA6_OPT2 = f"{MA6_DEVICE}:Optical 2"


@dataclass(frozen=True)
class EAudioCastSource:
    target_device_id: str
    slot: int
    source: str
    name: str


EAUDIOCAST_SOURCES = (
    EAudioCastSource(MA6_DEVICE, 0, M6250_OPT1, ".200 OPT1"),
    EAudioCastSource(MA6_DEVICE, 1, M6250_OPT2, ".200 OPT2"),
    EAudioCastSource(M6250_DEVICE, 0, MA6_OPT1, ".201 Optical 1"),
    EAudioCastSource(M6250_DEVICE, 1, MA6_OPT2, ".201 Optical 2"),
    EAudioCastSource(M6250_DEVICE, 2, MA6_ANALOG1, ".201 Analogue 1"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Add eAudioCast remote-source slots for the .200/.201 amp stack.")
    parser.add_argument(
        "host",
        nargs="?",
        default="10.1.0.200",
        help="Autonomic device hostname or IP address. Defaults to 10.1.0.200.",
    )
    args = parser.parse_args()

    with AutonomicClient(args.host, mode="amplifier") as client:
        for item in EAUDIOCAST_SOURCES:
            client.define_eaudiocast_source(
                target_device_id=item.target_device_id,
                slot=item.slot,
                source=item.source,
                name=item.name,
            )


if __name__ == "__main__":
    main()
