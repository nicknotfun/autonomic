# Autonomic Python SDK

Python SDK and protocol notes for Autonomic Mirage Media Server, Mirage Audio
System/MRAD systems, and direct Mirage amplifier control such as standalone
M-6250 devices.

This README is intentionally self-contained. It documents the protocol behavior
implemented by this library and observed against real devices.

## Client Layers

The SDK exposes three low-level clients and one unified high-level client.

Low-level clients:

- `MirageMediaServer`: raw MMS/media-server protocol on TCP port `5004`.
- `MirageAudioSystem`: raw MRAD/MAS output, source, group, and zone protocol on
  TCP port `5006`.
- `MirageAmplifier`: raw direct amplifier protocol on TCP port `17037`.

High-level client:

- `AutonomicClient`: one object that auto-detects MRAD/MAS or direct amplifier
  mode and exposes object-first output/source helpers.

The SDK supports two main output-control paths.

`mrad` mode:

- Media server control over TCP port `5004`.
- MRAD/MAS zone and source control over TCP port `5006`.
- Modern single-socket mode where MRAD commands are proxied over port `5004`
  by prefixing commands with `MRAD.`.

`amplifier` mode:

- Direct amplifier control over TCP port `17037`.
- Used by standalone matrix amplifiers where `5004` and `5006` are not open.
- The SDK exposes synthetic output/source browse lists and sends direct hex
  amplifier commands for source routing, volume, and mute.

`AutonomicClient` auto-detects the backend:

- If port `5006` is open, it uses MRAD/MAS mode.
- Otherwise, if port `17037` is open, it uses direct amplifier mode.
- Media transport controls such as play/pause are only available in MRAD/MMS
  mode.

## Installation

```bash
python -m pip install -e .
```

The runtime dependency is Pydantic, used for typed output/source models.

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

- `browse_all_zones()` returns the raw browse response.
- `list_outputs()` returns typed `AutonomicOutput` objects, omitting disabled
  outputs by default.
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
```

`BrowseSources` returns sources available to the active zone. `BrowseAllSources`
returns all sources available to any zone. `list_sources()` uses the all-sources
browse response, then omits disabled/unavailable sources by default.

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
    mas.assign_source_to_output(sources[0], outputs[0])
    mas.assign_source_to_all_outputs(sources[0])
    mas.assign_output_sources({
        outputs[0]: sources[0],
        outputs[1]: sources[1],
    })
```

Use `include_disabled=True` when an integration needs to inspect disabled
source/output metadata without controlling those items:

```python
all_outputs = mas.list_outputs(include_disabled=True)
all_sources = mas.list_sources(include_disabled=True)
disabled_sources = [source for source in all_sources if source.disabled]
```

### MRAD Output Power, Volume, and Mute

Active output:

```text
Volume <volume>
VolumeUp
VolumeDown
Mute true
Mute false
Mute toggle
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
```

All outputs:

```text
MuteAll On
MuteAll Off
MuteAll Toggle
```

Python:

```python
mas.set_output_volume("Zone_1", 60)
mas.set_output_mute("Zone_1", False)
mas.set_output_is_on("Zone_1", True)
mas.mute_all_outputs(True)
```

`set_output_power()` and `set_output_is_on()` set the runtime `PowerOn` /
`is_on` state for a zone. They are not enablement/configuration helpers and are
only available for MRAD/MAS mode.

For MRAD/MAS output-targeted controls, the SDK powers the zone on before
source assignment, volume changes, and targeted mute/unmute commands. If the
device still returns a zone-is-off error, the SDK sends `Power On` and retries
the original command once. Direct amplifier mode uses its own runtime power
command and does not apply this MRAD retry behavior.

### MRAD Zone Groups

The SDK parses zone-group browse responses so callers can inspect membership.
It does not provide helpers that modify zone groups or party-mode membership.

### MRAD Read-Only Utility Commands

The high-level SDK intentionally omits configuration-plane helpers such as
enable/disable, renaming, icon changes, party mode, and zone-group mutation.
Low-level `command()` remains available for protocol exploration; the
documented control surface is source routing, runtime zone power, mute, and
volume.

Useful read-only commands:

```text
GetMute <optional-zone>
GetVolume <optional-zone>
GetVersions
Uptime
Ping
```

Example:

```python
mas.command("GetVolume", "Zone_1")
mas.command("GetVersions")
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

The low-level protocol stores direct amplifier volume as `0x00` through
`0xA0`. The Python API exposes volume as a normal `0` through `100` percentage
and scales to/from the raw protocol value.

### Polling and Batched Commands

The direct amplifier endpoint accepts one or more CRLF-terminated hex commands
in a single request. The SDK exposes this as `send_commands()` and `poll()`.

TCP polling uses port `17037`:

```python
from autonomic import MirageAmplifier

amp = MirageAmplifier("192.168.1.60", transport="tcp")
rows = amp.poll(["0101", "0201", "0301", "0401"])
outputs = amp.list_outputs()  # polls power, mute, source, and volume
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
amp.set_output_volume(10, 40)
amp.set_output_volume("0A", 40)
amp.set_output_mute("all", True)
```

### Direct Amplifier Commands

| Command | Name | Data |
| --- | --- | --- |
| `01` | Runtime output power | `00` off, `01` on |
| `02` | Mute | `00` mute, `01` unmute, `02` toggle |
| `03` | Source selection | Source data value from the source map below |
| `04` | Volume | Raw protocol `00` through `A0`; Python API uses `0` through `100` |
| `09` | Send all parameters | Usually `00` |
| `11` | Volume up | Data ignored, SDK sends `00` |
| `12` | Volume down | Data ignored, SDK sends `00` |
| `14` | Device information request | Data varies by firmware |

The SDK implements the stable control-plane commands for mute, source
selection, runtime output power, absolute volume, volume up/down,
all-parameters readback, and device information readback. It intentionally does
not expose direct amplifier configuration helpers such as naming, enablement, or
stack setup.

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

The unified client exposes the same direct amplifier shape options:

```python
client = AutonomicClient(
    "192.168.1.60",
    mode="amplifier",
    amplifier_output_count=16,
    amplifier_source_count=12,
    amplifier_source_base=0,
)
```

When using `AutonomicClient` in direct amplifier mode, the raw direct slots are
presented with the local house names:

| Output | Presented name |
| --- | --- |
| 1 | Kitchen |
| 2 | Dining |
| 3 | Living |
| 4 | Master |
| 5 | Bathroom |
| 6 | Foyer |
| 7 | Sitting |
| 8 | Passthrough |
| 9 | Grill |
| 10 | Patio West |
| 11 | Patio East |
| 12 | Pool |

The raw direct source slots `S7` and `S8` are presented as `Alpha` and `Beta`.
The lower-level `MirageAmplifier` client still exposes the protocol slot names.

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
010101
010100
020100
020101
020102
110100
120100
```

Meaning:

- `040140`: set output 1 raw protocol volume to `0x40` (`40%` in the Python API).
- `010101`: turn output 1 on.
- `010100`: turn output 1 off.
- `020100`: mute output 1.
- `020101`: unmute output 1.
- `020102`: toggle mute on output 1.
- `110100`: volume up output 1.
- `120100`: volume down output 1.

Python:

```python
amp.set_output_power(1, True)
amp.set_output_power(1, False)
amp.set_output_volume(1, 40)
amp.set_output_mute(1, True)
amp.set_output_mute(1, False)
amp.toggle_output_mute(1)
amp.output_volume_up(1)
amp.output_volume_down(1)
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
from autonomic import MirageAmplifier

amp = MirageAmplifier("192.168.1.60")
device_id = amp.get_device_id()
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
- Returned by low-level `browse_*` methods when raw protocol detail is needed.

`AutonomicOutput` and `AutonomicSource`:

- Pydantic models returned by ergonomic `list_outputs()` and `list_sources()`.
  Disabled items are omitted unless `include_disabled=True` is passed.
- `id`, `guid`, `name`, `attributes`, and `raw_xml` expose parsed protocol data.
- `disabled` is read-only state parsed from common disabled, enabled, hidden,
  and availability attributes when devices expose it.
- Objects are bound to the client that created them, so helper methods work:
  `client.list_outputs()[0].mute()`, `output.set_volume(50)`, and
  `source.assign_to(output)`.
- Source/output assignment is symmetric: `source.assign_to(output)` and
  `output.assign(source)` make the same client call.
- `AutonomicClient.all_outputs()` returns an `AutonomicOutputGroup`, an
  iterable fanout proxy with the same control helpers as a single output:
  `assign()`, `set_volume()`, `mute()`, `unmute()`, `volume_up()`,
  `volume_down()`, `set_power()`, and `set_is_on()`.
- `AutonomicClient` supports a source alias map keyed by source GUID. Aliases
  change the returned object's display `name` and allow alias names in high
  level source assignment calls, while preserving the device's original source
  name in `source.attributes["name"]`. Aliases are local to the SDK and do not
  send any source rename or configuration command.
- `AutonomicClient.source_by_name(name)` and `output_by_name(name)` return
  typed objects by case-insensitive display name using the normal filtered list
  APIs. Pass `include_disabled=True` to search disabled items too.
- Output objects expose read-only state such as `is_on`, `muted`, `volume`,
  and current source fields when the device provides them.
- Output objects can set runtime power with `output.set_is_on()` or
  `output.set_power()`. On MRAD/MAS this updates the zone `PowerOn` state; in
  direct amplifier mode it sends the runtime `01` power command. It is not an
  enable/disable configuration-plane helper.
- Output/source objects do not expose enable, disable, or rename helpers.
- Client methods accept object instances, IDs, GUIDs, or names where applicable.

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
    sources[0].assign_to(outputs[0])
    outputs[1].assign(sources[0])
    sources[0].assign_to_all_outputs()
    outputs[0].set_volume(60)
    outputs[0].unmute()
```

### `MirageAmplifier`

Use for direct port `17037` amplifier control.

```python
from autonomic import MirageAmplifier

amp = MirageAmplifier("192.168.1.60")
amp.get_device_id()
outputs = amp.list_outputs()  # includes direct amp status where reported
sources = amp.list_sources()
sources[6].assign_to(outputs[0])
outputs[1].assign(sources[6])
sources[0].assign_to_all_outputs()
outputs[0].set_power(True)
outputs[0].set_volume(40)
outputs[0].unmute()
```

### `AutonomicClient`

Use for seamless control when the device may be either a MAS/MRAD system or a
standalone direct amplifier. `AutonomicClient` auto-detects and initializes
itself during construction. Use `auto_initialize=False` only when building an
offline object for tests or when you need to patch/customize low-level clients
before the first protocol call.

```python
from autonomic import AutonomicClient

with AutonomicClient("192.168.1.50") as client:
    print(client.detect_mode())  # "mrad" or "amplifier"

    outputs = client.list_outputs()
    sources = client.list_sources()
    source = client.source_by_name("Alpha")
    output = client.output_by_name("Kitchen")

    client.assign_source_to_output(source, output)
    output.set_is_on(True)
    output.set_volume(60)
    output.unmute()
```

The default source aliases are:

```python
{
    "000027fb-f8a9-f6be-a465-3d0fbee12977": "Alpha",
    "000027fc-f8a9-f6be-a465-3d0fbee12977": "Beta",
    "0000008a-f8a9-f6be-a465-3d0fbee12977": "Gamma",
    "0000008b-f8a9-f6be-a465-3d0fbee12977": "Delta",
}
```

Pass `source_aliases={...}` to provide a different map, or
`source_aliases=None` to disable aliases:

```python
from autonomic import AutonomicClient

with AutonomicClient("192.168.1.50") as client:
    alpha = client.source_by_name("Alpha")
    client.all_outputs().assign(alpha)

with AutonomicClient("192.168.1.50", source_aliases=None) as raw_client:
    print(raw_client.list_sources()[0].attributes["name"])
```

In amplifier mode, source identifiers are direct source numbers `1` through `8`.
In MRAD mode, source identifiers may be source GUIDs, source IDs, or names.

Set every output to a source:

```python
from autonomic import AutonomicClient

with AutonomicClient("192.168.1.50") as client:
    source = client.list_sources()[0]
    client.all_outputs().assign(source)
```

Equivalent object-first form:

```python
with AutonomicClient("192.168.1.50") as client:
    outputs = client.all_outputs()
    outputs.assign(client.source_by_name("Alpha"))
    outputs.set_volume(50)
    outputs.unmute()
```

The same operation is available as a runnable example:

```bash
python examples/set_all_outputs_to_source.py 192.168.1.50
python examples/set_all_outputs_to_source.py 192.168.1.50 COAX2
python examples/set_all_outputs_to_source.py 192.168.1.60 1 --mode amplifier
```

## Live Device Notes

The client has been sanity-checked against two device profiles:

MAS/MRAD system:

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
- `AutonomicClient` detected amplifier mode and used direct amplifier commands.

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
