from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

TAutonomicItem = TypeVar("TAutonomicItem", bound="AutonomicItem")


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
        return int(value) if value not in (None, "") else None

    @property
    def start(self) -> int | None:
        value = self.attributes.get("start") or self.attributes.get("Start")
        return int(value) if value not in (None, "") else None

    @property
    def more(self) -> bool | None:
        value = self.attributes.get("more") or self.attributes.get("More")
        if value is None:
            return None
        return value.lower() in {"1", "true", "yes", "more"}


class AutonomicItem(BaseModel):
    """Typed item returned by ergonomic list APIs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str | None = None
    guid: str | None = None
    name: str | None = None
    kind: str
    attributes: dict[str, str] = Field(default_factory=dict)
    raw_xml: str | None = None

    _client: Any = PrivateAttr(default=None)

    @property
    def ref(self) -> str:
        ref = self.guid or self.id or self.name
        if ref is None:
            raise ValueError(f"{self.kind} has no guid, id, or name")
        return str(ref)

    def bind(self: TAutonomicItem, client: Any) -> TAutonomicItem:
        self._client = client
        return self

    def __hash__(self) -> int:
        return hash((self.kind, self.guid, self.id, self.name))

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError(f"{self.kind} is not bound to an Autonomic client")
        return self._client

    @classmethod
    def from_browse_item(cls, item: BrowseItem, *, client: Any = None) -> "AutonomicItem":
        model = cls(
            kind=item.kind,
            id=item.id,
            guid=item.guid,
            name=item.name,
            attributes=dict(item.attributes),
            raw_xml=item.raw_xml,
        )
        if client is not None:
            model.bind(client)
        return model


class AutonomicSource(AutonomicItem):
    """Typed Autonomic source with convenience assignment helpers."""

    kind: str = "Source"
    address: str | None = None
    disabled: bool | None = None

    @classmethod
    def from_browse_item(cls, item: BrowseItem, *, client: Any = None) -> "AutonomicSource":
        attrs = dict(item.attributes)
        model = cls(
            kind=item.kind,
            id=item.id,
            guid=item.guid,
            name=item.name,
            address=attrs.get("address"),
            disabled=_disabled_attr(attrs),
            attributes=attrs,
            raw_xml=item.raw_xml,
        )
        if client is not None:
            model.bind(client)
        return model

    def select(self, output: Any = None, *, include_group: bool = False) -> Any:
        return self._require_client().select_source(self, output, include_group=include_group)

    def assign_to(self, output: Any, *, include_group: bool = False) -> Any:
        return self._require_client().assign_source_to_output(self, output, include_group=include_group)

    def assign_to_outputs(self, outputs: Iterable[Any]) -> Any:
        return self._require_client().assign_source_to_outputs(self, outputs)

    def assign_to_all_outputs(self) -> Any:
        return self._require_client().assign_source_to_all_outputs(self)


class AutonomicOutput(AutonomicItem):
    """Typed Autonomic output with bound control helpers."""

    kind: str = "Output"
    address: str | None = None
    disabled: bool | None = None
    is_on: bool | None = None
    muted: bool | None = None
    volume: int | None = None
    source_id: str | None = None
    source_guid: str | None = None
    source_name: str | None = None

    @classmethod
    def from_browse_item(cls, item: BrowseItem, *, client: Any = None) -> "AutonomicOutput":
        attrs = dict(item.attributes)
        model = cls(
            kind=item.kind,
            id=item.id,
            guid=item.guid,
            name=item.name,
            address=attrs.get("address"),
            disabled=_disabled_attr(attrs),
            is_on=_bool_attr(attrs, "isOn", "PowerOn"),
            muted=_bool_attr(attrs, "mute", "Mute", "muted", "Muted"),
            volume=_int_attr(attrs, "volume", "Volume"),
            source_id=_first_attr(attrs, "sourceId", "SourceId", "sId"),
            source_guid=_first_attr(attrs, "sGuid", "sourceGuid", "SourceGuid"),
            source_name=_first_attr(attrs, "sourceName", "SourceName"),
            attributes=attrs,
            raw_xml=item.raw_xml,
        )
        if client is not None:
            model.bind(client)
        return model

    def select(self) -> Any:
        return self._require_client().select_output(self)

    def set_source(self, source: Any, *, include_group: bool = False) -> Any:
        return self._require_client().assign_source_to_output(source, self, include_group=include_group)

    def assign_source(self, source: Any, *, include_group: bool = False) -> Any:
        return self.set_source(source, include_group=include_group)

    def assign(self, source: Any, *, include_group: bool = False) -> Any:
        return self.set_source(source, include_group=include_group)

    def set_volume(self, value: int) -> Any:
        return self._require_client().set_output_volume(self, value)

    def volume_up(self) -> Any:
        return self._require_client().volume_up(self)

    def volume_down(self) -> Any:
        return self._require_client().volume_down(self)

    def mute(self, state: bool | str = True) -> Any:
        return self._require_client().set_output_mute(self, state)

    def unmute(self) -> Any:
        return self._require_client().set_output_mute(self, False)

    def toggle_mute(self) -> Any:
        return self._require_client().set_output_mute(self, "toggle")

    def set_power(self, is_on: bool | str = True) -> Any:
        return self._require_client().set_output_power(self, is_on)

    def set_is_on(self, is_on: bool | str = True) -> Any:
        return self._require_client().set_output_is_on(self, is_on)


class AutonomicOutputGroup(BaseModel):
    """A fanout proxy for controlling a group of Autonomic outputs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    outputs: list[AutonomicOutput] = Field(default_factory=list)

    _client: Any = PrivateAttr(default=None)

    def bind(self, client: Any) -> "AutonomicOutputGroup":
        self._client = client
        self.outputs = [output.bind(client) for output in self.outputs]
        return self

    def __iter__(self) -> Iterator[AutonomicOutput]:
        return iter(self.outputs)

    def __len__(self) -> int:
        return len(self.outputs)

    def __getitem__(self, index: int | slice) -> AutonomicOutput | list[AutonomicOutput]:
        return self.outputs[index]

    def _fanout(self, operation: Callable[[AutonomicOutput], Any], *, flatten_lists: bool = False) -> list[Any]:
        results: list[Any] = []
        for output in self.outputs:
            result = operation(output)
            if flatten_lists and isinstance(result, list):
                results.extend(result)
            else:
                results.append(result)
        return results

    def select(self) -> list[Any]:
        return self._fanout(lambda output: output.select())

    def set_source(self, source: Any, *, include_group: bool = False) -> list[Any]:
        return self._fanout(lambda output: output.set_source(source, include_group=include_group), flatten_lists=True)

    def assign_source(self, source: Any, *, include_group: bool = False) -> list[Any]:
        return self.set_source(source, include_group=include_group)

    def assign(self, source: Any, *, include_group: bool = False) -> list[Any]:
        return self.set_source(source, include_group=include_group)

    def set_volume(self, value: int) -> list[Any]:
        return self._fanout(lambda output: output.set_volume(value))

    def volume_up(self) -> list[Any]:
        return self._fanout(lambda output: output.volume_up())

    def volume_down(self) -> list[Any]:
        return self._fanout(lambda output: output.volume_down())

    def mute(self, state: bool | str = True) -> list[Any]:
        return self._fanout(lambda output: output.mute(state))

    def unmute(self) -> list[Any]:
        return self._fanout(lambda output: output.unmute())

    def toggle_mute(self) -> list[Any]:
        return self._fanout(lambda output: output.toggle_mute())

    def set_power(self, is_on: bool | str = True) -> list[Any]:
        return self._fanout(lambda output: output.set_power(is_on))

    def set_is_on(self, is_on: bool | str = True) -> list[Any]:
        return self._fanout(lambda output: output.set_is_on(is_on))


def object_ref(value: Any) -> Any:
    if isinstance(value, AutonomicItem):
        return value.ref
    return value


def source_ref(value: Any) -> Any:
    if isinstance(value, AutonomicSource):
        return value.guid or value.id or value.name
    return object_ref(value)


def output_ref(value: Any) -> Any:
    if isinstance(value, AutonomicOutput):
        return value.id or value.guid or value.name
    return object_ref(value)


def source_id(value: Any) -> int:
    if isinstance(value, AutonomicSource):
        if value.id is None:
            raise ValueError(f"Source has no numeric id: {value}")
        return int(value.id)
    return int(value)


def omit_disabled(items: Iterable[TAutonomicItem], *, include_disabled: bool = False) -> list[TAutonomicItem]:
    if include_disabled:
        return list(items)
    return [item for item in items if not getattr(item, "disabled", None)]


def _first_attr(attrs: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = attrs.get(key)
        if value not in (None, ""):
            return value
    return None


def _disabled_attr(attrs: dict[str, str]) -> bool | None:
    disabled = _first_attr(
        attrs,
        "disabled",
        "Disabled",
        "isDisabled",
        "IsDisabled",
        "hidden",
        "Hidden",
        "isHidden",
        "IsHidden",
        "invalid",
        "Invalid",
        "isInvalid",
        "IsInvalid",
    )
    if disabled is not None:
        return _truthy(disabled, extra_true={"disabled", "hidden", "invalid"})

    enabled = _first_attr(attrs, "enabled", "Enabled", "isEnabled", "IsEnabled")
    if enabled is not None:
        return not _truthy(enabled)

    available = _first_attr(
        attrs,
        "available",
        "Available",
        "isAvailable",
        "IsAvailable",
        "sourceAvailable",
        "SourceAvailable",
        "zoneAvailable",
        "ZoneAvailable",
        "avail",
        "Avail",
    )
    if available is not None:
        return not _truthy(available)

    return None


def _truthy(value: object, *, extra_true: set[str] | None = None) -> bool:
    truthy = {"1", "true", "yes", "on", "enabled", "available", "valid"}
    if extra_true:
        truthy.update(extra_true)
    return str(value).strip().lower() in truthy


def _bool_attr(attrs: dict[str, str], *keys: str) -> bool | None:
    value = _first_attr(attrs, *keys)
    if value is None:
        return None
    return _truthy(value)


def _int_attr(attrs: dict[str, str], *keys: str) -> int | None:
    value = _first_attr(attrs, *keys)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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

    def get(self, source: str, name: str, default: Any = None) -> str | Any:
        return self.by_source.get(source, {}).get(name, default)
