# Typed MRAD/MAS protocol value objects and constants.
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import AutonomicOutput, AutonomicSource

MRAD_PORT = 5006
OutputRef = str | int | AutonomicOutput
SourceRef = str | int | AutonomicSource
XmlMode = Literal["Lists", "None"]


@dataclass(frozen=True)
class MRADVersion:
    """Firmware version row reported by the MRAD bridge."""

    component: str
    identifier: str
    sku: str | None = None
    firmware: str | None = None
    raw: str = ""


@dataclass(frozen=True)
class MRADCommandHelp:
    """One command entry from the MRAD bridge help catalog."""

    command: str
    description: str
    usage: tuple[str, ...] = ()
    raw_lines: tuple[str, ...] = ()
