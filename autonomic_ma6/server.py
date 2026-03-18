from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .protocol import ProtocolError, handle_amscp_command, handle_mrad_command
from .state import StateStore


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, amscp_port: int):
        self.amscp_port = amscp_port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if data.decode(errors="ignore").strip().upper() == "MA6_DISCOVER":
            payload = json.dumps({"device": "VirtualMA6", "amscp_port": self.amscp_port}).encode()
            if self.transport:
                self.transport.sendto(payload, addr)


class VirtualMA6Device:
    def __init__(
        self,
        state_file: str | Path,
        host: str = "0.0.0.0",
        amscp_port: int = 5004,
        mrad_port: int = 5005,
        discovery_port: int = 5006,
    ):
        self.host = host
        self.amscp_port = amscp_port
        self.mrad_port = mrad_port
        self.discovery_port = discovery_port
        self.store = StateStore(state_file)
        self._amscp_server: asyncio.AbstractServer | None = None
        self._mrad_server: asyncio.AbstractServer | None = None
        self._discovery_transport: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        self._amscp_server = await asyncio.start_server(self._handle_amscp_client, host=self.host, port=self.amscp_port)
        self._mrad_server = await asyncio.start_server(self._handle_mrad_client, host=self.host, port=self.mrad_port)
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DiscoveryProtocol(self.amscp_port),
            local_addr=(self.host, self.discovery_port),
        )
        self._discovery_transport = transport

    async def stop(self) -> None:
        if self._amscp_server:
            self._amscp_server.close()
            await self._amscp_server.wait_closed()
        if self._mrad_server:
            self._mrad_server.close()
            await self._mrad_server.wait_closed()
        if self._discovery_transport:
            self._discovery_transport.close()

    async def _handle_amscp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await self._handle_stream(reader, writer, handle_amscp_command)

    async def _handle_mrad_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await self._handle_stream(reader, writer, handle_mrad_command)

    async def _handle_stream(self, reader, writer, handler):
        session: dict[str, str] = {}
        while not reader.at_eof():
            raw = await reader.readline()
            if not raw:
                break
            command = raw.decode(errors="ignore").strip()
            if not command:
                continue
            try:
                response = handler(self.store, command, session)
            except (ProtocolError, KeyError, ValueError) as exc:
                response = f'ERROR {exc}'
            writer.write((response + "\r\n").encode())
            await writer.drain()
        writer.close()
        await writer.wait_closed()
