# Autonomic Python SDK

Dependency-free Python SDK and protocol notes for Autonomic Mirage Media Server,
Mirage Audio System/MRAD, MA6-style systems, and direct Mirage amplifier control
such as standalone M-6250 devices.

This README is intentionally self-contained. It documents the protocol behavior
implemented by this library and observed against real devices.

## Supported Device Paths

The SDK supports two main control paths.

`mrad` mode:

- Media server control over TCP port `5004`.
- MRAD/MAS zone and source control over TCP port `5006`.
- Modern single-socket mode where MRAD commands are proxied over port `5004`
  by prefixing commands with `MRAD.`.

`amplifier` mode:

- Direct amplifier control over TCP port `17037`.
- Used by standalone matrix amplifiers where `5004` and `5006` are not open.
- The SDK exposes synthetic output/source browse lists and sends direct hex
  amplifier commands for source routing, volume, mute, and output enable.

`MA6Client` auto-detects the backend:

- If port `5006` is open, it uses MRAD/MAS mode.
- Otherwise, if port `17037` is open, it uses direct amplifier mode.
- Media transport controls such as play/pause are only available in MRAD/MMS
  mode.

## Installation

```bash
python -m pip install -e .
```

The library has no runtime dependencies.

## Protocol Fundamentals

### Line Framing

MMS and MRAD sockets use a line-oriented TCP protocol.

- Encoding: UTF-8 is recommended.
- Command terminator: CRLF, `\r\n`.
- Response terminator: CRLF or LF.
- Commands are ASCII command names followed by space-separated arguments.
- Arguments with spaces must be quoted.
- Boolean arguments are usually accepted as textual values such as `true`,
  `false`, `On`, `Off`, or `Toggle`, depending on the command family.

Examples:

```text
SetXmlMode Lists\r\n
BrowseAlbums 1 25\r\n
SetSource 000027fb-f8a9-f6be-a465-3d0fbee12977 false Zone_1\r\n
```

The client is deliberately tolerant when reading responses:

- Blank lines are ignored.
- Trailing whitespace is stripped.
- MRAD banner lines are ignored when they appear before the real response.
- Command errors are detected case-insensitively, including `Error:`,
  `error:`, and mixed-case variants.

### Command Responses

Simple commands generally return one line:

```text
ClientType Ok
Encoding 65001
XmlMode Ok
SubscribeEvents Off
Zone "Kitchen"
Source "COAX2"
```

Errors generally return:

```text
Error: Unknown command 'Foo'.
Instance Error "Player_A is not a valid instance, see available instances with BrowseInstances"
```

The SDK raises `CommandError` for error lines it can identify.

### Event Responses

MMS and MRAD can send asynchronous event lines.

Format:

```text
ReportState <EventSource> <EventName>=<EventValue>
StateChanged <EventSource> <EventName>=<EventValue>
```

Single-socket MRAD events are prefixed:

```text
MRAD.ReportState Zone_1 Volume=60
MRAD.StateChanged Zone_1 Mute=False
```

Common MRAD status events:

```text
ReportState Amps SourceCount=27
ReportState Amps ZoneCount=16
ReportState Amps AmpCount=2
ReportState Amps DeviceReady=True
ReportState Amps AllMute=False
ReportState Amps AllOff=False
ReportState Amps ActiveZone=Zone_9
ReportState Amps ActiveSource=Source_10210
ReportState Zone_1 Mute=False
ReportState Zone_1 PowerOn=True
ReportState Zone_1 Volume=60
ReportState Zone_1 SourceId=10107
ReportState Zone_1 ZoneName=Kitchen
```

`GetStatus` returns a burst of events rather than a single XML document. The SDK
collects those events until the socket is idle and returns a `StatusSnapshot`.

### XML Browse Responses

List commands should use XML mode:

```text
SetXmlMode Lists
```

Browse responses are one-line XML documents.

Example zones response:

```xml
<Zones total="16" start="1" more="false" art="false" alpha="false" displayAs="List">
  <Zone guid="00000001-f8a9-f6be-a465-3d0fbee12977"
        name="Kitchen"
        dna="name"
        id="Zone_1"
        isOn="True"
        sourceId="10107"
        sourceName="COAX2"
        sId="10107"
        sGuid="000027fb-f8a9-f6be-a465-3d0fbee12977" />
</Zones>
```

Example sources response:

```xml
<Sources total="24" start="1" more="false" art="false" alpha="false" displayAs="List">
  <Source guid="000027f5-f8a9-f6be-a465-3d0fbee12977"
          name="A1"
          dna="name"
          smart="0"
          znCount="0"
          znList=""
          sId="10101"
          sGuid="000027f5-f8a9-f6be-a465-3d0fbee12977" />
</Sources>
```

Example zone-group response:

```xml
<ZoneGroups total="5" start="1" more="false" art="false" alpha="false" displayAs="List" srceAvail="0">
  <ZoneGroup guid="00000000-0000-277b-0000-000000000000"
             name="ZG_1"
             sId="10107"
             sGuid="000027fb-f8a9-f6be-a465-3d0fbee12977">
    <vol>
      <zone eventId="Zone_1" guid="00000001-f8a9-f6be-a465-3d0fbee12977" name="Kitchen" />
    </vol>
    <src>
      <zone eventId="Zone_1" guid="00000001-f8a9-f6be-a465-3d0fbee12977" name="Kitchen" />
    </src>
  </ZoneGroup>
</ZoneGroups>
```

The SDK parses these into `BrowseResponse` and `BrowseItem`.

## MMS Protocol: Port 5004

MMS controls media browsing, playback, queue actions, presets, and the selected
media output instance.

### Recommended Session Setup

```text
SetClientType PythonSDK
SetClientVersion 0.1.0.0
SetHost 192.168.1.50
SetXmlMode Lists
SetEncoding 65001
SetInstance <instance-name>
SubscribeEvents true
```

`SetInstance` is optional in this SDK because not all systems have a `Player_A`
instance. Discover valid instances with:

```text
BrowseInstances
```

Python:

```python
from autonomic import MirageMediaServer

with MirageMediaServer("192.168.1.50") as mms:
    mms.initialize(host_hint="192.168.1.50")
    instances = mms.browse_instances()
```

### MMS Common Commands

Session and formatting:

```text
SetClientType <type>
SetClientVersion <version>
SetHost <host-or-name>
SetXmlMode None
SetXmlMode Lists
SetEncoding 65001
SetInstance <instance>
SetOption <name>=<value>
SubscribeEvents true
SubscribeEvents false
SubscribeEvents EventA,EventB
GetStatus
```

Playback:

```text
Play
Pause
Stop
PlayPause
SkipNext
SkipPrevious
Seek <seconds>
ThumbsUp
ThumbsDown
SetStars <stars>
SendKeys <ir-key>
```

MMS output volume:

```text
SetVolume <0-50>
Mute true
Mute false
Mute toggle
```

Library and menu browsing:

```text
BrowseAlbums <start> <count>
BrowseArtists <start> <count>
BrowseComposers <start> <count>
BrowseFavorites <start> <count>
BrowseGenres <start> <count>
BrowseInstances <start> <count>
BrowseNowPlaying <start> <count>
BrowsePicklist <start> <count>
BrowsePlaylists <start> <count>
BrowseRadioSources <start> <count>
BrowseServiceAccounts <start> <count>
BrowseTitles <start> <count>
BrowseTopMenu <start> <count>
BrowseTopMenu <start> <count> itemGuid=<guid>
```

Browse commands usually accept `start` and `count`. Start is one-based.

Selection and menu interaction:

```text
AckPickItem <guid>
AckButton <guid> <button> <value>
Back <pages>
```

Filters:

```text
SetMusicFilter Clear
SetMusicFilter Artist="Peter Frampton"
SetMusicFilter Search="Diana*"
SetRadioFilter Clear
SetRadioFilter Source=000027fb-f8a9-f6be-a465-3d0fbee12977
```

Service accounts:

```text
SetServiceAccount <service> <account> <latch-to-output>
SetServiceAccount <service> Clear false
```

Presets:

```text
StorePreset <name>
RecallPreset <name-or-guid>
```

Direct play commands:

```text
PlayAlbum <guid-or-name> <enqueue>
PlayArtist <guid-or-name> <enqueue>
PlayGenre <guid-or-name> <enqueue>
PlayPlaylist <guid-or-name> <enqueue> <start-guid>
PlayTitle <guid-or-name> <enqueue>
PlayRadioStation <guid-or-name>
JumpToNowPlayingItem <guid-or-index>
RemoveNowPlayingItem <guid-or-index>
```

### MMS Single-Socket MRAD Proxy

Modern media servers can proxy MRAD over port `5004`. Enable or rely on
auto-enable:

```text
SetOption Supports_SingleSocket=True
MRAD.BrowseAllZones
MRAD.GetStatus
MRAD.SetSource 000027fb-f8a9-f6be-a465-3d0fbee12977 false Zone_1
```

Browse responses are still XML list responses and are not prefixed. MRAD events
are prefixed with `MRAD.`.

Python:

```python
from autonomic import MirageAudioSystem, MirageMediaServer

with MirageMediaServer("192.168.1.50") as mms:
    mms.initialize(host_hint="192.168.1.50", options={"Supports_SingleSocket": True})
    mas = MirageAudioSystem("192.168.1.50", mms_client=mms, single_socket=True)
    outputs = mas.list_outputs()
```

## MRAD/MAS Protocol: Port 5006

MRAD controls sources, zones, output volume, mute, power, party mode, and zone
groups. The protocol calls physical amplifier outputs "zones". The SDK exposes
both names:

- `browse_all_zones()` and `list_outputs()` are equivalent.
- `set_zone()` and `set_output()` are equivalent.

### Connection Banner

MRAD can send a banner immediately after connection:

```text
Autonomic Controls MRAD Bridge version <version> Release.
More info found on the Web http://www.autonomic-controls.com
Type '?' for help or 'help <command>' for help on <command>.
Server=<server-id>
```

The SDK ignores these banner lines when reading command responses.

### Recommended Session Setup

```text
*
SetClientType PythonSDK
SetEncoding 65001
SetXmlMode Lists
SetHost 192.168.1.50
SubscribeEvents Smart
GetStatus
BrowseAllSources
BrowseAllZones
BrowseZoneGroups
```

The `*` command enters the MRAD command mode required by some systems and is
safe on systems that do not require it.

Python:

```python
from autonomic import MirageAudioSystem

with MirageAudioSystem("192.168.1.50") as mas:
    mas.initialize(host_hint="192.168.1.50")
```

### MRAD Browse Commands

```text
BrowseZones
BrowseZones <start> <count>
BrowseAllZones
BrowseAllZones <start> <count>
BrowseSources
BrowseSources <start> <count>
BrowseAllSources
BrowseAllSources <start> <count>
BrowseZoneGroups
BrowseZoneGroups <start> <count>
BrowseZoneGroup <optional-zone-or-group>
BrowseZonesForGroup <group-guid>
BrowsePartyModeInclude
```

`BrowseSources` returns sources available to the active zone. `BrowseAllSources`
returns all sources available to any zone.

### MRAD Status

```text
GetStatus
```

Returns a burst of `ReportState` events. Important event sources:

- `Amps`: system-wide state.
- `Zone_1`, `Zone_2`, etc.: per-output state.
- `Source_<id>`: active source state.

Important zone/output event names:

- `Mute`
- `PowerOn`
- `Volume`
- `MaxVolume`
- `MinVolume`
- `SourceId`
- `ZoneGuid`
- `ZoneId`
- `ZoneName`
- `ZoneGroupId`

### MRAD Output Selection and Source Assignment

Select the active output:

```text
SetZone <zone-id-or-guid-or-name>
```

Set the active output source:

```text
SetSource <source-id-or-guid-or-name>
```

Set a source on a specified output:

```text
SetSource <source-id-or-guid-or-name> <include-group-bool> <zone-id-or-guid-or-name>
```

Examples:

```text
SetZone Zone_1
SetSource 000027fb-f8a9-f6be-a465-3d0fbee12977
SetSource 000027fb-f8a9-f6be-a465-3d0fbee12977 false Zone_1
SetSource 10107 false Zone_1
SetSource COAX2 false Zone_1
```

The middle boolean controls whether the source change should apply through the
zone group. The SDK defaults it to `false` for targeted single-output
assignments.

Python:

```python
with MirageAudioSystem("192.168.1.50") as mas:
    outputs = mas.list_outputs()
    sources = mas.list_sources()
    mas.assign_source_to_output(sources.items[0].guid, outputs.items[0].id)
    mas.assign_output_sources({
        outputs.items[0].id: sources.items[0].guid,
        outputs.items[1].id: sources.items[1].guid,
    })
```

### MRAD Output Volume, Mute, and Power

Active output:

```text
Volume <volume>
VolumeUp
VolumeDown
Mute true
Mute false
Mute toggle
Power On
Power Off
Power Toggle
```

Specified output:

```text
Volume <volume> <zone-id-or-guid-or-name>
VolumeUp <zone-id-or-guid-or-name>
VolumeDown <zone-id-or-guid-or-name>
Mute true <zone-id-or-guid-or-name>
Mute false <zone-id-or-guid-or-name>
Mute toggle <zone-id-or-guid-or-name>
Power On <zone-id-or-guid-or-name>
Power Off <zone-id-or-guid-or-name>
Power Toggle <zone-id-or-guid-or-name>
```

All outputs:

```text
MuteAll On
MuteAll Off
MuteAll Toggle
AllOff
```

Python:

```python
mas.set_output_volume("Zone_1", 60)
mas.set_output_mute("Zone_1", False)
mas.enable_output("Zone_1")
mas.disable_output("Zone_1")
mas.mute_all_outputs(True)
mas.disable_all_outputs()
```

### MRAD Zone Groups

Build a new zone group:

```text
SetZoneGroup <first-zone-guid> <member-zone-guid-1,member-zone-guid-2> <source-guid>
```

Modify an existing zone group:

```text
SetZoneGroup <zone-group-guid> <member-zone-guid-1,member-zone-guid-2>
```

Other useful grouping commands:

```text
SetSourceForGroup <source-id-or-guid-or-name>
SetZoneGroupTimer <minutes> <zone-or-group>
SetPartyModeInclude True <zone-or-group>
SetPartyModeInclude False <zone-or-group>
PartyMode On
PartyMode Off
PartyMode Toggle
PartyMode Toggle <zone-id-or-guid-or-name>
```

### MRAD Tone and Other Output Controls

The SDK leaves these available through `command()`:

```text
Bass <value> <optional-zone>
BassUp <optional-zone>
BassDown <optional-zone>
Treble <value> <optional-zone>
TrebleUp <optional-zone>
TrebleDown <optional-zone>
Balance <value> <optional-zone>
BalanceLeft <optional-zone>
BalanceRight <optional-zone>
Loudness <true|false|toggle> <optional-zone>
MonoDownmix <true|false|toggle> <optional-zone>
MaxVolume <value> <optional-zone>
PowerOnVolume <value> <optional-zone>
ZoneGain <value> <optional-zone>
ZoneGainUp <optional-zone>
ZoneGainDown <optional-zone>
ZoneName <name> <optional-zone>
ZoneIcon <icon> <optional-zone>
SourceName <name> <optional-source>
SourceIcon <icon> <optional-source>
IdentifyZone <zone>
GetMute <optional-zone>
GetVolume <optional-zone>
GetVersions
Uptime
Ping
```

Example:

```python
mas.command("Bass", 2, "Zone_1")
mas.command("Loudness", "Toggle", "Zone_1")
```

## Direct Amplifier Protocol: Port 17037

Direct amplifier control is a compact ASCII hex protocol over TCP.

### Framing

- Encoding: ASCII.
- Command terminator: CRLF, `\r\n`.
- Basic command format: `<command-byte><output-byte><data-byte>\r\n`.
- Bytes are transmitted as two uppercase hexadecimal characters.
- Some commands return or accept more than one data byte.
- Some diagnostic commands use fixed strings outside the three-byte pattern.

Example:

```text
040A40\r\n
```

This means:

- `04`: volume command.
- `0A`: output address 10.
- `40`: volume value 64 decimal.

### Polling and Batched Commands

The direct amplifier endpoint accepts one or more CRLF-terminated hex commands
in a single request. The SDK exposes this as `send_commands()` and `poll()`.

TCP polling uses port `17037`:

```python
from autonomic import MirageAmplifier

amp = MirageAmplifier("192.168.1.60", transport="tcp")
rows = amp.poll(["0101", "0201", "0301", "0401"])
```

Some systems expose the same polling shape over HTTP at `/poll.cgi`:

```python
amp = MirageAmplifier("192.168.1.60", port=80, transport="http")
rows = amp.poll(["0101", "0201", "0301", "0401"])
```

`poll()` returns `AmplifierResponse` objects:

- `command`: integer command byte.
- `output`: decoded output number, or `None` for invalid/reserved addresses.
- `raw_output`: raw output byte as an integer.
- `data`: list of integer data bytes.
- `raw`: raw hex response line after whitespace normalization.

The response parser is intentionally broad: it ignores blank lines, ignores
non-hex noise, strips trailing whitespace, accepts multi-byte data payloads,
and tolerates devices that return additional rows beyond the commands sent.

### Output Addressing

Outputs are one-byte hexadecimal addresses.

Examples:

```text
01 = output 1
02 = output 2
0A = output 10
1F = output 31
FF = all outputs
```

The SDK accepts integer outputs or hex-string outputs:

```python
amp.set_output_volume(10, 0x40)
amp.set_output_volume("0A", 0x40)
amp.set_output_mute("all", True)
```

### Direct Amplifier Commands

| Command | Name | Data |
| --- | --- | --- |
| `01` | Standby/output enable | `00` enable/output on, `01` standby/output off, `04` toggle |
| `02` | Mute | `00` mute, `01` unmute, `02` toggle |
| `03` | Source selection | Source data value from the source map below |
| `04` | Volume | `00` through `A0` |
| `05` | Bass | Signed range encoded as one byte |
| `06` | Treble | Signed range encoded as one byte |
| `07` | Balance | Signed range encoded as one byte |
| `09` | Send all parameters | Usually `00` |
| `0C` | Amplifier features | Feature byte |
| `0D` | Maximum volume limit | `00` through `A0` |
| `11` | Volume up | Data ignored, SDK sends `00` |
| `12` | Volume down | Data ignored, SDK sends `00` |
| `14` | Device information request | Data varies by firmware |
| `1C` | Zone name | Data is ASCII string in extended usage |
| `1D` | Preamplifier volume mode | Mode byte |
| `26` | Volume BCD format | BCD volume byte |

The SDK implements the stable output controls: standby/output enable, mute,
source selection, absolute volume, volume up/down, and all-parameters request.

### Direct Amplifier Source Map

The amplifier source-selection command does not use source numbers directly.
The data byte maps source numbers to protocol source values:

| Source | Data byte |
| --- | --- |
| S1 | `05` |
| S2 | `06` |
| S3 | `07` |
| S4 | `03` |
| S5 | `00` |
| S6 | `01` |
| S7 | `02` |
| S8 | `04` |

Some matrix-style integrations identify these same physical inputs with
zero-based source IDs. In that form, source `0` is the first analog input and
is encoded as data byte `05`. Construct the client with `source_base=0` when
using those IDs:

```python
amp = MirageAmplifier("192.168.1.60", source_base=0)
amp.assign_source_to_output(source=0, output=1)   # first analog input
amp.assign_source_to_output(source=7, output=1)   # eighth local input
amp.assign_source_to_output(source=33, output=1)  # extended matrix source
```

Examples:

```text
030102
```

Selects source 7 on output 1:

- `03`: source selection.
- `01`: output 1.
- `02`: protocol data for S7.

```text
03FF05
```

Selects source 1 on all outputs.

Python:

```python
from autonomic import MirageAmplifier

amp = MirageAmplifier("192.168.1.60")
amp.assign_source_to_output(source=7, output=1)
amp.assign_source_to_all_outputs(source=1)
amp.assign_matrix({1: 7, 2: 8, 3: 1})
```

### Direct Amplifier Volume and Mute

Examples:

```text
040140
020100
020101
020102
110100
120100
```

Meaning:

- `040140`: set output 1 volume to `0x40`.
- `020100`: mute output 1.
- `020101`: unmute output 1.
- `020102`: toggle mute on output 1.
- `110100`: volume up output 1.
- `120100`: volume down output 1.

Python:

```python
amp.set_output_volume(1, 0x40)
amp.set_output_mute(1, True)
amp.set_output_mute(1, False)
amp.toggle_output_mute(1)
amp.output_volume_up(1)
amp.output_volume_down(1)
```

### Direct Amplifier Output Enable

The direct amplifier protocol uses standby state. The SDK presents this as
output enabled/disabled:

```text
010100
010101
```

Meaning:

- `010100`: output 1 enabled, standby off.
- `010101`: output 1 disabled, standby on.

Python:

```python
amp.enable_output(1)
amp.disable_output(1)
amp.enable_all_outputs()
amp.disable_all_outputs()
```

### Direct Amplifier Diagnostics

Read device ID:

```text
2FFF
```

Example response:

```text
AFFF00D40102030405060708
```

The four hex characters after `AFFF` are the device ID, `00D4` in this example.

Python:

```python
from autonomic import MirageAmplifierDiagnostics

diag = MirageAmplifierDiagnostics("192.168.1.60")
device_id = diag.get_device_id()
```

Factory reset command construction:

```text
42FF<device-id>0355AA
```

The SDK requires `confirm=True` before sending this command:

```python
diag.factory_reset(confirm=True, device_id="00D4")
```

## High-Level Python API

### Response Objects

`CommandResponse`:

- `command`: command string sent.
- `lines`: non-event response lines.
- `events`: parsed event lines.
- `payload`: parsed `BrowseResponse` for XML or legacy list responses.
- `first_line`: first response line or `None`.
- `text`: response lines joined by newline.

`BrowseResponse`:

- `kind`: root XML element name, such as `Zones`, `Sources`, or `Albums`.
- `attributes`: root XML attributes.
- `items`: list of `BrowseItem`.
- `raw`: raw response text.
- `total`, `start`, `more`: convenience accessors.

`AmplifierResponse`:

- `command`: direct amplifier command byte.
- `output`: decoded output number.
- `raw_output`: raw output byte.
- `data`: response payload bytes.
- `raw`: normalized raw response line.

`BrowseItem`:

- `kind`: item XML element name.
- `attributes`: item XML attributes.
- `children`: nested child element attributes, used by zone groups.
- `guid`, `id`, `name`: convenience accessors.
- `get_bool()` and `get_int()` convenience methods.

`StatusSnapshot`:

- `events`: parsed status events.
- `by_source`: nested dictionary, keyed by event source.
- `get(source, name, default=None)`: convenience accessor.

### `MirageMediaServer`

Use for port `5004` media server control.

```python
from autonomic import MirageMediaServer

with MirageMediaServer("192.168.1.50") as mms:
    mms.initialize(host_hint="192.168.1.50")
    albums = mms.browse_albums(1, 10)
    mms.play_album(albums.items[0].guid)
    mms.set_volume(35)
    mms.mute(False)
```

### `MirageAudioSystem`

Use for port `5006` MRAD/MAS control.

```python
from autonomic import MirageAudioSystem

with MirageAudioSystem("192.168.1.50") as mas:
    mas.initialize(host_hint="192.168.1.50")
    outputs = mas.list_outputs()
    sources = mas.list_sources()
    mas.assign_source_to_output(sources.items[0].guid, outputs.items[0].id)
    mas.set_output_volume(outputs.items[0].id, 60)
    mas.set_output_mute(outputs.items[0].id, False)
    mas.enable_output(outputs.items[0].id)
```

### `MirageAmplifier`

Use for direct port `17037` amplifier control.

```python
from autonomic import MirageAmplifier

amp = MirageAmplifier("192.168.1.60")
amp.get_device_id()
amp.list_outputs()
amp.list_sources()
amp.assign_source_to_output(7, 1)
amp.set_output_volume(1, 0x40)
amp.set_output_mute(1, False)
amp.enable_output(1)
```

### `MA6Client`

Use for seamless control when the device may be either a MAS/MA6-style system
or a standalone amplifier.

```python
from autonomic import MA6Client

with MA6Client("192.168.1.50") as client:
    client.initialize(host_hint="192.168.1.50")
    print(client.detect_mode())  # "mrad" or "amplifier"

    outputs = client.list_outputs()
    sources = client.list_sources()

    output = outputs.items[0].id or outputs.items[0].guid
    source = sources.items[0].id or sources.items[0].guid

    client.assign_source_to_output(source, output)
    client.set_output_volume(output, 60)
    client.set_output_mute(output, False)
    client.enable_output(output)
```

In amplifier mode, source identifiers are direct source numbers `1` through `8`.
In MRAD mode, source identifiers may be source GUIDs, source IDs, or names.

The `autonomic_ma6` package is a compatibility shim:

```python
from autonomic_ma6 import MA6Client
```

## Compatibility Notes From Live Devices

The client has been sanity-checked against two device profiles:

MAS/MA6-style system:

- `5004` open for MMS.
- `5006` open for MRAD.
- `17037` open for amplifier diagnostics.
- MRAD returned banner lines on connection.
- MRAD accepted targeted source assignment as
  `SetSource <source> <include_group_bool> <output>`.

Standalone M-6250-style amplifier:

- `5004` closed.
- `5006` closed.
- `17037` open.
- `2FFF` returned an `AFFF...` device-id response.
- `MA6Client` detected amplifier mode and used direct amplifier commands.

## Tests

Run normal unit tests:

```bash
python -B -m unittest discover -s tests
```

Run live tests against a device:

```bash
AUTONOMIC_TEST_HOST=10.1.0.101 python -B -m unittest discover -s tests
AUTONOMIC_TEST_HOST=10.1.0.102 python -B -m unittest discover -s tests
```

Live tests are capability-based. MRAD/MMS tests are skipped on devices where
ports `5004` and `5006` are closed. Direct amplifier tests are skipped when
port `17037` is closed.
