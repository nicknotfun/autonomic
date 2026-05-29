# Example CLI for dumping all discovered source and output status as JSON.
from __future__ import annotations

import argparse
import json
from typing import TypeAlias

from autonomic import AutonomicClient, AutonomicOutput, AutonomicSource

StatusValue: TypeAlias = str | int | float | bool | None | dict[str, str]
StatusRecord: TypeAlias = dict[str, StatusValue]
SnapshotValue: TypeAlias = str | list[StatusRecord]


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump all Autonomic sources and outputs as a JSON status snapshot.")
    parser.add_argument(
        "host",
        nargs="?",
        default="10.1.0.200",
        help="Autonomic device hostname or IP address. Defaults to 10.1.0.200.",
    )
    args = parser.parse_args()

    with AutonomicClient(args.host) as client:
        snapshot: dict[str, SnapshotValue] = {
            "host": args.host,
            "mode": client.detect_mode(),
            "sources": [_source_record(source) for source in client.list_sources(include_disabled=True)],
            "outputs": [_output_record(output) for output in client.list_outputs(include_disabled=True, include_status=True)],
        }

    print(json.dumps(snapshot, indent=2, sort_keys=True))


def _source_record(source: AutonomicSource) -> StatusRecord:
    return {
        "id": source.id,
        "guid": source.guid,
        "name": source.name,
        "kind": source.kind,
        "address": source.address,
        "disabled": source.disabled,
        "attributes": source.attributes,
    }


def _output_record(output: AutonomicOutput) -> StatusRecord:
    return {
        "id": output.id,
        "guid": output.guid,
        "name": output.name,
        "kind": output.kind,
        "address": output.address,
        "disabled": output.disabled,
        "is_on": output.is_on,
        "muted": output.muted,
        "volume": output.volume,
        "max_volume": output.max_volume,
        "bass": output.bass,
        "treble": output.treble,
        "balance": output.balance,
        "gain": output.gain,
        "delay_ms": output.delay_ms,
        "loudness": output.loudness,
        "source_id": output.source_id,
        "source_guid": output.source_guid,
        "source_name": output.source_name,
        "attributes": output.attributes,
    }


if __name__ == "__main__":
    main()
