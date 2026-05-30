#!/usr/bin/env python3
from __future__ import annotations

import asyncio

import click

from amp.byte_utils import HexBytes
from amp.codec import (
    DeviceGuidQueryOp,
    DeviceInfoDiscoveryOp,
    OutputNameRefreshOp,
    PowerOp,
    SourceNameDiscoveryOp,
    connect,
)

DEFAULT_HOST = "10.1.0.200"
DEFAULT_DEVICE_ID = "00D4"


async def run(
    host: str,
    *,
    device_id: HexBytes,
    port: int,
    timeout: float,
    retry_wait: float,
    listen_secs: float,
    source_names: bool,
    source_output: int,
    quiet: bool,
) -> None:
    click.echo(f"Connecting to {host} and bootstrapping...")

    with connect(
        host,
        port=port,
        trace=not quiet,
        reconnection_wait_secs=retry_wait,
        connection_timeout_secs=timeout,
    ) as transport:
        bootstrap_ops = [
            PowerOp(),
            DeviceGuidQueryOp(device_id=device_id),
            OutputNameRefreshOp(),
            DeviceInfoDiscoveryOp(),
        ]
        if source_names:
            bootstrap_ops.append(SourceNameDiscoveryOp(output=source_output))
        transport.send(*bootstrap_ops)
        await asyncio.sleep(listen_secs)


class DeviceIdParam(click.ParamType):
    name = "device-id"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> HexBytes:
        if isinstance(value, HexBytes):
            return value
        if not isinstance(value, str):
            self.fail("must be a hexadecimal string", param, ctx)
        try:
            device_id = HexBytes(value)
        except ValueError as exc:
            self.fail(str(exc), param, ctx)
        if len(device_id) != 2:
            self.fail("must be exactly four hex digits", param, ctx)
        return device_id


class OutputByteParam(click.ParamType):
    name = "output-byte"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> int:
        if isinstance(value, int):
            output = value
        elif isinstance(value, str):
            raw = value.removeprefix("0x").removeprefix("0X")
            try:
                output = int(raw, 16)
            except ValueError as exc:
                self.fail(str(exc), param, ctx)
        else:
            self.fail("must be a hexadecimal byte", param, ctx)
        if output < 0 or output > 0xFF:
            self.fail("must be in range 00-FF", param, ctx)
        return output


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Probe a direct Autonomic amplifier and print decoded rows.",
)
@click.argument("host", default=DEFAULT_HOST, required=False)
@click.option(
    "--device-id",
    type=DeviceIdParam(),
    default=DEFAULT_DEVICE_ID,
    show_default=True,
    help="Four-hex-digit amplifier device id for GUID queries.",
)
@click.option(
    "--port",
    type=int,
    default=17037,
    show_default=True,
    help="Direct amplifier TCP port.",
)
@click.option(
    "--timeout",
    type=float,
    default=10.0,
    show_default=True,
    help="Connection timeout in seconds.",
)
@click.option(
    "--retry-wait",
    type=float,
    default=5.0,
    show_default=True,
    help="Seconds to wait before reconnecting.",
)
@click.option(
    "--listen-secs",
    type=float,
    default=60.0,
    show_default=True,
    help="Seconds to keep the probe running after sending bootstrap queries.",
)
@click.option("--source-names", is_flag=True, help="Also send the source-name query.")
@click.option(
    "--source-output",
    type=OutputByteParam(),
    default="01",
    show_default=True,
    help="Hex output byte to use for source-name discovery when --source-names is set.",
)
@click.option("--quiet", is_flag=True, help="Disable decoded row tracing.")
def main(
    host: str,
    device_id: HexBytes,
    port: int,
    timeout: float,
    retry_wait: float,
    listen_secs: float,
    source_names: bool,
    source_output: int,
    quiet: bool,
) -> None:
    asyncio.run(
        run(
            host,
            device_id=device_id,
            port=port,
            timeout=timeout,
            retry_wait=retry_wait,
            listen_secs=listen_secs,
            source_names=source_names,
            source_output=source_output,
            quiet=quiet,
        )
    )


if __name__ == "__main__":
    main()
