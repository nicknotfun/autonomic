# Shared typed containers for line-protocol events and browse responses.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar, overload

TDefault = TypeVar("TDefault")


@dataclass(frozen=True)
class Event:
    reason: str
    source: str
    name: str
    value: str
    namespace: str | None = None
    raw: str = ""

    @property
    def is_report(self) -> bool:
        return self.reason == "ReportState"

    @property
    def is_change(self) -> bool:
        return self.reason == "StateChanged"


@dataclass(frozen=True)
class BrowseItem:
    kind: str
    attributes: dict[str, str]
    children: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    raw_xml: str | None = None

    @property
    def guid(self) -> str | None:
        for key in ("guid", "Guid", "zoneGuid", "ZoneGuid", "sourceGuid", "SourceGuid", "sGuid"):
            if key in self.attributes and self.attributes[key]:
                return self.attributes[key]
        return None

    @property
    def id(self) -> str | None:
        for key in ("id", "Id", "zoneId", "ZoneId", "sourceId", "SourceId", "sId"):
            if key in self.attributes and self.attributes[key]:
                return self.attributes[key]
        return None

    @property
    def name(self) -> str | None:
        display_attr = self.attributes.get("dna")
        if display_attr and self.attributes.get(display_attr):
            return self.attributes[display_attr]
        for key in ("name", "Name", "zoneName", "ZoneName", "sourceName", "SourceName"):
            if key in self.attributes:
                return self.attributes[key]
        return None

    def get_bool(self, key: str, default: bool | None = None) -> bool | None:
        value = self.attributes.get(key)
        if value is None:
            return default
        return value.lower() in {"1", "true", "yes", "on"}

    def get_int(self, key: str, default: int | None = None) -> int | None:
        value = self.attributes.get(key)
        if value is None or value == "":
            return default
        return int(value)


@dataclass(frozen=True)
class BrowseResponse:
    kind: str
    attributes: dict[str, str]
    items: list[BrowseItem]
    raw: str
    terminator: str | None = None

    @property
    def total(self) -> int | None:
        value = self.attributes.get("total") or self.attributes.get("Total")
        if value is None or value == "":
            return None
        return int(value)

    @property
    def start(self) -> int | None:
        value = self.attributes.get("start") or self.attributes.get("Start")
        if value is None or value == "":
            return None
        return int(value)

    @property
    def more(self) -> bool | None:
        value = self.attributes.get("more") or self.attributes.get("More")
        if value is None:
            return None
        return value.lower() in {"1", "true", "yes", "more"}


@dataclass(frozen=True)
class CommandResponse:
    command: str
    lines: list[str]
    events: list[Event] = field(default_factory=list)
    payload: BrowseResponse | None = None

    @property
    def first_line(self) -> str | None:
        return self.lines[0] if self.lines else None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class StatusSnapshot:
    events: list[Event]
    by_source: dict[str, dict[str, str]]
    raw_lines: list[str] = field(default_factory=list)

    @overload
    def get(self, source: str, name: str) -> str | None: ...

    @overload
    def get(self, source: str, name: str, default: TDefault) -> str | TDefault: ...

    def get(self, source: str, name: str, default: TDefault | None = None) -> str | TDefault | None:
        return self.by_source.get(source, {}).get(name, default)
