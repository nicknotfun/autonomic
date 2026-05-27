# Public Pydantic models for outputs, sources, groups, and status snapshots.
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Protocol, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .model_attributes import bool_attr as _bool_attr
from .model_attributes import disabled_attr as _disabled_attr
from .model_attributes import first_attr as _first_attr
from .model_attributes import int_attr as _int_attr
from .model_attributes import zone_child_attrs as _zone_child_attrs
from .protocol_types import BrowseItem, BrowseResponse, CommandResponse, Event, StatusSnapshot

TAutonomicItem = TypeVar("TAutonomicItem", bound="AutonomicItem")
AutonomicRef: TypeAlias = str | int
SourceRef: TypeAlias = AutonomicRef | "AutonomicSource"
OutputRef: TypeAlias = AutonomicRef | "AutonomicOutput"
ControlResult: TypeAlias = (
    CommandResponse
    | str
    | int
    | float
    | bool
    | "AutonomicOutput"
    | None
    | list[CommandResponse]
    | list[str]
    | Sequence[CommandResponse | str]
)


class AutonomicControlClient(Protocol):
    """Structural interface required by bound model convenience methods."""

    def select_source(
        self,
        source: SourceRef,
        output: OutputRef | None = None,
        /,
        *,
        include_group: bool = False,
    ) -> ControlResult: ...

    def assign_source_to_output(
        self,
        source: SourceRef,
        output: OutputRef,
        /,
        *,
        include_group: bool = False,
    ) -> ControlResult: ...

    def assign_source_to_outputs(self, source: SourceRef, outputs: Iterable[OutputRef], /) -> ControlResult: ...

    def assign_source_to_all_outputs(self, source: SourceRef, /) -> ControlResult: ...

    def set_source_name(self, source: SourceRef, name: str, /) -> ControlResult: ...

    def set_source_icon(self, source: SourceRef, icon: str, /) -> ControlResult: ...

    def select_output(self, output: OutputRef, /) -> ControlResult: ...

    def get_output_status(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_volume(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_max_volume(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_mute(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_power(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_source_id(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_source_name(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_bass(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_treble(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_balance(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_gain(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_delay(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_loudness(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_mono_downmix(self, output: OutputRef, /) -> ControlResult: ...

    def read_output_power_on_volume(self, output: OutputRef, /) -> ControlResult: ...

    def set_output_volume(self, output: OutputRef, value: float, /) -> ControlResult: ...

    def set_output_max_volume(self, output: OutputRef, value: float, /) -> ControlResult: ...

    def output_max_volume_up(self, output: OutputRef, step: float = 1.0, /) -> ControlResult: ...

    def output_max_volume_down(self, output: OutputRef, step: float = 1.0, /) -> ControlResult: ...

    def set_output_bass(self, output: OutputRef, value: int, /) -> ControlResult: ...

    def output_bass_up(self, output: OutputRef | None = None, /) -> ControlResult: ...

    def output_bass_down(self, output: OutputRef | None = None, /) -> ControlResult: ...

    def set_output_treble(self, output: OutputRef, value: int, /) -> ControlResult: ...

    def output_treble_up(self, output: OutputRef | None = None, /) -> ControlResult: ...

    def output_treble_down(self, output: OutputRef | None = None, /) -> ControlResult: ...

    def set_output_balance(self, output: OutputRef, value: int, /) -> ControlResult: ...

    def output_balance_left(self, output: OutputRef | None = None, /) -> ControlResult: ...

    def output_balance_right(self, output: OutputRef | None = None, /) -> ControlResult: ...

    def set_output_gain(self, output: OutputRef, value: int, /) -> ControlResult: ...

    def output_gain_up(self, output: OutputRef | None = None, /) -> ControlResult: ...

    def output_gain_down(self, output: OutputRef | None = None, /) -> ControlResult: ...

    def set_output_delay(self, output: OutputRef, value_ms: int, /) -> ControlResult: ...

    def output_delay_up(self, output: OutputRef | None = None, step_ms: int = 5, /) -> ControlResult: ...

    def output_delay_down(self, output: OutputRef | None = None, step_ms: int = 5, /) -> ControlResult: ...

    def set_output_loudness(self, output: OutputRef, enabled: bool | str, /) -> ControlResult: ...

    def set_output_mono_downmix(self, output: OutputRef, enabled: bool | str, /) -> ControlResult: ...

    def set_output_power_on_volume(self, output: OutputRef, value: float, /) -> ControlResult: ...

    def set_output_name(self, output: OutputRef, name: str, /) -> ControlResult: ...

    def set_output_icon(self, output: OutputRef, icon: str, /) -> ControlResult: ...

    def volume_up(self, zone: OutputRef | None = None, /) -> ControlResult: ...

    def volume_down(self, zone: OutputRef | None = None, /) -> ControlResult: ...

    def set_output_mute(self, output: OutputRef, state: bool | str = "toggle", /) -> ControlResult: ...

    def set_output_power(self, output: OutputRef, is_on: bool | str = True, /) -> ControlResult: ...

    def set_output_is_on(self, output: OutputRef, is_on: bool | str = True, /) -> ControlResult: ...


class AutonomicItem(BaseModel):
    """Typed item returned by ergonomic list APIs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str | None = None
    guid: str | None = None
    name: str | None = None
    kind: str
    attributes: dict[str, str] = Field(default_factory=dict)
    raw_xml: str | None = None

    _client: AutonomicControlClient | None = PrivateAttr(default=None)

    @property
    def ref(self) -> str:
        ref = self.guid or self.id or self.name
        if ref is None:
            raise ValueError(f"{self.kind} has no guid, id, or name")
        return str(ref)

    def bind(self: TAutonomicItem, client: AutonomicControlClient) -> TAutonomicItem:
        self._client = client
        return self

    def __hash__(self) -> int:
        return hash((self.kind, self.guid, self.id, self.name))

    def _require_client(self) -> AutonomicControlClient:
        if self._client is None:
            raise RuntimeError(f"{self.kind} is not bound to an Autonomic client")
        return self._client

    @classmethod
    def from_browse_item(cls, item: BrowseItem, *, client: AutonomicControlClient | None = None) -> "AutonomicItem":
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
    def from_browse_item(cls, item: BrowseItem, *, client: AutonomicControlClient | None = None) -> "AutonomicSource":
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

    def select(self, output: OutputRef | None = None, *, include_group: bool = False) -> ControlResult:
        return self._require_client().select_source(self, output, include_group=include_group)

    def assign_to(self, output: OutputRef, *, include_group: bool = False) -> ControlResult:
        return self._require_client().assign_source_to_output(self, output, include_group=include_group)

    def assign_to_outputs(self, outputs: Iterable[OutputRef]) -> ControlResult:
        return self._require_client().assign_source_to_outputs(self, outputs)

    def assign_to_all_outputs(self) -> ControlResult:
        return self._require_client().assign_source_to_all_outputs(self)

    def set_name(self, name: str) -> ControlResult:
        return self._require_client().set_source_name(self, name)

    def set_icon(self, icon: str) -> ControlResult:
        return self._require_client().set_source_icon(self, icon)


class AutonomicOutput(AutonomicItem):
    """Typed Autonomic output with bound control helpers."""

    kind: str = "Output"
    address: str | None = None
    disabled: bool | None = None
    is_on: bool | None = None
    muted: bool | None = None
    volume: float | None = None
    min_volume: float | None = None
    min_min_volume: float | None = None
    max_volume: float | None = None
    max_max_volume: float | None = None
    bass: int | None = None
    treble: int | None = None
    balance: int | None = None
    gain: int | None = None
    delay_ms: int | None = None
    loudness: bool | None = None
    mono_downmix: bool | None = None
    power_on_volume: float | None = None
    adjusting_volume: bool | None = None
    device_type: str | None = None
    do_not_disturb: bool | None = None
    gain_mode: str | None = None
    icon_id: str | None = None
    party_mode: str | None = None
    source_id: str | None = None
    source_guid: str | None = None
    source_name: str | None = None
    qualified_source_name: str | None = None
    zone_exclusive_source: bool | None = None
    zone_group_id: str | None = None
    zone_group_name: str | None = None
    zone_group_power: bool | None = None
    zone_group_source: bool | None = None
    zone_group_volume: bool | None = None
    zone_is_locked: bool | None = None

    @classmethod
    def from_browse_item(cls, item: BrowseItem, *, client: AutonomicControlClient | None = None) -> "AutonomicOutput":
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
            min_volume=_int_attr(attrs, "minVolume", "MinVolume"),
            min_min_volume=_int_attr(attrs, "minMinVolume", "MinMinVolume"),
            max_volume=_int_attr(attrs, "maxVolume", "MaxVolume"),
            max_max_volume=_int_attr(attrs, "maxMaxVolume", "MaxMaxVolume"),
            bass=_int_attr(attrs, "bass", "Bass"),
            treble=_int_attr(attrs, "treble", "Treble"),
            balance=_int_attr(attrs, "balance", "Balance"),
            gain=_int_attr(attrs, "gain", "Gain"),
            delay_ms=_int_attr(attrs, "delayMs", "DelayMs"),
            loudness=_bool_attr(attrs, "loudness", "Loudness", "LoudnessEnabled"),
            mono_downmix=_bool_attr(attrs, "monoDownmix", "MonoDownmix"),
            power_on_volume=_int_attr(attrs, "powerOnVolume", "PowerOnVolume"),
            adjusting_volume=_bool_attr(attrs, "adjustingVolume", "AdjustingVolume"),
            device_type=_first_attr(attrs, "deviceType", "DeviceType"),
            do_not_disturb=_bool_attr(attrs, "doNotDisturb", "DoNotDisturb"),
            gain_mode=_first_attr(attrs, "gainMode", "GainMode"),
            icon_id=_first_attr(attrs, "iconId", "IconId"),
            party_mode=_first_attr(attrs, "partyMode", "PartyMode"),
            source_id=_first_attr(attrs, "sourceId", "SourceId", "sId"),
            source_guid=_first_attr(attrs, "sGuid", "sourceGuid", "SourceGuid"),
            source_name=_first_attr(attrs, "sourceName", "SourceName"),
            qualified_source_name=_first_attr(attrs, "qualifiedSourceName", "QualifiedSourceName"),
            zone_exclusive_source=_bool_attr(attrs, "zoneExclusiveSource", "ZoneExclusiveSource"),
            zone_group_id=_first_attr(attrs, "zoneGroupId", "ZoneGroupId"),
            zone_group_name=_first_attr(attrs, "zoneGroupName", "ZoneGroupName"),
            zone_group_power=_bool_attr(attrs, "zoneGroupPower", "ZoneGroupPower"),
            zone_group_source=_bool_attr(attrs, "zoneGroupSource", "ZoneGroupSource"),
            zone_group_volume=_bool_attr(attrs, "zoneGroupVolume", "ZoneGroupVolume"),
            zone_is_locked=_bool_attr(attrs, "zoneIsLocked", "ZoneIsLocked"),
            attributes=attrs,
            raw_xml=item.raw_xml,
        )
        if client is not None:
            model.bind(client)
        return model

    def select(self) -> ControlResult:
        return self._require_client().select_output(self)

    def set_source(self, source: SourceRef, *, include_group: bool = False) -> ControlResult:
        return self._require_client().assign_source_to_output(source, self, include_group=include_group)

    def assign_source(self, source: SourceRef, *, include_group: bool = False) -> ControlResult:
        return self.set_source(source, include_group=include_group)

    def assign(self, source: SourceRef, *, include_group: bool = False) -> ControlResult:
        return self.set_source(source, include_group=include_group)

    def set_volume(self, value: float) -> ControlResult:
        return self._require_client().set_output_volume(self, value)

    def refresh_status(self) -> ControlResult:
        return self._require_client().get_output_status(self)

    def read_volume(self) -> ControlResult:
        return self._require_client().read_output_volume(self)

    def read_max_volume(self) -> ControlResult:
        return self._require_client().read_output_max_volume(self)

    def read_mute(self) -> ControlResult:
        return self._require_client().read_output_mute(self)

    def read_power(self) -> ControlResult:
        return self._require_client().read_output_power(self)

    def read_source_id(self) -> ControlResult:
        return self._require_client().read_output_source_id(self)

    def read_source_name(self) -> ControlResult:
        return self._require_client().read_output_source_name(self)

    def read_bass(self) -> ControlResult:
        return self._require_client().read_output_bass(self)

    def read_treble(self) -> ControlResult:
        return self._require_client().read_output_treble(self)

    def read_balance(self) -> ControlResult:
        return self._require_client().read_output_balance(self)

    def read_gain(self) -> ControlResult:
        return self._require_client().read_output_gain(self)

    def read_delay(self) -> ControlResult:
        return self._require_client().read_output_delay(self)

    def read_loudness(self) -> ControlResult:
        return self._require_client().read_output_loudness(self)

    def read_mono_downmix(self) -> ControlResult:
        return self._require_client().read_output_mono_downmix(self)

    def read_power_on_volume(self) -> ControlResult:
        return self._require_client().read_output_power_on_volume(self)

    def set_max_volume(self, value: float) -> ControlResult:
        return self._require_client().set_output_max_volume(self, value)

    def max_volume_up(self, step: float = 1.0) -> ControlResult:
        return self._require_client().output_max_volume_up(self, step)

    def max_volume_down(self, step: float = 1.0) -> ControlResult:
        return self._require_client().output_max_volume_down(self, step)

    def set_bass(self, value: int) -> ControlResult:
        return self._require_client().set_output_bass(self, value)

    def bass_up(self) -> ControlResult:
        return self._require_client().output_bass_up(self)

    def bass_down(self) -> ControlResult:
        return self._require_client().output_bass_down(self)

    def set_treble(self, value: int) -> ControlResult:
        return self._require_client().set_output_treble(self, value)

    def treble_up(self) -> ControlResult:
        return self._require_client().output_treble_up(self)

    def treble_down(self) -> ControlResult:
        return self._require_client().output_treble_down(self)

    def set_balance(self, value: int) -> ControlResult:
        return self._require_client().set_output_balance(self, value)

    def balance_left(self) -> ControlResult:
        return self._require_client().output_balance_left(self)

    def balance_right(self) -> ControlResult:
        return self._require_client().output_balance_right(self)

    def set_gain(self, value: int) -> ControlResult:
        return self._require_client().set_output_gain(self, value)

    def gain_up(self) -> ControlResult:
        return self._require_client().output_gain_up(self)

    def gain_down(self) -> ControlResult:
        return self._require_client().output_gain_down(self)

    def set_delay(self, value_ms: int) -> ControlResult:
        return self._require_client().set_output_delay(self, value_ms)

    def delay_up(self, step_ms: int = 5) -> ControlResult:
        return self._require_client().output_delay_up(self, step_ms)

    def delay_down(self, step_ms: int = 5) -> ControlResult:
        return self._require_client().output_delay_down(self, step_ms)

    def set_loudness(self, enabled: bool) -> ControlResult:
        return self._require_client().set_output_loudness(self, enabled)

    def set_mono_downmix(self, enabled: bool | str) -> ControlResult:
        return self._require_client().set_output_mono_downmix(self, enabled)

    def set_power_on_volume(self, value: float) -> ControlResult:
        return self._require_client().set_output_power_on_volume(self, value)

    def set_name(self, name: str) -> ControlResult:
        return self._require_client().set_output_name(self, name)

    def set_icon(self, icon: str) -> ControlResult:
        return self._require_client().set_output_icon(self, icon)

    def volume_up(self) -> ControlResult:
        return self._require_client().volume_up(self)

    def volume_down(self) -> ControlResult:
        return self._require_client().volume_down(self)

    def mute(self, state: bool | str = True) -> ControlResult:
        return self._require_client().set_output_mute(self, state)

    def unmute(self) -> ControlResult:
        return self._require_client().set_output_mute(self, False)

    def toggle_mute(self) -> ControlResult:
        return self._require_client().set_output_mute(self, "toggle")

    def set_power(self, is_on: bool | str = True) -> ControlResult:
        return self._require_client().set_output_power(self, is_on)

    def set_is_on(self, is_on: bool | str = True) -> ControlResult:
        return self._require_client().set_output_is_on(self, is_on)


class AutonomicZoneGroup(AutonomicItem):
    """Typed MRAD zone group with volume/source members and available sources."""

    kind: str = "ZoneGroup"
    volume_outputs: list[AutonomicOutput] = Field(default_factory=list)
    source_outputs: list[AutonomicOutput] = Field(default_factory=list)
    sources: list[AutonomicSource] = Field(default_factory=list)

    @classmethod
    def from_browse_item(cls, item: BrowseItem, *, client: AutonomicControlClient | None = None) -> "AutonomicZoneGroup":
        attrs = dict(item.attributes)
        model = cls(
            kind=item.kind,
            id=item.id,
            guid=item.guid,
            name=item.name,
            attributes=attrs,
            raw_xml=item.raw_xml,
            volume_outputs=[
                AutonomicOutput.from_browse_item(BrowseItem(kind="Output", attributes=_zone_child_attrs(child)))
                for child in item.children.get("vol", ())
            ],
            source_outputs=[
                AutonomicOutput.from_browse_item(BrowseItem(kind="Output", attributes=_zone_child_attrs(child)))
                for child in item.children.get("src", ())
            ],
            sources=[
                AutonomicSource.from_browse_item(BrowseItem(kind="Source", attributes=dict(child)))
                for child in item.children.get("Sources", ())
            ],
        )
        if client is not None:
            model.bind(client)
            model.volume_outputs = [output.bind(client) for output in model.volume_outputs]
            model.source_outputs = [output.bind(client) for output in model.source_outputs]
            model.sources = [source.bind(client) for source in model.sources]
        return model


class AutonomicPartyModeInfo(AutonomicItem):
    """Typed MRAD party-mode inclusion row for a zone/output."""

    kind: str = "PartyModeInfo"
    enabled: bool | None = None
    hard_group_guid: str | None = None

    @classmethod
    def from_browse_item(cls, item: BrowseItem, *, client: AutonomicControlClient | None = None) -> "AutonomicPartyModeInfo":
        attrs = dict(item.attributes)
        model = cls(
            kind=item.kind,
            id=item.id,
            guid=item.guid,
            name=item.name,
            enabled=_bool_attr(attrs, "enabled", "Enabled"),
            hard_group_guid=_first_attr(attrs, "hardGroupGuid", "HardGroupGuid"),
            attributes=attrs,
            raw_xml=item.raw_xml,
        )
        if client is not None:
            model.bind(client)
        return model


class AutonomicOutputGroup(BaseModel):
    """A fanout proxy for controlling a group of Autonomic outputs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    outputs: list[AutonomicOutput] = Field(default_factory=list)

    _client: AutonomicControlClient | None = PrivateAttr(default=None)

    def bind(self, client: AutonomicControlClient) -> "AutonomicOutputGroup":
        self._client = client
        self.outputs = [output.bind(client) for output in self.outputs]
        return self

    def __iter__(self) -> Iterator[AutonomicOutput]:  # type: ignore[override]
        return iter(self.outputs)

    def __len__(self) -> int:
        return len(self.outputs)

    def __getitem__(self, index: int | slice) -> AutonomicOutput | list[AutonomicOutput]:
        return self.outputs[index]

    def _fanout(self, operation: Callable[[AutonomicOutput], ControlResult], *, flatten_lists: bool = False) -> list[ControlResult]:
        results: list[ControlResult] = []
        for output in self.outputs:
            result = operation(output)
            if flatten_lists and isinstance(result, list):
                results.extend(result)
            else:
                results.append(result)
        return results

    def select(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.select())

    def set_source(self, source: SourceRef, *, include_group: bool = False) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_source(source, include_group=include_group), flatten_lists=True)

    def assign_source(self, source: SourceRef, *, include_group: bool = False) -> list[ControlResult]:
        return self.set_source(source, include_group=include_group)

    def assign(self, source: SourceRef, *, include_group: bool = False) -> list[ControlResult]:
        return self.set_source(source, include_group=include_group)

    def set_volume(self, value: float) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_volume(value))

    def set_max_volume(self, value: float) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_max_volume(value))

    def max_volume_up(self, step: float = 1.0) -> list[ControlResult]:
        return self._fanout(lambda output: output.max_volume_up(step))

    def max_volume_down(self, step: float = 1.0) -> list[ControlResult]:
        return self._fanout(lambda output: output.max_volume_down(step))

    def set_bass(self, value: int) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_bass(value))

    def bass_up(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.bass_up())

    def bass_down(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.bass_down())

    def set_treble(self, value: int) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_treble(value))

    def treble_up(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.treble_up())

    def treble_down(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.treble_down())

    def set_balance(self, value: int) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_balance(value))

    def balance_left(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.balance_left())

    def balance_right(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.balance_right())

    def set_gain(self, value: int) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_gain(value))

    def gain_up(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.gain_up())

    def gain_down(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.gain_down())

    def set_delay(self, value_ms: int) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_delay(value_ms))

    def delay_up(self, step_ms: int = 5) -> list[ControlResult]:
        return self._fanout(lambda output: output.delay_up(step_ms))

    def delay_down(self, step_ms: int = 5) -> list[ControlResult]:
        return self._fanout(lambda output: output.delay_down(step_ms))

    def set_loudness(self, enabled: bool) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_loudness(enabled))

    def set_mono_downmix(self, enabled: bool | str) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_mono_downmix(enabled))

    def set_power_on_volume(self, value: float) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_power_on_volume(value))

    def set_icon(self, icon: str) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_icon(icon))

    def volume_up(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.volume_up())

    def volume_down(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.volume_down())

    def mute(self, state: bool | str = True) -> list[ControlResult]:
        return self._fanout(lambda output: output.mute(state))

    def unmute(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.unmute())

    def toggle_mute(self) -> list[ControlResult]:
        return self._fanout(lambda output: output.toggle_mute())

    def set_power(self, is_on: bool | str = True) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_power(is_on))

    def set_is_on(self, is_on: bool | str = True) -> list[ControlResult]:
        return self._fanout(lambda output: output.set_is_on(is_on))


def object_ref(value: str | int | AutonomicItem | None) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, AutonomicItem):
        return value.ref
    return value


def source_ref(value: str | int | AutonomicSource) -> str | int:
    if isinstance(value, AutonomicSource):
        ref = value.guid or value.id or value.name
        if ref is None:
            raise ValueError(f"{value.kind} has no guid, id, or name")
        return ref
    return value


def output_ref(value: str | int | AutonomicOutput) -> str | int:
    if isinstance(value, AutonomicOutput):
        ref = value.id or value.guid or value.name
        if ref is None:
            raise ValueError(f"{value.kind} has no id, guid, or name")
        return ref
    return value


def source_id(value: SourceRef) -> int:
    if isinstance(value, AutonomicSource):
        if value.id is None:
            raise ValueError(f"Source has no numeric id: {value}")
        value = value.id
    if isinstance(value, str) and ":" in value:
        value = value.rsplit(":", 1)[1]
    if not isinstance(value, str | int):
        raise TypeError("source must be an integer, string, or AutonomicSource")
    return int(value)


def omit_disabled(items: Iterable[TAutonomicItem], *, include_disabled: bool = False) -> list[TAutonomicItem]:
    if include_disabled:
        return list(items)
    return [item for item in items if not getattr(item, "disabled", None)]
