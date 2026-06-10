from __future__ import annotations

"""Live AMP probe runner for the known local amplifier stack.

This script intentionally lives outside the `amp` package. It is a diagnostic
tool, not part of the supported library API. It exercises the public codec,
transport, and system-state layers against live devices and writes a JSON
artifact that is useful for answering three practical questions:

1. Which query/read paths did the devices answer?
2. Which raw rows did `amp.codec.CommandEncoder` fail to deserialize?
3. Did no-op write helpers preserve the observed output state?

The default mode is read-only. Use `--write-noops` to include the write phase
that re-sends only values already observed from the amplifiers. Rename writes,
identity writes, remote-source configuration writes, link writes, source
metadata writes, relative volume commands, and full input-gain writes are always
skipped.
"""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
import sys
import time
from typing import Any, Iterable


# Keep imports working when the script is run from a different current working
# directory. This mirrors the example scripts but is relative to this file
# rather than hard-coding the checkout path.
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from amp.byte_utils import HexBytes
import amp.codec as codec
from amp.codec import Command, CommandEncoder
from amp.system import System
from amp.toggle_bool import ToggleBool


# These defaults match the two-device system currently documented in
# PROTOCOL.md. The probe still expands the sets from live responses, but seeding
# them makes the later phases robust when one discovery query is missed by a
# device or by the shared diagnostic bus.
DEFAULT_HOSTS = ("10.1.0.200", "10.1.0.201")
KNOWN_DEVICE_IDS = (HexBytes("00D4"), HexBytes("6012"))
KNOWN_OUTPUTS = tuple(range(1, 17))
REMOTE_SLOTS = tuple(range(0x20))


def json_default(value: Any) -> Any:
    """Serialize protocol helper types cleanly in the probe artifact."""

    if isinstance(value, HexBytes):
        return str(value)
    if isinstance(value, bytes):
        return str(HexBytes(value))
    if isinstance(value, ToggleBool):
        return str(value)
    if value.__class__.__name__ == "UUID":
        return str(value)
    return str(value)


def op_fields(op: Command) -> dict[str, Any]:
    """Return dataclass fields for an op with JSON-friendly scalar values."""

    try:
        data = asdict(op)  # type: ignore[arg-type]
    except TypeError:
        # This is defensive; current Command implementations are dataclasses when
        # they have fields, but keeping this tolerant helps future experiments.
        data = {}
    return {key: json_default(value) for key, value in data.items()}


def op_summary(op: Command | None) -> dict[str, Any] | None:
    """Build the compact decoded-op record stored next to each raw row."""

    if op is None:
        return None
    return {
        "type": type(op).__name__,
        "repr": repr(op),
        "fields": op_fields(op),
    }


@dataclass
class RawEvent:
    """One row received from the diagnostic socket."""

    raw: str
    decoded: bool
    op_type: str | None
    op_repr: str | None
    fields: dict[str, Any] | None


@dataclass
class SentRow:
    """One row sent by the raw socket probe."""

    phase: str
    raw: str
    op_type: str
    op_repr: str


@dataclass
class HostProbe:
    """All raw-socket probe activity for one amplifier endpoint."""

    host: str
    sent: list[SentRow] = field(default_factory=list)
    received: list[RawEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class RawSocketProbe:
    """Small raw-line client that records rows before and after decoding.

    `amp.transport.Transport` is the right production path, but it intentionally
    drops rows that fail decoding. For codec coverage work that is the wrong
    tradeoff: we need to keep the raw row so the missing pattern can be fixed.
    This class therefore uses the same `CommandEncoder` but owns the socket loop.
    """

    def __init__(self, host: str, *, port: int, send_gap: float, idle_wait: float) -> None:
        self.host = host
        self.port = port
        self.send_gap = send_gap
        self.idle_wait = idle_wait

        # read_only=False is used only so the encoder can serialize write-shaped
        # ops during optional no-op write tests. The script controls whether
        # those writes are sent with `--write-noops`.
        self.encoder = CommandEncoder(read_only=False)

        self.probe = HostProbe(host=host)
        self._ops: list[Command] = []
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None

    @property
    def ops(self) -> list[Command]:
        return self._ops

    async def __aenter__(self) -> "RawSocketProbe":
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self._read_task = asyncio.create_task(self._read_loop())

        # The diagnostic bus can emit status rows caused by other clients. Give
        # it a short window before the first query so unsolicited rows are still
        # captured but do not race the first probe phase too tightly.
        await self.drain_idle(self.idle_wait)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                # Close errors are not useful for protocol coverage.
                pass

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                line_bytes = await self._reader.readuntil()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.probe.errors.append(f"read loop ended: {type(exc).__name__}: {exc}")
                return

            line = line_bytes.decode("ascii", errors="replace").strip()
            if not line:
                continue

            op = None
            decoded = False
            try:
                op = self.encoder.decoder(HexBytes(line))
                decoded = op is not None
            except Exception as exc:
                # Decoder exceptions are logged but do not stop the probe. A bad
                # row is exactly the information this script is meant to keep.
                self.probe.errors.append(f"decode exception for {line}: {type(exc).__name__}: {exc}")

            if op is not None:
                self._ops.append(op)

            summary = op_summary(op)
            self.probe.received.append(
                RawEvent(
                    raw=line,
                    decoded=decoded,
                    op_type=summary["type"] if summary else None,
                    op_repr=summary["repr"] if summary else None,
                    fields=summary["fields"] if summary else None,
                )
            )

    async def drain_idle(self, wait: float | None = None) -> None:
        """Wait for trailing responses after a probe batch."""

        await asyncio.sleep(self.idle_wait if wait is None else wait)

    async def send_ops(self, phase: str, ops: Iterable[Command]) -> None:
        """Encode and send a phase of rows, retaining every outbound row."""

        assert self._writer is not None
        for op in ops:
            encoded = self.encoder.encode(op)
            if encoded is None:
                self.probe.errors.append(f"{phase}: encoder filtered {op!r}")
                continue

            raw = str(encoded)
            self._writer.write(raw.encode("ascii") + b"\r\n")
            await self._writer.drain()
            self.probe.sent.append(
                SentRow(phase=phase, raw=raw, op_type=type(op).__name__, op_repr=repr(op))
            )

            # A tiny gap reduces row bursts enough that older firmware does not
            # appear to coalesce or skip replies under normal LAN conditions.
            await asyncio.sleep(self.send_gap)

        await self.drain_idle()


def unique_ops(ops: Iterable[Command]) -> tuple[Command, ...]:
    """Deduplicate ops by encoded row while preserving the original order."""

    seen: set[str] = set()
    out: list[Command] = []
    encoder = CommandEncoder(read_only=False)
    for op in ops:
        raw = encoder.encode(op)
        if raw is None:
            continue
        key = str(raw)
        if key in seen:
            continue
        seen.add(key)
        out.append(op)
    return tuple(out)


def discovered_device_ids(ops: Iterable[Command]) -> tuple[HexBytes, ...]:
    """Return known plus live-discovered device ids."""

    ids = set(KNOWN_DEVICE_IDS)
    for op in ops:
        if isinstance(op, codec.DeviceIdCommand):
            ids.add(op.device_id)
    return tuple(sorted(ids, key=str))


def discovered_outputs(ops: Iterable[Command]) -> tuple[int, ...]:
    """Return known plus live-discovered output ids."""

    outputs = set(KNOWN_OUTPUTS)
    for op in ops:
        if isinstance(op, (codec.RequestDeviceInformationCommandResponse, codec.RequestZoneAssignmentsCommandResponse)):
            outputs.update(op.zones)
    return tuple(sorted(outputs))


def discovered_selectors(ops: Iterable[Command]) -> tuple[int, ...]:
    """Return known plus live-discovered source selectors.

    The seed includes M6250 local/remote selectors and MA6 local/casting
    selectors. Live source-name and source-status rows can add anything missing.
    """

    selectors = {
        0x00,
        0x01,
        0x02,
        0x03,
        0x04,
        0x05,
        0x06,
        0x07,
        0x08,
        0x09,
        0x0A,
        0x0B,
        0x0C,
        0x0D,
        0x0E,
        0x0F,
        0x20,
        0x21,
        0x22,
        0x23,
        0x24,
        0x25,
        0x26,
        0x27,
        0x50,
        0x51,
        0x52,
    }
    for op in ops:
        if isinstance(op, codec.SourceNameOptionsCommand):
            selectors.add(op.source_selector)
        elif isinstance(op, codec.SourceSelectionCommand) and op.source is not None:
            # Status rows can set bit 7. System state clears it before comparing
            # selectors, so the probe summary does the same.
            selectors.add(op.source & 0x7F)
        elif isinstance(op, codec.SourceGainCommand) and op.source_selector is not None:
            selectors.add(op.source_selector)
    return tuple(sorted(selectors))


def op_type_counts(events: Iterable[RawEvent]) -> dict[str, int]:
    """Count decoded op classes, plus an UNDECODED bucket."""

    counts: dict[str, int] = {}
    for event in events:
        key = event.op_type or "UNDECODED"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def rows_by_type(events: Iterable[RawEvent], op_type: str) -> list[str]:
    """Collect unique raw rows for one decoded op class."""

    return sorted({event.raw for event in events if event.op_type == op_type})


def first_by_output(ops: Iterable[Command], cls: type[Command]) -> dict[int, Command]:
    """Pick the latest observed op of a given class for each real output."""

    values: dict[int, Command] = {}
    for op in ops:
        if isinstance(op, cls) and isinstance(op, codec.OutputCommand):
            if op.output != codec.ALL_OUTPUTS:
                values[op.output] = op
    return values


async def run_raw_host(host: str, args: argparse.Namespace) -> tuple[HostProbe, dict[str, Any]]:
    """Run the broad raw-row coverage pass for one TCP endpoint."""

    async with RawSocketProbe(
        host,
        port=args.port,
        send_gap=args.send_gap,
        idle_wait=args.idle_wait,
    ) as probe:
        # Phase 1: cheap broad discovery. These rows establish device ids,
        # output ids, remote slot data, and current dynamic output status.
        await probe.send_ops(
            "discovery",
            [
                codec.RequestZoneAssignmentsCommand(),
                codec.RequestDeviceInformationCommand(),
                codec.UndocumentedHostIdentityCommand(),
                codec.RequestExtendedDeviceInformationCommand(),
                codec.DistributedSourceDefinitionRequestCommand(),
                codec.StandbyPowerCommand(),
                codec.MuteCommand(),
                codec.SourceSelectionCommand(),
                codec.VolumeCommand(),
                codec.MaximumVolumeCommand(),
                codec.ZoneNameRequestCommand(),
            ],
        )

        device_ids = discovered_device_ids(probe.ops)
        outputs = discovered_outputs(probe.ops)

        # Phase 2: device-scoped reads. The link rows are query-shaped here; the
        # script never sends state-changing link writes.
        await probe.send_ops(
            "device_reads",
            unique_ops(
                op
                for device_id in device_ids
                for op in (
                    codec.RequestExtendedDeviceInformationCommand(device_id=device_id),
                    codec.NetworkSettingsDeviceGuidRequestCommand(device_id=device_id),
                    codec.KeypadPortZoneMappingCommand(device_id=device_id),
                    codec.KeypadPortOccupancyCommand(device_id=device_id),
                )
            ),
        )

        outputs = discovered_outputs(probe.ops)

        # Phase 3: output-scoped reads. This covers the output status surface
        # modeled by `amp.codec`, including tone/gain/loudness/delay rows that
        # System discovery does not currently persist in `OutputState`.
        await probe.send_ops(
            "output_reads",
            unique_ops(
                op
                for output in outputs
                for op in (
                    codec.StandbyPowerCommand(output=output),
                    codec.MuteCommand(output=output),
                    codec.SourceSelectionCommand(output=output),
                    codec.VolumeCommand(output=output),
                    codec.MaximumVolumeCommand(output=output),
                    codec.BassCommand(output=output),
                    codec.TrebleCommand(output=output),
                    codec.BalanceCommand(output=output),
                    codec.AmplifierSpecialFeaturesCommand(output=output),
                    codec.SendAllParametersCommand(output=output),
                    codec.AudioDelayCommand(output=output),
                    codec.LinkZonesCommand(output=output, flags=None),
                    codec.SourceGainCommand(output=output, source_selector=0xFF),
                    codec.ZoneGainCommand(output=output),
                    codec.ZoneNameRequestCommand(output=output),
                    codec.SourceNameOptionsRequestCommand(output=output),
                )
            ),
        )

        # Phase 4: exact source-name lookups. These are read rows because only
        # the output and selector are populated. Rename writes would include
        # misc/name/hidden_name fields and are intentionally not generated.
        selectors = discovered_selectors(probe.ops)
        await probe.send_ops(
            "source_name_single_reads",
            unique_ops(
                codec.SourceNameOptionsCommand(output=output, source_selector=selector)
                for output in outputs
                for selector in selectors
            ),
        )

        # Phase 5: metadata reads for currently-selected sources. The MA6/M6250
        # stack tends to answer only when metadata is available, so absence of a
        # row is recorded as "no response" rather than as a decode failure.
        current_selectors = sorted(
            {
                op.source & 0x7F
                for op in probe.ops
                if isinstance(op, codec.SourceSelectionCommand) and op.source is not None
            }
        )
        metadata_selectors = current_selectors or list(selectors[:4])
        await probe.send_ops(
            "source_metadata_reads",
            unique_ops(
                codec.SourceSpecificMetadataRequestCommand(
                    output=codec.ALL_OUTPUTS,
                    source_selector=selector,
                    position=position,
                )
                for selector in metadata_selectors
                for position in range(8)
            ),
        )

        # Phase 6: remote slots and preset groups. Remote source info/delete rows
        # are write-shaped in the codec when they carry payloads, but the rows
        # sent here are discovery queries only.
        await probe.send_ops(
            "remote_and_preset_reads",
            unique_ops(
                [
                    codec.DistributedSourceDefinitionRequestCommand(),
                    *[codec.DistributedSourceDefinitionRequestCommand(slot_id=slot) for slot in REMOTE_SLOTS],
                    *[codec.ArbitraryDataStorageCommand(slot_id=slot) for slot in range(16)],
                    *[codec.ArbitraryDataStorageCommand(slot_id=0x8000 + slot) for slot in range(16)],
                ]
            ),
        )

        # Phase 7: optional direct no-op setting writes. Each row is generated
        # from a value that was already observed from the device in this run.
        # Source selectors are normalized with bit 7 cleared, matching System
        # state. This phase deliberately avoids renames and configuration writes.
        current_ops = list(probe.ops)
        no_op_writes: list[Command] = []
        for output, op in first_by_output(current_ops, codec.StandbyPowerCommand).items():
            if isinstance(op, codec.StandbyPowerCommand) and op.is_on is not None:
                no_op_writes.append(codec.StandbyPowerCommand(output=output, is_on=op.is_on))
        for output, op in first_by_output(current_ops, codec.MuteCommand).items():
            if isinstance(op, codec.MuteCommand) and op.is_muted is not None:
                no_op_writes.append(codec.MuteCommand(output=output, is_muted=op.is_muted))
        for output, op in first_by_output(current_ops, codec.SourceSelectionCommand).items():
            if isinstance(op, codec.SourceSelectionCommand) and op.source is not None:
                no_op_writes.append(codec.SourceSelectionCommand(output=output, source=op.source & 0x7F))
        for output, op in first_by_output(current_ops, codec.VolumeCommand).items():
            if isinstance(op, codec.VolumeCommand) and op.volume is not None:
                no_op_writes.append(codec.VolumeCommand(output=output, volume=op.volume))
        for output, op in first_by_output(current_ops, codec.MaximumVolumeCommand).items():
            if isinstance(op, codec.MaximumVolumeCommand) and op.max_volume is not None:
                no_op_writes.append(codec.MaximumVolumeCommand(output=output, max_volume=op.max_volume))
        for output, op in first_by_output(current_ops, codec.BassCommand).items():
            if isinstance(op, codec.BassCommand) and op.bass is not None:
                no_op_writes.append(codec.BassCommand(output=output, bass=op.bass))
        for output, op in first_by_output(current_ops, codec.TrebleCommand).items():
            if isinstance(op, codec.TrebleCommand) and op.treble is not None:
                no_op_writes.append(codec.TrebleCommand(output=output, treble=op.treble))
        for output, op in first_by_output(current_ops, codec.BalanceCommand).items():
            if isinstance(op, codec.BalanceCommand) and op.balance is not None:
                no_op_writes.append(codec.BalanceCommand(output=output, balance=op.balance))
        for output, op in first_by_output(current_ops, codec.AmplifierSpecialFeaturesCommand).items():
            if isinstance(op, codec.AmplifierSpecialFeaturesCommand) and op.is_loud is not None:
                no_op_writes.append(codec.AmplifierSpecialFeaturesCommand(output=output, is_loud=op.is_loud))
        for output, op in first_by_output(current_ops, codec.AudioDelayCommand).items():
            if isinstance(op, codec.AudioDelayCommand) and op.delay is not None:
                no_op_writes.append(codec.AudioDelayCommand(output=output, delay=op.delay))
        for output, op in first_by_output(current_ops, codec.ZoneGainCommand).items():
            if isinstance(op, codec.ZoneGainCommand) and op.gain is not None:
                no_op_writes.append(codec.ZoneGainCommand(output=output, gain=op.gain))

        if args.write_noops:
            await probe.send_ops("no_op_setting_writes", unique_ops(no_op_writes))
            await probe.send_ops(
                "post_write_refresh",
                unique_ops(
                    op
                    for output in outputs
                    for op in (
                        codec.StandbyPowerCommand(output=output),
                        codec.MuteCommand(output=output),
                        codec.SourceSelectionCommand(output=output),
                        codec.VolumeCommand(output=output),
                        codec.MaximumVolumeCommand(output=output),
                        codec.BassCommand(output=output),
                        codec.TrebleCommand(output=output),
                        codec.BalanceCommand(output=output),
                        codec.AmplifierSpecialFeaturesCommand(output=output),
                        codec.AudioDelayCommand(output=output),
                        codec.ZoneGainCommand(output=output),
                    )
                ),
            )

    host_summary = {
        "sent_count": len(probe.probe.sent),
        "received_count": len(probe.probe.received),
        "decoded_count": sum(1 for event in probe.probe.received if event.decoded),
        "undecoded_rows": sorted({event.raw for event in probe.probe.received if not event.decoded}),
        "op_type_counts": op_type_counts(probe.probe.received),
        "device_ids_seen": [str(device_id) for device_id in discovered_device_ids(probe.ops)],
        "outputs_seen": list(discovered_outputs(probe.ops)),
        "selectors_seen": [f"0x{selector:02X}" for selector in discovered_selectors(probe.ops)],
        "no_op_write_count": len(
            [sent for sent in probe.probe.sent if sent.phase == "no_op_setting_writes"]
        ),
        # These examples make the top-level summary useful without opening the
        # full artifact. The full row list is still stored under raw_details.
        "rows_by_interesting_type": {
            "KeypadPortZoneMappingCommand": rows_by_type(probe.probe.received, "KeypadPortZoneMappingCommand"),
            "KeypadPortOccupancyCommand": rows_by_type(probe.probe.received, "KeypadPortOccupancyCommand"),
            "KeypadPortOccupancyCommandResponse": rows_by_type(probe.probe.received, "KeypadPortOccupancyCommandResponse"),
            "ArbitraryDataStorageCommand": rows_by_type(probe.probe.received, "ArbitraryDataStorageCommand")[:20],
            "SourceSpecificMetadataCommand": rows_by_type(probe.probe.received, "SourceSpecificMetadataCommand")[:20],
            "PowerOnVolumeLevelCommand": rows_by_type(probe.probe.received, "PowerOnVolumeLevelCommand"),
            "PreampVolumeModeCommand": rows_by_type(probe.probe.received, "PreampVolumeModeCommand"),
            "PresetSelectionStatusCommand": rows_by_type(probe.probe.received, "PresetSelectionStatusCommand"),
        },
    }
    return probe.probe, host_summary


async def run_system_discovery(hosts: tuple[str, ...], args: argparse.Namespace) -> dict[str, Any]:
    """Exercise the supported high-level `System.discover()` workflow."""

    system = System(
        hosts,
        port=args.port,
        reconnection_wait_secs=0.2,
        connection_timeout_secs=3.0,
    )
    try:
        try:
            await asyncio.wait_for(
                system.discover(
                    target_devices=args.target_devices,
                    time_between_probes_secs=0.2,
                    time_to_wait_for_devices_with_unknown_inputs=1.0,
                ),
                timeout=args.system_timeout,
            )
            timed_out = False
        except asyncio.TimeoutError:
            # Timeouts are intentionally non-fatal: partial state is still the
            # useful artifact when a device is slow or skips a row family.
            timed_out = True
        return {"timed_out": timed_out, "state": system.state.to_json()}
    finally:
        system.shutdown()


def output_snapshot(system: System) -> dict[str, dict[str, Any]]:
    """Capture the output fields that selector no-op writes should preserve."""

    return {
        str(output_id): {
            "name": output.name,
            "on": output.on,
            "muted": output.muted,
            "source_raw": output.source_raw,
            "source_detail": output.source_detail,
            "selected_source": output.selected_reported_source_selector,
            "volume": output.volume,
            "max_volume": output.max_volume,
        }
        for output_id, output in system.state.outputs.items()
    }


async def run_selector_noop_writes(hosts: tuple[str, ...], args: argparse.Namespace) -> dict[str, Any]:
    """Exercise public selector write helpers using only current values."""

    if not args.write_noops:
        return {"skipped": "pass --write-noops to exercise current-value writes"}

    system = System(
        hosts,
        port=args.port,
        read_only=False,
        reconnection_wait_secs=0.2,
        connection_timeout_secs=3.0,
    )
    attempts: list[dict[str, Any]] = []
    try:
        try:
            await asyncio.wait_for(
                system.discover(
                    target_devices=args.target_devices,
                    time_between_probes_secs=0.2,
                    time_to_wait_for_devices_with_unknown_inputs=1.0,
                ),
                timeout=args.system_timeout,
            )
            discover_timed_out = False
        except asyncio.TimeoutError:
            discover_timed_out = True

        before = output_snapshot(system)

        # Each selector call below uses an already-observed value. This validates
        # routing and serialization without intentionally changing output state.
        for output_id, output in list(system.state.outputs.items()):
            selector = system.output(output_id)
            if output.on is not None:
                selector.enable(output.on)
                attempts.append({"output": output_id, "action": "enable", "value": output.on})
            if output.muted is not None:
                selector.mute(output.muted)
                attempts.append({"output": output_id, "action": "mute", "value": output.muted})
            if output.volume is not None:
                selector.set_volume(output.volume)
                attempts.append({"output": output_id, "action": "set_volume", "value": output.volume})
            if output.max_volume is not None:
                selector.set_max_volume(output.max_volume)
                attempts.append(
                    {"output": output_id, "action": "set_max_volume", "value": output.max_volume}
                )
            selected_source = output.selected_reported_source_selector
            if selected_source is not None:
                device = system.state.device_for_output(output_id)
                source = (
                    system.state.inputs.get((device.id, selected_source))
                    if device is not None
                    else None
                )
                if source is not None:
                    selector.set_input(system.input_by_id(source.device_id, source.selector))
                    attempts.append(
                        {
                            "output": output_id,
                            "action": "set_input",
                            "value": f"{source.device_id}:0x{source.selector:02X}",
                        }
                    )

        await asyncio.sleep(args.write_settle)
        system.refresh_outputs()
        await asyncio.sleep(args.write_settle)

        after = output_snapshot(system)
        changed = {
            output_id: {"before": before_value, "after": after.get(output_id)}
            for output_id, before_value in before.items()
            if after.get(output_id) != before_value
        }
        return {
            "discover_timed_out": discover_timed_out,
            "attempts": attempts,
            "attempt_count": len(attempts),
            "changed_outputs_after_noops": changed,
            "before": before,
            "after": after,
        }
    finally:
        system.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe live AMP devices and write a raw/decode coverage artifact."
    )
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        default=[],
        help=(
            "Amplifier hostname/IP. Repeat for multiple transports. "
            f"Defaults to {', '.join(DEFAULT_HOSTS)}."
        ),
    )
    parser.add_argument("--port", type=int, default=17037)
    parser.add_argument("--target-devices", type=int, default=2)
    parser.add_argument("--system-timeout", type=float, default=20.0)
    parser.add_argument(
        "--send-gap",
        type=float,
        default=0.012,
        help="Seconds to wait between raw probe rows.",
    )
    parser.add_argument(
        "--idle-wait",
        type=float,
        default=0.18,
        help="Seconds to wait for trailing responses after each raw probe phase.",
    )
    parser.add_argument(
        "--write-settle",
        type=float,
        default=1.0,
        help="Seconds to wait after selector no-op writes and refreshes.",
    )
    parser.add_argument(
        "--write-noops",
        action="store_true",
        help="Also send current-value output writes and verify state is unchanged.",
    )
    parser.add_argument(
        "--output",
        default="/tmp/amp_live_probe_results.json",
        help="Path for the full JSON artifact.",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    hosts = tuple(args.hosts or DEFAULT_HOSTS)
    started_at = time.time()

    raw_results: dict[str, Any] = {}
    host_details: dict[str, HostProbe] = {}
    for host in hosts:
        try:
            details, summary = await run_raw_host(host, args)
            host_details[host] = details
            raw_results[host] = summary
        except Exception as exc:
            raw_results[host] = {"error": f"{type(exc).__name__}: {exc}"}

    system_discovery = await run_system_discovery(hosts, args)
    selector_noops = await run_selector_noop_writes(hosts, args)

    result = {
        "started_at": started_at,
        "duration_secs": round(time.time() - started_at, 3),
        "hosts": list(hosts),
        "write_noops": args.write_noops,
        "raw_summary": raw_results,
        "raw_details": {
            host: {
                "sent": [asdict(row) for row in detail.sent],
                "received": [asdict(event) for event in detail.received],
                "errors": detail.errors,
            }
            for host, detail in host_details.items()
        },
        "system_discovery": system_discovery,
        "selector_noop_writes": selector_noops,
        "skipped_live_writes": [
            "ZoneNameCommand and SourceNameOptionsCommand rename writes (explicitly excluded)",
            "NetworkSettingsDeviceGuidCommand identity write (unsafe identity repair/write path)",
            "DistributedSourceDefinitionCommand and DistributedSourceDefinitionUnusedCommand remote-slot config writes",
            "KeypadPortOccupancyCommand/KeypadPortOccupancyCommandResponse state-changing link writes",
            "SourceSpecificMetadataCommand writes",
            "VolumeUpCommand/VolumeDownCommand transient relative volume commands",
            "SourceGainCommand full-table writes",
        ],
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True, default=json_default))

    # The console output is intentionally much smaller than the artifact. It is
    # meant to answer "did this basically work?" while the JSON file carries the
    # complete row log for later analysis.
    print(
        json.dumps(
            {
                "output": args.output,
                "duration_secs": result["duration_secs"],
                "raw_summary": raw_results,
                "system_discovery_timed_out": system_discovery.get("timed_out"),
                "selector_noop_attempt_count": selector_noops.get("attempt_count"),
                "selector_noop_changed_outputs": selector_noops.get(
                    "changed_outputs_after_noops"
                ),
            },
            indent=2,
            sort_keys=True,
            default=json_default,
        )
    )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
