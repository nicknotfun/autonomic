import asyncio

import pytest

from amp.transport import ConnectionInterrupted, Transport, TransportQueue


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
