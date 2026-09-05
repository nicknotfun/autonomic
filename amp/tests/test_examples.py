import argparse
import asyncio
import importlib
from collections.abc import AsyncGenerator
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

from amp.byte_utils import HexBytes
from amp.codec import (
    Command,
    DistributedSourceDefinitionCommand,
    MuteCommand,
    SourceSelectionCommand,
    StandbyPowerCommand,
    VolumeCommand,
)
from amp.state import DeviceState, InputState, OutputState, SystemState
from amp.system import System
from amp.toggle_bool import ToggleBool
from amp.transport import BaseTransport, ConnectionInterrupted


FIRST_DEVICE = HexBytes("00D4")
SECOND_DEVICE = HexBytes("6012")
GUID = UUID("674e1900-f8a9-f6be-a465-3d0fbee12977")


class FakeTransport(BaseTransport[Command]):
    def __init__(self, host: str) -> None:
        self.host = host
        self.sent: list[Command] = []
        self.events: asyncio.Queue[Command | ConnectionInterrupted | None] = asyncio.Queue()

    def send(self, *ops: Command) -> None:
        self.sent.extend(ops)

    async def recv(self) -> AsyncGenerator[Command | ConnectionInterrupted, None]:
        while (event := await self.events.get()) is not None:
            yield event

    def shutdown(self) -> None:
        self.events.put_nowait(None)

    async def aclose(self) -> None:
        self.shutdown()


class FakeSystem(System):
    discovery_timeout = False

    async def discover(
        self,
        *,
        target_devices: int = 2,
        time_between_probes_secs: float = 0.5,
        time_to_wait_for_devices_with_unknown_inputs: float = 2.0,
        timeout_secs: float | None = None,
    ) -> None:
        if self.discovery_timeout:
            raise TimeoutError


class ExampleRunner:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "examples"))
        self.transports = (FakeTransport("amp-one"), FakeTransport("amp-two"))
        self.state = SystemState()
        self.state.devices[FIRST_DEVICE] = DeviceState(
            id=FIRST_DEVICE, host="amp-one", outputs=(1,), guid=GUID
        )
        self.state.devices[SECOND_DEVICE] = DeviceState(
            id=SECOND_DEVICE, host="amp-two", outputs=(2,)
        )
        self.state.outputs[1] = OutputState(id=1, muted=False)
        self.state.outputs[2] = OutputState(id=2, muted=True)
        self.state.inputs[(FIRST_DEVICE, 2)] = InputState(
            device_id=FIRST_DEVICE, selector=2, assigned_name="W1"
        )
        self.state.inputs[(SECOND_DEVICE, 2)] = InputState(
            device_id=SECOND_DEVICE, selector=2, assigned_name=".200 OPT1"
        )
        self.systems: list[FakeSystem] = []
        self.discovery_timeout = False

    def module(self, name: str) -> ModuleType:
        return importlib.import_module(name)

    def run(self, name: str, *args: str) -> None:
        module = self.module(name)

        def create_system(
            hosts: tuple[str, ...], *, read_only: bool, trace: bool
        ) -> FakeSystem:
            assert read_only is False
            system = FakeSystem(self.transports, state=self.state, read_only=False)
            system.discovery_timeout = self.discovery_timeout
            self.systems.append(system)
            return system

        self.monkeypatch.setattr(module, "System", create_system)
        self.monkeypatch.setattr("sys.argv", [name, "--settle", "0", *args])
        asyncio.run(module.async_main())

    @property
    def sent(self) -> list[Command]:
        return [command for transport in self.transports for command in transport.sent]


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> ExampleRunner:
    return ExampleRunner(monkeypatch)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("add_eaudiocast_sources", [f"00D4:0:{GUID}:2:W1"]),
        ("set_all_outputs_to_source", []),
        ("set_all_zones_to_200_opt1", []),
        ("unmute_set_all_to_50_and_power_on", []),
    ],
)
def test_writing_examples_abort_on_discovery_timeout(
    runner: ExampleRunner, name: str, arguments: list[str]
) -> None:
    runner.discovery_timeout = True

    with pytest.raises(TimeoutError, match="no configuration writes"):
        runner.run(name, *arguments)

    assert runner.sent == []


def test_reading_examples_can_still_use_partial_discovery(
    runner: ExampleRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    async def exercise() -> None:
        system = FakeSystem(runner.transports, state=runner.state)
        system.discovery_timeout = True
        with system:
            await runner.module("_system_example").discover_or_timeout(
                system, argparse.Namespace(target_devices=2, timeout=1)
            )

    asyncio.run(exercise())

    assert "continuing with partial state" in capsys.readouterr().out
    assert runner.sent == []


def test_eaudiocast_unknown_later_device_sends_nothing(runner: ExampleRunner) -> None:
    with pytest.raises(ValueError, match="Target device 1234 was not discovered"):
        runner.run(
            "add_eaudiocast_sources",
            f"00D4:0:{GUID}:2:W1",
            f"1234:1:{GUID}:4:W2",
        )

    assert runner.sent == []


@pytest.mark.parametrize("host", [None, "unconnected-amp"])
def test_eaudiocast_missing_later_host_mapping_sends_nothing(
    runner: ExampleRunner, host: str | None
) -> None:
    runner.state.devices[SECOND_DEVICE].host = host

    with pytest.raises(ValueError, match="Target device 6012 has no known transport"):
        runner.run(
            "add_eaudiocast_sources",
            f"00D4:0:{GUID}:2:W1",
            f"6012:1:{GUID}:4:W2",
        )

    assert runner.sent == []


@pytest.mark.parametrize(
    "definition",
    [
        f"D4:0:{GUID}:2:W1",
        f"00D4:-1:{GUID}:2:W1",
        f"00D4:32:{GUID}:2:W1",
        f"00D4:0:{GUID}:256:W1",
        f"00D4:0:{GUID}:-1:W1",
        "00D4:0:bad-guid:2:W1",
    ],
)
def test_eaudiocast_bad_definition_is_rejected_before_connecting(
    runner: ExampleRunner, definition: str
) -> None:
    with pytest.raises(SystemExit) as exc:
        runner.run("add_eaudiocast_sources", f"00D4:0:{GUID}:2:W1", definition)

    assert exc.value.code == 2
    assert runner.systems == []
    assert runner.sent == []


def test_eaudiocast_preflight_encodes_all_programmatic_definitions(
    runner: ExampleRunner,
) -> None:
    module = runner.module("add_eaudiocast_sources")
    definitions = [
        module.DistributedSourceDefinition(FIRST_DEVICE, 0, GUID, 2, "W1"),
        module.DistributedSourceDefinition(SECOND_DEVICE, 1, GUID, 256, "W2"),
    ]

    async def exercise() -> None:
        with FakeSystem(runner.transports, state=runner.state) as system:
            with pytest.raises(ValueError):
                module.prepare_definitions(system, definitions)

    asyncio.run(exercise())

    assert runner.sent == []


def test_eaudiocast_uses_each_exact_device_transport(runner: ExampleRunner) -> None:
    runner.run(
        "add_eaudiocast_sources",
        f"00D4:0:{GUID}:2:W1",
        f"6012:1:{GUID}:4:W2",
    )

    assert runner.transports[0].sent == [
        DistributedSourceDefinitionCommand(
            slot_id=0, backing_device_guid=GUID, source_index=2, name="W1"
        )
    ]
    assert runner.transports[1].sent == [
        DistributedSourceDefinitionCommand(
            slot_id=1, backing_device_guid=GUID, source_index=4, name="W2"
        )
    ]


def test_source_example_resolves_both_inputs_before_sending(runner: ExampleRunner) -> None:
    runner.state.inputs.pop((SECOND_DEVICE, 2))

    with pytest.raises(ValueError, match="was not discovered on device 6012"):
        runner.run("set_all_zones_to_200_opt1")

    assert runner.sent == []


def test_all_outputs_source_example_validates_cross_device_route_before_writes(
    runner: ExampleRunner,
) -> None:
    with pytest.raises(ValueError):
        runner.run("set_all_outputs_to_source", "W1")

    assert runner.sent == []


def test_source_example_mutes_during_routes_then_restores_mute(runner: ExampleRunner) -> None:
    runner.run("set_all_zones_to_200_opt1")

    for output_id, transport in enumerate(runner.transports, start=1):
        assert transport.sent[0] == MuteCommand(output=output_id, is_muted=ToggleBool.On)
        assert isinstance(transport.sent[1], SourceSelectionCommand)
        assert transport.sent[-1] == MuteCommand(
            output=output_id,
            is_muted=ToggleBool.Off if output_id == 1 else ToggleBool.On,
        )


@pytest.mark.parametrize("volume", ["-0.01", "1.01", "nan", "inf", "-inf"])
def test_volume_example_rejects_invalid_volume_before_connecting(
    runner: ExampleRunner, volume: str
) -> None:
    with pytest.raises(SystemExit) as exc:
        runner.run("unmute_set_all_to_50_and_power_on", f"--volume={volume}")

    assert exc.value.code == 2
    assert runner.systems == []
    assert runner.sent == []


def test_volume_example_mutes_then_sets_volume_before_power_and_unmute(
    runner: ExampleRunner,
) -> None:
    runner.run("unmute_set_all_to_50_and_power_on", "--volume=0.6")

    for output_id, transport in enumerate(runner.transports, start=1):
        assert transport.sent == [
            MuteCommand(output=output_id, is_muted=ToggleBool.On),
            VolumeCommand(output=output_id, volume=0.6),
            StandbyPowerCommand(output=output_id, is_on=ToggleBool.On),
            MuteCommand(output=output_id, is_muted=ToggleBool.Off),
        ]


@pytest.mark.parametrize(
    "example",
    ["set_all_zones_to_200_opt1", "unmute_set_all_to_50_and_power_on"],
)
def test_output_examples_validate_later_host_before_any_writes(
    runner: ExampleRunner, example: str
) -> None:
    runner.state.devices[SECOND_DEVICE].host = None

    with pytest.raises(ValueError, match="Output 2 has no known device transport"):
        runner.run(example)

    assert runner.sent == []


def test_output_plan_encodes_later_command_before_first_mute(runner: ExampleRunner) -> None:
    async def exercise() -> None:
        with FakeSystem(runner.transports, state=runner.state) as system:
            with pytest.raises(ValueError):
                runner.module("_system_example").send_output_plan(
                    system,
                    [VolumeCommand(output=1, volume=0.6), VolumeCommand(output=2, volume=1.5)],
                    unmute=True,
                )

    asyncio.run(exercise())

    assert runner.sent == []
