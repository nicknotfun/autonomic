from __future__ import annotations

import ipaddress
import json
import socket
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .models import (
    DiscoveryResponse,
    Guid,
    SourcesListResponse,
    SourceXml,
    StatusResponse,
    ZonesListResponse,
    ZoneXml,
)


class MA6Client:
    def __init__(self, host: str, amscp_port: int = 5004, mrad_port: int = 5005):
        self.host = host
        self.amscp_port = amscp_port
        self.mrad_port = mrad_port
        self._selected_zone_guid: Guid | None = None

    @classmethod
    def discover(
        cls,
        discovery_port: int = 5006,
        amscp_port: int = 5004,
        timeout: float = 0.35,
        network: str | None = None,
        max_workers: int = 128,
    ) -> "MA6Client":
        udp = cls._discover_udp(discovery_port=discovery_port, timeout=timeout)
        if udp:
            return cls(host=udp.host, amscp_port=udp.amscp_port)

        if network is None:
            raise RuntimeError("UDP discovery failed; supply a network (e.g. 192.168.1.0/24) for TCP scan")

        host = cls._scan_network(network=network, amscp_port=amscp_port, timeout=timeout, max_workers=max_workers)
        if not host:
            raise RuntimeError("Unable to discover MA6 server")
        return cls(host=host, amscp_port=amscp_port)

    @staticmethod
    def _discover_udp(discovery_port: int, timeout: float) -> DiscoveryResponse | None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(timeout)
            try:
                sock.sendto(b"MA6_DISCOVER", ("255.255.255.255", discovery_port))
                data, addr = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                return None
            payload = json.loads(data.decode())
            payload["host"] = addr[0]
            return DiscoveryResponse.model_validate(payload)

    @staticmethod
    def _probe_host(host: str, amscp_port: int, timeout: float) -> str | None:
        try:
            with socket.create_connection((host, amscp_port), timeout=timeout) as conn:
                conn.sendall(b"GetStatus\n")
                resp = conn.recv(2048).decode(errors="ignore")
                if "<Status" in resp:
                    return host
        except OSError:
            return None
        return None

    @classmethod
    def _scan_network(cls, network: str, amscp_port: int, timeout: float, max_workers: int) -> str | None:
        net = ipaddress.ip_network(network, strict=False)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(cls._probe_host, str(ip), amscp_port, timeout) for ip in net.hosts()]
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    return result
        return None

    def _send_many(self, commands: list[str], port: int) -> str:
        payload = ""
        with socket.create_connection((self.host, port), timeout=2.0) as conn:
            for command in commands:
                conn.sendall((command.strip() + "\n").encode())
                response = b""
                while not response.endswith(b"\n"):
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    response += chunk
                payload = response.decode(errors="ignore").strip()
                if payload.startswith("ERROR"):
                    raise RuntimeError(payload)
        return payload

    def _send(self, command: str, port: int) -> str:
        return self._send_many([command], port)

    def _send_zone_scoped(self, command: str) -> str:
        if self._selected_zone_guid:
            return self._send_many([f"SetZone Guid={str(self._selected_zone_guid)}", command], self.amscp_port)
        return self._send(command, self.amscp_port)

    def initialize(self, instance: str = "Player_A", client_type: str = "DemoClient", client_version: str = "1.0.0.0") -> None:
        init_cmds = [
            f"SetClientType {client_type}",
            f"SetClientVersion {client_version}",
            f"SetHost {self.host}",
            "SetXmlMode Lists",
            "SetEncoding 65001",
            f"SetInstance {instance}",
            "SubscribeEvents true",
        ]
        self._send_many(init_cmds, self.amscp_port)

    def list_zones(self) -> ZonesListResponse:
        xml = self._send("BrowseAllZones", self.amscp_port)
        root = ET.fromstring(xml)
        return ZonesListResponse(zones=[ZoneXml.model_validate(dict(elem.attrib)) for elem in root.findall("Zone")])

    def list_sources(self) -> SourcesListResponse:
        xml = self._send("BrowseAllSources", self.amscp_port)
        root = ET.fromstring(xml)
        return SourcesListResponse(sources=[SourceXml.model_validate(dict(elem.attrib)) for elem in root.findall("Source")])

    def select_zone(self, *, guid: Guid | str | None = None, zone_id: str | None = None, name: str | None = None) -> None:
        if guid:
            self._selected_zone_guid = Guid(str(guid))
            self._send(f"SetZone Guid={str(guid)}", self.amscp_port)
        elif zone_id:
            self._send(f"SetZone Id={zone_id}", self.amscp_port)
            for z in self.list_zones().zones:
                if z.zoneId == zone_id:
                    self._selected_zone_guid = z.zoneGuid
                    break
        elif name:
            self._send(f"SetZone Name={name}", self.amscp_port)
            for z in self.list_zones().zones:
                if z.zoneName == name:
                    self._selected_zone_guid = z.zoneGuid
                    break
        else:
            raise ValueError("provide guid, zone_id, or name")

    def select_source(self, *, guid: Guid | str | None = None, source_id: str | None = None, name: str | None = None) -> None:
        if guid:
            self._send_zone_scoped(f"SetSource Guid={str(guid)}")
        elif source_id:
            self._send_zone_scoped(f"SetSource Id={source_id}")
        elif name:
            self._send_zone_scoped(f"SetSource Name={name}")
        else:
            raise ValueError("provide guid, source_id, or name")

    def volume(self, value: int) -> None:
        self._send_zone_scoped(f"Volume {value}")

    def mute(self, state: bool | str) -> None:
        arg = state if isinstance(state, str) else ("true" if state else "false")
        self._send_zone_scoped(f"Mute {arg}")

    def media_control(self, action: str) -> None:
        self._send_zone_scoped(f"MediaControl {action}")

    def get_status(self) -> StatusResponse:
        xml = self._send_zone_scoped("GetStatus")
        root = ET.fromstring(xml)
        return StatusResponse.model_validate(dict(root.attrib))

    def mrad_get_status(self) -> StatusResponse:
        xml = self._send("MRAD.GetStatus", self.mrad_port)
        root = ET.fromstring(xml)
        return StatusResponse.model_validate(dict(root.attrib))
