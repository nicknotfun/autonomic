from __future__ import annotations

import argparse
import asyncio

from _system_example import add_connection_args, discover_or_timeout, selected_hosts

from amp.system import OutputState, System


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Print a compact per-device source and output summary."
    )
    add_connection_args(parser)
    args = parser.parse_args()

    with System(selected_hosts(args), trace=args.trace) as system:
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
        for input in system.state.inputs_by_device(device.id):
            print(f"    {_input_label(input):<10} {input.name}")
        print("  OUTPUTS")
        for output_id in device.outputs or ():
            output = system.state.outputs[output_id]
            print(f"    {_output_row(system, output)}")


def _input_label(input) -> str:
    if input.physical_source_id is not None:
        return f"#{input.physical_source_id} (0x{input.selector:02X})"
    return f"0x{input.selector:02X}"


def _output_row(system: System, output: OutputState) -> str:
    source = "-"
    output_selector = system.output(output.id)
    try:
        selected_input = output_selector.input
    except ValueError:
        selected_input = None
    if selected_input is not None:
        source = selected_input.name
    elif output_selector.source is not None:
        source = f"0x{output_selector.source:02X}"
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
