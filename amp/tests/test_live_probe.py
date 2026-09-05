import argparse
import asyncio
from typing import cast

import pytest

from amp.byte_utils import HexBytes
from amp import codec
from amp.codec import (
    RequestZoneAssignmentsCommandResponse,
    SourceNameOptionsCommand,
    SourceSelectionCommand,
)
from amp.toggle_bool import ToggleBool
from amp_live_probe import (
    RawSocketProbe,
    discovered_device_ids,
    discovered_outputs,
    discovered_selectors,
    run_raw_host,
)


class RecordingWriter:
    def __init__(self) -> None:
        self.rows: list[bytes] = []

    def write(self, row: bytes) -> None:
        self.rows.append(row)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def test_live_probe_reports_only_observed_topology() -> None:
    ops = (
        RequestZoneAssignmentsCommandResponse(
            device_id=HexBytes("00DC"),
            zones=(9, 10),
        ),
        SourceNameOptionsCommand(
            output=9,
            source_selector=0x05,
            options=HexBytes("000001"),
            name="A1",
        ),
        SourceSelectionCommand(output=9, source=0x85, detail=(0x20,)),
    )

    assert discovered_device_ids(ops) == (HexBytes("00DC"),)
    assert discovered_outputs(ops) == (9, 10)
    assert discovered_selectors(ops) == (0x05, 0x20)


@pytest.mark.parametrize("enable_noops", [False, True])
def test_raw_query_phase_filters_deletes_and_writes_even_when_noops_are_enabled(
    enable_noops: bool,
) -> None:
    async def scenario() -> None:
        probe = RawSocketProbe(
            "10.1.0.200", port=17037, send_gap=0, idle_wait=0, write_noops=enable_noops
        )
        writer = RecordingWriter()
        probe._writer = cast(asyncio.StreamWriter, writer)

        await probe.send_ops(
            "queries",
            [
                codec.VolumeCommand(output=1),
                codec.ArbitraryDataStorageCommand(slot_id=0x8000),
                codec.VolumeCommand(output=1, volume=0.6),
                codec.MediaServersCommand(device_id=HexBytes("00D4"), entry_index=0),
                codec.ArbitraryDataStorageCommand(slot_id=0),
            ],
        )

        assert writer.rows == [b"0401\r\n", b"4EFF0000\r\n"]
        assert len(probe.probe.errors) == 3

    asyncio.run(scenario())


def test_raw_current_value_writes_require_explicit_opt_in_and_reject_deletes() -> None:
    async def scenario() -> None:
        writer = RecordingWriter()
        probe = RawSocketProbe("10.1.0.200", port=17037, send_gap=0, idle_wait=0)
        probe._writer = cast(asyncio.StreamWriter, writer)
        with pytest.raises(ValueError, match="require --write-noops"):
            await probe.send_ops(
                "noops", [codec.VolumeCommand(output=1, volume=0.6)], write_noops=True
            )
        assert writer.rows == []

        probe.write_noops = True
        with pytest.raises(ValueError, match="not an allowed current-value"):
            await probe.send_ops(
                "noops", [codec.ArbitraryDataStorageCommand(slot_id=0x8000)], write_noops=True
            )
        with pytest.raises(ValueError, match="toggle operations"):
            await probe.send_ops(
                "noops",
                [codec.StandbyPowerCommand(output=1, is_on=ToggleBool.Toggle)],
                write_noops=True,
            )
        assert writer.rows == []

        await probe.send_ops(
            "noops", [codec.VolumeCommand(output=1, volume=0.6)], write_noops=True
        )
        assert writer.rows == [b"040160\r\n"]

    asyncio.run(scenario())


def test_default_raw_probe_never_generates_storage_delete_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        writer = RecordingWriter()

        async def connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            reader = asyncio.StreamReader()
            reader.feed_eof()
            return reader, cast(asyncio.StreamWriter, writer)

        monkeypatch.setattr(asyncio, "open_connection", connect)
        results, _ = await run_raw_host(
            "10.1.0.200", argparse.Namespace(port=17037, send_gap=0, idle_wait=0, write_noops=False)
        )

        storage_rows = [row.raw for row in results.sent if row.op_type == "ArbitraryDataStorageCommand"]
        assert storage_rows == [f"4EFF{slot:04X}" for slot in range(16)]
        assert all("encoder filtered" not in error for error in results.errors)
        encoder = codec.CommandEncoder()
        for row in results.sent:
            command = encoder.decoder(HexBytes(row.raw))
            assert command is not None
            assert not command.is_write()

    asyncio.run(scenario())
