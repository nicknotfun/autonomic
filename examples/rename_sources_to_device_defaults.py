from __future__ import annotations

import argparse
import asyncio

from _system_example import add_connection_args, discover_or_timeout, selected_hosts

from amp.byte_utils import HexBytes
from amp.codec import Command, SourceNameOptionsCommand
from amp.hardware import HardwareModelInfo, SourceModelInfo, model_by_number
from amp.system import DeviceState, System
from amp.transport import Transport


DEFAULT_SOURCE_MISC = HexBytes("000001")


def parse_device_id(value: str) -> HexBytes:
    try:
        return HexBytes(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def should_rename_source(
    system: System,
    device: DeviceState,
    source: SourceModelInfo,
    *,
    include_unchanged: bool,
) -> bool:
    if include_unchanged:
        return True
    current_input = system.input_for_device_selector(device.id, source.selector)
    return (
        current_input is None
        or not current_input.name_discovered
        or current_input.name != source.name
    )


def default_source_name_ops(
    system: System,
    device: DeviceState,
    model: HardwareModelInfo,
    *,
    include_unchanged: bool,
) -> tuple[SourceNameOptionsCommand, ...]:
    if not device.outputs:
        return ()
    representative_output = device.outputs[0]
    return tuple(
        SourceNameOptionsCommand(
            output=representative_output,
            source_selector=source.selector,
            options=DEFAULT_SOURCE_MISC,
            name=source.name,
        )
        for source in model.sources
        if should_rename_source(
            system,
            device,
            source,
            include_unchanged=include_unchanged,
        )
    )


def device_transport(system: System, device: DeviceState) -> Transport[Command]:
    return system.transport_for_device(device) or system.transport


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore discovered device source names to hardware model defaults."
    )
    add_connection_args(parser, write=True)
    parser.add_argument(
        "--device",
        action="append",
        dest="device_ids",
        type=parse_device_id,
        help=(
            "Device id to rename. Repeat to target multiple devices. "
            "Defaults to every known hardware model."
        ),
    )
    parser.add_argument(
        "--include-unchanged",
        action="store_true",
        help="Send default-name writes even when the discovered runtime name already matches.",
    )
    args = parser.parse_args()

    selected_device_ids = set(args.device_ids or ())
    with System(selected_hosts(args), read_only=False, trace=args.trace) as system:
        await discover_or_timeout(system, args)
        for device in system.state.devices.values():
            if selected_device_ids and device.id not in selected_device_ids:
                continue
            if device.model_id is None:
                print(f"{device.id}: skipping, model is unknown")
                continue
            model = model_by_number(device.model_id)
            if model is None:
                print(f"{device.id}: skipping, model {device.model_id} is not in hardware.py")
                continue

            ops = default_source_name_ops(
                system,
                device,
                model,
                include_unchanged=args.include_unchanged,
            )
            if not ops:
                print(f"{device.id} {model.name}: no source names need updates")
                continue

            system.send_ops(*ops, transport=device_transport(system, device))
            print(f"{device.id} {model.name}: queued {len(ops)} source-name updates")
        await asyncio.sleep(args.settle)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
