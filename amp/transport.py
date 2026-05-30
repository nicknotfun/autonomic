import asyncio
import logging
from typing import (
    Any,
    AsyncGenerator,
    Generic,
    Protocol,
    TypeVar,
)

from amp.byte_utils import HexBytes

logger = logging.getLogger(__name__)


T = TypeVar("T")


class Encoder(Protocol, Generic[T]):
    def encode(self, value: T) -> bytes | None: ...
    def decoder(self, value: bytes) -> T | None: ...


class ConnectionInterrupted:
    pass


class TransportQueue(Generic[T]):
    def __init__(self, encoder: Encoder[Any]) -> None:
        self._queue: asyncio.Queue[tuple[T, bytes]] = asyncio.Queue()
        self._incomplete: list[tuple[T, bytes]] = []
        self._queued_encoded: set[bytes] = set()
        self.encoder = encoder

    def shutdown(self) -> None:
        self._queue.shutdown()

    def _encode(self, value: T) -> bytes | None:
        if isinstance(value, ConnectionInterrupted):
            return b""
        return self.encoder.encode(value)

    def push(self, value: T, *, encoded: bytes | None = None) -> None:
        if not value:
            return
        if encoded is None:
            encoded = self._encode(value)
        if encoded is None:
            return
        if encoded in self._queued_encoded:
            return
        self._queued_encoded.add(encoded)
        self._queue.put_nowait((value, encoded))

    def task_done(self) -> None:
        _, encoded = self._incomplete.pop(0)
        self._queued_encoded.discard(encoded)

    async def pull(self) -> tuple[T, bytes]:
        if self._incomplete:
            return self._incomplete[0]
        value = await self._queue.get()
        self._incomplete.append(value)
        return value


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
        self.outbound: TransportQueue[T] = TransportQueue(encoder)
        self.inbound: TransportQueue[T | ConnectionInterrupted] = TransportQueue(encoder)
        self.host = host
        self.port = port
        self.reconnection_wait_secs = reconnection_wait_secs
        self.connection_timeout_secs = connection_timeout_secs
        self._loop_task: asyncio.Task[None] | None = None
        self.trace = trace
        self.encoder = encoder

    def fork(self) -> "Transport[T]":
        return Transport(
            self.encoder,
            self.host,
            self.port,
            reconnection_wait_secs=self.reconnection_wait_secs,
            connection_timeout_secs=self.connection_timeout_secs,
            trace=self.trace,
        )

    def _maybe_start_loop(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop())

    def send(self, *ops: T) -> None:
        self._maybe_start_loop()
        for op in ops:
            self.outbound.push(op)

    async def recv(self) -> AsyncGenerator[T | ConnectionInterrupted, None]:
        self._maybe_start_loop()
        while True:
            try:
                inbound = self.inbound
                op, _ = await inbound.pull()
                try:
                    yield op
                finally:
                    inbound.task_done()
            except asyncio.CancelledError:
                break
            except asyncio.QueueShutDown:
                break

    def shutdown(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None

        self.inbound.shutdown()
        self.inbound = TransportQueue(self.encoder)

        self.outbound.shutdown()
        self.outbound = TransportQueue(self.encoder)

    def __enter__(self) -> "Transport[T]":
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
        connected = False
        while True:
            try:
                async with asyncio.timeout(self.connection_timeout_secs):
                    reader, writer = await asyncio.open_connection(self.host, self.port)
                connected = True

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
                            inbound.push(op, encoded=line_data)
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.exception("Error while reading lines: %s", exc)
                        writer.close()
                        raise

                read_task = asyncio.create_task(pull_inbound())
                pull_task: asyncio.Task[tuple[T, bytes]] | None = None
                try:
                    while True:
                        pull_task = asyncio.create_task(outbound.pull())
                        done, pending = await asyncio.wait(
                            {read_task, pull_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if read_task in done:
                            if pull_task in pending:
                                pull_task.cancel()
                            error = read_task.exception()
                            if error is not None:
                                raise error
                            raise ConnectionError("connection closed")

                        op, encoded = pull_task.result()
                        self._trace(f"--> {encoded.hex()}: {op}")
                        writer.write(str(encoded).encode("ascii") + b"\r\n")
                        await writer.drain()
                        outbound.task_done()
                finally:
                    if not read_task.done():
                        read_task.cancel()
                    if pull_task is not None and not pull_task.done():
                        pull_task.cancel()
            except asyncio.QueueShutDown:
                break
            except Exception as exc:
                if connected:
                    inbound.push(ConnectionInterrupted())
                    connected = False
                logger.exception("Connection error: %s", exc)
                await asyncio.sleep(self.reconnection_wait_secs)
