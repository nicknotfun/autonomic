# Autonomic External Driver Reverse Engineering

Working extraction root: `/tmp/autonomic-driver-analysis.XLBAra`

Scope note: each section below is limited to one top-level artifact from `external/`. The repository root is intentionally not compared here.

## Method

- Expanded every top-level zip into a private `/tmp` work directory.
- Expanded nested zip-style driver bundles (`.c4z`, Crestron sample zips, AMX `.AXW`).
- Extracted RTI `.rtidriver` OLE streams and decompressed zlib-compressed JavaScript streams.
- Parsed the URC `URC_COLLECT_SEC` container table and extracted all inner files. The inner URC member payloads remain opaque binary payloads with no useful plaintext strings and no zlib/deflate stream detected.
- Parsed the Savant XML profile directly and counted/inspected its command strings.

## Shared Protocol Vocabulary

| Protocol | Endpoint style | Wire shape | Where clearly observed |
| --- | --- | --- | --- |
| MMS/MCS Direct API | TCP, usually port `5004` | ASCII commands terminated with CRLF | AMX, RTI MMS, Crestron MMS |
| MMS artwork HTTP | HTTP from `BaseWebURL` or configured art port | `/getart?...` URLs | AMX, RTI MMS, Crestron MMS |
| MRAD amp API | TCP port `17037` or host-system external IP client | ASCII hex opcode frames terminated with CRLF or LF | RTI amp, Control4 amp, Crestron amp, Savant eAudioAmp |
| Control4 MMS MCP / AutonomicNet, likely MAS-family | TCP port `5006` | Implementation encrypted in Control4 Lua | Control4 MMS MCP drivers |

## `Autonomic AMX Module.zip`

Extracted focus files:

- `/tmp/autonomic-driver-analysis.XLBAra/amx_expanded/Autonomic Controls MCS Main.axs`
- `/tmp/autonomic-driver-analysis.XLBAra/amx_expanded/Autonomic Controls MCS Comm.axs`
- `/tmp/autonomic-driver-analysis.XLBAra/amx_expanded/Autonomic Controls MCS UI.axs`
- `/tmp/autonomic-driver-analysis.XLBAra/amx/readme.txt`

### Endpoints

| Endpoint | Evidence | Purpose |
| --- | --- | --- |
| TCP `host:5004` by default | `iMCS_SERVER_PORT=5004`, `ip_client_open(..., TCP)`, `send_string dvMCS, cmd,13,10` | MMS/MCS Direct control API |
| HTTP art port `5005` for MCS, `80` for MMS | readme plus `iMCS_ART_PORT=5005` | Artwork retrieval |
| `getart` HTTP path | `%Fgetart?guid=...&h=...&w=...&c=1...&fmt=png` | Album/now-playing art |

### Protocols And Operations

| Protocol | Operations supported |
| --- | --- |
| MMS/MCS Direct API | Connect/reconnect/disconnect, `Ping`, `SetClientType AMX`, `SetClientVersion 4.5.4`, `SetInstance`, `GetStatus`, `StartMCE`, `Connect:<host>[:port]` |
| MMS/MCS Direct API | Source/instance selection: `Main`, `Player_A` through `Player_D` in sample, plus list-provided `Instance` handling |
| MMS/MCS Direct API | Browsing: `Browse<list> <start> <count>`, including albums, artists, genres, playlists, favorites, titles/songs, now playing, radio sources/stations, movies, DVR, pictures, videos |
| MMS/MCS Direct API | Filtering: `SetMusicFilter Clear`, `SetRadioFilter Clear`, `SetMovieFilter Clear`, `SetDVRFilter Clear`, and item-derived filters such as `Artist=`, `Genre=`, `Playlist=` |
| MMS/MCS Direct API | Playback and queue: `Play<type> <guid> [True/False]`, `PlayFavorite`, `PlayRadioStation`, `PlayMovieChapter`, `AckPickItem`, `JumpToNowPlayingItem`, `RemoveNowPlayingItem`, `ClearNowPlaying`, `SavePlaylist "<name>"` |
| MMS/MCS Direct API | Transport/control: `PlayPause`, `Stop`, `SkipNext`, `SkipPrevious`, `SendRemote FastForward`, `SendRemote Rewind`, `SendRemote Record`, `SendRemote VolumeUp`, `SendRemote VolumeDown` |
| MMS/MCS Direct API | State toggles: `Mute True/False`, `Shuffle True/False`, `Repeat True/False`, `SetVolume <0-100>`, `ThumbsUp`, `ThumbsDown` |
| MMS/MCS Direct API | UI/dialog: `AckButton CONTEXT`, list-provided UI actions, `... OK "<text>"`, `... CANCEL` |
| MMS artwork HTTP | Now-playing and browse art via `getart?guid=<guid>` with sizing/crop/rendering parameters |

No MRAD amp protocol is present in this zip.

## `Autonomic_MMS_RTI.zip`

Extracted focus files:

- `/tmp/autonomic-driver-analysis.XLBAra/rti_decoded/mms/mms.js`
- `/tmp/autonomic-driver-analysis.XLBAra/rti_decoded/mms/SystemFunctions.xml`
- `/tmp/autonomic-driver-analysis.XLBAra/rti_decoded/amps/StandAloneCommAgent.js`
- `/tmp/autonomic-driver-analysis.XLBAra/rti_decoded/amps/MRAD.js`
- `/tmp/autonomic-driver-analysis.XLBAra/rti_decoded/amps/SystemFunctions.xml`

### Endpoints

| Endpoint | Evidence | Purpose |
| --- | --- | --- |
| TCP `MMSAddress:5004` | `mms.js` uses `g_port = 5004` and `Config.Get("MMSAddress")` | MMS Direct control API |
| HTTP `BaseWebURL/getart?...` | `createArtUrl()` uses `baseweburl` and appends `getart?...` | Artwork |
| TCP `AmpAddress:17037` | `MRAD.js`/amp JS use `g_port = 17037` | MRAD amp API |

### MMS Direct Operations

| Category | Operations |
| --- | --- |
| Session/preamble | `SetClientType RTIV2`, `SetClientVersion 5.0.20.0`, `SetOption supports_inputbox=true`, `SetOption supports_playnow=true`, `SetEncoding 65001`, `SetXMLMode Lists`, `SetPickListCount 100`, `SetHost <addr>`, `SubscribeEvents`, `GetStatus`, `GetVersions` |
| Instance/source | `SetInstance <Main/Player_A..E/USB>`, `GetStatus`, `BrowseInstances` |
| Browse/filter | `BrowseTopMenu`, `BrowseAlbums`, `BrowseArtists`, `BrowseFavorites`, `BrowseGenres`, `BrowsePlaylists`, `BrowseTitles`, `BrowseComposers`, `BrowseRadioSources`, `BrowseNowPlaying`, `SetMusicFilter Clear`, `SetMusicFilter <type>=<guid>`, `SetRadioFilter Clear`, `SetRadioFilter <type/source>=<guid>` |
| Direct services | `SetRadioFilter Source=<serviceGuid>`, then `BrowseRadioGenres` or Pandora-special `BrowseRadioStations`; GUID choices include Amazon Music, Apple Music, Calm Radio, Deezer, iHeartRadio, Murfie, Napster, Pandora, RADIO.COM, SiriusXM, LiveOne/Slacker, Spotify, TIDAL, TuneIn |
| Playback/transport | `Play`, `Pause`, `PlayPause`, `Stop`, `SkipNext`, `SkipPrevious`, `Seek <seconds>` |
| Queue/library actions | `ClearNowPlaying`, `JumpToNowPlayingItem <index>`, list-provided actions, `PlayFavorite`, `PlayRadioStation`, `AckPickItem` |
| Ratings/toggles | `Shuffle Toggle`, `Repeat Toggle`, `Scrobble Toggle`, `ThumbsUp`, `ThumbsDown`, `SetStars <0-5>` |
| Volume | `VolumeUp`, `VolumeDown`, `Mute Toggle` |
| Presets | `StorePreset "<id-or-name>"`, `RecallPreset "<id-or-name>"`, plus preset 1-10 wrappers |
| UI/input | `AckButton <guid> CANCEL`, `AckButton CONTEXT`, list-provided alert actions, `<action> OK "<text>"`, `<action> CANCEL`, `back <distance>` |
| Artwork | `getart?h=<h>&w=<w>&c=1&fmt=png&guid=<guid>&nolabel=1[&secs=...]` |

### MRAD Amp Operations

RTI uses ASCII hex frames with CRLF. Zone `FF` means all zones. Physical zone 96 is encoded as `00`; zones 32-63 use the `0x80` range and zones 64-95 use the `0xC0` range.

| Opcode | Direction | Operation |
| --- | --- | --- |
| `01` | TX/RX | Power query/set/toggle. Set `01` on, `00` off, `04` toggle. |
| `02` | TX/RX | Mute query/set/toggle. Set `00` muted, `01` unmuted, `02` toggle. |
| `03` | TX/RX | Source selection query/set. Local source values are mapped between logical and physical ids; remote sources use `32`-`63`. |
| `04` | TX/RX | Volume query/set. RTI maps UI `0-100` to amp `0-160`. |
| `05` | TX/RX | Bass query/set, encoded signed byte `0xF4` through `0x0C` for `-12..+12`. |
| `06` | TX/RX | Treble query/set, encoded signed byte `0xF4` through `0x0C` for `-12..+12`. |
| `07` | TX/RX | Balance query/set, encoded signed byte `0xEC` through `0x14` for `-20..+20`. |
| `0D` | TX/RX | Max zone volume query/set, same `0-160` scale as volume. |
| `11` | TX | Volume up. |
| `12` | TX | Volume down. |
| `1C` | RX | Zone name. |
| `29` | TX/RX | Source name query/status. RTI also uses `29FF05` as a keepalive/ping. |
| `30` | TX/RX | Zone group flags and members; flags are source-link, volume-link, standby/power-link. |
| `38` | TX | Queried in preamble; this RTI driver does not decode a handler for it. |
| `44` | TX/RX | Gain query/set, encoded like bass/treble. |
| `46` | RX | Source metadata slot value. |
| `47` | TX | Source metadata request: `47FF<source><slot>` for slots `00`-`03`. |
| `4F` | TX/RX | Remote source list/definition. |

## `Autonomic Crestron Modules.zip`

Extracted focus files:

- `/tmp/autonomic-driver-analysis.XLBAra/crestron_nested/Autonomic_Crestron_Modules_Autonomic_Controls_Mirage_Amp_Controller_v3.4.zip/Autonomic Controls Mirage Amp Comms Processor 3.4.usp`
- `/tmp/autonomic-driver-analysis.XLBAra/crestron_nested/Autonomic_Crestron_Modules_Autonomic_Controls_Mirage_Amp_Controller_v3.4.zip/Autonomic Controls Mirage Amp Controller v3.4.umc`
- `/tmp/autonomic-driver-analysis.XLBAra/crestron_nested/Autonomic_Crestron_Modules_Samples_SIMPL_Module_Non-SmartGraphics_Autonomic_MMS_CP3_Xpanel_v3.2.14_compiled.zip/Autonomic MMS IP Processor v3.2.14.usp`
- `/tmp/autonomic-driver-analysis.XLBAra/crestron_nested/Autonomic_Crestron_Modules_Samples_SIMPL_Module_Non-SmartGraphics_Autonomic_MMS_CP3_Xpanel_v3.2.14_compiled.zip/Autonomic MMS v3.2.14.umc`

### Endpoints

| Endpoint | Evidence | Purpose |
| --- | --- | --- |
| External TCP client, normally pointed at MMS Direct API | MMS module exposes `IP_TX$`, `IP_RX$`, `To_IP_Connect`, IP client status, but no socket is embedded in the SIMPL+ source | MMS control, usually `host:5004` in Autonomic integrations |
| HTTP art from server-provided `BaseWebURL` | `gsArtServer` is populated from `BaseWebURL`; `:80` is stripped for Crestron iPad compatibility | Artwork |
| External TCP client to MRAD | Amp module exposes `TX$` and `RX$`; no socket is embedded | MRAD control, normally `host:17037` |

### MMS Direct Operations

| Category | Operations |
| --- | --- |
| Session/preamble | Optional default `SetInstance "<instance>"`, `SubscribeEvents True`, `SetClientType Crestron`, `SetClientVersion 3.2.6`, `SetEncoding 20105`, `StartMCE`, `SetPicklistCount <n>`, `SetOption supports_playnow=true`, `GetStatus` |
| Browse/filter | `Browse<list> <start> <count>`, `BrowseTopMenu`, `BrowseInstances`, `BrowseAlpha`, `Set<Music/Movie/Dvr/Picture/Video/Radio>Filter Clear`, `Push<...>Filter <filter>` |
| Browse categories | Music albums/artists/composers/genres/playlists/radio/titles, movies titles/genres/people/ratings/years, DVR titles/genres/ratings/dates/stations, pictures, videos, radio sources/stations, favorites, now playing, pick lists |
| Playback/queue | `Play<type> <guid> [True]`, `PlayFavorite`, `AckPickItem`, `JumpToNowPlayingItem`, `RemoveNowPlayingItem`, `ClearNowPlaying`, `SavePlaylist "<name>"` |
| Transport/control | Commands are passed through from `Command$`; explicit handling includes seek, direct volume, context actions, clear queue, search, list add/play-now, title play-now/add-to-playlist |
| Ratings/UI | `ThumbsUp`, `ThumbsDown`, `AckButton CONTEXT`, dialog button list actions, keyboard `OK`/`CANCEL`, `Search="*text*"` filter |
| Artwork | `getart?guid=<guid>&h=<h>&w=<w>&c=1...&fmt=png` for now-playing, thumbnails, browse art |

### MRAD Amp Operations

The Crestron amp module uses the same MRAD frame format as RTI and Control4, but as a SIMPL+ TX/RX processor instead of a direct socket client.

| Opcode | Direction | Operation |
| --- | --- | --- |
| `01` | TX/RX | Power all/per-zone query, on/off/toggle. |
| `02` | TX/RX | Mute all/per-zone query, on/off/toggle. |
| `03` | TX/RX | Source select all/per-zone, including remote source slots. |
| `04` | TX/RX | Volume query/set and post-ramp query. |
| `05` | TX/RX | Bass query/set. |
| `06` | TX/RX | Treble query/set. |
| `07` | TX/RX | Balance query/set. |
| `11` | TX | Volume up ramp. |
| `12` | TX | Volume down ramp. |
| `1C` | RX | Zone name. |
| `29` | TX/RX | Source name query/status. |
| `38` | TX | Queried during initialize. |
| `46` | TX/RX | Source metadata set/status for slots `00`-`03`. |
| `47` | TX | Source metadata request for slots `00`-`03`. |
| `4F` | TX/RX | Remote source definitions/names. |

The v3.4 Crestron amp source does not expose the newer Control4-only advanced amp operations such as loudness, delay, input gain, preset groups, or network metadata.

## `Autonomic Control4 Modules.zip`

Extracted focus files:

- `/tmp/autonomic-driver-analysis.XLBAra/control4_expanded/AutonomicM401e32DistributedZones/libs/ac_amputils.lua`
- `/tmp/autonomic-driver-analysis.XLBAra/control4_expanded/AutonomicM401e32DistributedZones/driver.xml`
- `/tmp/autonomic-driver-analysis.XLBAra/control4_expanded/AutonomicMMSMCP1e/driver.xml`
- `/tmp/autonomic-driver-analysis.XLBAra/control4_expanded/AutonomicMMSInstance/driver.xml`
- `/tmp/autonomic-driver-analysis.XLBAra/control4_expanded/AutonomicMMSMusic/driver.xml`

Most Control4 `driver.lua` files are present only as `driver.lua.encrypted`. The amp helper library is readable; MMS MCP/service protocol internals are not.

### Endpoints

| Endpoint | Evidence | Purpose |
| --- | --- | --- |
| TCP `host:17037` | Amp `driver.xml` Network Connection uses TCP port `17037`; `ac_amputils.lua` writes CRLF frames via `C4:SendToNetwork` | MRAD amp API |
| TCP `host:5006` | MMS MCP `driver.xml` Server Connection uses TCP port `5006` with keep-alive/auto-connect | Control4 MMS MCP / AutonomicNet server |
| Control4 internal bindings | `RF_AUTONOMICNET_INSTANCE`, `RF_AUTONOMICNET_SERVICE`, `RF_AUTONOMICNET_EAUDIOSTREAM`, `RF_AUTONOMICNET_AMPLIFIER` | Instance, service, eAudioCast, and amplifier integration |

### MRAD Amp Operations

Control4 amp drivers cover the broadest readable MRAD set in these external archives.

| Opcode | Direction | Operation |
| --- | --- | --- |
| `01` | TX/RX | Power query/set. |
| `02` | TX/RX | Mute query/set. |
| `03` | TX/RX | Source query/set; includes local source routing and eAudioCast/remote-source slot assignment. |
| `04` | TX/RX | Volume query/set, scaled between UI `0-100` and amp `0-160`. |
| `05` | TX/RX | Bass query/set. |
| `06` | TX/RX | Treble query/set. |
| `07` | TX/RX | Balance query/set. |
| `0C` | TX/RX | Loudness query/set. |
| `0D` | TX/RX | Max volume query/set. |
| `11` | TX | Volume up. |
| `12` | TX | Volume down. |
| `14` | TX | Device ping/query used for amp discovery/completeness. |
| `29` | TX | Configure/query physical source names. |
| `30` | TX/RX | Zone grouping flags and member zones. |
| `31` | TX/RX | Zone/source delay, 5 ms units, UI range up to 600 ms. |
| `32` | TX | Input/source gain set and query. |
| `39` | TX | Request GUID/device info for a known amp id. |
| `3A` | TX/RX | Network info, GUID, system id, and self-repairing GUID writes. |
| `3D` | RX | Media-control payload forwarded to MMS MCP. |
| `44` | TX/RX | Output gain query/set. |
| `4B` | RX | Keypad event forwarded to MMS MCP. |
| `4E` | RX | Preset group map and preset group data forwarded to MCP. |
| `4F` | TX/RX | Remote source define/delete/list. |
| `94` | RX/TX follow-up | Amp identity/model/zones; may trigger `14FF06`. |
| `AF` | RX | Amp id discovery/minimal object creation. |
| `B9` | RX | Extended device info; extracts amp MAC and updates GUID state. |

Control4 also implements source maps matching RTI/Crestron: physical sources 1-12 are mapped to logical source bytes, remote/eAudio sources occupy logical `32`-`63`, and zones use the same `00`/`80`/`C0` extended-zone encoding.

### Control4 MMS MCP / AutonomicNet Operations

Exact wire operations are not recoverable from the encrypted Lua. The manifests show the operation surface:

| Driver family | Operations visible in manifests |
| --- | --- |
| MCP drivers (`AutonomicMMSMCP*`) | Server TCP connection on `5006`, `ConfigureInstances`, `ConfigureMMSNew`, `ClearLogFiles`, one `avswitch` proxy, up to 32 instance bindings, up to 32 service bindings, optional amplifier binding |
| Instance drivers (`AutonomicMMSInstance`, `AutonomicMMSeSeriesInstance`) | IP Address, MMS Instance, enabled/debug/logging properties; media service proxy; MMS instance binding; audio out; eAudioCast |
| Service drivers | Media service proxy plus AutonomicNet service binding. Bundles found for Amazon Music, Apple Music, Audacy, Calm Radio, Deezer, Favorites, iHeartRadio, LiveOne, Murfie, Music, Napster, Pandora, Qobuz, SiriusXM, SoundMachine, Spotify, TIDAL, TuneIn |

Because the MMS Lua is encrypted, this zip cannot be used to enumerate MMS/MAS textual commands or numeric opcodes beyond the exposed Control4 connection model and manifest actions.

## `autonomic controls_eaudioamp_1.6.xml`

Extracted focus file:

- `/home/nick/code/autonomic/external/autonomic controls_eaudioamp_1.6.xml`

This is a Savant profile for Autonomic Controls `eAudioAmp`. It is not a zip,
but it is a readable integration artifact with direct MRAD command examples.
It declares 24 logical zones and 864 `command_string` entries, with 432 unique
command strings.

### Endpoints

| Endpoint | Evidence | Purpose |
| --- | --- | --- |
| TCP `host:17037` | `<ip port="17037" ... protocol="tcp" name_on_component="LAN">` | MRAD/eAudioAmp control |
| LF terminator | `<send_postfix type="hex">0A</send_postfix>` | Frames are ASCII hex strings followed by LF |

### MRAD Amp Operations

The Savant profile uses the same MRAD opcode family as RTI, Control4, and
Crestron, but terminates sends with LF instead of CRLF.

| Opcode | Direction | Operation |
| --- | --- | --- |
| `01` | TX | Power on/off/toggle. Examples: `010101`, `010100`, `010104`. |
| `02` | TX | Mute on/off. Examples: `020100`, `020101`. |
| `03` | TX | Source selection. `RS1`-`RS16` map to source bytes `20`-`2F`, for example `030120` through `03012F`. |
| `04` | TX | Volume query or set-volume preamble. Example: `0401`; action argument supplies a hex volume value. |
| `05` | TX | Bass set template, for example `05010` plus a Savant hex parameter. |
| `06` | TX | Treble set template, for example `06010` plus a Savant hex parameter. |
| `07` | TX | Balance set template, for example `07010` plus a Savant hex parameter. |
| `11` | TX | Volume up. Example: `110100`; other integrations usually send `11<zone>` without the trailing `00`. |
| `12` | TX | Volume down. Example: `120100`; other integrations usually send `12<zone>` without the trailing `00`. |

Savant also provides evidence that remote/eAudio source selection uses protocol
source bytes `20`-`2F` for at least `RS1`-`RS16`. This agrees with the
RTI/Control4 remote-source range of `20`-`3F`.

### Profile Quirks

- Zones 17-24 mostly duplicate zone 15 command strings even though their
  logical component names and state variables are named Zone17 through Zone24.
- `RS17`-`RS24` selections are frequently hardcoded to `03012F`, which appears
  to be a copy/paste fallback to zone 1 remote source 16.
- Bass, treble, and balance command templates such as `05010` are odd-length
  preambles that rely on Savant parameter substitution; they should not be read
  as complete raw MRAD frames by themselves.
- Volume state variables use max `160`, while inline action notes say `0~50`.

## `Autonomic URC Driver.zip`

Extracted focus files:

- `/tmp/autonomic-driver-analysis.XLBAra/urc/AutonomicMMSv3.0.0.6.tcm2`
- `/tmp/autonomic-driver-analysis.XLBAra/urc/AutonomicMirageAmpv2.0.tcm2`
- `/tmp/autonomic-driver-analysis.XLBAra/urc/AutonomicMirageAmpv1.3.tcm`
- `/tmp/autonomic-driver-analysis.XLBAra/urc/updateHistory.txt`
- Extracted inner payloads under `/tmp/autonomic-driver-analysis.XLBAra/urc_extracted`

### Container Findings

All three driver files are `URC_COLLECT_SEC` containers. The outer table format is:

- Signature: `URC_COLLECT_SEC\0`
- Count: big-endian 32-bit at offset `0x14`
- Entry table starts at offset `0x1b`
- Entry size: `140` bytes
- Entry layout: 128-byte null-padded ASCII name, then little-endian `size`, `offset`, `compressed_or_stored_size`

Extracted members:

| URC file | Members |
| --- | --- |
| `AutonomicMMSv3.0.0.6.tcm2` | `Autonomic_MMS.vfd.TCM1`, `AUTONOMIC_MMSGUI.VFD.TCM8/9/10`, `info.dat`, `MMS_Core.cd2.TCM0`, `MMS_Main_Interface.cd2.TCM0`, `MMS_Player_A` through `MMS_Player_E` interface modules, `MMS_USB_Interface.cd2.TCM0` |
| `AutonomicMirageAmpv2.0.tcm2` | `Autonomic_Amplifier.vfd.TCM1`, `info.dat`, `M-x00a.cd2.TCM0`, `M-x00e.cd2.TCM0` |
| `AutonomicMirageAmpv1.3.tcm` | `info.dat`, `M-x00.vfd.TCM1`, `M-x00a.csd.TCM0`, `M-x00e.csd.TCM0` |

The member payloads are still proprietary/opaque. `strings` only produced fragments such as `MMS`/`mcs`; no endpoints, commands, opcodes, XML, JavaScript, Lua, or text resources were recoverable.

### Endpoints

No endpoint is visible in the cleartext parts of the URC files. Based on driver names and the surrounding Autonomic driver set, the MMS driver likely controls MMS/MCS or MMS API endpoints and the amp driver likely controls MRAD, but the URC payloads do not expose host/port constants in readable form.

### Protocols And Operations

| Protocol | Confidence | Operations visible |
| --- | --- | --- |
| MMS/MCS or MMS API | Inferred from filename, component names, and release notes | Now Playing UI, browse UI, player A-E and USB interfaces, direct search from Now Playing, queue clear/reorder, resume browse, service/local-music direct launch, favorites/scenes, service account/default-service controls, TuneBridge user command, trigger in/out events, generic `MMS API` two-way command |
| MRAD amp | Inferred from amp driver name and release notes | Amplifier UI/control module; exact commands are not recoverable. Release notes mention reliability improvements for queries/events and conversion to Accelerator 2/3 module format. |

The URC driver cannot be used as a reliable source for opcode-level comparison unless a URC module decompiler, payload key, or vendor format documentation is available.
