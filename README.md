# Virtual Autonomic MA6

This project provides a virtual MA6-like device and client that use command names from:

- Autonomic Media Server Control Protocol (AMSCP)
- MRAD Communications on MAS

It focuses on playback, zones, sources, and current state (no music library deep browse actions beyond zone/source listing).

## Default ports

- AMSCP TCP: `5004`
- MRAD TCP: `5005`
- UDP discovery: `5006`

## Initialization sequence

Client initialization sends:

1. `SetClientType <type>`
2. `SetClientVersion <version>`
3. `SetHost <host-or-name>`
4. `SetXmlMode Lists`
5. `SetEncoding 65001`
6. `SetInstance <instance>`
7. `SubscribeEvents true`

## Implemented command support

### Session / common
- `SetClientType`
- `SetClientVersion`
- `SetHost`
- `SetXmlMode` (`None` / `Lists`)
- `SetEncoding`
- `SetInstance`
- `SubscribeEvents`
- `SetOption`

### Zone / source list and selection
- `BrowseZones <start> <count>`
- `BrowseAllZones`
- `BrowseSources`
- `BrowseAllSources`
- `SetZone Id=<id>` / `SetZone Guid=<guid>` / `SetZone Name=<name>`
- `SetSource Id=<id>` / `SetSource Guid=<guid>` / `SetSource Name=<name>`

### Playback / control
- Direct playback commands: `Play`, `Pause`, `Stop`, `Next`, `Previous`, `SkipNext`, `SkipPrevious`, `Back`
- `Volume <vol>`
- `VolumeUp`
- `VolumeDown`
- `Mute <true|false|toggle>`

### Status
- `GetStatus`
- `MRAD.GetStatus`

## Python usage

```python
from autonomic_ma6 import MA6Client

client = MA6Client("192.168.1.50")
client.initialize()

zones = client.list_zones().zones   # Pydantic typed XML response
sources = client.list_sources().sources

client.select_zone(guid=zones[0].zoneGuid)
client.select_source(guid=sources[0].sourceGuid)
client.volume(45)
client.play()

print(client.get_status().model_dump())
```

## Tests

```bash
pytest -q
```
