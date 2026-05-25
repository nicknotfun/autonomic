#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterable

from autonomic import AutonomicClient, AutonomicOutput, AutonomicSource
from autonomic.amplifier import AMPLIFIER_RAW_MAX_VOLUME


DEFAULT_HOSTS = ("10.1.0.200", "10.1.0.201")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print direct amplifier source and zone summaries.",
    )
    parser.add_argument(
        "hosts",
        nargs="*",
        default=DEFAULT_HOSTS,
        help="Autonomic amplifier IPs or hostnames. Defaults to 10.1.0.200 and 10.1.0.201.",
    )
    parser.add_argument(
        "--outputs",
        type=int,
        default=16,
        help="Direct amplifier output count. Defaults to 16.",
    )
    parser.add_argument(
        "--sources",
        type=int,
        default=12,
        help="Direct amplifier local source count. Defaults to 12.",
    )
    parser.add_argument(
        "--source-base",
        type=int,
        choices=(0, 1),
        default=0,
        help="Direct amplifier local source numbering base. Defaults to 0.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Socket timeout in seconds. Defaults to 5.",
    )
    parser.add_argument(
        "--raw-status",
        action="store_true",
        help="Include the raw direct amplifier source-status row for each zone.",
    )
    args = parser.parse_args()

    for index, host in enumerate(args.hosts):
        if index:
            print()
        print_host_summary(
            host,
            output_count=args.outputs,
            source_count=args.sources,
            source_base=args.source_base,
            timeout=args.timeout,
            raw_status=args.raw_status,
        )


def print_host_summary(
    host: str,
    *,
    output_count: int,
    source_count: int,
    source_base: int,
    timeout: float,
    raw_status: bool,
) -> None:
    try:
        client = AutonomicClient(
            host,
            mode="amplifier",
            timeout=timeout,
            amplifier_output_count=output_count,
            amplifier_source_count=source_count,
            amplifier_source_base=source_base,
        )
        sources = client.list_sources(include_disabled=True)
        outputs = client.list_outputs(include_disabled=True, include_status=False)
        output_status = _read_output_status(client)
    except Exception as exc:
        print(f"HOST {host}")
        print(f"  ERROR {type(exc).__name__}: {exc}")
        return

    print(f"HOST {host} mode={client.detect_mode()} device_id={client._amplifier_device_id}")
    print("Sources")
    for source in _sort_by_numeric_id(sources):
        print(f"  {_source_line(source)}")

    print("Zones")
    for output in _sort_by_numeric_id(outputs):
        print(f"  {_output_line(output, output_status.get(str(output.id), {}), raw_status=raw_status)}")


def _read_output_status(client: AutonomicClient) -> dict[str, dict[str, object]]:
    statuses: dict[str, dict[str, object]] = {}
    source_names = {str(source.id): source.name for source in client.list_sources(include_disabled=True)}

    for command in ("01FF", "02FF", "03FF", "04FF"):
        for row in client.amplifier.poll(command):
            _merge_status_row(client, source_names, statuses, row)

    fallback_queries = (
        ("01", "is_on"),
        ("02", "muted"),
        ("03", "source_id"),
        ("04", "volume"),
    )
    for output in range(1, client.amplifier.output_count + 1):
        status = statuses.setdefault(str(output), {})
        for command, field in fallback_queries:
            if field in status:
                continue
            query = client.amplifier.build_query_command(command, output)
            for row in client.amplifier.poll(query):
                _merge_status_row(client, source_names, statuses, row)

    return statuses


def _merge_status_row(
    client: AutonomicClient,
    source_names: dict[str, str | None],
    statuses: dict[str, dict[str, object]],
    row: object,
) -> None:
    if row.output is None or row.output == 0xFF or not row.data:
        return
    output_id = str(row.output)
    status = statuses.setdefault(output_id, {})

    if row.command == 0x01:
        status["is_on"] = row.data[0] == 0x01
    elif row.command == 0x02 and row.data[0] in {0x00, 0x01}:
        status["muted"] = row.data[0] == 0x00
    elif row.command == 0x03:
        source_byte = row.data[0]
        if len(row.data) > 1 and (row.data[-1] & 0x7F) >= 0x20:
            source_byte = row.data[-1]
        source_data_value = source_byte & 0x7F
        source_id = str(client.amplifier._source_id_for_instance(source_data_value))
        status["source_id"] = source_id
        status["source_name"] = source_names.get(source_id) or client.amplifier._source_name_for_instance(int(source_id))
        status["source_raw"] = row.raw
    elif row.command == 0x04:
        status["volume"] = _volume_from_raw(row.data[0])


def _sort_by_numeric_id(items: Iterable[AutonomicSource | AutonomicOutput]) -> list[AutonomicSource | AutonomicOutput]:
    return sorted(items, key=lambda item: _numeric_id(item.id))


def _numeric_id(value: str | None) -> tuple[int, str]:
    if value is None:
        return (10_000, "")
    try:
        return (int(value), value)
    except ValueError:
        return (10_000, value)


def _source_line(source: AutonomicSource) -> str:
    details: list[str] = []
    raw_name = source.attributes.get("rawName")
    remote_slot = source.attributes.get("remoteSlot")
    if raw_name and raw_name != source.name:
        details.append(f"raw={raw_name}")
    if remote_slot:
        details.append(f"remoteSlot={remote_slot}")
    if source.address:
        details.append(f"address={source.address}")
    if source.guid:
        details.append(f"guid={source.guid}")
    suffix = " " + " ".join(details) if details else ""
    return f"{str(source.id):>2} {source.name}{suffix}"


def _output_line(output: AutonomicOutput, status: dict[str, object], *, raw_status: bool) -> str:
    line = (
        f"{str(output.id):>2} {str(output.name):<12} "
        f"on={status.get('is_on')} muted={status.get('muted')} volume={status.get('volume')} "
        f"source_id={status.get('source_id')} source={status.get('source_name')}"
    )
    if raw_status and status.get("source_raw"):
        line = f"{line} raw_source={status['source_raw']}"
    return line


def _volume_from_raw(raw_volume: int) -> int:
    raw = max(0, min(AMPLIFIER_RAW_MAX_VOLUME, int(raw_volume)))
    return int((raw * 100) / AMPLIFIER_RAW_MAX_VOLUME)


if __name__ == "__main__":
    main()
