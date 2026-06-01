# Autonomic Direct Amplifier Protocol

This document describes the direct amplifier diagnostic protocol observed on
Autonomic amplifiers over TCP port `17037`.

It does not describe MAS/MRAD media-server command sessions or playback APIs.

## Wire Format

Messages are CRLF-terminated ASCII hexadecimal rows. Two ASCII characters encode
one byte.

```text
010101\r\n
```

represents:

```text
01 01 01
```

Most rows have this shape:

```text
<opcode><target>[payload...]
```

`target` is usually a one-byte output address. `FF` is used for broadcast,
endpoint, or device-wide scope depending on the opcode.

Rows with no payload are usually queries. Rows with payload can be commands,
status readbacks, or configuration writes depending on the opcode family.

The diagnostic socket behaves like a shared row bus. A client can receive rows
caused by other clients, and some query families are multicast to all connected
diagnostic sockets. Clients should decode rows by opcode family and tolerate
unrelated rows before, during, and after the expected response.

## Field Types

The row descriptions below use this notation:

| Notation | Meaning |
| --- | --- |
| `BYTE` | One unsigned byte. |
| `S8` | One signed byte, two's-complement. |
| `U16` | Two-byte unsigned integer, big-endian. |
| `HEX[n]` | Exactly `n` raw bytes. |
| `HEX...` | Remaining raw bytes. |
| `TEXT...` | Remaining bytes decoded as UTF-8. |
| `LTEXT` | One-byte length prefix followed by UTF-8 text. |
| `UUID` | 16 bytes in RFC UUID byte order. |
| `GUID` | 16 bytes in Autonomic/Windows GUID byte order. |
| `VOL160` | One byte `00` through `A0`, representing `0.0` through `1.0`. |
| `GAIN18` | One byte `00` through `12`, representing `0.0` through `1.0`. |
| `[field]` | Optional field. |
| `field...` | Repeated field until the row ends. |

All multi-byte integer fields observed here are big-endian unless explicitly
called out as `GUID`.

## Common Values

Power and mute use different byte polarity:

| Operation | Off | On | Toggle |
| --- | --- | --- | --- |
| Power | `00` | `01` | `04` |
| Mute | `01` | `00` | `02` |

Volume and maximum volume use raw `00`-`A0`, scaled linearly to `0.0`-`1.0`.
Input gain uses raw `00`-`12`, also scaled linearly to `0.0`-`1.0`.

Bass, treble, balance, and output gain are signed one-byte values. For example,
`FD` is `-3`.

Output-name rows with no text payload, such as `1C0D`, are valid readbacks for
known unnamed outputs.

Source status bytes can set bit `7`. Clear that bit with `7F` before comparing
the source selector. For example, observed source-status bytes `A6`, `A7`, and
`A1` decode to selectors `26`, `27`, and `21`.

## Opcode Families

### Output Power, Mute, Source, and Volume

| Opcode | Row shape | Meaning |
| --- | --- | --- |
| `01` | `01 <output> [power-byte]` | Power query, command, or status. |
| `02` | `02 <output> [mute-byte]` | Mute query, command, or status. |
| `03` | `03 <output> [source-selector] [detail...]` | Source query, command, or status. |
| `04` | `04 <output> [VOL160] [detail...]` | Volume query, command, or status. |
| `0D` | `0D <output> [VOL160] [detail...]` | Maximum-volume query, command, or status. |
| `11` | `11 <output>` | Volume-up command. |
| `12` | `12 <output>` | Volume-down command. |

The source-select `detail` bytes are not fully decoded. Preserve them if a
client needs lossless logging or replay.

### Output Tone, Gain, Loudness, and Delay

| Opcode | Row shape | Meaning |
| --- | --- | --- |
| `05` | `05 <output> [S8]` | Bass query, command, or status. |
| `06` | `06 <output> [S8]` | Treble query, command, or status. |
| `07` | `07 <output> [S8]` | Balance query, command, or status. |
| `09` | `09 <output> [request-byte]` | Output-parameter refresh request. |
| `0C` | `0C <output> [00-or-01] [detail...]` | Loudness query, command, or status. |
| `31` | `31 <output> [delay-byte]` | Delay query or command. |
| `31` | `31 <output> <delay-byte>...` | Per-source delay status. |
| `32` | `32 <output> [source-selector] [GAIN18...]` | Input-gain query, command, or status. |
| `44` | `44 <output> [S8]` | Output-gain query, command, or status. |

For input-gain status, querying source selector `FF` can return the full gain
table for the output's device.

### Names and Source Metadata

| Opcode | Row shape | Meaning |
| --- | --- | --- |
| `1C` | `1C <output> <TEXT...>` | Output-name status or write. |
| `29` | `29 <output>` | Source-name table query for an output. |
| `29` | `29 <output> <source-selector> [HEX[3]] [LTEXT] [TEXT...]` | Source-name status or write. |
| `38` | `38 <output>` | Output-name refresh query. |
| `46` | `46 <output> <source-selector> <position> <TEXT...>` | Source metadata status or write. |
| `47` | `47 <output> <source-selector> <position>` | Source metadata query. |

The three optional bytes in source-name rows are a short-name or flags field.
Some source-name rows include an internal length-prefixed name before the display
name.

Source-name discovery is output-addressed even when names are shared across an
amplifier's outputs. Querying one representative output per source table avoids
large fanout responses. On the observed MA6/M6250 stack, `29FF` returned the MA6
source table once per local output (`8 * 19 = 152` rows).

### Device Discovery and Identity

| Opcode | Row shape | Meaning |
| --- | --- | --- |
| `14` | `14 FF 06` | Device-info discovery query. |
| `94` | `94 FF 00 <firmware> <model-id> <device-id:U16> <output>...` | Device-info response. |
| `2F` | `2F FF` | Current endpoint device-id query. |
| `AF` | `AF FF <device-id:U16> [output...]` | Current endpoint device-id response. |
| `39` | `39 FF [device-id:U16]` | Extended device-info query. |
| `B9` | `B9 FF <prefix:HEX[2]> <device-id:U16> <model-info:HEX[9]> <mac:HEX[6]> <detail...>` | Extended identity response. |
| `3A` | `3A FF <device-id:U16> 85` | GUID query. |
| `3A` | `3A FF <device-id:U16> 05 <GUID>` | Device GUID status or write. |
| `3A` | `3A FF <device-id:U16> 06 <system-id>` | System-id status. |
| `3A` | `3A FF <device-id:U16> <subtype> <payload...>` | Other device sub-info status. |
| `58` | `58 FF 00` | Host identity query. |
| `58` | `58 FF 00 <UUID> <mac:HEX[6]> <detail...>` | Host identity response. |

`3A` GUID fields use Autonomic/Windows GUID byte order. For example:

```text
UUID: 674e1900-f8a9-f6be-a465-3d0fbee12977
Wire: 00194E67A9F8BEF6A4653D0FBEE12977
```

`58` identity rows have been observed with GUID-like bytes that can be
interpreted in more than one UUID byte order. The MAC address is the most direct
identity field in this row.

### Grouping, Linking, Presets, and Remote Sources

| Opcode | Row shape | Meaning |
| --- | --- | --- |
| `30` | `30 <output> [flags] [member-output...]` | Zone-group status. |
| `4A` | `4A FF <device-id:U16> [state...]` | Opaque device state. |
| `4D` | `4D FF <device-id:U16> [00-or-01]` | Device-link query, command, or status. |
| `CD` | `CD FF <device-id:U16> [00-or-01]` | Alternate device-link query, command, or status. |
| `4E` | `4E FF <slot-id:U16> [payload...]` | Preset group map or slot payload. |
| `4F` | `4F FF [slot-id]` | Remote source-slot query. |
| `4F` | `4F FF <slot-id> <GUID> <source-index> <TEXT...>` | Remote source-slot definition or status. |
| `4F` | `4F FF <slot-id> 00` | Remote source-slot delete. |

Remote source slots are `00` through `1F`.

### Opaque Diagnostic Rows

| Opcode | Row shape | Meaning |
| --- | --- | --- |
| `1D` | `1D <output> <payload...>` | Opaque diagnostic status. |
| `1E` | `1E <output> <payload...>` | Opaque diagnostic status. |
| `48` | `48 <output> <payload...>` | Opaque output status. |

## Source Selectors

Source selector bytes are logical crosspoint/source ids. They are not physical
display order, reversed physical indexes, or one-hot masks.

### M6250 Hardware Sources

- Model byte: `B0`
- Output count: 8
- Hardware input count: 8

| Physical source | Default label | Selector |
| --- | --- | --- |
| 1 | A1 | `05` |
| 2 | A2 | `06` |
| 3 | A3 | `07` |
| 4 | A4 | `03` |
| 5 | COAX1 | `00` |
| 6 | COAX2 | `01` |
| 7 | OPT1 | `02` |
| 8 | OPT2 | `04` |

In selector order:

| Selector | Default label |
| --- | --- |
| `00` | COAX1 |
| `01` | COAX2 |
| `02` | OPT1 |
| `03` | A4 |
| `04` | OPT2 |
| `05` | A1 |
| `06` | A2 |
| `07` | A3 |

Runtime source-name rows can report user-assigned labels for these same
selectors.

### MA6 Hardware Sources

- Model byte: `E9`
- Output count: 8
- Hardware input count: 19

| Source kind | Default label | Selector |
| --- | --- | --- |
| Local | Player_A | `05` |
| Local | Player_B | `06` |
| Local | Player_C | `07` |
| Local | Analog 1 | `03` |
| Local | Analog 2 | `00` |
| Local | Analog 3 | `01` |
| Local | Analog 4 | `02` |
| Local | Coaxial 1 | `04` |
| Local | Coaxial 2 | `08` |
| Local | Optical 1 | `09` |
| Local | Optical 2 | `0A` |
| Casting | Casting_1 | `0B` |
| Casting | Casting_2 | `0C` |
| Casting | Casting_3 | `0D` |
| Casting | Casting_4 | `0E` |
| Casting | Casting_5 | `0F` |
| Casting | Casting_6 | `50` |
| Casting | Casting_7 | `51` |
| Casting | Casting_8 | `52` |

Runtime source-name rows can report user-assigned labels for these same
selectors.

### Remote Sources

Remote and eAudioCast selectors use the higher selector range. The broad driver
convention is `20`-`3F`; the observed M6250 table accepted `20`-`27`:

| Selector | Observed label |
| --- | --- |
| `20` | W1 |
| `21` | Player_A@ACE14F006012 |
| `22` | W2 |
| `23` | W3 |
| `24` | W4 |
| `25` | Passthrough In |
| `26` | Player_C@ACE14F006012 |
| `27` | Player_B@ACE14F006012 |

Remote selectors are runtime configuration, not hardware-owned local source
order.

## Observed MA6/M6250 Stack

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

Observed source-name query behavior:

| Output query | Device/source table | Rows | Notes |
| --- | --- | --- | --- |
| `2901`-`2908` | M6250 | 16 | 8 local selectors `00`-`07`, plus remote selectors `20`-`27`. |
| `2921`-`2928` | M6250 aliases | 16 | Alias back to outputs `01`-`08`. |
| `29FE` | M6250 alias | 16 | Alias to output `01`. |
| `2909`-`2910` hex | MA6 | 19 | 11 local selectors plus 8 casting selectors. |
| `29FF` | Broadcast/fanout | varies | `.201` returned the MA6 table repeated for output bytes `09` through `10` hex, for `152` rows. |

Focused output-name probing against `.201` also showed outputs `0D`-`10`
returning empty names (`1C0D`, `1C0E`, `1C0F`, `1C10`).
