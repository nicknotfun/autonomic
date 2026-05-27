<!-- Wire-format reference for the implemented Autonomic control protocols. -->

# Autonomic Wire Protocols

This file documents the wire formats implemented by the SDK. Playback, queue,
and MMS/media-server commands are intentionally out of scope.

## MRAD/MAS TCP Protocol

- Port: `5006`
- Encoding: UTF-8 text
- Command terminator: CRLF (`\r\n`)
- Response terminator: CRLF/LF, or NUL after `SetResponseEolZero`
- Shape: `Command [arg ...]\r\n`
- Arguments with spaces are quoted. Booleans are command-family strings such as
  `True`/`False`, `On`/`Off`, or `Toggle`.
- `GetStatus` returns event bursts:
  `ReportState <source> <name>=<value>` or
  `StateChanged <source> <name>=<value>`.
- `SetXmlMode Lists` makes browse commands return one-line XML lists.

Live `GetStatus` zone rows have been observed with these typed fields:
`PowerOn`, `Mute`, `Volume`, `MinVolume`, `MinMinVolume`, `MaxVolume`,
`MaxMaxVolume`, `Bass`, `Treble`, `Balance`, `ZoneGain`, `LoudnessEnabled`,
`MonoDownmix`, `PowerOnVolume`, `AdjustingVolume`, `DeviceType`,
`DoNotDisturb`, `GainMode`, `IconId`, `PartyMode`, `SourceId`, `SourceName`,
`QualifiedSourceName`, `ZoneExclusiveSource`, `ZoneGroupId`, `ZoneGroupName`,
`ZoneGroupPower`, `ZoneGroupSource`, `ZoneGroupVolume`, `ZoneGuid`, `ZoneId`,
`ZoneIsLocked`, and `ZoneName`. `AutonomicClient` normalizes MRAD volume-range
fields to `0..100%` while preserving raw values in attributes such as
`raw_volume` and `raw_max_max_volume`.

### MRAD Session And Diagnostics

| Command | Wire form | Notes |
| --- | --- | --- |
| Command mode | `!Autonomic` | Enter abstract MRAD command mode. |
| Passthrough toggle | `*Autonomic` or `*` | Toggle passthrough/command mode. SDK initialization sends `*`. |
| Passthrough enter | `@Autonomic` | Enter passthrough mode. |
| Help | `help [command]` | Multi-line text command catalog. |
| Banner | `Banner` | Multi-line connection banner. |
| Client type | `SetClientType <text>` | Session metadata. |
| Client version | `SetClientVersion <text>` | Session metadata. |
| Encoding | `SetEncoding <codepage>` | Usually `65001`. |
| Host hint | `SetHost <host-or-ip>` | Session host hint. |
| XML mode | `SetXmlMode Lists\|None` | Controls browse response format. |
| NUL EOL | `SetResponseEolZero` | Subsequent responses are NUL-delimited. |
| Subscribe | `SubscribeEvents <Smart\|True\|False\|csv>` | Event subscriptions. |
| Versions | `GetVersions` | Firmware/component versions. |
| Status | `GetStatus` | Event burst for system, zones, sources. |
| Ping | `Ping` | Responds with `Pong`. |
| Echo | `Echo <text>` | Encoding check. |
| Uptime | `Uptime` | Daemon uptime. |
| Time | `Time [format]` | Device time echo. |
| Sync time | `SyncTime` | Sync bridge time to client. |
| Log comment | `LogComment <text>` | Write a log comment. |
| Clear terminal | `cls` | ANSI terminal clear. |
| Close | `Exit` | Close connection. |

### MRAD Browse

| Command | Wire form | Result |
| --- | --- | --- |
| Active zones | `BrowseZones [start count]` | XML `Zones`. |
| All zones | `BrowseAllZones [start count]` | XML `Zones`. |
| Active-zone sources | `BrowseSources [start count]` | XML `Sources`. |
| All sources | `BrowseAllSources [start count]` | XML `Sources`. |
| Zone groups | `BrowseZoneGroups [start count]` | XML `ZoneGroups`. |
| Zone group | `BrowseZoneGroup [zone-or-group]` | XML `ZoneGroups`. |
| Zones for group | `BrowseZonesForGroup <group>` | XML `Zones`. |
| Party include | `BrowsePartyModeInclude` | XML `PartyModeInclude`. |
| Page up/down | `BrowsePageUp`, `BrowsePageDown` | Re-page previous browse result. |

### MRAD Zone, Source, And Group Control

| Command | Wire form | Notes |
| --- | --- | --- |
| Select zone | `SetZone <zone-id-guid-name>` | Sets active zone. |
| Select source | `SetSource <source-id-guid-name>` | Uses active zone. |
| Target source | `SetSource <source> <include-group-bool> <zone>` | SDK defaults include-group to `False`. |
| Source by name | `SetSourceByName <engine-name>` | Active source by friendly engine name. |
| Source for group | `SetSourceForGroup <source>` | Applies source to active zone group. |
| Source name | `SourceName <name> [source]` | Active source if source omitted. |
| Source icon | `SourceIcon <icon> [source]` | Active source if source omitted. |
| Zone name | `ZoneName <name> <zone>` | Rename zone. |
| Zone icon | `ZoneIcon <icon> <zone>` | Set zone icon. |
| Identify zone | `IdentifyZone <zone>` | Test tone. |
| Party mode | `PartyMode <On\|Off\|Toggle> [zone]` | Host/zone party mode. |
| Party include | `SetPartyModeInclude <True\|False\|Toggle> [zone-or-group]` | Include/exclude. |
| Zone group | `SetZoneGroup <zone-or-group> <comma-zone-list> [source]` | Group/ungroup. |
| Group timer | `SetZoneGroupTimer <time> [zone-or-group]` | Timed off. |

### MRAD Output Controls

All active-zone forms use the selected zone from `SetZone`; targeted forms add
the zone as the final argument. The SDK powers the selected/target zone on
before source, volume, tone, max-volume, loudness, mono, and power-on-volume
commands, then retries once on a zone-off error.

| Control | Active form | Targeted form | Range/state |
| --- | --- | --- | --- |
| Power | N/A | `Power <On\|Off\|Toggle> <zone>` | Runtime `PowerOn`. |
| Volume | `Volume <n>` | `Volume <n> <zone>` | Device scale, normalized by `AutonomicClient`. |
| Volume up | `VolumeUp` | `VolumeUp <zone>` | Relative. |
| Volume down | `VolumeDown` | `VolumeDown <zone>` | Relative. |
| Mute | `Mute <state>` | `Mute <state> <zone>` | `true`, `false`, `toggle`. |
| Mute all | `MuteAll <On\|Off\|Toggle>` | N/A | All zones. |
| All off | `AllOff` | N/A | Native all-zone power off. |
| Bass | `Bass <n>` | `Bass <n> <zone>` | `-12..12`. |
| Bass up/down | `BassUp`, `BassDown` | `BassUp <zone>`, `BassDown <zone>` | Relative. |
| Treble | `Treble <n>` | `Treble <n> <zone>` | `-12..12`. |
| Treble up/down | `TrebleUp`, `TrebleDown` | `TrebleUp <zone>`, `TrebleDown <zone>` | Relative. |
| Balance | `Balance <n>` | `Balance <n> <zone>` | `-20..20`. |
| Balance left/right | `BalanceLeft`, `BalanceRight` | `BalanceLeft <zone>`, `BalanceRight <zone>` | Relative. |
| Zone gain | `ZoneGain <n>` | `ZoneGain <n> <zone>` | `-12..12`. |
| Zone gain up/down | `ZoneGainUp`, `ZoneGainDown` | `ZoneGainUp <zone>`, `ZoneGainDown <zone>` | Relative. |
| Max volume | `MaxVolume <n>` | `MaxVolume <n> <zone>` | `0..100` command value. |
| Loudness | `Loudness <state>` | `Loudness <state> <zone>` | `True`, `False`, `Toggle`. |
| Mono downmix | `MonoDownmix <state>` | `MonoDownmix <state> <zone>` | `True`, `False`, `Toggle`. |
| Power-on volume | `PowerOnVolume <n>` | `PowerOnVolume <n> <zone>` | `0..100`, `0` disables. |
| Read volume | `GetVolume [zone]` | Same | Simple response. |
| Read mute | `GetMute [zone]` | Same | Simple response. |

Playback/transport commands advertised by firmware (`Play`, `Pause`,
`PlayPause`, `Stop`, `SkipNext`, `SkipPrevious`, `Seek`, `SeekRelative`,
`Shuffle`, `Repeat`, `RecallPreset`, `StorePreset`) are deliberately not part
of this SDK surface.

## Direct Amplifier TCP/HTTP Protocol

- TCP port: `17037`
- Optional HTTP poll endpoint: `POST /poll.cgi?id=<client-id>` with the same
  CRLF command body
- Encoding: ASCII hex
- Terminator: CRLF (`\r\n`)
- Standard command shape: `<opcode><output><payload...>\r\n`
- Bytes are two uppercase hex characters. `FF` targets all outputs for many
  commands.
- Responses use the same hex-row shape. The parser ignores blank/non-hex noise.

### Direct Output Addressing

| Address | Meaning |
| --- | --- |
| `01`..`1F` | Output 1..31 |
| `09`..`10` | Observed MA6 outputs 9..16 when controlled through the stack |
| `FF` | All outputs, or broadcast query depending on opcode |

### Direct Output And Source Opcodes

| Opcode | Command | Payload | Response/parser |
| --- | --- | --- | --- |
| `01` | Runtime power | `00` off, `01` on, `04` toggle | `PowerOn` bool |
| `02` | Mute | `00` mute, `01` unmute, `02` toggle | `Mute` bool |
| `03` | Source select/read | Source selector byte | `sourceId`, `sourceName`, extra source-status bytes |
| `04` | Volume | Raw `00..A0`; API `0..100%` | `Volume`, `rawVolume` |
| `05` | Bass | Signed range `-12..12` | `Bass` |
| `06` | Treble | Signed range `-12..12` | `Treble` |
| `07` | Balance | Signed range `-20..20` | `Balance` |
| `09` | All parameters | Usually `00` | Device readback burst |
| `0C` | Loudness | `00` off, `01` on | `Loudness` |
| `0D` | Max volume | Raw `00..A0`; API `0..100%` | `MaxVolume`, `rawMaxVolume`; extra readback bytes preserved |
| `11` | Volume up | No payload | Relative; observed response is `04<out><volume>` |
| `12` | Volume down | No payload | Relative; observed response is `04<out><volume>` |
| `14` | Device/ping info | Observed `06` | Device/model info rows |
| `1C` | Output name readback/write | UTF-8 name bytes | UTF-8 output name; empty readback means default `Zone N` |
| `29` | Source names | Query: `29<out>` or `29<out><source>`; write below | Source display/hidden names |
| `30` | Zone groups | Observed query `30FF20` | Flags + member zones |
| `31` | Delay | `0..120` ticks, 5 ms each | `DelayMs`; multi-byte readback is per-source delay table |
| `32` | Input gain | `<source><raw-gain>` | Per-output/source input-gain table |
| `38` | Output name refresh | Query `38FF` | Causes `1C<out><name>` rows |
| `44` | Zone gain | Signed range `-12..12` | `Gain` |
| `46` | Source metadata write/readback | `<source><position><utf8-hex>` | Metadata field value; output may be `FF` or a real zone |
| `47` | Source metadata query | `<source><position>` | Causes matching `46` response |
| `4E` | Preset-group map/slot query | `4EFF<slot16>` | Read-only group map/slot state |
| `4F` | Remote source slot | Define/delete/read slots | Remote source table |
| `4A` | Device state row | `4AFF<amp-id>` | Observed raw stack-local payload |
| `4D`/`CD` | Device link rows | `<opcode>FF<amp-id>` | Observed stack peer rows |
| `2FFF` | Device ID special | None | `AFFF<device-id>...` |
| `39` | Extended device info | `39FF<amp-id>` | MAC/zones/model bytes |
| `3A` | Device sub-info | `3AFF<amp-id><subop>` | Network/system/GUID/status |

Direct zone-group rows are queried with `30FF20`. The first payload byte is a
flag mask: `01` source-linked, `02` volume/mute-linked, and `04` power-linked.
Remaining bytes are member output addresses. The unified client exposes these
as `AutonomicZoneGroup` objects in direct-amplifier mode; the extracted
Control4 driver uses the same table to decide whether direct source, volume,
mute, and power commands should fan out locally.

Observed `3A` sub-ops:

| Sub-op | Meaning |
| --- | --- |
| `05` | Device GUID readback payload |
| `07` | Raw status/info payload; observed response to query `87` |
| `83` | Network: DHCP/OvrC/IP/subnet/DNS/gateway |
| `85` | Device GUID query; response uses `05` |
| `86` | System ID |
| `87` | Raw status/info query; response uses `07` |

Autonomic's Control4 amp utility can self-repair stale GUIDs by writing `05`,
but this library intentionally treats device GUIDs as read-only identity. It
parses `05` readbacks and can query them with `85`; it does not expose a GUID
rewrite API.

Observed `4A`, `4D`, and `CD` rows are stack-local and not decoded further by
the vendor modules. On the probed stack, `4AFF00D4` returned
`4AFF00D4FFFFFFFFFFFF`, `4DFF6012` returned `CDFF00D400`, and `CDFF00D4`
returned `4DFF6012`.

### Direct Source Selector Map

The selector byte is not linear for local inputs.

| Logical source | Selector |
| --- | --- |
| `0` / S1 | `05` |
| `1` / S2 | `06` |
| `2` / S3 | `07` |
| `3` / S4 | `03` |
| `4` / S5 | `00` |
| `5` / S6 | `01` |
| `6` / S7 | `02` |
| `7` / S8 | `04` |
| `8`..`11` | `08`..`0B` |
| Remote slot 0..31 | `20`..`3F` |

`source_base=0` uses zero-based direct IDs. `source_base=1` presents S1 as
source `1`. Hardware labels are model-specific: observed M-6250 `00D4`
labels `A1`, `A2`, `A3`, `A4`, `COAX1`, `COAX2`, `OPT1`, `OPT2`; observed
MA6 `6012` labels `Player_A`, `Player_B`, `Player_C`, `Analog 1`,
`Analog 2`, `Analog 3`, `Analog 4`, `Coaxial 1`, `Coaxial 2`, `Optical 1`,
`Optical 2`, `Casting_1`.

### Direct Source Name, Metadata, And Remote Slot Payloads

Source-name query/write:

```text
29<output-or-FF>
29<output><source-selector>
29<output><source-selector>000001<utf8-name-hex>
```

The two-byte query refreshes source names in bulk. The three-byte query is the
Crestron source-details form for one source on one output; using `FF` as the
output with a source selector fans that one source out across stack outputs. On
the probed stack, `290105` returned the M-6250 output-1 source row and `290905`
returned the MA6 output-9 source row; `29FF05` returned rows for every output
where that selector was known. The Python direct client combines the single
`29<output><source>` query with four `47<output><source><position>` metadata
queries in `refresh_source_details()`.

Source metadata write/query:

```text
46<output-or-FF><source-selector><position><utf8-value-hex>
47<output-or-FF><source-selector><position>
```

Vendor modules write metadata with `FF`, and the Crestron module also queries
zone-scoped metadata with a real output byte. Responses are `46` rows. On the
probed M-6250 and MA6, both `47FF...` and `47<output>...` produced `46FF...`
readbacks for empty fields, so `FF` should be treated as unscoped readback and
not proof that the original query was broadcast. A non-`FF` `46<output>...`
response identifies the zone/output that supplied the metadata. Metadata
positions `00..03` are queried as a set for one source, or in bulk across the
configured local source range.

Source delay readback:

```text
31<output><delay0><delay1>...
```

Single-byte `31` rows are a zone default delay. Multi-byte rows are per-source
delays in logical-source order; each byte is 5 ms.

Max-volume up/down helpers are synthesized by reading current `0D`/`MaxVolume`
state, clamping to `0..100%`, and writing the new value with the same set
command; there is no separate observed relative opcode.

Direct bass, treble, balance, gain, and delay relative helpers are likewise
synthesized from a narrow read of `05`/`06`/`07`/`44`/`31`, clamping to the
documented range, then writing the adjusted value. Delay relative steps are in
5 ms increments.

Remote source define/delete:

```text
4FFF<slot><16-byte-guid><source-position><utf8-name-hex>
4FFF<slot>00
```

Remote source response payload:

| Byte range | Meaning |
| --- | --- |
| `0` | Remote slot |
| `1..16` | Backing device/source UUID |
| `17` | Source/player index |
| `18+` | UTF-8 source name |

Static JSON config stores remote slots per target amplifier. `target_device_id`
is the amp that owns the remote slot and therefore prefixes the synthetic source
id (`6012:32`), while `source_device_id` optionally records the amp that
provides the backing source. This avoids collisions because every amplifier can
have a slot `32`.

Preset-group query:

```text
4EFF0000      # map
4EFF<slot16>  # slots 0001..0064
```

Response payloads begin with a 16-bit slot id. High bit set means unavailable
or empty (`8002` for slot 2). Available slot rows skip the next two bytes before
the preset payload. The preset payload is `id/name/masks`: byte 0 high bit is
read-only and low 7 bits are the preset id; byte 1 is UTF-8 name length; name
bytes follow; remaining bytes are reversed 8-zone member masks.

The slot-0 map row is `0000<signature><bitmap...>`. Observed firmware uses
ASCII signature `MP`; each bitmap bit marks an available slot, least-significant
bit first, so bitmap byte `01` means slot 1.

### Direct Device Discovery

Device ID:

```text
2FFF -> AFFF<device-id>...
```

Stack/model discovery:

```text
14FF06
39FF<amp-id>
3AFF<amp-id>83
3AFF<amp-id>85
3AFF<amp-id>86
```

The direct amplifier client caches discovered device rows and inferred physical
layout for its lifetime. For unconfigured devices it infers the output span from
reported zone addresses, and source count/base from the primary device's model
byte in `hardware.py`. Configured direct stacks can omit output/source counts;
the JSON loader derives them from `model_byte` and assigns omitted output starts
sequentially across the listed devices.

Known models in `hardware.py`:

| Model byte | Model | Outputs | Sources | Source base | Notes |
| --- | --- | --- | --- | --- | --- |
| `87` | M400 | 4 | 6 | 0 | Control4 `M400_TYPE_ID`; four-zone M400/M401e model family |
| `88` | M800 | 8 | 8 | 0 | Control4 `M800_TYPE_ID`; eight-zone M800/M801e model family |
| `8D` | M401e | 4 | 6 | 0 | Control4 `M401E_TYPE_ID` |
| `8E` | M801e | 8 | 12 | 0 | Control4 `M801E_TYPE_ID` |
| `93`/`98` | M120e | 4 | 2 | 0 | Control4 marks M120e unstackable |
| `9B` | M250e | unknown | 3 | 0 | Source count from Control4 utility |
| `B0` | M-6250 | 8 | 8 | 0 | Observed device `00D4`, host `10.1.0.200`, MRAD volume max 80 |
| `E1`/`E3`/`E5` | MMS eSeries | 0 | 0 | 0 | Tracked only for stack discovery; MMS control is not exposed |
| `E2` | MSB-20e | 1 | 5 | 0 | Integrated-MMS model; physical-source offset 1 |
| `E9` | MA6 | 8 | 12 | 0 | Observed device `6012`, host `10.1.0.201`, outputs 9-16 in stack; extra casting labels from the Control4 module are tracked as observed, non-listed selectors |

Live probing showed a direct connection to `.200` can address `.201` outputs
9-16, while MRAD on `.201` did not control `.200` Zone 1 in the tested setup.

### Direct Reset Defaults

`reset_all_to_defaults()` sends a reversible test-cleanup batch:

```text
02FF00        # safety mute
0DFF<max>     # max volume
04FF<vol>     # volume
03FF<src>     # source
05FF<bass>
06FF<treble>
07FF<balance>
44FF<gain>
31FF<delay>
0CFF<loudness>
1C<out><name>  # default Zone N names, one command per configured output
32FF<src><gain> for each local source
4FFF<slot>00  # optional remote-slot clear
02FF<state>
01FF<state>
```

The high-level `AutonomicClient.reset_all_to_defaults()` also attempts an
initialized MRAD fallback when available so MRAD-only zone settings such as
mono downmix and power-on volume are returned to their defaults.
