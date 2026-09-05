import asyncio

import pytest

from amp.byte_utils import HexBytes
from amp.codec import CommandEncoder, StandbyPowerCommand, VolumeUpCommand
from amp.toggle_bool import ToggleBool
from amp.transport import (
    DEFAULT_CONNECTION_TIMEOUT_SECS,
    ConnectionInterrupted,
    Transport,
    TransportQueue,
    TransportQueueClosed,
)


class DummyEncoder:
    def encode(self, value: str) -> HexBytes | None:
        if value == "invalid":
            raise ValueError("invalid operation")
        if value == "skip":
            return None
        if value == "first-copy":
            return HexBytes.from_utf8("first")
        return HexBytes.from_utf8(value)

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


def test_transport_queue_preserves_equal_encoded_representations() -> None:
    async def scenario() -> None:
        queue: TransportQueue[str] = TransportQueue(DummyEncoder())
        queue.push("first")
        queue.push("first-copy")
        queue.push("second")

        assert await queue.pull() == ("first", b"first")
        queue.task_done()
        assert await queue.pull() == ("first-copy", b"first")
        queue.task_done()
        assert await queue.pull() == ("second", b"second")

    asyncio.run(scenario())


def test_transport_queue_preserves_new_items_matching_an_incomplete_item() -> None:
    async def scenario() -> None:
        queue: TransportQueue[str] = TransportQueue(DummyEncoder())
        queue.push("first")

        assert await queue.pull() == ("first", b"first")
        queue.push("first-copy")
        queue.push("second")
        assert await queue.pull() == ("first", b"first")
        queue.task_done()

        queue.push("first-copy")
        assert await queue.pull() == ("first-copy", b"first")
        queue.task_done()
        assert await queue.pull() == ("second", b"second")
        queue.task_done()
        assert await queue.pull() == ("first-copy", b"first")

    asyncio.run(scenario())


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_transport_queues_preserve_state_transitions_and_repeated_increments(
    direction: str,
) -> None:
    async def scenario() -> None:
        transport = Transport(CommandEncoder(read_only=False), "127.0.0.1")
        queue = getattr(transport, direction)
        commands = [
            StandbyPowerCommand(output=1, is_on=ToggleBool.On),
            StandbyPowerCommand(output=1, is_on=ToggleBool.Off),
            StandbyPowerCommand(output=1, is_on=ToggleBool.On),
            VolumeUpCommand(output=1),
            VolumeUpCommand(output=1),
        ]
        for command in commands:
            queue.push(command)
        queue.shutdown()

        for expected in commands:
            actual, _ = await queue.pull()
            assert actual == expected
            queue.task_done()
        with pytest.raises(TransportQueueClosed):
            await queue.pull()

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


@pytest.mark.parametrize("already_running", [False, True])
def test_transport_send_validates_entire_batch_before_mutating_queue(
    already_running: bool,
) -> None:
    async def scenario() -> None:
        transport = Transport(DummyEncoder(), "127.0.0.1")

        async def fake_loop() -> None:
            await asyncio.Event().wait()

        transport._loop = fake_loop  # type: ignore[method-assign]
        if already_running:
            transport.send("existing")
        previous_loop = transport._loop_task
        try:
            with pytest.raises(ValueError, match="invalid operation"):
                transport.send("first", "invalid")

            assert transport._loop_task is previous_loop
            transport.outbound.shutdown()
            if already_running:
                assert await transport.outbound.pull() == ("existing", b"existing")
                transport.outbound.task_done()
            with pytest.raises(TransportQueueClosed):
                await transport.outbound.pull()
        finally:
            await transport.aclose()

    asyncio.run(scenario())


def test_transport_validate_send_does_not_start_loop_or_enqueue() -> None:
    async def scenario() -> None:
        transport = Transport(DummyEncoder(), "127.0.0.1")

        transport.validate_send("first", "skip", "second")
        with pytest.raises(ValueError, match="invalid operation"):
            transport.validate_send("first", "invalid")

        assert transport._loop_task is None
        transport.outbound.shutdown()
        with pytest.raises(TransportQueueClosed):
            await transport.outbound.pull()

    asyncio.run(scenario())


def test_transport_send_encodes_each_operation_once_and_omits_suppressed_ops() -> None:
    async def scenario() -> None:
        encoded_values: list[str] = []

        class CountingEncoder(DummyEncoder):
            def encode(self, value: str) -> HexBytes | None:
                encoded_values.append(value)
                return super().encode(value)

        transport = Transport(CountingEncoder(), "127.0.0.1")

        async def fake_loop() -> None:
            await asyncio.Event().wait()

        transport._loop = fake_loop  # type: ignore[method-assign]
        try:
            transport.send("first", "skip", "first-copy")

            assert encoded_values == ["first", "skip", "first-copy"]
            transport.outbound.shutdown()
            assert await transport.outbound.pull() == ("first", b"first")
            transport.outbound.task_done()
            assert await transport.outbound.pull() == ("first-copy", b"first")
            transport.outbound.task_done()
            with pytest.raises(TransportQueueClosed):
                await transport.outbound.pull()
        finally:
            await transport.aclose()

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
            assert written == [b"6669727374\r\n"]
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


def test_transport_recv_stops_after_shutdown_while_paused_at_yield() -> None:
    async def scenario() -> None:
        transport = Transport(DummyEncoder(), "127.0.0.1")

        async def fake_loop() -> None:
            await asyncio.Event().wait()

        transport._loop = fake_loop  # type: ignore[method-assign]
        transport.inbound.push("decoded")
        receiver = transport.recv()

        assert await receiver.__anext__() == "decoded"
        await transport.aclose()

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(receiver.__anext__(), timeout=1)

    asyncio.run(scenario())


def test_transport_can_restart_immediately_after_shutdown() -> None:
    async def scenario() -> None:
        transport = Transport(DummyEncoder(), "127.0.0.1")

        async def fake_loop() -> None:
            await asyncio.Event().wait()

        transport._loop = fake_loop  # type: ignore[method-assign]
        transport.send("first")
        old_loop = transport._loop_task
        assert old_loop is not None
        transport.shutdown()
        transport.send("second")
        new_loop = transport._loop_task
        assert new_loop is not None
        try:
            assert new_loop is not old_loop
            await asyncio.gather(old_loop, return_exceptions=True)
            assert transport._loop_task is new_loop
            assert not new_loop.done()
            assert await transport.outbound.pull() == ("second", b"second")
        finally:
            await transport.aclose()

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
