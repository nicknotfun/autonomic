import asyncio
import logging
from typing import Any, Callable, Generic, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel
from sortedcontainers import SortedDict  # type: ignore[import-untyped]

from amp.byte_utils import HexBytes
from amp.codec import ALL_OUTPUTS, Op, SourceNameOp
import amp.codec as codec
from amp.transport import ConnectionInterrupted, Transport

logger = logging.getLogger(__name__)


class Device(BaseModel):
    id: HexBytes
    ip: str | None = None
    firmware: int | None = None
    model_id: HexBytes | None = None
    input_count: int | None = None
    outputs: tuple[int, ...] | None = None
    mac: HexBytes | None = None
    guid: UUID | None = None

    def needed_update_ops(self) -> list[codec.Op]:
        ops: list[codec.Op] = []
        if self.firmware is None or self.model_id is None or self.outputs is None:
            ops.append(codec.DeviceInfoDiscoveryOp())
        if self.guid is None:
            ops.append(codec.DeviceGuidQueryOp(device_id=self.id))
        if self.mac is None:
            ops.append(codec.ExtendedDeviceInfoDiscoveryOp(device_id=self.id))
        return ops

    def update(self, op: codec.DeviceIdOp) -> None:
        if op.device_id != self.id:
            return
        match op:
            case codec.DeviceInfoOp():
                self.firmware = op.firmware
                self.model_id = op.model_id
                self.outputs = op.zones
            case codec.DeviceGuidOp():
                self.guid = op.guid
            case codec.ExtendedDeviceInfoOp():
                self.mac = op.mac


PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR = {
    0x05: 1,
    0x06: 2,
    0x07: 3,
    0x03: 4,
    0x00: 5,
    0x01: 6,
    0x02: 7,
    0x04: 8,
    0x08: 9,
    0x09: 10,
    0x0A: 11,
    0x0B: 12,
    0x0C: 13,
    0x0D: 14,
    0x0E: 15,
    0x0F: 16,
}


class Input(BaseModel):
    device_id: HexBytes
    selector: int
    name: str
    hidden_name: str | None

    @property
    def remote(self) -> bool:
        return self.selector >= 0x20 and self.selector < 0x50

    @property
    def physical_source_id(self) -> int | None:
        if self.selector >= 0x20:
            return None
        return PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR.get(self.selector)

    @property
    def qualified_name(self) -> str:
        if self.physical_source_id is not None:
            return f"{self.device_id}:{self.physical_source_id}"
        else:
            return f"{self.device_id}:0x{self.selector:02X}"

    def update(self, op: SourceNameOp) -> None:
        if op.name is None:
            return
        self.name = op.name
        self.hidden_name = op.hidden_name


class UnknownValue:
    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN: Any = UnknownValue()


class Output(BaseModel):
    id: int
    name: str | None = UNKNOWN
    on: bool | None = UNKNOWN
    muted: bool | None = UNKNOWN
    source: HexBytes | None = UNKNOWN
    volume: float | None = UNKNOWN

    def needed_update_ops(self) -> list[codec.Op]:
        ops: list[codec.Op] = []
        if self.name is UNKNOWN:
            ops.append(codec.OutputNameRefreshOp(output=self.id))
        if self.on is UNKNOWN:
            ops.append(codec.PowerOp(output=self.id))
        if self.muted is UNKNOWN:
            ops.append(codec.MuteOp(output=self.id))
        if self.source is UNKNOWN:
            ops.append(codec.SourceSelectOp(output=self.id))
        if self.volume is UNKNOWN:
            ops.append(codec.VolumeOp(output=self.id))
        return ops

    def update(self, op: codec.OutputOp) -> None:
        if op.output != self.id and op.output != ALL_OUTPUTS:
            return
        match op:
            case codec.PowerOp():
                if op.is_on is not None:
                    self.on = op.is_on.as_bool(self.on)
            case codec.MuteOp():
                if op.is_muted is not None:
                    self.muted = op.is_muted.as_bool(self.muted)
            case codec.SourceSelectOp():
                self.source = op.source
            case codec.VolumeOp():
                if op.volume is not None:
                    self.volume = op.volume
            case codec.OutputNameOp():
                if op.name is not None:
                    self.name = op.name


class RemoteInput(BaseModel):
    id: int
    device_guid: UUID | None = None
    source_index: int | None = None
    name: str | None = None


KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


class KeyBasedDefaultDict(SortedDict, Generic[KeyT, ValueT]):  # type: ignore[misc]
    def __init__(self, default_factory: Callable[[KeyT], ValueT]) -> None:
        super().__init__()
        self.default_factory = default_factory

    def __getitem__(self, key: KeyT) -> ValueT:
        if key not in self:
            self[key] = self.default_factory(key)
        return cast(ValueT, super().__getitem__(key))


class System:
    def __init__(self, transport: Transport[Op]) -> None:
        self.devices: dict[HexBytes, Device] = KeyBasedDefaultDict(
            lambda device_id: Device(id=device_id)
        )
        self.inputs: dict[tuple[HexBytes, int], Input] = KeyBasedDefaultDict(
            lambda device_and_selector: Input(
                device_id=device_and_selector[0],
                selector=device_and_selector[1],
                name=f"Input {device_and_selector[1]:02X}",
                hidden_name=None,
            )
        )
        self.outputs: dict[int, Output] = KeyBasedDefaultDict(
            lambda output_id: Output(id=output_id)
        )
        self.transport = transport
        self._state_changed = asyncio.Event()
        self.task = asyncio.create_task(self._handle_events())

    def shutdown(self) -> None:
        self.task.cancel()
        self.transport.shutdown()

    def __enter__(self) -> "System":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown()

    def dump(self) -> None:
        print(f"Devices ({len(self.devices)}):")
        for device in self.devices.values():
            print(f"  {device}")
        print(f"Inputs ({len(self.inputs)}):")
        for (device_id, selector), input in sorted(
            self.inputs.items(),
            key=lambda item: item[1].qualified_name,
        ):
            print(f"  {input.qualified_name}: {input}")
        print(f"Outputs ({len(self.outputs)}):")
        for _, output in self.outputs.items():
            print(f"  {output}")
        print()

    def device_for_output(self, output_id: int) -> Device | None:
        for device in self.devices.values():
            if isinstance(device.outputs, tuple) and output_id in device.outputs:
                return device
        return None

    def inputs_by_device(self, device_id: HexBytes, include_remote: bool = True) -> list[Input]:
        return [
            input
            for (input_device_id, _), input in self.inputs.items()
            if input_device_id == device_id and (include_remote or not input.remote)
        ]

    async def _wait_for_state_change_or_timeout(
        self,
        is_complete: Callable[[], bool],
        timeout_secs: float,
    ) -> None:
        if timeout_secs <= 0:
            await asyncio.sleep(0)
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_secs
        while not is_complete():
            self._state_changed.clear()
            remaining_secs = deadline - loop.time()
            if remaining_secs <= 0:
                break
            try:
                await asyncio.wait_for(self._state_changed.wait(), timeout=remaining_secs)
            except asyncio.TimeoutError:
                break

    async def _handle_events(self) -> None:
        async for op in self.transport.recv():
            match op:
                case ConnectionInterrupted():
                    self.refresh()
                case codec.ThisDeviceIdOp():
                    # Special case as this lets us discover the IP that only we know!
                    self.devices[op.device_id].ip = self.transport.host
                case codec.SourceNameOp():
                    if device := self.device_for_output(op.output):
                        self.inputs[(device.id, op.source_selector)].update(op)
                case codec.DeviceIdOp():
                    self.devices[op.device_id].update(op)
                case codec.OutputOp():
                    if isinstance(op, codec.InputGainOp):
                        # Input gain ops are how we can discover the number of expected outputs for a device!
                        device = self.device_for_output(op.output)
                        if (
                            device is not None
                            and device.input_count is None
                            and op.source_selector == 0xFF
                            and op.gains is not None
                        ):
                            device.input_count = len(op.gains)

                    self.outputs[op.output].update(op)
                case _:
                    continue
            self._state_changed.set()

    async def discover_devices(
        self,
        target_devices: int = 2,
        *,
        time_between_probes_secs: float = 0.5,
    ) -> None:
        def needed_probe_ops() -> set[codec.Op]:
            probe_ops: set[codec.Op] = set()
            for device in self.devices.values():
                probe_ops.update(device.needed_update_ops())
                if device.outputs and device.input_count is None:
                    # Use input gains to detect input count.
                    probe_ops.add(codec.InputGainOp(output=device.outputs[0], source_selector=0xFF))

            if len(self.devices) < target_devices:
                probe_ops.add(codec.DeviceIdDiscoveryOp())
                probe_ops.add(codec.DeviceInfoDiscoveryOp())
            return probe_ops

        while True:
            probe_ops = needed_probe_ops()
            if len(probe_ops) == 0:
                break

            self.transport.send(*probe_ops)
            await self._wait_for_state_change_or_timeout(
                lambda: len(needed_probe_ops()) == 0,
                time_between_probes_secs,
            )

    async def discover_inputs(self, *, time_between_probes_secs: float = 0.5) -> None:
        # For every device where we don't know the input count, send a single hail mary attempt to detect anything.
        hail_marys = 0
        for device in self.devices.values():
            if device.outputs and device.input_count is None:
                self.transport.send(codec.SourceNameDiscoveryOp(output=device.outputs[0]))
                hail_marys += 1
        # Give a bit of a wait to let them work.
        await self._wait_for_state_change_or_timeout(
            lambda: all(
                not device.outputs or device.input_count is not None
                for device in self.devices.values()
            ),
            time_between_probes_secs * hail_marys,
        )

        def missing_input_count() -> int:
            total_missing_inputs = 0
            for device in self.devices.values():
                if not device.outputs or device.input_count is None:
                    # We don't know enough about this device to know when we're done.
                    continue
                detected_inputs = len(self.inputs_by_device(device.id, include_remote=False))
                missing_inputs = device.input_count - detected_inputs
                total_missing_inputs += missing_inputs
            return total_missing_inputs

        def send_missing_input_probes() -> None:
            for device in self.devices.values():
                if not device.outputs or device.input_count is None:
                    continue
                detected_inputs = len(self.inputs_by_device(device.id, include_remote=False))
                missing_inputs = device.input_count - detected_inputs
                if missing_inputs > 0:
                    self.transport.send(codec.SourceNameDiscoveryOp(output=device.outputs[0]))

        while missing_input_count() > 0:
            send_missing_input_probes()
            await self._wait_for_state_change_or_timeout(
                lambda: missing_input_count() == 0,
                time_between_probes_secs,
            )

    def _refresh_all_output_info(self, include_names: bool = False) -> None:
        self.transport.send(
            codec.PowerOp(output=ALL_OUTPUTS),
            codec.MuteOp(output=ALL_OUTPUTS),
            codec.SourceSelectOp(output=ALL_OUTPUTS),
            codec.VolumeOp(output=ALL_OUTPUTS),
        )
        if include_names:
            self.transport.send(codec.OutputNameRefreshOp(output=ALL_OUTPUTS))

    async def discover_outputs(self, *, time_between_probes_secs: float = 0.5) -> None:
        for device in self.devices.values():
            for output_id in device.outputs or ():
                _ = self.outputs[
                    output_id
                ]  # Ensure the output exists in our dict so we can update it.

        self._refresh_all_output_info(include_names=True)

        def needed_probe_ops() -> set[codec.Op]:
            probe_ops: set[codec.Op] = set()
            for output in self.outputs.values():
                probe_ops.update(output.needed_update_ops())
            return probe_ops

        while True:
            await self._wait_for_state_change_or_timeout(
                lambda: len(needed_probe_ops()) == 0,
                time_between_probes_secs,
            )
            probe_ops = needed_probe_ops()
            if len(probe_ops) == 0:
                break
            if len(probe_ops) > 10:
                # Reduce spam.
                probe_ops = list(probe_ops)[:10]
            self.transport.send(*probe_ops)

    async def discover(
        self,
        *,
        target_devices: int = 2,
        time_between_probes_secs: float = 0.5,
    ) -> None:
        self._refresh_all_output_info()
        await self.discover_devices(
            target_devices=target_devices,
            time_between_probes_secs=time_between_probes_secs,
        )
        await asyncio.gather(
            self.discover_inputs(
                time_between_probes_secs=time_between_probes_secs,
            ),
            self.discover_outputs(
                time_between_probes_secs=time_between_probes_secs,
            ),
        )

    def refresh(self) -> None:
        """Don't block on configuration, but attempt to fill in system gaps as well as refresh dynamic info."""
        for device in self.devices.values():
            self.transport.send(*device.needed_update_ops())
        for output in self.outputs.values():
            self.transport.send(*output.needed_update_ops())
        self._refresh_all_output_info()
