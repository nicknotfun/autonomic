# Autonomic Amplifier Binary Protocol

This summarizes the binary amplifier protocol extracted from the external
drivers and the Savant `eAudioAmp` profile. The protocol is binary at the
payload level, but every driver observed sends it as ASCII hexadecimal over
TCP.

## Evidence

- Control4 `AutonomicM401e32DistributedZones` Lua driver.
- RTI `eSeriesAmplifier`, `M400Amplifier`, `M401Amplifier`, `M800Amplifier`,
  and `MirageAudioSystem` JavaScript drivers.
- Crestron `Autonomic Controls Mirage Amp Comms Processor 3.4` module.
- Savant `autonomic controls_eaudioamp_1.6.xml` profile.

The URC modules identify the same integration family, but their compiled
artifacts did not expose comparable protocol handlers in the extracted files.

## Transport And Framing

Amplifier/MRAD traffic uses TCP port `17037`.

Frames are ASCII hex bytes followed by a line terminator:

```text
<OP><ZONE>[DATA...]<LF or CRLF>
```

Examples:

```text
010101\r\n  # power on zone 1, CRLF used by RTI/Control4/Crestron
010101\n    # same frame, LF used by Savant
0401\n      # query zone 1 volume
```

The binary interpretation of `010101` is:

```text
01 01 01
OP ZN DATA
```

Most writes are followed by a query for confirmation. For example, power-on
may be sent as `01 01 01`, followed by `01 01`, and volume set may be sent as
`04 01 50`, followed by `04 01`.

## Frame Grammar

```text
request  := opcode zone [payload]
response := opcode zone [payload]
opcode   := one byte
zone     := one byte
payload  := opcode-specific bytes
```

If the payload is omitted, the frame is normally a query.

`FF` is used as a broadcast/all-zones address for some operations.

## Zone Encoding

The readable drivers use a segmented zone byte encoding:

| Logical zone | Encoded byte |
| --- | --- |
| 1-31 | `01`-`1F` |
| 32-63 | `80` + `(zone - 32)` |
| 64-95 | `C0` + `(zone - 64)` |
| 96 | `00` |
| all zones | `FF` |

The Savant profile only emits zones `01`-`10` directly, and appears to contain
copy/paste defects for zones 17-24, described below.

## Source Encoding

Physical amplifier sources are not encoded in display order. RTI, Control4,
and Crestron agree on this mapping:

| Physical source | Protocol byte |
| --- | --- |
| 1 | `05` |
| 2 | `06` |
| 3 | `07` |
| 4 | `03` |
| 5 | `00` |
| 6 | `01` |
| 7 | `02` |
| 8 | `04` |
| 9 | `08` |
| 10 | `09` |
| 11 | `0A` |
| 12 | `0B` |

The inverse mapping is therefore:

| Protocol byte | Physical source |
| --- | --- |
| `00` | 5 |
| `01` | 6 |
| `02` | 7 |
| `03` | 4 |
| `04` | 8 |
| `05` | 1 |
| `06` | 2 |
| `07` | 3 |
| `08` | 9 |
| `09` | 10 |
| `0A` | 11 |
| `0B` | 12 |

Remote/eAudio sources use higher source bytes. The RTI and Control4 drivers
treat `20`-`3F` as remote/eAudioCast source slots. The Savant `eAudioAmp`
profile maps `RS1`-`RS16` to `20`-`2F`, providing explicit examples:

```text
03 01 20  # zone 1 select remote source 1
03 01 2F  # zone 1 select remote source 16
03 10 20  # zone 16 select remote source 1
```

Source feedback handlers mask the source byte with `7F`. This suggests the
high bit may carry a status flag, commonly interpreted by the drivers as a
power/status indication alongside the selected source.

## Common Value Encodings

### Volume

Volume is a byte in the range `00`-`A0` decimal 0-160.

Drivers commonly expose this as a percentage:

```text
percent = floor(value / 160 * 100)
value   = floor(percent * 160 / 100)
```

Examples:

```text
04 01     # query zone 1 volume
04 01 00  # set zone 1 volume to 0
04 01 50  # set zone 1 volume to 80, about 50 percent
04 01 A0  # set zone 1 volume to 160, 100 percent
```

The Savant XML contains volume state variables with max `160`, but some action
notes say `0~50`. The command examples and other drivers support the 0-160
interpretation.

### Signed Tone/Gain Values

Bass, treble, and source gain use signed byte-style values. Positive values
are direct hex. Negative values are represented as `256 + value`.

For bass and treble, observed range is `-12` to `+12`:

| Value | Byte |
| --- | --- |
| -12 | `F4` |
| -1 | `FF` |
| 0 | `00` |
| 12 | `0C` |

Balance uses the same encoding with an observed range of `-20` to `+20`:

| Value | Byte |
| --- | --- |
| -20 | `EC` |
| -1 | `FF` |
| 0 | `00` |
| 20 | `14` |

## Opcode Summary

| Opcode | Operation | Payload | Notes |
| --- | --- | --- | --- |
| `01` | Power | omitted=query, `00` off, `01` on, `04` toggle | Savant, RTI, Control4, Crestron. |
| `02` | Mute | omitted=query, `00` mute on, `01` mute off, `02` toggle | Savant exposes on/off; RTI/Crestron also expose toggle. |
| `03` | Source select/status | one source byte | Physical source mapping above; remote sources use `20`-`3F`. |
| `04` | Volume | omitted=query, one volume byte | 0-160 byte scale. |
| `05` | Bass | omitted=query, one signed byte | Savant, RTI, Control4, Crestron. |
| `06` | Treble | omitted=query, one signed byte | Savant, RTI, Control4, Crestron. |
| `07` | Balance | omitted=query, one signed byte | Savant, RTI, Control4, Crestron. |
| `0C` | Loudness | omitted=query, `00` off, `01` on | Present in RTI/Control4/Crestron. |
| `0D` | Max volume | omitted=query, one volume byte | 0-160 byte scale. |
| `11` | Volume up | usually no payload | Savant sends an extra `00` payload byte. |
| `12` | Volume down | usually no payload | Savant sends an extra `00` payload byte. |
| `14` | Device ping/query | observed subvalue `06` | Used by Control4 for device/zone discovery checks. |
| `1C` | Zone name | UTF-8 hex name in response | RX/status handling observed. |
| `29` | Source name | source byte plus name data | RTI parses source short/long names; Crestron queries source names. |
| `30` | Zone group | flags plus member zone bytes | Flags: bit 0 source-link, bit 1 volume-link, bit 2 standby/power-link. |
| `31` | Delay | one byte in 5 ms units | Control4 exposes up to 600 ms. |
| `32` | Input/source gain | source byte plus gain byte | Control4 writes per-source gain and queries with `32 FF`. |
| `38` | Unknown query | unknown | Queried by RTI/Crestron; no decoded handler found. |
| `39` | Amp GUID/info request | amp id/subquery | Control4 sends forms such as `39 FF <ampId>`. |
| `3A` | Network/device identity | subtype and data | Subtypes observed: `03` network info, `05` GUID, `06` system id, `85` GUID repair/write action. |
| `3D` | Media control bridge | opaque payload | Control4 forwards payload to MMS MCP. |
| `44` | Gain | one signed byte | Same signed encoding family as tone controls. |
| `46` | Source metadata set/status | source, slot, UTF-8 hex text | Slots `00`-`03` observed. |
| `47` | Source metadata request | source, slot | Requests metadata slot for source. |
| `4B` | Keypad event bridge | opaque payload | Control4 forwards payload to MMS MCP. |
| `4E` | Preset/group map bridge | opaque payload | Control4 forwards payload to MMS MCP. |
| `4F` | Remote source definition | slot, GUID, player/source, optional name | Also used for remote-source delete/list style operations. |
| `94` | Amp identity/model/zones | model and zone inventory payload | Control4 comments include decoded model/zone inventory examples. |
| `AF` | Amp id discovery | amp id related payload | Used by Control4 discovery/minimal object creation paths. |
| `B9` | Extended device info | device info/MAC payload | Control4 extracts MAC/identity details. |

## Operation Examples

Power:

```text
01 01     # query zone 1 power
01 01 01  # zone 1 power on
01 01 00  # zone 1 power off
01 01 04  # zone 1 power toggle
01 FF 00  # all zones power off
```

Mute:

```text
02 01     # query zone 1 mute
02 01 00  # zone 1 mute on
02 01 01  # zone 1 mute off
02 01 02  # zone 1 mute toggle
```

Volume:

```text
04 01     # query zone 1 volume
04 01 50  # set zone 1 volume to 80/160
11 01     # zone 1 volume up
12 01     # zone 1 volume down
```

Source selection:

```text
03 01 05  # zone 1 select physical source 1
03 01 00  # zone 1 select physical source 5
03 01 20  # zone 1 select remote/eAudio source 1
03 01 2F  # zone 1 select remote/eAudio source 16
```

Tone:

```text
05 01 F4  # zone 1 bass -12
05 01 00  # zone 1 bass 0
05 01 0C  # zone 1 bass +12
06 01 FF  # zone 1 treble -1
07 01 EC  # zone 1 balance -20
07 01 14  # zone 1 balance +20
```

Delay:

```text
31 01     # query zone 1 delay
31 01 14  # set zone 1 delay to 100 ms, because 0x14 * 5 ms = 100 ms
```

Source metadata:

```text
46 FF 20 00 <utf8-hex-text>  # set/status metadata slot 0 for remote source 0x20
47 FF 20 00                  # request metadata slot 0 for remote source 0x20
```

Remote source definition:

```text
4F FF <slot> <16-byte-guid> <player-or-source-byte> [utf8-hex-name]
4F FF <slot> 00  # delete/clear remote source slot, as seen in Control4 handling
```

## Savant eAudioAmp Profile Notes

The Savant profile is a useful protocol example because it is declarative and
plain XML. It defines:

- TCP port `17037`.
- LF send postfix `0A`.
- 24 logical amplifier outputs.
- Command strings for power, mute, source selection, volume, bass, treble,
  balance, and volume ramping.

Confirmed Savant command examples include:

```text
010101  # zone 1 power on
010100  # zone 1 power off
010104  # zone 1 power toggle
020100  # zone 1 mute on
020101  # zone 1 mute off
0401    # zone 1 volume query or set-volume preamble
110100  # zone 1 volume up, with extra 00 payload
120100  # zone 1 volume down, with extra 00 payload
030120  # zone 1 RS1 select, remote source byte 20
03012F  # zone 1 RS16 select, remote source byte 2F
```

The profile has several likely defects or profile-specific quirks:

- Zones 17-24 mostly duplicate zone 15 command strings even though their
  logical component names and variables say Zone17 through Zone24.
- `RS17`-`RS24` selections are hardcoded to `03012F` in many places instead
  of using distinct source or zone bytes.
- Bass/treble/balance set commands use odd-length Savant command templates
  such as `05010` plus a hex parameter. The intended protocol operation is
  still `05 <zone> <value>`, `06 <zone> <value>`, and `07 <zone> <value>`.
- Volume state variables use max `160`, while some inline notes say `0~50`.
- Volume up/down include a trailing `00`; other drivers use `11 <zone>` and
  `12 <zone>` without an explicit value byte.

## Unknowns And Partial Decodes

The core zone-control protocol is consistent across integrations. The remaining
uncertainty is mostly in discovery, identity, and MMS bridge opcodes:

- `38` is queried but not decoded in the readable drivers.
- `14`, `39`, `3A`, `94`, `AF`, and `B9` have partial discovery/identity
  decodes, but not a complete public schema.
- `3D`, `4B`, and `4E` are bridge payloads forwarded by Control4 to MMS/MCP
  logic; their payloads should be treated as opaque until the MMS side is
  decoded further.
