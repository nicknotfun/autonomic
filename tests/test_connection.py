# Tests for socket line framing, timeouts, and connection lifecycle behavior.
from __future__ import annotations

import unittest

from autonomic import LineConnection


class FakeSocket:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.timeout = None
        self.sent: list[bytes] = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size: int) -> bytes:
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        pass


class LineConnectionTests(unittest.TestCase):
    def test_reads_crlf_and_zero_delimited_responses(self):
        conn = LineConnection("example.test", 5006)
        conn._sock = FakeSocket([b"Alpha\r\nBeta\x00Gamma\x00"])  # type: ignore[assignment]

        self.assertEqual(conn.read_line(), "Alpha")
        conn.set_response_delimiter(b"\x00")

        self.assertEqual(conn.read_line(), "Beta")
        self.assertEqual(conn.read_line(), "Gamma")

    def test_rejects_empty_response_delimiter(self):
        conn = LineConnection("example.test", 5006)

        with self.assertRaises(ValueError):
            conn.set_response_delimiter(b"")


if __name__ == "__main__":
    unittest.main()
