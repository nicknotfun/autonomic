"""Experimental MAS/MRAD prototype.

This module is incomplete, unsupported, and should not be used for integration
work. The supported direct-amplifier code lives under `amp/`.
"""

import asyncio
from collections import defaultdict
from sortedcontainers import SortedDict
import logging
from typing import Any, AsyncIterator, Callable, Iterable, Mapping
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


DEFAULT_STRIP_PREFIXES = ["MRAD."]


class TaskQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._incomplete: list[str] = []

    def shutdown(self) -> None:
        self._queue.shutdown()

    def push(self, value: str) -> None:
        self._queue.put_nowait(value)

    def task_done(self) -> None:
        self._incomplete.pop(0)

    async def pull(self) -> str:
        if self._incomplete:
            return self._incomplete[0]
        value = await self._queue.get()
        self._incomplete.append(value)
        return value


class Transport:
    def __init__(
        self,
        host: str,
        port: int = 5006,
        *,
        encoding: int = 65001,  # UTF-8
        client_type: str = "AMPY",
        delimiter: str = "\r\n",
        strip_prefixes: Iterable[str] = DEFAULT_STRIP_PREFIXES,
        reconnection_wait_secs: float = 5.0,
        connection_timeout_secs: float = 10.0,
        trace: bool = False,
    ) -> None:
        self.outbound = TaskQueue()
        self.inbound: asyncio.Queue[str] = asyncio.Queue()
        self.host = host
        self.port = port
        self.encoding = encoding
        self.client_type = client_type
        self.delimiter = delimiter
        self.strip_prefixes = strip_prefixes
        self.reconnection_wait_secs = reconnection_wait_secs
        self.connection_timeout_secs = connection_timeout_secs
        self._loop_task: asyncio.Task[None] | None = None
        self.trace = trace

    def _maybe_start_loop(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop())

    def send(self, *lines: str) -> None:
        self._maybe_start_loop()
        for line in lines:
            self.outbound.push(line)

    async def recv(self) -> AsyncIterator[str]:
        self._maybe_start_loop()
        while True:
            try:
                yield await self.inbound.get()
            except asyncio.CancelledError:
                break
            except asyncio.QueueShutDown:
                break

    def shutdown(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None

        self.inbound.shutdown()
        self.inbound = asyncio.Queue()

        self.outbound.shutdown()
        self.outbound = TaskQueue()

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown()

    def _trace(self, message: str) -> None:
        if self.trace:
            if message.startswith("<-- <") and len(message) > 60:
                print(message[:60] + "... long message abbreviated")
            else:
                print(message)

    async def _loop(self) -> None:
        inbound, outbound = self.inbound, self.outbound
        while True:
            try:
                async with asyncio.timeout(self.connection_timeout_secs):
                    reader, writer = await asyncio.open_connection(self.host, self.port)

                async def write_lines(*lines: str) -> None:
                    for line in lines:
                        if not line:
                            continue
                        self._trace("--> " + line)
                        writer.write((line + self.delimiter).encode("utf-8"))
                        await writer.drain()

                await write_lines(
                    "*",
                    "SetClientType " + self.client_type,
                    "SetEncoding " + str(self.encoding),
                    "SetXMLMode Lists",
                    "SetHost " + self.host,
                    "SubscribeEvents",
                )

                async def pull_inbound() -> None:
                    try:
                        while True:
                            line_bytes = await reader.readuntil(self.delimiter.encode("utf-8"))
                            line = line_bytes.decode("utf-8").rstrip(self.delimiter)
                            for prefix in self.strip_prefixes:
                                line = line.removeprefix(prefix)
                            line = line.strip()
                            if line:
                                self._trace("<-- " + line)
                                await inbound.put(line)
                    except Exception as exc:
                        logger.exception("Error while reading lines: %s", exc)
                        writer.close()
                    except asyncio.CancelledError:
                        pass

                read_task = asyncio.create_task(pull_inbound())
                try:
                    while True:
                        line = await outbound.pull()
                        await write_lines(line)
                        outbound.task_done()
                finally:
                    read_task.cancel()
            except asyncio.QueueShutDown:
                break
            except Exception as e:
                logger.warning("Connection error: %s", e)
                await asyncio.sleep(self.reconnection_wait_secs)


def ignore_if_zero(value: str) -> str | None:
    if value == "00000000-0000-0000-0000-000000000000" or value == "0":
        return None
    return value


def ignore_if_empty(value: str) -> str | None:
    if value == "":
        return None
    return value


LIST_COMMANDS: dict[str, str] = {
    "Zones": "BrowseAllZones",
    "Sources": "BrowseAllSources",
    "ZoneGroups": "BrowseZoneGroups",
}


PROPERTY_ALIASES: dict[str, str] = {
    "gId": "ZoneGroupId",
    "gName": "ZoneGroupName",
    "gPwr": "ZoneGroupPower",
    "gSrc": "ZoneGroupSource",
    "gVol": "ZoneGroupVolume",
    "sId": "SourceId",
    "sourceId": "SourceId",
    "sourceName": "SourceName",
    "isOn": "PowerOn",
    "name": "Name",
}


PROPERTY_MAP: dict[str, Callable[[str], str | None] | None] = {
    "PartyMode": None,
    "button": None,
    "dna": None,
    "gId": None,
    "gName": None,
    "gPwr": None,
    "gSrc": None,
    "gVol": None,
    "iconId": None,
    "isSearchable": None,
    "m1": None,
    "m2": None,
    "m3": None,
    "m4": None,
    "mArt": None,
    "sGuid": ignore_if_zero,
    "sId": ignore_if_zero,
    "sourceId": ignore_if_zero,
    "sourceName": ignore_if_empty,
    "IconId": None,
    "ZoneGroupId": None,
    "ZoneGroupName": None,
    "ZoneGroupPower": None,
    "ZoneGroupSource": None,
    "ZoneGroupVolume": None,
    "MCSControl": None,
    "MCSInstance": None,
    "MCSWebPort": None,
    "MediaControl": None,
    "MetaData1": None,
    "MetaData2": None,
    "MetaData3": None,
    "MetaData4": None,
    "MetaLabel1": None,
    "MetaLabel2": None,
    "MetaLabel3": None,
    "MetaLabel4": None,
    "NuVoSmartSource": None,
    "Repeat": None,
    "Shuffle": None,
    "TrackDuration": None,
    "TrackTime": None,
    "DoNotDisturb": None,
}


class AutonomicEntity:
    def __init__(self, **initial: str) -> None:
        self.version = 0
        self._properties: dict[str, str] = SortedDict()
        for key, value in initial.items():
            self._properties[key] = value

    @property
    def type(self) -> str | None:
        return self._properties.get("type", "Unknown")

    @property
    def id(self) -> str | None:
        return self._properties.get("id")

    @property
    def guid(self) -> str | None:
        return self._properties.get("guid")

    def merge(self, other: "AutonomicEntity") -> bool:
        updated = False
        for key, value in other._properties.items():
            if self.update(key, value):
                updated = True
        return updated

    @property
    def friendly_name(self) -> str | None:
        match self.type:
            case "Zones" | "Sources":
                return f"{self.get('Name', self.id)} ({self.guid})"
        return self.id

    def update(self, key: str, value: str) -> bool:
        key = PROPERTY_ALIASES.get(key, key)
        existing = self._properties.get(key)
        if existing == value:
            return False
        # if self.id is not None:
        #    print(f"{self.friendly_name}: {key} = {existing} -> {value}")
        self._properties[key] = value

        # Infer Additional Values
        match self.type:
            case "Sources":
                match key:
                    case "sId" | "SourceId":
                        self._properties["id"] = f"Source_{value}"
            case "ZoneGroups":
                match key:
                    case "Name":
                        self._properties["id"] = value

        self.version += 1
        return True

    def __contains__(self, key: str) -> bool:
        return key in self._properties

    def all_items(self) -> Iterable[tuple[str, str]]:
        return self._properties.items()

    def items(self) -> Iterable[tuple[str, str]]:
        for key, value in self._properties.items():
            if key not in PROPERTY_MAP:
                yield key, value
            mapping = PROPERTY_MAP.get(key)
            if mapping is None:
                continue
            updated_value = mapping(value)
            if updated_value is not None:
                yield key, updated_value

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._properties.get(key, default)

    def __get__(self, key: str) -> str:
        return self._properties[key]

    def __str__(self) -> str:
        contents = " ".join(
            [str(self.version)]
            + [f"{key}={value}" for key, value in sorted(self._properties.items())]
        )
        return f"<{contents}>"

    @staticmethod
    def from_xml(xml: str) -> "tuple[str, list[AutonomicEntity]]":
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            raise ValueError(f"Failed to parse XML: {e}") from e

        items: list[AutonomicEntity] = []
        for child in root:
            entity = AutonomicEntity(type=root.tag)
            for key, value in child.attrib.items():
                entity.update(key, value)
            items.append(entity)
        return root.tag, items


class EntityDict(dict[str, AutonomicEntity]):
    def __missing__(self, key: str) -> AutonomicEntity:
        value = AutonomicEntity(id=key)
        self[key] = value
        return value


class Ampy:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.root = AutonomicEntity()
        self.entities: Mapping[str, AutonomicEntity] = EntityDict()
        self._listen_task: asyncio.Task[None] | None = None
        self.version = 0
        self._received_lists: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)

    async def __aenter__(self) -> "Ampy":
        self._listen_task = asyncio.create_task(self._listen())
        await self._bootstrap()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._listen_task:
            self._listen_task.cancel()

    async def _bootstrap(self) -> None:
        for type, command in LIST_COMMANDS.items():
            self.transport.send(command)
            await self._received_lists[type].wait()
        self.transport.send("GetStatus")
        await self._received_lists["GetStatus"].wait()

    async def _listen(self) -> None:
        async for line in self.transport.recv():
            if line.startswith("Server="):
                if self.root.update("id", line.split("=", 1)[1]):
                    self.version += 1
                continue
            elif line.startswith("<"):
                try:
                    type, entities = AutonomicEntity.from_xml(line)
                    self._received_lists[type].set()
                    for entity in entities:
                        if entity.id is None:
                            logger.warning("Received entity with no ID: %s", entity)
                            continue
                        if self.entities[entity.id].merge(entity):
                            self.version += 1
                except ValueError as e:
                    logger.warning("Malformed XML: %s", e)
                continue
            elif line.startswith("ReportState ") or line.startswith("StateChanged "):
                parts = line.split(maxsplit=2)
                if len(parts) != 3:
                    logger.warning("Malformed event: %s", line)
                    continue
                id_part, assignment_part = parts[1:]
                assignment = assignment_part.split("=", 1)
                if len(assignment) != 2:
                    logger.warning("Malformed event value assignment: %s", line)
                    continue
                key_part, value_part = assignment
                if id_part == "Amps" and key_part == "GetStatus" and value_part == "Done":
                    self._received_lists["GetStatus"].set()
                if self.entities[id_part].update(key_part, value_part):
                    self.version += 1
                continue
