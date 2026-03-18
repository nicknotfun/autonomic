from __future__ import annotations

from typing import Any, NewType

from pydantic import BaseModel, ConfigDict, Field


Guid = NewType("Guid", str)


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ZoneState(FlexibleModel):
    zone_id: str
    zone_guid: Guid
    zone_name: str
    power: bool = False
    source_guid: Guid | str = ""
    volume: int = 30
    muted: bool = False
    play_state: str = "Stopped"
    track_time: int = 0
    track_duration: int = 0


class SourceState(FlexibleModel):
    source_id: str
    source_guid: Guid
    source_name: str
    source_type: str = "stream"
    mms_instance: str = "Player_A"
    mms_addr: str = "127.0.0.1:5004"


class MA6State(FlexibleModel):
    zones: dict[str, ZoneState] = Field(default_factory=dict)
    sources: dict[str, SourceState] = Field(default_factory=dict)


class DiscoveryResponse(FlexibleModel):
    device: str
    amscp_port: int
    host: str


class ZoneXml(FlexibleModel):
    zoneId: str
    zoneGuid: Guid
    zoneName: str
    power: str
    volume: str
    mute: str
    playState: str
    sourceGuid: Guid | str
    trackTime: str
    trackDuration: str


class SourceXml(FlexibleModel):
    sourceId: str
    sourceGuid: Guid
    sourceName: str
    sourceType: str
    mmsInstance: str
    mmsAddress: str


class ZonesListResponse(FlexibleModel):
    zones: list[ZoneXml]


class SourcesListResponse(FlexibleModel):
    sources: list[SourceXml]


class StatusResponse(FlexibleModel):
    ActiveZone: str
    ActiveSource: str
    ZoneGuid: Guid
    SourceGuid: Guid
    Volume: str
    Mute: str
    PowerOn: str
    PlayState: str
    TrackTime: str
    TrackDuration: str
    MCSWebPort: str


class CommandAck(FlexibleModel):
    value: str = "OK"


def model_to_dict(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(model, BaseModel):
        return model.model_dump()
    return model
