# Socket-backed line connection used by the Autonomic text protocols.
from __future__ import annotations

import socket
from types import TracebackType

from .exceptions import AutonomicTimeoutError, ConnectionClosedError, NotConnectedError
from .protocol import frame_command


class LineConnection:
    """Persistent command socket used by the Autonomic line-style APIs."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = 5.0,
        encoding: str = "utf-8",
        response_delimiter: bytes = b"\n",
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.encoding = encoding
        self.response_delimiter = _normalize_delimiter(response_delimiter)
        self._sock: socket.socket | None = None
        self._buffer = bytearray()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self._sock is not None:
            return
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close()
        finally:
            self._sock = None
            self._buffer.clear()

    def send_line(self, command: str) -> None:
        if self._sock is None:
            raise NotConnectedError("Socket is not connected")
        self._sock.sendall(frame_command(command))

    def set_response_delimiter(self, delimiter: bytes) -> None:
        self.response_delimiter = _normalize_delimiter(delimiter)

    def read_line(self, timeout: float | None = None) -> str:
        if self._sock is None:
            raise NotConnectedError("Socket is not connected")

        previous_timeout = self._sock.gettimeout()
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            delimiter = self.response_delimiter
            while delimiter not in self._buffer:
                try:
                    chunk = self._sock.recv(4096)
                except socket.timeout as exc:
                    raise AutonomicTimeoutError("Timed out waiting for a protocol line") from exc
                if not chunk:
                    if self._buffer:
                        break
                    raise ConnectionClosedError("Socket closed while waiting for a protocol line")
                self._buffer.extend(chunk)

            raw: bytes
            if delimiter in self._buffer:
                raw_buffer, _, rest = self._buffer.partition(delimiter)
                raw = bytes(raw_buffer)
                self._buffer = bytearray(rest)
            else:
                raw = bytes(self._buffer)
                self._buffer.clear()
            return raw.decode(self.encoding, errors="replace").rstrip("\r\n")
        finally:
            if timeout is not None and self._sock is not None:
                self._sock.settimeout(previous_timeout)

    def __enter__(self) -> "LineConnection":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _normalize_delimiter(delimiter: bytes) -> bytes:
    if not delimiter:
        raise ValueError("response delimiter must not be empty")
    return bytes(delimiter)
