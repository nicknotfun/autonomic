from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urljoin

from .exceptions import ProtocolError
from .models import BrowseItem, BrowseResponse, Event

CRLF = "\r\n"

_EVENT_RE = re.compile(r"^(?:(?P<namespace>MRAD)\.)?(?P<reason>StateChanged|ReportState)\s+(?P<source>\S+)\s+(?P<name>[^=\s]+)=(?P<value>.*)$")
_BEGIN_RE = re.compile(r"^Begin(?P<kind>\w+)(?P<rest>.*)$", re.IGNORECASE)
_END_RE = re.compile(r"^End(?P<kind>\w+)(?:\s+(?P<terminator>\S+))?", re.IGNORECASE)
_BANNER_PREFIXES = (
    "Autonomic Controls MRAD Bridge version",
    "More info found on the Web",
    "Type '?' for help",
    "Server=",
)


def quote_arg(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""

    text = str(value)
    if text == "":
        return '""'
    if "=" in text and not text.split("=", 1)[0].strip().count(" "):
        return text

    needs_quotes = any(ch.isspace() for ch in text) or '"' in text
    if not needs_quotes:
        return text

    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_command(name: str, *args: object) -> str:
    rendered = [name]
    rendered.extend(quote_arg(arg) for arg in args if arg is not None)
    return " ".join(part for part in rendered if part != "")


def format_assignment(name: str, value: object) -> str:
    rendered = quote_arg(value)
    if isinstance(value, str) and name.lower() == "search" and not rendered.startswith('"'):
        rendered = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{name}={rendered}"


def frame_command(command: str) -> bytes:
    return (command.rstrip("\r\n") + CRLF).encode("utf-8")


def parse_event(line: str) -> Event | None:
    match = _EVENT_RE.match(line.strip())
    if not match:
        return None
    return Event(
        namespace=match.group("namespace"),
        reason=match.group("reason"),
        source=match.group("source"),
        name=match.group("name"),
        value=match.group("value"),
        raw=line,
    )


def parse_response_value(value: str) -> str | bool | int:
    text = value.strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def parse_xml_list(xml_text: str) -> BrowseResponse:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ProtocolError(f"Invalid XML response: {exc}") from exc

    items: list[BrowseItem] = []
    for child in list(root):
        raw_xml = ET.tostring(child, encoding="unicode")
        nested: dict[str, list[dict[str, str]]] = {}
        for nested_parent in list(child):
            nested[nested_parent.tag] = [dict(elem.attrib) for elem in list(nested_parent)]
        items.append(BrowseItem(kind=child.tag, attributes=dict(child.attrib), children=nested, raw_xml=raw_xml))

    return BrowseResponse(kind=root.tag, attributes=dict(root.attrib), items=items, raw=xml_text)


def parse_legacy_list(lines: list[str]) -> BrowseResponse:
    if not lines:
        raise ProtocolError("No lines supplied")

    begin = _BEGIN_RE.match(lines[0].strip())
    if not begin:
        raise ProtocolError(f"Not a legacy list: {lines[0]}")

    kind = begin.group("kind")
    attrs = _parse_attribute_tail(begin.group("rest"))
    items: list[BrowseItem] = []
    terminator: str | None = None

    for line in lines[1:]:
        end = _END_RE.match(line.strip())
        if end:
            terminator = end.group("terminator")
            break
        tokens = shlex.split(line, posix=False)
        if not tokens:
            continue
        item_kind = tokens[0]
        item_attrs: dict[str, str] = {}
        if len(tokens) > 1:
            item_attrs["guid"] = tokens[1].strip('"')
        if len(tokens) > 2:
            item_attrs["name"] = tokens[2].strip('"')
        for index, token in enumerate(tokens[3:], start=1):
            item_attrs[f"value{index}"] = token.strip('"')
        items.append(BrowseItem(kind=item_kind, attributes=item_attrs))

    return BrowseResponse(kind=kind, attributes=attrs, items=items, raw="\n".join(lines), terminator=terminator)


def is_legacy_list_start(line: str) -> bool:
    return bool(_BEGIN_RE.match(line.strip()))


def is_legacy_list_end(line: str) -> bool:
    return bool(_END_RE.match(line.strip()))


def is_xml_response(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("<") and stripped.endswith(">")


def is_banner_line(line: str) -> bool:
    return line.strip().startswith(_BANNER_PREFIXES)


def is_error_response(line: str) -> bool:
    return bool(re.match(r"^(?:\w+\s+)?Error\b", line.strip(), re.IGNORECASE))


def events_to_snapshot(events: list[Event]) -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = {}
    for event in events:
        snapshot.setdefault(event.source, {})[event.name] = event.value
    return snapshot


def album_art_url(
    base_web_url: str,
    *,
    guid: str | None = None,
    instance: str | None = None,
    width: int | None = None,
    height: int | None = None,
    constrain: bool | None = None,
    fmt: str | None = None,
    endpoint: str = "GetArt",
    **extra: object,
) -> str:
    query: dict[str, object] = {}
    if guid is not None:
        query["guid"] = guid
    if instance is not None:
        query["instance"] = instance
    if width is not None:
        query["w"] = width
    if height is not None:
        query["h"] = height
    if constrain is not None:
        query["c"] = 1 if constrain else 0
    if fmt is not None:
        query["fmt"] = fmt
    query.update({key: value for key, value in extra.items() if value is not None})

    base = base_web_url.rstrip("/") + "/"
    return urljoin(base, endpoint.lstrip("/")) + ("?" + urlencode(query) if query else "")


def _parse_attribute_tail(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for token in shlex.split(text, posix=False):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        attrs[key] = value.strip('"')
    return attrs
