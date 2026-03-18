from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from .models import Guid, MA6State, SourceState, ZoneState


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = RLock()
        self.state = self._load()

    @staticmethod
    def _guid(seed: str) -> Guid:
        return Guid(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"virtual-ma6-{seed}")))

    @classmethod
    def default_state(cls) -> MA6State:
        sources = {
            "1": SourceState(source_id="1", source_guid=cls._guid("source-1"), source_name="Streamer", source_type="stream"),
            "2": SourceState(source_id="2", source_guid=cls._guid("source-2"), source_name="Tuner", source_type="radio"),
            "3": SourceState(source_id="3", source_guid=cls._guid("source-3"), source_name="AUX", source_type="line-in"),
        }
        default_source_guid = sources["1"].source_guid
        zones = {
            str(i): ZoneState(
                zone_id=str(i),
                zone_guid=cls._guid(f"zone-{i}"),
                zone_name=f"Zone {i}",
                source_guid=default_source_guid,
            )
            for i in range(1, 7)
        }
        return MA6State(zones=zones, sources=sources)

    def _load(self) -> MA6State:
        if not self.path.exists():
            state = self.default_state()
            self._save_no_lock(state)
            return state

        payload = json.loads(self.path.read_text())
        return MA6State.model_validate(payload)

    def _save_no_lock(self, state: MA6State) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state.model_dump(), indent=2, sort_keys=True))

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return self.state.model_dump()

    def save(self) -> None:
        with self._lock:
            self._save_no_lock(self.state)

    def get_zone(self, *, zone_id: str | None = None, zone_guid: Guid | str | None = None, zone_name: str | None = None) -> ZoneState:
        with self._lock:
            if zone_id and zone_id in self.state.zones:
                return self.state.zones[zone_id]
            for zone in self.state.zones.values():
                if zone_guid and str(zone.zone_guid).lower() == str(zone_guid).lower():
                    return zone
                if zone_name and zone.zone_name.lower() == zone_name.lower():
                    return zone
            raise KeyError("Unknown zone")

    def get_source(
        self,
        *,
        source_id: str | None = None,
        source_guid: Guid | str | None = None,
        source_name: str | None = None,
    ) -> SourceState:
        with self._lock:
            if source_id and source_id in self.state.sources:
                return self.state.sources[source_id]
            for source in self.state.sources.values():
                if source_guid and str(source.source_guid).lower() == str(source_guid).lower():
                    return source
                if source_name and source.source_name.lower() == source_name.lower():
                    return source
            raise KeyError("Unknown source")

    def set_zone(self, zone: ZoneState, **changes: Any) -> ZoneState:
        with self._lock:
            updated = zone.model_copy(update=changes)
            self.state.zones[zone.zone_id] = updated
            self._save_no_lock(self.state)
            return updated
