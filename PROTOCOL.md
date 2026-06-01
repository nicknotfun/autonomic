# Direct Amplifier Protocol

This repository currently models the Autonomic direct amplifier diagnostic
protocol in the `amp` package. The implementation is intentionally small:

- `amp/transport.py` opens TCP connections to amplifier port `17037`.
- `amp/byte_utils.py` wraps ASCII-hex byte handling.
- `amp/encoder.py` compiles declarative message patterns.
- `amp/codec.py` is the protocol catalog: one dataclass per modeled row.
- `amp/system.py` builds a read-only in-memory model from decoded rows.

MRAD/MAS command sessions on port `5006` and media-server APIs are not part of
the supported direct amplifier codec.

## Wire Shape

Rows are CRLF-terminated ASCII hex strings. Two text characters encode one
binary byte:

```text
010101
```

means:

```text
01 01 01
OP OUT DATA
```

Most rows are:

```text
<opcode><output>[payload...]
```

`output` is usually a one-byte output address, with `FF` meaning endpoint/device
scope. Rows with no payload are usually reads. Rows with payload are usually
writes or status readbacks.

The diagnostic port behaves like a shared row bus. Rows from commands issued by
other clients can arrive on the same socket, and some query families are
multicast to all connected diagnostic sockets. Consumers should decode rows by
family and tolerate unrelated rows before, during, and after the requested
response.

`amp.transport.Transport` emits a local `ConnectionInterrupted` event when an
established TCP session is lost. The event is not a wire row; it lets higher
layers queue read-only refreshes while the transport reconnects.

## Pattern Syntax

`amp/encoder.py` compiles `PATTERN` strings from `amp/codec.py`.

| Syntax | Meaning |
| --- | --- |
| Literal hex, e.g. `01FF` | Fixed bytes. |
| `{field}` | One byte of raw hex (`2X`). |
| `{field:N}` / `{field:4N}` | Unsigned integer, one byte / two bytes. |
| `{field:S}` | Signed integer, one byte. |
| `{field:X}` / `{field:4X}` | Raw bytes, one byte / two bytes. |
| `{field:float(160,0.0,1.0)}` | Scaled numeric byte range to `float`. |
| `{field:bool}` | `00` false, `01` true. |
| `{field:power_bool}` | `00` off, `01` on, `04` toggle. |
| `{field:mute_bool}` | `01` off/unmuted, `00` on/muted, `02` toggle. |
| `{field:utf8}` | UTF-8 text consuming the remaining row. |
| `{field:lenutf8}` | One-byte length prefix, then UTF-8 text. |
| `{field:uuid}` | 16 bytes in RFC UUID byte order. |
| `{field:guid}` | 16-byte Autonomic/Windows GUID order (`UUID.bytes_le`). |
| `{field:hex}` | Raw bytes consuming the remaining row. |
| `{field:T?}` | Optional field. |
| `{field:T*}` / `{field:T+}` | Repeated field, zero-or-more / one-or-more. |
| `{:=ABCD}` or `{:4X}` | Fixed/ignored bytes. |
| Trailing `!` | Pattern must consume the full row. |

`OpEncoder(read_only=True)` is the default. It suppresses ops whose
`is_write()` returns true.

## Opcode Catalog

This table mirrors the dataclasses in `amp/codec.py`.

| Opcode | Class | Pattern | Notes |
| --- | --- | --- | --- |
| `01` | `PowerOp` | `01{output:N}{is_on:power_bool?}!` | Read/write output power. |
| `02` | `MuteOp` | `02{output:N}{is_muted:mute_bool?}!` | Mute polarity differs from power. |
| `03` | `SourceSelectOp` | `03{output:N}{source:N?}{detail:N*}!` | Source select/status; extra status bytes are preserved. |
| `04` | `VolumeOp` | `04{output:N}{volume:float(160,0.0,1.0)?}{detail:N*}!` | Raw `00`-`A0` mapped to `0.0`-`1.0`. |
| `05` | `BassOp` | `05{output:N}{bass:S?}!` | Signed byte. |
| `06` | `TrebleOp` | `06{output:N}{treble:S?}!` | Signed byte. |
| `07` | `BalanceOp` | `07{output:N}{balance:S?}!` | Signed byte. |
| `09` | `OutputParametersRefreshOp` | `09{output:N}{request:N?}!` | Status refresh request, default payload `00`. |
| `0C` | `LoudnessOp` | `0C{output:N}{is_loud:bool?}{detail:N*}!` | Extra status bytes are preserved. |
| `0D` | `MaxVolumeOp` | `0D{output:N}{max_volume:float(160,0.0,1.0)?}{detail:N*}!` | Raw `00`-`A0` mapped to `0.0`-`1.0`, plus optional detail. |
| `11` | `VolumeUpOp` | `11{output:N}!` | Write-only command; usually followed by `04` status. |
| `12` | `VolumeDownOp` | `12{output:N}!` | Write-only command; usually followed by `04` status. |
| `14` | `DeviceInfoDiscoveryOp` | `14FF06!` | Model/device discovery query. |
| `1C` | `OutputNameOp` | `1C{output:N}{name:utf8}!` | Output-name readback/write. |
| `1D` | `DiagnosticStatus1DOp` | `1D{output:N}{payload:hex}!` | Observed opaque status row. |
| `1E` | `DiagnosticStatus1EOp` | `1E{output:N}{payload:hex}!` | Observed opaque status row. |
| `29` | `SourceNameDiscoveryOp` | `29{output:N}!` | Requests source label rows for one output/source table. Avoid `FF` for normal discovery; observed MA6 replies fan out across all local outputs. |
| `29` | `SourceNameOp` | `29{output:N}{source_selector:N}{misc:6X?}{hidden_name:lenutf8?}{name:utf8?}!` | Source label query/readback/write; `source_selector` is the raw logical source byte, `misc` is the three-byte short-name/flags field, and some rows include a length-prefixed internal name before the display name. |
| `2F` | `DeviceIdDiscoveryOp` | `2FFF!` | Device-id query. |
| `30` | `ZoneGroupOp` | `30{output:N}{flags:N?}{members:N*}!` | Zone group flags and member outputs. |
| `31` | `DelayOp` | `31{output:N}{delay:N?}!` | One-byte delay value. |
| `31` | `SourceDelayStatusOp` | `31{output:N}{source_delays:N+}!` | Multi-byte per-source delay readback. |
| `32` | `InputGainOp` | `32{output:N}{source_selector:N?}{gains:float(18,0.0,1.0)*?}!` | Per-source gain table; `32xxFF...` is useful for discovering input count. |
| `38` | `OutputNameRefreshOp` | `38{output:N}!` | Requests `1C` rows; default output is `FF`. |
| `39` | `ExtendedDeviceInfoDiscoveryOp` | `39FF{device_id:4X?}!` | Requests `B9` and sometimes `3A` rows. |
| `3A` | `DeviceGuidQueryOp` | `3AFF{device_id:4X}85!` | GUID-related query form. |
| `3A` | `DeviceGuidOp` | `3AFF{device_id:4X}05{guid:guid}!` | Device GUID readback/write form. |
| `3A` | `DeviceSystemIdOp` | `3AFF{device_id:4X}06{system_id:N}!` | System id readback. |
| `3A` | `DeviceSubInfoOp` | `3AFF{device_id:4X}{subtype:N}{payload:hex}!` | Generic sub-info fallback. |
| `44` | `OutputGainOp` | `44{output:N}{gain:S?}!` | Signed output gain. |
| `46` | `SourceMetadataOp` | `46{output:N}{source_selector:N}{position:N}{value:utf8}!` | Metadata set/readback. |
| `47` | `SourceMetadataQueryOp` | `47{output:N}{source_selector:N}{position:N}!` | Requests `46` rows. |
| `48` | `UnknownOutputStatusOp` | `48{output:N}{payload:hex}!` | Observed opaque output status. |
| `58` | `DeviceHostInfoDiscoveryOp` | `58FF00!` | Host identity query observed during device discovery. |
| `58` | `DeviceHostInfoOp` | `58FF00{guid:uuid}{mac:12X}{detail:hex}!` | Host identity row with MAC and GUID candidate bytes. |
| `4A` | `DeviceStateOp` | `4AFF{device_id:4X}{state:hex?}!` | Opaque device state. |
| `4D` | `DeviceLinkQueryOp` | `4DFF{device_id:4X}{linked:bool?}!` | Stack/link query/status. |
| `4E` | `PresetGroupOp` | `4EFF{slot_id:4N}{payload:hex?}!` | Preset group map or slot payload. |
| `4F` | `RemoteSourceDiscoveryOp` | `4FFF{slot_id:N?}!` | Remote source table query; slots are `00` through `1F`. |
| `4F` | `RemoteSourceInfoOp` | `4FFF{slot_id:N}{backing_device_guid:guid}{source_index:N}{name:utf8}!` | Remote source definition/readback. |
| `4F` | `RemoteSourceDeleteOp` | `4FFF{slot_id:N}00!` | Remote source delete. |
| `94` | `DeviceInfoOp` | `94FF00{firmware:N}{model_id}{device_id:4X}{zones:N+}!` | Model/device discovery response. |
| `AF` | `ThisDeviceIdOp` | `AFFF{device_id:4X}{zones:N*}!` | Current transport's local device-id response. |
| `B9` | `ExtendedDeviceInfoOp` | `B9FF{prefix:4X}{device_id:4X}{model_info:18X}{mac:12X}{detail:hex}!` | Extended identity; preserves opaque prefix/model/detail bytes while extracting device id and MAC. |
| `CD` | `DeviceLinkOp` | `CDFF{device_id:4X}{linked:bool?}!` | Alternate stack/link row. |

## Value Notes

Power and mute use different byte polarity:

| Operation | Off | On | Toggle |
| --- | --- | --- | --- |
| Power | `00` | `01` | `04` |
| Mute | `01` | `00` | `02` |

Volume is modeled as a float, with raw `00`-`A0` mapped to `0.0`-`1.0`.
Input gain is modeled similarly, with raw `00`-`12` mapped to `0.0`-`1.0`.

Bass, treble, balance, and output gain are signed one-byte values. For example,
`FD` decodes to `-3`.

Output-name rows with no payload, such as `1C0D`, are valid readbacks for known
unnamed outputs.

Source-name discovery is output-addressed even when names are shared across an
amplifier's outputs. Query one representative output for each source table
instead of `FF`; on the probed stack, `29FF` returned the MA6 table once per
output (`8 * 19 = 152` rows).

## System Model

`amp/system.py` consumes decoded rows from the transport and maintains a
read-only view of devices, inputs, and outputs. The maps are `SortedDict`
instances, so iteration is stable by device id, `(device id, selector)`, and
output id.

Discovery sends only read/query operations through the default read-only encoder:

- device discovery: `14`, `2F`, `39`, `3A ... 85`, and `32 ... FF` query rows
- input discovery: representative-output `29` source-name queries
- output discovery: `38`, `01`, `02`, `03`, `04`, and `0D` query rows

The system model does not issue GUID repair/write rows or other identity-repair
operations.

When `System` receives `ConnectionInterrupted`, it queues the same read-only
device/output gap-fill and dynamic output-status queries used by manual refresh.

### Source Selector Bytes

Source selector bytes are logical crosspoint/source ids, not physical display
order, not a reversed physical index, and not a one-hot mask. External RTI,
Crestron, and Control4 drivers all use the same physical-source lookup table,
and live M6250 probing matched the first eight entries exactly:

| Physical source number | M6250 label | Selector |
| --- | --- | --- |
| 1 | A1 | `05` |
| 2 | A2 | `06` |
| 3 | A3 | `07` |
| 4 | A4 | `03` |
| 5 | COAX1 | `00` |
| 6 | COAX2 | `01` |
| 7 | W1 | `02` |
| 8 | W2 | `04` |
| 9 | driver physical 9 | `08` |
| 10 | driver physical 10 | `09` |
| 11 | driver physical 11 | `0A` |
| 12 | driver physical 12 | `0B` |

In raw selector order, the M6250 local table is therefore:

| Selector | M6250 local source |
| --- | --- |
| `00` | COAX1 |
| `01` | COAX2 |
| `02` | W1 |
| `03` | A4 |
| `04` | W2 |
| `05` | A1 |
| `06` | A2 |
| `07` | A3 |

The apparent randomness comes from the device's internal source numbering:
selectors `00`-`04` are not the first five rear-panel labels, and the analog
bank is split (`A4` at `03`, then `A1`-`A3` at `05`-`07`). Treat this as a
model/source lookup table.

Source feedback may set bit `7` on the source byte. Clear it with `7F` before
looking up the selector. For example, observed M6250 source-status bytes `A6`,
`A7`, and `A1` decode to remote selectors `26`, `27`, and `21`.

Remote/eAudioCast selectors use the higher range. The broad driver convention
is `20`-`3F`; the probed M6250 table currently accepted `20`-`27`:

| Selector | Observed M6250 remote/eAudio label |
| --- | --- |
| `20` | W1 |
| `21` | Player_A@ACE14F006012 |
| `22` | W2 |
| `23` | W3 |
| `24` | W4 |
| `25` | Passthrough In |
| `26` | Player_C@ACE14F006012 |
| `27` | Player_B@ACE14F006012 |

These remote selectors are not part of the physical local-source order.

### Observed MA6/M6250 Stack

Read-only probes against `10.1.0.200:17037` and `10.1.0.201:17037` on
2026-05-30 showed both endpoints exposing the same stacked devices:

| Device | Device id | Model byte | Firmware | Outputs | MAC | GUID |
| --- | --- | --- | --- | --- | --- | --- |
| M6250 | `00D4` | `B0` | `06` | `01`-`08` | `ACE14F0055B4` | `674e1900-f8a9-f6be-a465-3d0fbee12977` |
| MA6 | `6012` | `E9` | `08` | `09`-`10` hex | `ACE14F006012` | `6c126887-df88-bd41-abbd-079c4e743694` |

`14FF06` returned both device-info rows from both endpoints. `2FFF` returned
the M6250 `AF` row from `.200`; `.201` did not return a `2F` row during the
focused probe. `39FF00D4`, `39FF6012`, `3AFF00D485`, and `3AFF601285`
returned the MAC/GUID data above from both endpoints.

`58FF00` identity rows decode as `DeviceHostInfoOp`. They carry a MAC and GUID
candidate bytes, but they are not treated as proof that the row's device is
local to the transport that emitted it; `AFFF...` and concrete output readbacks
are used for direct transport ownership.

Observed source-name tables:

| Output query | Device/source table | Rows | Notes |
| --- | --- | --- | --- |
| `2901`-`2908` | M6250 | 16 | 8 local selectors `00`-`07`, plus remote selectors `20`-`27`. |
| `2921`-`2928` | M6250 aliases | 16 | Alias back to outputs `01`-`08`. |
| `29FE` | M6250 alias | 16 | Alias to output `01`. |
| `2909`-`2910` hex | MA6 | 19 | 11 non-casting selectors plus 8 casting selectors. |
| `29FF` | broadcast/fanout | varies | Avoid for discovery; `.201` returned the MA6 table repeated for output bytes `09` through `10` hex (`152` rows). |

MA6 non-casting selectors observed on output bytes `09` through `10` hex:

| Selector | MA6 source |
| --- | --- |
| `05` | Player_A |
| `06` | Player_B |
| `07` | Player_C |
| `03` | Analog 1 |
| `00` | Analog 2 |
| `01` | Analog 3 |
| `02` | Analog 4 |
| `04` | Coaxial 1 |
| `08` | Coaxial 2 |
| `09` | W3 |
| `0A` | W4 |

MA6 casting selectors observed were `0B`-`0F` and `50`-`52`.

Focused output-name probing against `.201` also showed outputs `0D`-`10`
returning empty names (`1C0D`, `1C0E`, `1C0F`, `1C10`). Those are currently
treated as known unnamed outputs.

Remote-source and `3A` GUID fields use Autonomic/Windows GUID order, not RFC
UUID byte order:

```text
UUID: 674e1900-f8a9-f6be-a465-3d0fbee12977
Wire: 00194E67A9F8BEF6A4653D0FBEE12977
```

## Tests

Protocol behavior is covered under `amp/tests`, with `amp/tests/test_codec.py`
covering representative encode/decode rows for every modeled opcode family.
