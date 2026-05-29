import asyncio
import logging
from typing import (
    Any,
    AsyncIterator,
    Generic,
    Protocol,
    TypeVar,
)

from amp.byte_utils import HexBytes

logger = logging.getLogger(__name__)


T = TypeVar("T")


class OpQueue(Generic[T]):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue()
        self._incomplete: list[T] = []

    def shutdown(self) -> None:
        self._queue.shutdown()

    def push(self, value: T) -> None:
        self._queue.put_nowait(value)

    def task_done(self) -> None:
        self._incomplete.pop(0)

    async def pull(self) -> T:
        if self._incomplete:
            return self._incomplete[0]
        value = await self._queue.get()
        self._incomplete.append(value)
        return value


class Encoder(Protocol, Generic[T]):
    def encode(self, value: T) -> bytes | None: ...
    def decoder(self, value: bytes) -> T: ...


class Transport(Generic[T]):
    def __init__(
        self,
        encoder: Encoder[T],
        host: str,
        port: int = 17037,
        *,
        reconnection_wait_secs: float = 5.0,
        connection_timeout_secs: float = 10.0,
        trace: bool = False,
    ) -> None:
        self.outbound = OpQueue()
        self.inbound: asyncio.Queue[T] = asyncio.Queue()
        self.host = host
        self.port = port
        self.reconnection_wait_secs = reconnection_wait_secs
        self.connection_timeout_secs = connection_timeout_secs
        self._loop_task: asyncio.Task[None] | None = None
        self.trace = trace
        self.encoder = encoder

    def _maybe_start_loop(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop())

    def send(self, *ops: T) -> None:
        self._maybe_start_loop()
        for op in ops:
            self.outbound.push(op)

    async def recv(self) -> AsyncIterator[T]:
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
        self.outbound = OpQueue()

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

                async def write_lines(*ops: T) -> None:
                    for op in ops:
                        if not op:
                            continue
                        encoded = self.encoder.encode(op)
                        if encoded is None:
                            continue
                        writer.write(str(encoded).encode("ascii") + b"\r\n")
                        await writer.drain()

                async def pull_inbound() -> None:
                    try:
                        while True:
                            line_bytes = await reader.readuntil()
                            line = line_bytes.decode("ascii").strip()
                            if not line:
                                continue
                            try:
                                line_data = HexBytes(line)
                            except ValueError as e:
                                self._trace(f"<-- {line}: failed to parse as hex: {e}")
                                continue

                            op = self.encoder.decoder(line_data)
                            if op is None:
                                self._trace(f"<-- {line}: could not decode")
                                continue

                            self._trace(f"<-- {line}: {op}")
                            await inbound.put(op)
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
            except Exception:
                logger.exception("Connection error: %s")
                await asyncio.sleep(self.reconnection_wait_secs)
