# autonomic-sdk

Python experiments around Autonomic amplifier protocols. The supported surface
in this checkout is the `amp` package, which models the direct amplifier
diagnostic protocol on TCP port `17037`.

The old high-level `autonomic` client package is not present in this branch.

## Status

- `amp/` is the active direct-amplifier codec, transport, hardware metadata, and
  system-state package.
- `mas/`, `mas_summary.py`, and other MAS/MRAD files are experimental
  prototypes. They are incomplete, unsupported, and should not be used for
  integration work.
- Media-server playback APIs and MRAD/MAS command sessions are not implemented
  by the supported `amp` package.

## Install

```bash
python -m pip install -e .
```

For this checkout, `.venv` points at `~/.venvs/default`.

## Protocol Reference

[PROTOCOL.md](PROTOCOL.md) is a repository-independent description of the direct
amplifier wire protocol: row format, command families, value encoding, source
selector tables, and observed MA6/M6250 behavior.

The implementation catalog lives in [amp/codec.py](amp/codec.py). It models rows
as typed command objects and uses [amp/encoder.py](amp/encoder.py) to encode
and decode declarative patterns.

## Quick Start

Decode a row:

```python
from amp.codec import CommandEncoder

command = CommandEncoder().decoder(bytes.fromhex("040150"))
print(command)
```

Encode a write row:

```python
from amp.codec import CommandEncoder, VolumeCommand

encoder = CommandEncoder(read_only=False)
row = encoder.encode(VolumeCommand(output=1, volume=0.5))
print(row)  # 040150
```

Open a direct amplifier transport from an active asyncio event loop:

```python
import asyncio

from amp.codec import VolumeCommand, connect


async def main() -> None:
    transport = connect("10.1.0.200")
    try:
        transport.send(VolumeCommand(output=1))
        await asyncio.sleep(1)
    finally:
        await transport.aclose()


asyncio.run(main())
```

`CommandEncoder(read_only=True)` is the default, so write-like commands are filtered
unless `read_only=False` is passed.

Build a system snapshot:

```python
import asyncio

from amp.system import System


async def main() -> None:
    async with System(("10.1.0.109", "10.1.0.200")) as system:
        await system.discover(target_devices=2, timeout_secs=10)
        system.state.dump()


asyncio.run(main())
```

`System` accepts a host string, an iterable of host strings, an existing
transport, or an iterable of transports. It starts transport event tasks during
construction, so create it inside an active asyncio event loop. Pass
`read_only=False` when constructing a write-enabled system. Prefer
`async with System(...)` or `await system.aclose()` when cleanup must wait for
listener and transport tasks to finish; the synchronous context manager still
performs best-effort shutdown.

## System State

`SystemState` is the high-level view maintained by [amp/system.py](amp/system.py).
It has four key-sorted maps:

- `state.devices`: keyed by device id (`HexBytes`).
- `state.inputs`: keyed by `(device id, source selector)`.
- `state.outputs`: keyed by output id.
- `state.remote_inputs`: keyed by remote source slot id.

The maps preserve sorted iteration, so output is stable by device id,
`(device id, selector)`, output id, and remote slot id.

`DeviceState` tracks:

- host ownership for routing writes when multiple transports are used
- firmware, model id, MAC, GUID, output ids
- authoritative `input_count` and `output_count` from `amp.hardware` when the
  model number is known

`InputState` tracks:

- source selector and device id
- exact source option bytes required to write the source row back safely
- `assigned_name` from source-name rows
- `hardware_name` from hardware metadata when available
- hardware source kind and physical source id when model metadata is available
- whether an assigned runtime name has actually been discovered

Hardware names are only defaults. `InputState.name` dynamically returns
`assigned_name`, then `hardware_name`, then a generic selector label. Discovery
continues until assigned runtime names have been observed, so hardware names
cannot complete discovery by themselves or override source-name rows. Remote
source names are runtime configuration; they are not assumed from hardware model
metadata.

`OutputState` tracks:

- output name, power, mute, volume (including extended-volume detail), and
  maximum volume
- raw source-selection readback bytes: the reported source byte and any extra
  reported source bytes

`OutputState` also has helpers for interpreting those raw source bytes, such as
reported local and remote source selectors. Cross-state relationships, such as
active sources and remote backing inputs, are derived at selector time from
output, device, input, and remote-input state rather than copied into
`OutputState`.

`RemoteInput` tracks:

- slot presence, backing device GUID, backing source index, and display name

`SystemState.save_to_file()` and `SystemState.load_from_file()` serialize the
in-memory view. The `System` client adds configuration backup and hardware
restore methods:

```python
async with System(("10.1.0.200", "10.1.0.201")) as system:
    await system.discover(target_devices=2, timeout_secs=10)
    await system.save_state("state.json")

async with System(
    ("10.1.0.200", "10.1.0.201"),
    read_only=False,
) as system:
    await system.discover(target_devices=2, timeout_secs=10)
    await system.restore_state("state.json")
```

`restore_state()` validates device and output ownership before sending anything.
It restores the tracked writable configuration: source names with their exact
option bytes, output names, and maximum volumes. It never writes device identity
or GUID fields, and it does not replay saved power, volume, mute, or selected
source state. Instead, it temporarily mutes each restored output, sets its final
volume to `0.5`, then unmutes it; power is left unchanged.

There is no documented no-source selector: an omitted source is a query and
`00` is a real local input. Restore therefore leaves the selected source
unchanged. Distributed-source definitions remain in the JSON for reference but
are not written because the current state model does not retain per-device slot
ownership. Other codec-level settings not tracked by `SystemState` are likewise
outside this restore operation.

## Discovery

`System.discover()` first discovers devices, then runs input, output, and remote
source discovery concurrently. Pass `timeout_secs` to bound the complete
workflow; `time_between_probes_secs` controls only the wait between individual
probe rounds.

Device discovery uses read/query rows to find device ids, device metadata, MACs,
GUIDs, output ownership, and transport host ownership.

Input discovery queries source-name tables from device outputs. Known hardware
models provide authoritative input counts, so discovery retries source-name
queries when fewer runtime names have been detected than the model expects. If a
device has no known model input count, input discovery can infer a count from
observed source-name rows after a wait window.

Output discovery creates output state from discovered device output lists, asks
for names, and refreshes dynamic output status: power, mute, source, volume, and
maximum volume.

Remote source discovery queries slots `00` through `1F`.

When a TCP transport detects a dropped established connection it emits a local
`ConnectionInterrupted` event. `System` handles that by queuing read-only refresh
queries so dynamic state is repopulated after reconnect.

## Selectors

The public selector API wraps state entries and emits typed protocol commands:

```python
system.output(1).set_volume(0.5)
system.output_by_name("Kitchen").mute()
system.all_outputs().enable()
system.all_outputs().set_input(system.input_by_name("W1"))
```

`system.all_outputs()` operates on the canonical outputs already present in
`system.state.outputs`; discovery and refresh helpers are responsible for
populating that set.
`OutputSelector.set_input()` resolves the source command for each target output:
same-device inputs use the local selector, while cross-device inputs require a
matching distributed source slot and raise `ValueError` when no route is known.

`system.all_inputs()` returns non-remote `InputSelector` objects in physical
source order by default. `system.input_by_name()` uses the same filtered view.
Pass `include_remote=True` to include distributed source rows from
`system.state.inputs`.

Name lookups are case-insensitive and whitespace-insensitive:

```python
system.input_by_name(" player c ")
system.output_by_name("patio west")
```

Assigned input names take precedence over model-derived hardware aliases.
Lookups raise `ValueError` when multiple local (or multiple remote) inputs share
the same name, or when multiple outputs share a name, instead of selecting an
arbitrary device. Use `input_by_id(device_id, selector)` or `output(output_id)`
to disambiguate.

## Hardware Metadata

[amp/hardware.py](amp/hardware.py) contains model metadata keyed by model number.
It currently includes:

- M6250 (`B0`): 8 outputs, 8 hardware inputs
- MA6 (`E9`): 8 outputs, 19 hardware inputs including casting sources

Only local and casting hardware sources are included. Remote/eAudioCast sources
are discovered from runtime source-name and distributed-source-slot rows.

## Examples

Example scripts live under [examples/](examples):

- `dump_system_status.py`: dump discovered state as JSON.
- `short_dump.py`: print a compact device/input/output summary.
- `set_all_outputs_to_source.py`: route every output to a named input.
- `set_all_zones_to_200_opt1.py`: route known M6250/MA6 devices by input name.
- `unmute_set_all_to_50_and_power_on.py`: enable, unmute, and set volume.
- `add_eaudiocast_sources.py`: define remote source slots.

Write examples construct `System(..., read_only=False)`.

## Layout

- `amp/byte_utils.py`: `HexBytes` helpers for ASCII hex, integers, UUID/GUID
  byte order, and UTF-8.
- `amp/toggle_bool.py`: shared power/mute toggle value type.
- `amp/encoder.py`: declarative pattern compiler and subclass encoder.
- `amp/codec.py`: protocol command dataclasses and command-byte patterns.
- `amp/hardware.py`: observed hardware model metadata keyed by model number.
- `amp/system.py`: in-memory system state and selector API built from decoded
  rows.
- `amp/transport.py`: async TCP row transport for port `17037`.
- `amp/versioned.py`: versioned state helpers and sorted tracked maps.
- `amp/tests/`: tests split by `amp/*.py` module.

## Tests

```bash
python -m pytest
python -m compileall -q amp
python -m mypy amp
```

The pytest configuration collects only `amp/tests`.
