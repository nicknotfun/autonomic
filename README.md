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

## Layout

- `amp/byte_utils.py`: `HexBytes` helpers for ASCII hex, integers, UUID/GUID
  byte order, and UTF-8.
- `amp/types.py`: shared value types such as `ToggleBool`.
- `amp/encoder.py`: declarative pattern compiler and subclass encoder.
- `amp/codec.py`: protocol dataclasses and opcode patterns.
- `amp/transport.py`: async TCP row transport for port `17037`.
- `amp/tests/`: tests split by `amp/*.py` module.

See `PROTOCOL.md` for the current wire-format summary and opcode catalog.

## Tests

```bash
python -m pytest
```

The pytest configuration collects only `amp/tests`.
