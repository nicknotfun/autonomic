import asyncio

from amp.transport import OpQueue, Transport


class DummyEncoder:
    def encode(self, value: str) -> bytes | None:
        return value.encode("ascii")

    def decoder(self, value: bytes) -> str:
        return value.decode("ascii")


def test_op_queue_retries_incomplete_item_until_task_done():
    async def scenario() -> None:
        queue = OpQueue[int]()
        queue.push(1)
        queue.push(2)

        assert await queue.pull() == 1
        assert await queue.pull() == 1
        queue.task_done()
        assert await queue.pull() == 2

    asyncio.run(scenario())


def test_transport_send_starts_loop_and_queues_outbound_ops():
    async def scenario() -> None:
        transport = Transport(DummyEncoder(), "127.0.0.1")

        async def fake_loop() -> None:
            await asyncio.Event().wait()

        transport._loop = fake_loop  # type: ignore[method-assign]
        transport.send("first", "second")

        assert transport._loop_task is not None
        assert await transport.outbound.pull() == "first"
        transport.outbound.task_done()
        assert await transport.outbound.pull() == "second"
        transport.shutdown()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_transport_recv_starts_loop_and_yields_inbound_ops():
    async def scenario() -> None:
        transport = Transport(DummyEncoder(), "127.0.0.1")

        async def fake_loop() -> None:
            await asyncio.Event().wait()

        transport._loop = fake_loop  # type: ignore[method-assign]
        transport.inbound.put_nowait("decoded")
        receiver = transport.recv()

        assert await receiver.__anext__() == "decoded"
        assert transport._loop_task is not None
        await receiver.aclose()
        transport.shutdown()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_transport_context_manager_returns_self_and_shutdown_is_idempotent():
    transport = Transport(DummyEncoder(), "127.0.0.1")

    with transport as active:
        assert active is transport

    assert transport._loop_task is None
    transport.shutdown()
    assert transport._loop_task is None


def test_transport_trace_respects_trace_flag_and_truncates_long_inbound_rows(capsys):
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
