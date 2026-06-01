# autonomic-sdk

Python experiments around Autonomic amplifier protocols. The supported code in
this checkout is the `amp` package, which models the direct amplifier
diagnostic protocol on TCP port `17037`.

The old high-level `autonomic` client package is not present in this branch.

## Status

- `amp/` is the active direct-amplifier codec and transport package.
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

## Quick Start

Decode a row:

```python
from amp.codec import OpEncoder

op = OpEncoder().decoder(bytes.fromhex("040150"))
print(op)
```

Encode a write row:

```python
from amp.codec import OpEncoder, VolumeOp

encoder = OpEncoder(read_only=False)
row = encoder.encode(VolumeOp(output=1, volume=0.5))
print(row)  # 040150
```

Open a direct amplifier transport:

```python
from amp.codec import VolumeOp, connect

transport = connect("10.1.0.200")
transport.send(VolumeOp(output=1))
```

`OpEncoder(read_only=True)` is the default, so write-like ops are filtered
unless `read_only=False` is passed.

Build a read-only system snapshot:

```python
import asyncio

from amp.codec import connect
from amp.system import System


async def main() -> None:
    transport = connect("10.1.0.200")
    with System(transport) as system:
        await system.discover(target_devices=2)
        system.dump()


asyncio.run(main())
```

`System` stores devices, inputs, and outputs in key-sorted maps, so iteration is
stable by device id, `(device id, selector)`, and output id. Output name rows
with an empty payload are treated as known unnamed outputs.

When the TCP transport detects a dropped established connection it emits a
`ConnectionInterrupted` event before reconnecting. `System` handles that event by
queueing a read-only refresh so dynamic state is repopulated on the next
connection.

## Layout

- `amp/byte_utils.py`: `HexBytes` helpers for ASCII hex, integers, UUID/GUID
  byte order, and UTF-8.
- `amp/toggle_bool.py`: shared power/mute toggle value type.
- `amp/encoder.py`: declarative pattern compiler and subclass encoder.
- `amp/codec.py`: protocol dataclasses and opcode patterns.
- `amp/hardware.py`: observed hardware model metadata keyed by model number.
- `amp/system.py`: read-only in-memory system model built from decoded rows.
- `amp/transport.py`: async TCP row transport for port `17037`.
- `amp/tests/`: tests split by `amp/*.py` module.

See `PROTOCOL.md` for the current wire-format summary and opcode catalog.

## Tests

```bash
python -m pytest
python -m compileall -q amp
python -m mypy amp
```

The pytest configuration collects only `amp/tests`.
