from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .models import CommandAck, SourceState, ZoneState
from .state import StateStore


class ProtocolError(ValueError):
    pass


def _kv_ok(message: str = "OK") -> str:
    return CommandAck(value=message).value


def _xml_to_str(elem: ET.Element) -> str:
    return ET.tostring(elem, encoding="unicode")


def _parse_pairs(tokens: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in tokens:
        if "=" in token:
            k, v = token.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _zone_attrs(zone: ZoneState) -> dict[str, str]:
    return {
        "zoneId": zone.zone_id,
        "zoneGuid": str(zone.zone_guid),
        "zoneName": zone.zone_name,
        "power": str(zone.power),
        "volume": str(zone.volume),
        "mute": str(zone.muted),
        "playState": zone.play_state,
        "sourceGuid": str(zone.source_guid),
        "trackTime": str(zone.track_time),
        "trackDuration": str(zone.track_duration),
    }


def _source_attrs(source: SourceState) -> dict[str, str]:
    return {
        "sourceId": source.source_id,
        "sourceGuid": str(source.source_guid),
        "sourceName": source.source_name,
        "sourceType": source.source_type,
        "mmsInstance": source.mms_instance,
        "mmsAddress": source.mms_addr,
    }


def _build_list_xml(root_name: str, item_name: str, attrs: list[dict[str, str]]) -> str:
    root = ET.Element(root_name)
    for item_attrs in attrs:
        ET.SubElement(root, item_name, item_attrs)
    return _xml_to_str(root)


def _apply_playback(cmd: str) -> dict[str, Any]:
    c = cmd.lower()
    if c == "play":
        return {"play_state": "Playing"}
    if c == "pause":
        return {"play_state": "Paused"}
    if c == "stop":
        return {"play_state": "Stopped"}
    if c in {"next", "previous", "skipnext", "skipprevious", "back"}:
        return {"play_state": "Playing"}
    raise ProtocolError(f"Unsupported playback control: {cmd}")


def _handle_common(store: StateStore, command: str, session: dict[str, Any]) -> str | None:
    parts = command.split()
    if not parts:
        raise ProtocolError("Empty command")

    cmd = parts[0]
    args = parts[1:]

    if cmd in {"SetClientType", "SetClientVersion", "SetHost", "SetInstance", "SetEncoding", "SetOption"}:
        session[cmd] = " ".join(args)
        return _kv_ok()

    if cmd == "SetXmlMode":
        mode = args[0] if args else "None"
        if mode not in {"None", "Lists"}:
            raise ProtocolError("SetXmlMode must be None or Lists")
        session["SetXmlMode"] = mode
        return _kv_ok()

    if cmd == "SubscribeEvents":
        session["SubscribeEvents"] = " ".join(args) if args else "true"
        return _kv_ok()

    if cmd in {"BrowseZones", "BrowseAllZones"}:
        zones = list(store.state.zones.values())
        if cmd == "BrowseZones" and len(args) >= 2:
            start = max(1, int(args[0]))
            count = max(0, int(args[1]))
            zones = zones[start - 1 : start - 1 + count]
        return _build_list_xml("Zones", "Zone", [_zone_attrs(z) for z in zones])

    if cmd in {"BrowseSources", "BrowseAllSources"}:
        sources = list(store.state.sources.values())
        return _build_list_xml("Sources", "Source", [_source_attrs(s) for s in sources])

    if cmd == "SetZone":
        kv = _parse_pairs(args)
        zone = store.get_zone(zone_id=kv.get("Id"), zone_guid=kv.get("Guid"), zone_name=kv.get("Name"))
        session["active_zone_guid"] = str(zone.zone_guid)
        return _kv_ok()

    if cmd == "SetSource":
        kv = _parse_pairs(args)
        source = store.get_source(source_id=kv.get("Id"), source_guid=kv.get("Guid"), source_name=kv.get("Name"))
        zone_guid = session.get("active_zone_guid")
        if not zone_guid:
            raise ProtocolError("SetZone must be called before SetSource")
        zone = store.get_zone(zone_guid=zone_guid)
        store.set_zone(zone, source_guid=source.source_guid)
        return _kv_ok()

    if cmd == "Volume":
        zone_guid = session.get("active_zone_guid")
        if not zone_guid:
            raise ProtocolError("SetZone must be called before Volume")
        zone = store.get_zone(zone_guid=zone_guid)
        vol = max(0, min(100, int(args[0])))
        store.set_zone(zone, volume=vol)
        return _kv_ok()

    if cmd in {"VolumeUp", "VolumeDown"}:
        zone_guid = session.get("active_zone_guid")
        if not zone_guid:
            raise ProtocolError("SetZone must be called before volume controls")
        zone = store.get_zone(zone_guid=zone_guid)
        delta = 1 if cmd == "VolumeUp" else -1
        store.set_zone(zone, volume=max(0, min(100, zone.volume + delta)))
        return _kv_ok()

    if cmd == "Mute":
        zone_guid = session.get("active_zone_guid")
        if not zone_guid:
            raise ProtocolError("SetZone must be called before Mute")
        zone = store.get_zone(zone_guid=zone_guid)
        state = (args[0] if args else "toggle").lower()
        muted = (not zone.muted) if state == "toggle" else state in {"true", "1", "on"}
        store.set_zone(zone, muted=muted)
        return _kv_ok()

    if cmd in {"Play", "Pause", "Stop", "Next", "Previous", "SkipNext", "SkipPrevious", "Back"}:
        zone_guid = session.get("active_zone_guid")
        if not zone_guid:
            raise ProtocolError("SetZone must be called before playback controls")
        zone = store.get_zone(zone_guid=zone_guid)
        store.set_zone(zone, **_apply_playback(cmd))
        return _kv_ok()

    if cmd == "MediaControl":
        raise ProtocolError("MediaControl is event-oriented; use direct commands like Play, Pause, Stop, Next, or Previous")

    if cmd in {"GetStatus", "MRAD.GetStatus"}:
        zone_guid = session.get("active_zone_guid")
        zone = store.get_zone(zone_guid=zone_guid) if zone_guid else next(iter(store.state.zones.values()))
        source = store.get_source(source_guid=zone.source_guid)
        root = ET.Element(
            "Status",
            {
                "ActiveZone": zone.zone_name,
                "ActiveSource": source.source_name,
                "ZoneGuid": str(zone.zone_guid),
                "SourceGuid": str(source.source_guid),
                "Volume": str(zone.volume),
                "Mute": str(zone.muted),
                "PowerOn": str(zone.power),
                "PlayState": zone.play_state,
                "TrackTime": str(zone.track_time),
                "TrackDuration": str(zone.track_duration),
                "MCSWebPort": "5004",
            },
        )
        ET.SubElement(root, "Zone", _zone_attrs(zone))
        ET.SubElement(root, "Source", _source_attrs(source))
        return _xml_to_str(root)

    return None


def handle_amscp_command(store: StateStore, command: str, session: dict[str, Any]) -> str:
    response = _handle_common(store, command.strip(), session)
    if response is not None:
        return response
    raise ProtocolError(f"Unknown AMSCP command: {command}")


def handle_mrad_command(store: StateStore, command: str, session: dict[str, Any]) -> str:
    cleaned = command.strip()
    if cleaned.startswith("MRAD."):
        cleaned = cleaned[5:]
    response = _handle_common(store, cleaned, session)
    if response is not None:
        return response
    raise ProtocolError(f"Unknown MRAD command: {command}")
