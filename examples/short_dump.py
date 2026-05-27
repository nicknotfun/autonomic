# Example CLI for a compact per-device source and zone status dump.
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from autonomic import AutonomicClient, AutonomicOutput, AutonomicSource


@dataclass
class DeviceBlock:
    label: str
    sources: list[AutonomicSource] = field(default_factory=list)
    zones: list[AutonomicOutput] = field(default_factory=list)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a compact per-device Autonomic source and zone summary.")
    parser.add_argument(
        "host",
        nargs="?",
        default="10.1.0.200",
        help="Autonomic device hostname or IP address. Defaults to 10.1.0.200.",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "mrad", "mas", "amplifier", "amp", "direct"),
        default="auto",
        help="Control mode. Defaults to auto-detection.",
    )
    args = parser.parse_args()

    with AutonomicClient(args.host, mode=args.mode) as client:
        sources = client.list_sources(include_disabled=True)
        zones = client.list_outputs(include_disabled=True, include_status=True)
        blocks = _device_blocks(sources, zones)

    for index, block in enumerate(blocks):
        if index:
            print()
        print(block.label)
        print("  SOURCES")
        for source in _sort_sources(block.sources):
            print(f"    {_source_row(source)}")
        print("  ZONES")
        for zone in _sort_zones(block.zones):
            print(f"    {_zone_row(zone)}")


def _device_blocks(sources: list[AutonomicSource], zones: list[AutonomicOutput]) -> list[DeviceBlock]:
    blocks: dict[str, DeviceBlock] = {}
    for source in sources:
        key, label = _device_key_and_label(source)
        blocks.setdefault(key, DeviceBlock(label=label)).sources.append(source)
    for zone in zones:
        key, label = _device_key_and_label(zone)
        blocks.setdefault(key, DeviceBlock(label=label)).zones.append(zone)
    return [blocks[key] for key in sorted(blocks)]


def _device_key_and_label(item: AutonomicSource | AutonomicOutput) -> tuple[str, str]:
    device_id = item.attributes.get("deviceId")
    host = item.attributes.get("deviceHost")
    if device_id and host:
        return (f"{device_id}:{host}", f"{device_id} ({host})")
    if device_id:
        return (device_id, device_id)
    if host:
        return (host, host)
    return ("system", "system")


def _source_row(source: AutonomicSource) -> str:
    return _columns(
        kind="source",
        name=source.name,
        address=source.address,
        volume=None,
        source=None,
    )


def _zone_row(zone: AutonomicOutput) -> str:
    return _columns(
        kind="zone",
        name=zone.name,
        address=zone.address,
        volume=_format_volume(zone.volume),
        source=zone.source_name or zone.source_id,
    )


def _columns(
    *,
    kind: str,
    name: str | None,
    address: str | None,
    volume: str | None,
    source: str | None,
) -> str:
    return (
        f"{kind:<6} "
        f"name={_value(name):<18} "
        f"address={_value(address):<8} "
        f"volume={_value(volume):<6} "
        f"source={_value(source)}"
    )


def _format_volume(value: float | None) -> str | None:
    if value is None:
        return None
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _value(value: str | None) -> str:
    if value is None or value == "":
        return "-"
    return value


def _sort_sources(sources: list[AutonomicSource]) -> list[AutonomicSource]:
    return sorted(sources, key=lambda source: (_numeric_ref(source.id), source.name or ""))


def _sort_zones(zones: list[AutonomicOutput]) -> list[AutonomicOutput]:
    return sorted(zones, key=lambda zone: (_numeric_ref(zone.id), zone.name or ""))


def _numeric_ref(value: str | None) -> tuple[int, str]:
    if value is None:
        return (10_000, "")
    native = value.rsplit(":", 1)[-1].removeprefix("Zone_")
    try:
        return (int(native), value)
    except ValueError:
        return (10_000, value)


if __name__ == "__main__":
    main()
