"""Observed hardware model metadata keyed by AMP model number."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from amp.byte_utils import HexBytes

SourceKind = Literal["local", "casting"]


@dataclass(frozen=True)
class SourceModelInfo:
    selector: int
    name: str
    kind: SourceKind
    physical_source_id: int | None = None


@dataclass(frozen=True)
class HardwareModelInfo:
    model_number: HexBytes
    name: str
    output_count: int
    input_count: int
    sources: tuple[SourceModelInfo, ...]

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def local_sources(self) -> tuple[SourceModelInfo, ...]:
        return tuple(source for source in self.sources if source.kind == "local")

    @property
    def casting_sources(self) -> tuple[SourceModelInfo, ...]:
        return tuple(source for source in self.sources if source.kind == "casting")


M6250 = HardwareModelInfo(
    model_number=HexBytes("B0"),
    name="M6250",
    output_count=8,
    input_count=8,
    sources=(
        SourceModelInfo(selector=0x05, name="A1", kind="local", physical_source_id=1),
        SourceModelInfo(selector=0x06, name="A2", kind="local", physical_source_id=2),
        SourceModelInfo(selector=0x07, name="A3", kind="local", physical_source_id=3),
        SourceModelInfo(selector=0x03, name="A4", kind="local", physical_source_id=4),
        SourceModelInfo(selector=0x00, name="COAX1", kind="local", physical_source_id=5),
        SourceModelInfo(selector=0x01, name="COAX2", kind="local", physical_source_id=6),
        SourceModelInfo(selector=0x02, name="OPT1", kind="local", physical_source_id=7),
        SourceModelInfo(selector=0x04, name="OPT2", kind="local", physical_source_id=8),
    ),
)

MA6 = HardwareModelInfo(
    model_number=HexBytes("E9"),
    name="MA6",
    output_count=8,
    input_count=19,
    sources=(
        SourceModelInfo(selector=0x05, name="Player_A", kind="local", physical_source_id=1),
        SourceModelInfo(selector=0x06, name="Player_B", kind="local", physical_source_id=2),
        SourceModelInfo(selector=0x07, name="Player_C", kind="local", physical_source_id=3),
        SourceModelInfo(selector=0x03, name="Analog 1", kind="local", physical_source_id=4),
        SourceModelInfo(selector=0x00, name="Analog 2", kind="local", physical_source_id=5),
        SourceModelInfo(selector=0x01, name="Analog 3", kind="local", physical_source_id=6),
        SourceModelInfo(selector=0x02, name="Analog 4", kind="local", physical_source_id=7),
        SourceModelInfo(selector=0x04, name="Coaxial 1", kind="local", physical_source_id=8),
        SourceModelInfo(selector=0x08, name="Coaxial 2", kind="local", physical_source_id=9),
        SourceModelInfo(selector=0x09, name="Optical 1", kind="local", physical_source_id=10),
        SourceModelInfo(selector=0x0A, name="Optical 2", kind="local", physical_source_id=11),
        SourceModelInfo(selector=0x0B, name="Casting_1", kind="casting"),
        SourceModelInfo(selector=0x0C, name="Casting_2", kind="casting"),
        SourceModelInfo(selector=0x0D, name="Casting_3", kind="casting"),
        SourceModelInfo(selector=0x0E, name="Casting_4", kind="casting"),
        SourceModelInfo(selector=0x0F, name="Casting_5", kind="casting"),
        SourceModelInfo(selector=0x50, name="Casting_6", kind="casting"),
        SourceModelInfo(selector=0x51, name="Casting_7", kind="casting"),
        SourceModelInfo(selector=0x52, name="Casting_8", kind="casting"),
    ),
)

MODELS_BY_MODEL_NUMBER = MappingProxyType(
    {
        M6250.model_number: M6250,
        MA6.model_number: MA6,
    }
)


def model_by_number(
    model_number: HexBytes | str | bytes | bytearray | int,
) -> HardwareModelInfo | None:
    return MODELS_BY_MODEL_NUMBER.get(HexBytes(model_number))
