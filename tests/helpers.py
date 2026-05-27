# Shared scripted connection helpers for protocol and client unit tests.
from __future__ import annotations

from collections import defaultdict, deque

from autonomic.exceptions import AutonomicTimeoutError


class ScriptedConnection:
    def __init__(self, responses: dict[str, list[str]] | None = None, default: list[str] | None = None):
        self.connected = False
        self.sent: list[str] = []
        self.responses = {key: deque(value) for key, value in (responses or {}).items()}
        self.default = list(default or ["OK"])
        self._pending: deque[str] = deque()
        self.calls = defaultdict(int)
        self.response_delimiter = b"\n"

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def send_line(self, command: str) -> None:
        self.sent.append(command)
        self.calls[command] += 1
        response = self.responses.get(command)
        if response is None:
            self._pending.extend(self.default)
        else:
            self._pending.extend(response)

    def set_response_delimiter(self, delimiter: bytes) -> None:
        self.response_delimiter = bytes(delimiter)

    def read_line(self, timeout: float | None = None) -> str:
        if not self._pending:
            raise AutonomicTimeoutError("scripted timeout")
        return self._pending.popleft()
