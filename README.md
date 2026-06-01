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

Open a direct amplifier transport:

```python
from amp.codec import VolumeCommand, connect

transport = connect("10.1.0.200")
transport.send(VolumeCommand(output=1))
```

`CommandEncoder(read_only=True)` is the default, so write-like commands are filtered
unless `read_only=False` is passed.

Build a system snapshot:

```python
import asyncio

from amp.system import System


async def main() -> None:
    async with System(("10.1.0.200", "10.1.0.201")) as system:
        await system.discover(target_devices=2)
        system.dump()


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
- `assigned_name` from source-name rows
- `hardware_name` from hardware metadata when available
- whether an assigned runtime name has actually been discovered

Hardware names are only defaults. `InputState.name` dynamically returns
`assigned_name`, then `hardware_name`, then a generic selector label. Discovery
continues until assigned runtime names have been observed, so hardware names
cannot complete discovery by themselves or override source-name rows. Remote
source names are runtime configuration; they are not assumed from hardware model
metadata.

`OutputState` tracks:

- output name, power, mute, selected source, volume, and maximum volume

`RemoteInput` tracks:

- slot presence, backing device GUID, backing source index, and display name

System state can be serialized with `SystemState.save_to_file()` and restored
with `SystemState.load_from_file()`.

## Discovery

`System.discover()` first discovers devices, then runs input, output, and remote
source discovery concurrently.

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

`system.all_outputs()` sends one `FF` all-output command to each device
transport, so a multi-device system gets one broadcast per connected device.

`system.all_inputs()` returns a tuple of `InputSelector` objects for every input
currently in `system.state.inputs`.

Name lookups are case-insensitive and whitespace-insensitive:

```python
system.input_by_name(" player c ")
system.output_by_name("patio west")
```

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
