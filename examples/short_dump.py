from __future__ import annotations

import argparse
import asyncio

from _system_example import add_connection_args, discover_or_timeout, make_system, selected_hosts

from amp.system import OutputState, System


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Print a compact per-device source and output summary."
    )
    add_connection_args(parser)
    args = parser.parse_args()

    with make_system(selected_hosts(args), read_only=True, trace=args.trace) as system:
        await discover_or_timeout(system, args)
        print_summary(system)


def print_summary(system: System) -> None:
    for index, device in enumerate(system.state.devices.values()):
        if index:
            print()
        label = f"{device.id}"
        if device.host is not None:
            label += f" ({device.host})"
        print(label)
        print("  INPUTS")
        for input in system.inputs_by_device(device.id):
            print(f"    {_input_label(input.selector):<6} {input.name}")
        print("  OUTPUTS")
        for output_id in device.outputs or ():
            output = system.state.outputs[output_id]
            print(f"    {_output_row(system, output)}")


def _input_label(selector: int) -> str:
    return f"0x{selector:02X}"


def _output_row(system: System, output: OutputState) -> str:
    source = "-"
    if output.source is not None:
        device = system.device_for_output(output.id)
        input = (
            system.input_for_device_selector(device.id, output.source)
            if device is not None
            else None
        )
        source = input.name if input is not None else f"0x{output.source:02X}"
    return (
        f"{output.id:<3} "
        f"name={_value(output.name):<18} "
        f"on={_value(output.on):<5} "
        f"muted={_value(output.muted):<5} "
        f"volume={_value(output.volume):<6} "
        f"source={source}"
    )


def _value(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
