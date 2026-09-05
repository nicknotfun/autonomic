import asyncio
from abc import ABC, abstractmethod
from contextlib import suppress
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
DEFAULT_CONNECTION_TIMEOUT_SECS = 0.25


class Encoder(Protocol, Generic[T]):
    def encode(self, value: T) -> HexBytes | None: ...

    def decoder(self, value: bytes) -> T | None: ...


class ConnectionInterrupted:
    pass


class BaseTransport(ABC, Generic[T]):
    host: str

    def validate_send(self, *ops: T) -> None:
        """Validate a batch without starting a connection or queueing operations."""

    @abstractmethod
    def send(self, *ops: T) -> None: ...

    @abstractmethod
    def recv(self) -> AsyncGenerator[T | ConnectionInterrupted, None]: ...

    @abstractmethod
    def shutdown(self) -> None: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class TransportQueueClosed(Exception):
    pass


class TransportQueue(Generic[T]):
    def __init__(self, encoder: Encoder[Any]) -> None:
        self._queue: asyncio.Queue[tuple[T, HexBytes] | None] = asyncio.Queue()
        self._incomplete: list[tuple[T, HexBytes]] = []
        self._closed = False
        self.encoder = encoder

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)

    def _encode(self, value: T) -> HexBytes | None:
        if isinstance(value, ConnectionInterrupted):
            return HexBytes("")
        return self.encoder.encode(value)

    def push(self, value: T, *, encoded: HexBytes | None = None) -> None:
        if self._closed:
            raise TransportQueueClosed
        if not value:
            return
        if encoded is None:
            encoded = self._encode(value)
        if encoded is None:
            return
        self._queue.put_nowait((value, encoded))

    def task_done(self) -> None:
        self._incomplete.pop(0)

    async def pull(self) -> tuple[T, HexBytes]:
        if self._incomplete:
            return self._incomplete[0]
        value = await self._queue.get()
        if value is None:
            raise TransportQueueClosed
        self._incomplete.append(value)
        return value


class Transport(BaseTransport[T]):
    def __init__(
        self,
        encoder: Encoder[T],
        host: str,
        port: int = 17037,
        *,
        reconnection_wait_secs: float = 5.0,
        connection_timeout_secs: float = DEFAULT_CONNECTION_TIMEOUT_SECS,
        trace: bool = False,
    ) -> None:
        self.outbound: TransportQueue[T] = TransportQueue(encoder)
        self.inbound: TransportQueue[T | ConnectionInterrupted] = TransportQueue(encoder)
        self.host = host
        self.port = port
        self.reconnection_wait_secs = reconnection_wait_secs
        self.connection_timeout_secs = connection_timeout_secs
        self._loop_task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
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
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._loop())
            self._loop_task.add_done_callback(self._handle_loop_task_done)

    def _handle_loop_task_done(self, task: asyncio.Future[None]) -> None:
        if self._loop_task is task:
            self._loop_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Transport loop exited unexpectedly: %s", exc)

    def validate_send(self, *ops: T) -> None:
        for op in ops:
            self.encoder.encode(op)

    def send(self, *ops: T) -> None:
        # Encoding may reject a later operation. Complete it before starting the
        # loop or queueing any earlier writes from the batch.
        encoded_ops = [(op, self.encoder.encode(op)) for op in ops]
        self._maybe_start_loop()
        for op, encoded in encoded_ops:
            if encoded is not None:
                self.outbound.push(op, encoded=encoded)

    async def recv(self) -> AsyncGenerator[T | ConnectionInterrupted, None]:
        self._maybe_start_loop()
        # A receiver belongs to one connection lifecycle. Shutdown replaces the
        # public queues, but a generator paused at yield must finish on its old
        # queue instead of waiting forever on the new one when resumed.
        inbound = self.inbound
        while True:
            try:
                op, _ = await inbound.pull()
                try:
                    yield op
                finally:
                    inbound.task_done()
            except asyncio.CancelledError:
                break
            except TransportQueueClosed:
                break

    def shutdown(self) -> None:
        loop_task = self._loop_task
        self._loop_task = None
        if loop_task is not None:
            loop_task.cancel()

        if self._writer is not None:
            self._writer.close()

        self.inbound.shutdown()
        self.inbound = TransportQueue(self.encoder)

        self.outbound.shutdown()
        self.outbound = TransportQueue(self.encoder)

    async def aclose(self) -> None:
        loop_task = self._loop_task
        self.shutdown()
        if loop_task is not None:
            await asyncio.gather(loop_task, return_exceptions=True)

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
                try:
                    async with asyncio.timeout(self.connection_timeout_secs):
                        reader, writer = await asyncio.open_connection(self.host, self.port)
                except (OSError, TimeoutError) as exc:
                    logger.info("Connection attempt failed, retrying: %s", exc)
                    await asyncio.sleep(self.reconnection_wait_secs)
                    continue
                self._writer = writer
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
                    except TransportQueueClosed:
                        pass
                    except Exception as exc:
                        logger.exception("Error while reading lines: %s", exc)
                        writer.close()
                        raise

                read_task = asyncio.create_task(pull_inbound())
                pull_task: asyncio.Task[tuple[T, HexBytes]] | None = None
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
                        self._trace(f"--> {encoded}: {op}")
                        writer.write(str(encoded).encode("ascii") + b"\r\n")
                        await writer.drain()
                        outbound.task_done()
                finally:
                    if not read_task.done():
                        read_task.cancel()
                    if pull_task is not None and not pull_task.done():
                        pull_task.cancel()
                    writer.close()
                    cleanup_tasks: list[asyncio.Task[Any]] = [read_task]
                    if pull_task is not None:
                        cleanup_tasks.append(pull_task)
                    try:
                        await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                    finally:
                        try:
                            with suppress(Exception):
                                await writer.wait_closed()
                        finally:
                            if self._writer is writer:
                                self._writer = None
            except TransportQueueClosed:
                break
            except Exception as exc:
                if connected:
                    try:
                        inbound.push(ConnectionInterrupted())
                    except TransportQueueClosed:
                        break
                    connected = False
                logger.exception("Connection error: %s", exc)
                await asyncio.sleep(self.reconnection_wait_secs)
