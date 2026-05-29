# Direct Amplifier Protocol

This repository currently models the Autonomic direct amplifier diagnostic
protocol in the `amp` package. The implementation is intentionally small:

- `amp/transport.py` opens TCP connections to amplifier port `17037`.
- `amp/byte_utils.py` wraps ASCII-hex byte handling.
- `amp/encoder.py` compiles declarative message patterns.
- `amp/codec.py` is the protocol catalog: one dataclass per modeled row.

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

## Pattern Syntax

`amp/encoder.py` compiles `PATTERN` strings from `amp/codec.py`.

| Syntax | Meaning |
| --- | --- |
| Literal hex, e.g. `01FF` | Fixed bytes. |
| `{field}` | One byte of raw hex (`2X`). |
| `{field:N}` / `{field:4N}` | Unsigned integer, one byte / two bytes. |
| `{field:S}` | Signed integer, one byte. |
| `{field:X}` / `{field:4X}` | Raw bytes, one byte / two bytes. |
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
| `0D` | `MaxVolumeOp` | `0D{output:N}{max_volume:N?}{detail:N*}!` | Raw max volume byte plus optional detail. |
| `11` | `VolumeUpOp` | `11{output:N}!` | Write-only command; usually followed by `04` status. |
| `12` | `VolumeDownOp` | `12{output:N}!` | Write-only command; usually followed by `04` status. |
| `14` | `DeviceInfoDiscoveryOp` | `14FF06!` | Model/device discovery query. |
| `1C` | `OutputNameOp` | `1C{output:N}{name:utf8}!` | Output-name readback/write. |
| `1D` | `DiagnosticStatus1DOp` | `1D{output:N}{payload:hex}!` | Observed opaque status row. |
| `1E` | `DiagnosticStatus1EOp` | `1E{output:N}{payload:hex}!` | Observed opaque status row. |
| `29` | `SourceNameOp` | `29{output:N}{source_selector:X?}{misc:6X?}{hidden_name:lenutf8?}{name:utf8?}!` | Source label query/readback/write. |
| `2F` | `DeviceIdDiscoveryOp` | `2FFF!` | Device-id query. |
| `30` | `ZoneGroupOp` | `30{output:N}{flags:N?}{members:N*}!` | Zone group flags and member outputs. |
| `31` | `DelayOp` | `31{output:N}{delay:N?}!` | One-byte delay value. |
| `31` | `SourceDelayStatusOp` | `31{output:N}{source_delays:N+}!` | Multi-byte per-source delay readback. |
| `32` | `InputGainOp` | `32{output:N}{source_selector:N?}{gain:float(18,0.0,1.0)?}{source_gains:N*}!` | Per-source gain plus optional table. |
| `38` | `OutputNameRefreshOp` | `38FF!` | Requests `1C` rows. |
| `39` | `ExtendedDeviceInfoDiscovery` | `39FF{device_id:4X?}!` | Requests `B9` and sometimes `3A` rows. |
| `3A` | `DeviceGuidQueryOp` | `3AFF{device_id:4X}85!` | GUID-related query form. |
| `3A` | `DeviceGuidOp` | `3AFF{device_id:4X}05{guid:guid}!` | Device GUID readback/write form. |
| `3A` | `DeviceSystemIdOp` | `3AFF{device_id:4X}06{system_id:N}!` | System id readback. |
| `3A` | `DeviceSubInfoOp` | `3AFF{device_id:4X}{subtype:N}{payload:hex}!` | Generic sub-info fallback. |
| `44` | `OutputGainOp` | `44{output:N}{gain:S?}!` | Signed output gain. |
| `46` | `SourceMetadataOp` | `46{output:N}{source_selector:N}{position:N}{value:utf8}!` | Metadata set/readback. |
| `47` | `SourceMetadataQueryOp` | `47{output:N}{source_selector:N}{position:N}!` | Requests `46` rows. |
| `48` | `UnknownOutputStatusOp` | `48{output:N}{payload:hex}!` | Observed opaque output status. |
| `4A` | `DeviceStateOp` | `4AFF{device_id:4X}{state:hex?}!` | Opaque device state. |
| `4D` | `DeviceLinkQueryOp` | `4DFF{device_id:4X}{linked:bool?}!` | Stack/link query/status. |
| `4E` | `PresetGroupOp` | `4EFF{slot_id:4N}{payload:hex?}!` | Preset group map or slot payload. |
| `4F` | `RemoteSourceDiscoveryOp` | `4FFF{slot_id:N?}!` | Remote source table query. |
| `4F` | `RemoteSourceInfoOp` | `4FFF{slot_id:N}{backing_device_guid:guid}{source_index:N}{name:utf8}!` | Remote source definition/readback. |
| `4F` | `RemoteSourceDeleteOp` | `4FFF{slot_id:N}00!` | Remote source delete. |
| `94` | `DeviceInfoOp` | `94FF00{firmware:N}{model_id}{device_id:4X}{zones:N+}!` | Model/device discovery response. |
| `AF` | `DeviceIdOp` | `AFFF{device_id:4X}{zones:N*}!` | Device-id response. |
| `B9` | `ExtendedDeviceInfoOp` | `B9{output:N}{:4X}{device_id:4X}{:18X}{mac:12X}` | Extended identity; extracts device id and MAC. |
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

Remote-source and `3A` GUID fields use Autonomic/Windows GUID order, not RFC
UUID byte order:

```text
UUID: 674e1900-f8a9-f6be-a465-3d0fbee12977
Wire: 00194E67A9F8BEF6A4653D0FBEE12977
```

## Tests

Protocol behavior is covered under `amp/tests`, with `amp/tests/test_codec.py`
covering representative encode/decode rows for every modeled opcode family.
