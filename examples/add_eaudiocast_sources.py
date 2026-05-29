# Example CLI for adding cross-amplifier eAudioCast source slots.
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import cast

from autonomic import AutonomicClient
from autonomic.config import ConfigMapping

M6250_DEVICE = "00D4"
MA6_DEVICE = "6012"

M6250_OPT1 = f"{M6250_DEVICE}:OPT1"
M6250_OPT2 = f"{M6250_DEVICE}:OPT2"
MA6_ANALOG1 = f"{MA6_DEVICE}:Analog 1"
MA6_OPT1 = f"{MA6_DEVICE}:Optical 1"
MA6_OPT2 = f"{MA6_DEVICE}:Optical 2"

EAUDIOCAST_CONFIG = cast(ConfigMapping, {
    "direct_amplifier": {
        "devices": [
            {
                "device_id": M6250_DEVICE,
                "host": "10.1.0.200",
                "guid": "674e1900-f8a9-f6be-a465-3d0fbee12977",
                "output_start": 1,
                "native_output_start": 1,
                "model_byte": "0xB0",
            },
            {
                "device_id": MA6_DEVICE,
                "host": "10.1.0.201",
                "guid": "6c126887-df88-bd41-abbd-079c4e743694",
                "output_start": 9,
                "native_output_start": 9,
                "model_byte": "0xE9",
            },
        ],
    }
})


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

    with AutonomicClient(args.host, config=EAUDIOCAST_CONFIG) as client:
        for item in EAUDIOCAST_SOURCES:
            client.define_eaudiocast_source(
                target_device_id=item.target_device_id,
                slot=item.slot,
                source=item.source,
                name=item.name,
            )


if __name__ == "__main__":
    main()
