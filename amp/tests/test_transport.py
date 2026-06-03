import asyncio

import pytest

from amp.transport import (
    DEFAULT_CONNECTION_TIMEOUT_SECS,
    ConnectionInterrupted,
    Transport,
    TransportQueue,
    TransportQueueClosed,
)


class DummyEncoder:
    def encode(self, value: str) -> bytes | None:
        if value == "skip":
            return None
        if value == "first-copy":
            return b"first"
        return value.encode("ascii")

    def decoder(self, value: bytes) -> str:
        return value.decode("ascii")


def test_transport_queue_encodes_and_retries_incomplete_item_until_task_done() -> None:
    async def scenario() -> None:
        queue: TransportQueue[str] = TransportQueue(DummyEncoder())
        queue.push("first")
        queue.push("skip")
        queue.push("second")

        assert await queue.pull() == ("first", b"first")
        assert await queue.pull() == ("first", b"first")
        queue.task_done()
        assert await queue.pull() == ("second", b"second")

    asyncio.run(scenario())


def test_transport_queue_encodes_connection_interrupted_as_empty_bytes() -> None:
    async def scenario() -> None:
        queue: TransportQueue[str | ConnectionInterrupted] = TransportQueue(DummyEncoder())
        event = ConnectionInterrupted()

        queue.push(event)

        assert await queue.pull() == (event, b"")

    asyncio.run(scenario())


def test_transport_queue_dedupes_by_encoded_representation() -> None:
    async def scenario() -> None:
        queue: TransportQueue[str] = TransportQueue(DummyEncoder())
        queue.push("first")
        queue.push("first-copy")
        queue.push("second")

        assert await queue.pull() == ("first", b"first")
        queue.task_done()
        assert await queue.pull() == ("second", b"second")

    asyncio.run(scenario())


def test_transport_queue_dedupes_incomplete_items_until_task_done() -> None:
    async def scenario() -> None:
        queue: TransportQueue[str] = TransportQueue(DummyEncoder())
        queue.push("first")

        assert await queue.pull() == ("first", b"first")
        queue.push("first-copy")
        queue.push("second")
        assert await queue.pull() == ("first", b"first")
        queue.task_done()

        queue.push("first-copy")
        assert await queue.pull() == ("second", b"second")
        queue.task_done()
        assert await queue.pull() == ("first-copy", b"first")

    asyncio.run(scenario())


def test_transport_queue_shutdown_wakes_waiters() -> None:
    async def scenario() -> None:
        queue: TransportQueue[str] = TransportQueue(DummyEncoder())
        waiter = asyncio.create_task(queue.pull())

        await asyncio.sleep(0)
        queue.shutdown()

        with pytest.raises(TransportQueueClosed):
            await waiter

    asyncio.run(scenario())


def test_transport_send_starts_loop_and_queues_outbound_ops() -> None:
    async def scenario() -> None:
        transport = Transport(DummyEncoder(), "127.0.0.1")

        async def fake_loop() -> None:
            await asyncio.Event().wait()

        transport._loop = fake_loop  # type: ignore[method-assign]
        transport.send("first", "second")

        assert transport._loop_task is not None
        assert await transport.outbound.pull() == ("first", b"first")
        transport.outbound.task_done()
        assert await transport.outbound.pull() == ("second", b"second")
        transport.shutdown()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_transport_default_connect_timeout_is_short_for_local_network() -> None:
    transport = Transport(DummyEncoder(), "127.0.0.1")

    assert transport.connection_timeout_secs == 0.25
    assert transport.connection_timeout_secs == DEFAULT_CONNECTION_TIMEOUT_SECS


def test_transport_retries_connect_oserror_and_preserves_outbound_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        attempts = 0
        connected = asyncio.Event()
        wrote = asyncio.Event()
        never_read = asyncio.Event()
        written: list[bytes] = []

        class WaitingReader:
            async def readuntil(self) -> bytes:
                await never_read.wait()
                return b""

        class FakeWriter:
            def write(self, data: bytes) -> None:
                written.append(data)
                wrote.set()

            async def drain(self) -> None:
                pass

            def close(self) -> None:
                pass

            async def wait_closed(self) -> None:
                pass

        async def fake_open_connection(
            host: str,
            port: int,
        ) -> tuple[WaitingReader, FakeWriter]:
            nonlocal attempts
            attempts += 1
            assert host == "127.0.0.1"
            assert port == 17037
            if attempts == 1:
                raise OSError("connect call failed")
            connected.set()
            return WaitingReader(), FakeWriter()

        monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
        transport = Transport(
            DummyEncoder(),
            "127.0.0.1",
            reconnection_wait_secs=0,
            connection_timeout_secs=0.25,
        )
        transport.send("first")
        try:
            await asyncio.wait_for(connected.wait(), timeout=1)
            await asyncio.wait_for(wrote.wait(), timeout=1)

            assert attempts == 2
            assert written == [b"b'first'\r\n"]
        finally:
            transport.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_transport_recv_starts_loop_and_yields_inbound_ops() -> None:
    async def scenario() -> None:
        transport = Transport(DummyEncoder(), "127.0.0.1")

        async def fake_loop() -> None:
            await asyncio.Event().wait()

        transport._loop = fake_loop  # type: ignore[method-assign]
        transport.inbound.push("decoded")
        receiver = transport.recv()

        assert await receiver.__anext__() == "decoded"
        assert transport._loop_task is not None
        await receiver.aclose()
        transport.shutdown()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_transport_emits_connection_interrupted_after_reader_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        class DisconnectingReader:
            async def readuntil(self) -> bytes:
                raise ConnectionError("lost connection")

        class FakeWriter:
            def __init__(self) -> None:
                self.closed = False

            def write(self, data: bytes) -> None:
                pass

            async def drain(self) -> None:
                pass

            def close(self) -> None:
                self.closed = True

            async def wait_closed(self) -> None:
                pass

        writers: list[FakeWriter] = []

        async def fake_open_connection(
            host: str,
            port: int,
        ) -> tuple[DisconnectingReader, FakeWriter]:
            assert host == "127.0.0.1"
            assert port == 17037
            writer = FakeWriter()
            writers.append(writer)
            return DisconnectingReader(), writer

        monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
        transport = Transport(
            DummyEncoder(),
            "127.0.0.1",
            reconnection_wait_secs=0,
            connection_timeout_secs=1,
        )
        receiver = transport.recv()
        try:
            event = await asyncio.wait_for(receiver.__anext__(), timeout=1)

            assert isinstance(event, ConnectionInterrupted)
            assert writers
            assert writers[0].closed is True
        finally:
            await receiver.aclose()
            transport.shutdown()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_transport_shutdown_closes_active_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        connected = asyncio.Event()
        closed = asyncio.Event()
        waited_closed = asyncio.Event()
        never_read = asyncio.Event()

        class WaitingReader:
            async def readuntil(self) -> bytes:
                await never_read.wait()
                return b""

        class FakeWriter:
            def write(self, data: bytes) -> None:
                pass

            async def drain(self) -> None:
                pass

            def close(self) -> None:
                closed.set()

            async def wait_closed(self) -> None:
                waited_closed.set()

        async def fake_open_connection(
            host: str,
            port: int,
        ) -> tuple[WaitingReader, FakeWriter]:
            connected.set()
            return WaitingReader(), FakeWriter()

        monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
        transport = Transport(
            DummyEncoder(),
            "127.0.0.1",
            reconnection_wait_secs=0,
            connection_timeout_secs=1,
        )
        receiver = transport.recv()
        next_event = asyncio.create_task(receiver.__anext__())

        await asyncio.wait_for(connected.wait(), timeout=1)
        transport.shutdown()

        await asyncio.wait_for(closed.wait(), timeout=1)
        await asyncio.wait_for(waited_closed.wait(), timeout=1)
        with pytest.raises(StopAsyncIteration):
            await next_event

    asyncio.run(scenario())


def test_transport_context_manager_returns_self_and_shutdown_is_idempotent() -> None:
    transport = Transport(DummyEncoder(), "127.0.0.1")

    with transport as active:
        assert active is transport

    assert transport._loop_task is None
    transport.shutdown()
    assert transport._loop_task is None


def test_transport_trace_respects_trace_flag_and_truncates_long_inbound_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    quiet = Transport(DummyEncoder(), "127.0.0.1", trace=False)
    quiet._trace("visible only when tracing")
    assert capsys.readouterr().out == ""

    loud = Transport(DummyEncoder(), "127.0.0.1", trace=True)
    loud._trace("<-- <" + "A" * 80)
    loud._trace("short")

    output = capsys.readouterr().out.splitlines()
    assert output[0].endswith("... long message abbreviated")
    assert len(output[0]) < 90
    assert output[1] == "short"
