# Autonomic Direct Amplifier Protocol

This file is a concise implementation reference for the supported `amp` package
and should stay aligned with [amp/codec.py](amp/codec.py).

MAS/MRAD media-server command sessions are not described here.

## Wire Format

Messages are LF-terminated ASCII hexadecimal rows. A CR before LF is accepted.
Two ASCII characters encode one byte.

```text
010101\r\n
```

represents:

```text
01 01 01
```

Rows have this manufacturer shape:

```text
<command><zone>[data...]
```

The `zone` byte is usually an output address. Special manufacturer values:

| Zone | Meaning |
| --- | --- |
| `FF` | All zones or command-specific device scope. |
| `FE` | All local zones. |
| `FD` | Interface dependent. |
| `FC` | Unassigned. |
| `FB` | Disabled. |
| `FA` | All used zones. |

Rows with no data are usually queries. Rows with data can be commands, status
readbacks, or configuration writes depending on the command family.

## Pattern Notation

`amp.codec` patterns use these field types:

| Notation | Meaning |
| --- | --- |
| `N` | One unsigned byte. |
| `4N` | Two-byte unsigned integer, big-endian. |
| `S` | One signed byte, two's-complement. |
| `4X`, `12X` | Fixed raw byte count expressed in hex characters. |
| `hex` | Remaining raw bytes. |
| `utf8` | Remaining bytes decoded as UTF-8. |
| `lenutf8` | One-byte length prefix followed by UTF-8 text. |
| `guid` | 16 bytes in Autonomic/Windows GUID byte order. |
| `uuid` | 16 bytes in RFC UUID byte order. |
| `field?` | Optional field. |
| `field*` | Zero or more repeated fields. |
| `field+` | One or more repeated fields. |
| `!` | Pattern must consume the whole row. |

## Common Values

Power and mute use different byte polarity:

| Command | Off | On | Toggle |
| --- | --- | --- | --- |
| Standby / Power | `00` | `01` | `04` |
| Mute | `01` | `00` | `02` |

Volume, maximum volume, master volume, and power-on volume use raw `00`-`A0`,
scaled linearly to `0.0`-`1.0`. Source gain uses raw `00`-`12`, also scaled
linearly to `0.0`-`1.0`.

Bass, treble, balance, and zone gain are signed one-byte values.

`guid` fields use Autonomic/Windows wire order:

```text
UUID: 674e1900-f8a9-f6be-a465-3d0fbee12977
Wire: 00194E67A9F8BEF6A4653D0FBEE12977
```

## Command Catalog

The codec registers documented manufacturer commands, including obsolete and
unused command bytes. Obsolete/unused status is exposed on the command classes
with `COMMAND_STATUS` and `COMMAND_NOTE`; parameter comments are recorded in
`amp.codec.PARAMETER_COMMENTS`. The `58` host identity rows are an observed,
undocumented extension retained for live-device discovery.

| Byte | Codec class | Purpose | Pattern |
| --- | --- | --- | --- |
| `00` | `NoOperationCommand` | No Operation | `00{output:N}{payload:hex?}!` |
| `01` | `StandbyPowerCommand` | Standby / Power | `01{output:N}{is_on:power_bool?}!` |
| `02` | `MuteCommand` | Mute | `02{output:N}{is_muted:mute_bool?}!` |
| `03` | `SourceSelectionCommand` | Source Selection | `03{output:N}{source:N?}{detail:N*}!` |
| `04` | `VolumeCommand` | Volume | `04{output:N}{volume:float(160,0.0,1.0)?}{detail:N*}!` |
| `05` | `BassCommand` | Bass | `05{output:N}{bass:S?}!` |
| `06` | `TrebleCommand` | Treble | `06{output:N}{treble:S?}!` |
| `07` | `BalanceCommand` | Balance | `07{output:N}{balance:S?}!` |
| `08` | `RequestProtocolVersionCommand` | Request Protocol Version | `08{output:N}!` |
| `88` | `RequestProtocolVersionCommandResponse` | Protocol Version response | `88{output:N}{version:N}!` |
| `09` | `SendAllParametersCommand` | Send All Parameters, deprecated by manufacturer | `09{output:N}{request:N?}!` |
| `0A` | `ReportErrorCommand` | Report Error, obsolete | `0A{output:N}{payload:hex?}!` |
| `0B` | `EmulateKeyPressOnKeypadCommand` | Emulate key press on Keypad, obsolete | `0B{output:N}{key:N?}!` |
| `0C` | `AmplifierSpecialFeaturesCommand` | Amplifier special features | `0C{output:N}{is_loud:bool?}{detail:N*}!` |
| `0D` | `MaximumVolumeCommand` | Maximum volume | `0D{output:N}{max_volume:float(160,0.0,1.0)?}{detail:N*}!` |
| `0E` | `ObsoletePresetSelectionStatusCommand` | Preset Selection / Status, obsolete old form | `0E{output:N}{payload:hex?}!` |
| `0F` | `LinkZonePairCommand` | Link zone pair, obsolete | `0F{output:N}{linked_output:N?}{options:N?}!` |
| `10` | `MediaFavouritesCommand` | Media Favourites | `10{output:N}{device_id:4X}{favorite_index:N}{payload:hex?}!` |
| `11` | `VolumeUpCommand` | Volume Up | `11{output:N}{amount:float(160,0.0,1.0)?}!` |
| `12` | `VolumeDownCommand` | Volume Down | `12{output:N}{amount:float(160,0.0,1.0)?}!` |
| `13` | `AutoDistributedSourceAssignmentAdvisoryCommand` | Auto distributed source assignment advisory | `13{output:N}{payload:hex}!` |
| `14` | `RequestDeviceInformationCommand` | Request Device information | `14{output:N}{options:N?}!` |
| `94` | `RequestDeviceInformationCommandResponse` | Device information response | `94FF00{firmware:N}{model_id}{device_id:4X}{zones:N*}!` |
| `15` | `FirmwareUpdateCommand` | Firmware update | `15{output:N}{device_id:4X}{status:N}{payload:hex?}!` |
| `16` | `AutoPowerCommand` | Auto power on/off | `16{output:N}{device_id:4X}{payload:hex?}!` |
| `17` | `DigitalInputOutputOptionsCommand` | Digital input/output options | `17{output:N}{device_id:4X}{payload:hex?}!` |
| `18` | `DynamicZoneLinkingCommand` | Dynamic zone linking | `18{output:N}{operation:N}{zones:N*}!` |
| `19` | `MasterVolumeCommand` | Master volume | `19{output:N}{volume:float(160,0.0,1.0)?}!` |
| `1A` | `Unused1ACommand` | Unused manufacturer command byte | `1A{output:N}{payload:hex?}!` |
| `1B` | `PresetParametersCommand` | Preset Parameters | `1B{output:N}{payload:hex?}!` |
| `1C` | `ZoneNameCommand` | Zone name | `1C{output:N}{name:utf8}!` |
| `1D` | `PreampVolumeModeCommand` | Preamp volume mode | `1D{output:N}{preamp_volume_mode:S?}!` |
| `1E` | `PresetSelectionStatusCommand` | Preset Selection / Status | `1E{output:N}{preset:N?}{status:N?}!` |
| `1F` | `NoLongerInUse1FCommand` | No longer in use | `1F{output:N}{payload:hex?}!` |
| `20` | `PresetSoundSetupCommand` | Preset Sound setup | `20{output:N}{payload:hex?}!` |
| `21` | `EqualizationCommand` | Equalization | `21{output:N}{payload:hex?}!` |
| `22` | `RequestDeviceLogEntryCommand` | Request device log entry | `22{output:N}{device_id:4X}{payload:hex?}!` |
| `A2` | `RequestDeviceLogEntryCommandResponse` | Device log entry response | `A2{output:N}{device_id:4X}{payload:hex}!` |
| `23` | `PresetAlarmControlCommand` | Preset alarm control | `23{output:N}{preset:N}{action:N}!` |
| `24` | `RequestPcmCapabilitiesCommand` | Request PCM capabilities, obsolete | `24{output:N}{device_id:4X}{purpose:N}!` |
| `A4` | `RequestPcmCapabilitiesCommandResponse` | Request PCM capabilities response, obsolete | `A4{output:N}{device_id:4X}{purpose:N}{payload:hex}!` |
| `25` | `PcmStreamCommand` | PCM Stream, obsolete | `25{output:N}{device_id:4X}{purpose:N}{stream_format:N}{position_or_length:8N}{payload:hex?}!` |
| `A5` | `PcmStreamCommandResponse` | PCM Stream response, obsolete | `A5{output:N}{device_id:4X}{purpose:N}{position:8N}!` |
| `26` | `KeypadPortOptionsCommand` | Keypad port options | `26{output:N}{device_id:4X}{options:hex?}!` |
| `27` | `SetTimeZoneDateTimeCommand` | Set time zone, date and time | `27{output:N}{payload:hex}!` |
| `28` | `VideoSourceSelectionCommand` | Video Source Selection | `28{output:N}{source:N?}!` |
| `29` | `SourceNameOptionsRequestCommand` | Source Name and Options request | `29{output:N}!` |
| `29` | `SourceNameOptionsCommand` | Source Name and Options | `29{output:N}{source_selector:N}{options:6X?}{hidden_name:lenutf8?}{name:utf8?}!` |
| `2A` | `PresetNameCommand` | Preset Name | `2A{output:N}{preset:N}{name:utf8?}!` |
| `2B` | `RequestPresetNameCommand` | Request preset name | `2B{output:N}{preset:N}!` |
| `2C` | `SourceUpCommand` | Source Up | `2C{output:N}{mode:N}!` |
| `2D` | `SourceDownCommand` | Source Down | `2D{output:N}{mode:N}!` |
| `2E` | `ZoneAssignmentCommand` | Zone assignment | `2E{output:N}{device_id:4X}{zones:N*}!` |
| `2F` | `RequestZoneAssignmentsCommand` | Request zone assignments | `2F{output:N}{device_id:4X?}!` |
| `AF` | `RequestZoneAssignmentsCommandResponse` | Zone assignments response | `AF{output:N}{device_id:4X}{zones:N*}!` |
| `30` | `LinkZonesCommand` | Link zones | `30{output:N}{flags:N?}{members:N*}!` |
| `31` | `AudioDelayCommand` | Audio delay query/write | `31{output:N}{delay:N?}!` |
| `31` | `AudioDelayCommandResponse` | Per-source audio delay response | `31{output:N}{source_delays:N+}!` |
| `32` | `SourceGainCommand` | Source Gain | `32{output:N}{source_selector:N?}{gains:float(18,0.0,1.0)*?}!` |
| `33` | `PagePreset2SelectionCommand` | Page Preset 2 Selection | `33{output:N}{preset:N}!` |
| `34` | `ClippingNotificationCommand` | Clipping notification | `34{output:N}{event:N}{info:N}!` |
| `35` | `IrRoutingAssignmentsCommand` | IR routing assignments | `35{output:N}{device_id:4X}{payload:hex?}!` |
| `36` | `PartyModeSelectionCommand` | Party mode select/deselect, obsolete | `36{output:N}{is_selected:bool}!` |
| `37` | `PartyModeConfigurationCommand` | Party mode configuration, obsolete | `37{output:N}{device_id:4X}{source:N}!` |
| `38` | `ZoneNameRequestCommand` | Zone name request | `38{output:N}!` |
| `39` | `RequestExtendedDeviceInformationCommand` | Request extended device information | `39{output:N}{device_id:4X?}!` |
| `B9` | `RequestExtendedDeviceInformationCommandResponse` | Extended device information response | `B9FF{prefix:4X}{device_id:4X}{model_info:18X}{mac:12X}{detail:hex}!` |
| `3A` | `NetworkSettingsDeviceGuidRequestCommand` | Network settings Device GUID request | `3AFF{device_id:4X}85!` |
| `3A` | `NetworkSettingsDeviceGuidCommand` | Network settings Device GUID | `3AFF{device_id:4X}05{guid:guid}!` |
| `3A` | `NetworkSettingsAmplifierStackAssignmentCommandResponse` | Amplifier stack assignment response | `3AFF{device_id:4X}06{system_id:N}!` |
| `3A` | `NetworkSettingsCommandResponse` | Network settings response | `3AFF{device_id:4X}{setting_id:N}{payload:hex}!` |
| `3A` | `NetworkSettingsCommand` | Network settings | `3A{output:N}{device_id:4X}{setting_id:N}{payload:hex?}!` |
| `3B` | `MediaServersCommand` | Media servers | `3B{output:N}{device_id:4X}{entry_index:N}{payload:hex?}!` |
| `3C` | `ListSourcesCommand` | List sources | `3C{output:N}{payload:hex?}!` |
| `3D` | `MediaPlayerPlayControlCommand` | Media Player play control | `3D{output:N}{source:N}{payload:hex}!` |
| `3E` | `PlayStatusNotificationCommand` | Play status notification | `3E{output:N}{source:N}{parameter:N}{payload:hex?}!` |
| `3F` | `PlayStatusRequestCommand` | Play status request | `3F{output:N}{source:N}{payload:hex}!` |
| `40` | `ReportMessageCommand` | Report message | `40{output:N}{message_type:N}{message:utf8?}!` |
| `41` | `RequestTimeCommand` | Request time | `41{output:N}{mode:N}!` |
| `C1` | `RequestTimeCommandResponse` | Time response | `C1{output:N}{hour:N}{minute:N}{second:N}{weekday:N}{day:N}{month:N}{year:N}!` |
| `42` | `SettingsManagementCommand` | Settings management | `42{output:N}{device_id:4X}{instruction:N}{payload:hex?}!` |
| `43` | `MiscellaneousDeviceSettingsCommand` | Miscellaneous device settings and management | `43{output:N}{device_id:4X}{option:N}{command:N}{payload:hex?}!` |
| `44` | `ZoneGainCommand` | Zone gain | `44{output:N}{gain:S?}!` |
| `45` | `UserAccountsCommand` | User accounts, obsolete | `45{output:N}{device_id:4X}{entry_index:N}{payload:hex?}!` |
| `46` | `SourceSpecificMetadataCommand` | Source specific metadata | `46{output:N}{source_selector:N}{position:N}{value:utf8}!` |
| `47` | `SourceSpecificMetadataRequestCommand` | Source specific metadata request | `47{output:N}{source_selector:N}{position:N}!` |
| `48` | `PowerOnVolumeLevelCommand` | Power on volume level | `48{output:N}{power_on_volume:float(160,0.0,1.0)?}{detail:N*}!` |
| `49` | `RequestKeypadZoneAssignmentCommand` | Request keypad zone assignment | `49{output:N}{keypad_id:8X}!` |
| `C9` | `RequestKeypadZoneAssignmentCommandResponse` | Keypad zone assignment response | `C9{output:N}{keypad_id:8X}{assigned_output:N}!` |
| `4A` | `KeypadPortZoneMappingCommand` | Keypad port/zone mapping | `4A{output:N}{device_id:4X}{payload:hex?}!` |
| `4B` | `KpeKeyEventCommand` | KPE key event | `4B{output:N}{key_code:4N?}!` |
| `4C` | `KpeLedControlCommand` | KPE LED control | `4C{output:N}{payload:hex?}!` |
| `4D` | `KeypadPortOccupancyCommand` | Keypad port occupancy request | `4D{output:N}{device_id:4X}!` |
| `CD` | `KeypadPortOccupancyCommandResponse` | Keypad port occupancy response | `CD{output:N}{device_id:4X}{occupancy:N}!` |
| `4E` | `ArbitraryDataStorageCommand` | Arbitrary data storage | `4E{output:N}{slot_id:4N}{payload:hex?}!` |
| `4F` | `DistributedSourceDefinitionRequestCommand` | Distributed Source Definition request | `4F{output:N}{slot_id:N?}!` |
| `4F` | `DistributedSourceDefinitionCommand` | Distributed Source Definition | `4F{output:N}{slot_id:N}{backing_device_guid:guid}{source_index:N}{name:utf8}!` |
| `4F` | `DistributedSourceDefinitionUnusedCommand` | Unused Distributed Source Definition slot | `4F{output:N}{slot_id:N}00!` |
| `50` | `DistributedSourceAudioDelayCommand` | Distributed Source Audio Delay | `50{output:N}{payload:hex?}!` |
| `51` | `RegisterServiceCommand` | Register Service | `51{output:N}{source:N}{flags:N?}{payload:hex?}!` |
| `52` | `ExtendedPlayControlCommand` | Extended Play Control | `52{output:N}{service_id:N}{command:N}{payload:hex?}!` |
| `53` | `ExtendedPlayStatusCommand` | Extended Play Status | `53{output:N}{service_id:N}{parameter:N}{payload:hex?}!` |
| `54` | `ExtendedPlayStatusRequestCommand` | Extended Play Status Request | `54{output:N}{service_id:N}{payload:hex}!` |
| `55` | `ServiceStatusCommand` | Service Status | `55{output:N}{service_id:N}{flags:N}{zones:N*}!` |
| `56` | `SourceMappingCommand` | Source Mapping | `56{output:N}{device_id:4X}{digital_output:N}{source:N}{zones:N*}!` |
| `57` | `ExtensibleCommandHandlingCommand` | Arbitrary and extensible command handling | `57{output:N}{purpose:N}{command:N}{payload:hex?}!` |
| `58` | `UndocumentedHostIdentityCommand` | Observed host identity request | `58FF00!` |
| `58` | `UndocumentedHostIdentityCommandResponse` | Observed host identity response | `58FF00{guid:uuid}{mac:12X}{detail:hex}!` |

## Obsolete And Unused Status

The manufacturer marks these commands obsolete, unused, or reserved. Obsolete
commands are still registered so captured manufacturer rows can be decoded, but
their command classes carry `COMMAND_STATUS = "obsolete"` and a note.

| Byte | Manufacturer name |
| --- | --- |
| `0A` | Report Error |
| `0B` | Emulate key press on Keypad |
| `0E` | Preset Selection / Status, old form |
| `0F` | Link zone pair |
| `24`, `A4` | Request PCM capabilities and response |
| `25`, `A5` | PCM Stream and response |
| `36` | Party mode select/deselect |
| `37` | Party mode configuration |
| `45` | User accounts |
| `1A` | Unused |
| `1F` | No longer in use |
| `60`-`6F` | Reserved undocumented commands, not modeled as named functions |
| `70`-`7F` | Unused range, not modeled as named functions |

## Source Selectors

Source selector bytes are logical source IDs. They are not physical display
order, reversed physical indexes, or one-hot masks. Source selection status can
set bit 7 to indicate the zone should turn on. Values `40`-`4F` set bit 6 for
audio-only local source selection and should be compared as `00`-`0F`.
Source-selection reports may include a second source byte when the active source
has both local and distributed IDs. Source-name rows may expose newer eSeries
selectors outside the original local/distributed ranges, such as casting
selectors `50`-`52`.

No no-source selector is documented. Omitting the source byte makes `03` a read
request, while `00` selects a real local input; clients must not invent a clear
value from output sentinels such as `FC` or `FF`.

### M6250 Observed Hardware Sources

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

### MA6 Observed Hardware Sources

| Selector | Default label |
| --- | --- |
| `00` | Analog 2 |
| `01` | Analog 3 |
| `02` | Analog 4 |
| `03` | Analog 1 |
| `04` | Coaxial 1 |
| `05` | Player_A |
| `06` | Player_B |
| `07` | Player_C |
| `08` | Coaxial 2 |
| `09` | Optical 1 |
| `0A` | Optical 2 |
| `0B` | Casting_1 |
| `0C` | Casting_2 |
| `0D` | Casting_3 |
| `0E` | Casting_4 |
| `0F` | Casting_5 |
| `50` | Casting_6 |
| `51` | Casting_7 |
| `52` | Casting_8 |

Distributed/eAudioCast source selector convention is `20`-`3F`; observed M6250
tables accepted `20`-`27`.

## Observed Stack

Read-only probes against `10.1.0.109:17037` and `10.1.0.200:17037` observed:

| Device | Device ID | Model | Firmware | Outputs | MAC | GUID |
| --- | --- | --- | --- | --- | --- | --- |
| M6250 | `00D4` | `B0` | `06` | `01`-`08` | `ACE14F0055B4` | `674e1900-f8a9-f6be-a465-3d0fbee12977` |
| M6250 | `00DC` | `B0` | `06` | `09`-`10` hex | `ACE14F0055BC` | `ed6cd4b9-60a8-e845-b485-ace14f0055bc` |

The MA6 source table above is retained from earlier hardware captures.
