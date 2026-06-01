import asyncio
import json
import logging
from collections.abc import Callable, Hashable, Iterable
from pathlib import Path
from typing import Any, TypeAlias, TypeGuard, TypeVar, cast
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.codec import ALL_OUTPUTS, Command, SourceNameOptionsCommand
import amp.codec as codec
from amp.hardware import model_by_number
from amp.toggle_bool import ToggleBool
from amp.transport import ConnectionInterrupted, Transport
from amp.versioned import (
    TrackedDict,
    VersionTrackerMixin,
    VersionedState,
    wait_for_any_change,
)

logger = logging.getLogger(__name__)

REMOTE_INPUT_SLOT_IDS = tuple(range(0x20))
OutputValueT = TypeVar("OutputValueT", bound=Hashable)


def _normalize_name(name: str) -> str:
    return "".join(name.split()).casefold()


class DeviceState(VersionedState):
    id: HexBytes
    host: str | None = None
    firmware: int | None = None
    model_id: HexBytes | None = None
    input_count: int | None = None
    output_count: int | None = None
    outputs: tuple[int, ...] | None = None
    mac: HexBytes | None = None
    guid: UUID | None = None

    def apply_hardware_defaults(self) -> bool:
        old_version = self.version
        if self.model_id is not None:
            model = model_by_number(self.model_id)
            if model is not None:
                self.input_count = model.input_count
                self.output_count = model.output_count
        if self.output_count is None and self.outputs is not None:
            self.output_count = len(self.outputs)
        return self.version != old_version

    def update(self, op: codec.DeviceIdCommand) -> bool:
        if op.device_id != self.id:
            return False
        old_version = self.version
        match op:
            case codec.RequestDeviceInformationCommandResponse():
                self.firmware = op.firmware
                self.model_id = op.model_id
                self.outputs = op.zones
            case codec.RequestZoneAssignmentsCommandResponse():
                self.outputs = op.zones
            case codec.NetworkSettingsDeviceGuidCommand():
                self.guid = op.guid
            case codec.RequestExtendedDeviceInformationCommandResponse():
                self.mac = op.mac
        self.apply_hardware_defaults()
        return self.version != old_version

    def needed_update_ops(self) -> list[codec.Command]:
        ops: list[codec.Command] = []
        if self.firmware is None or self.model_id is None or self.outputs is None:
            ops.append(codec.RequestDeviceInformationCommand())
        if self.guid is None:
            ops.append(codec.NetworkSettingsDeviceGuidRequestCommand(device_id=self.id))
        if self.mac is None:
            ops.append(codec.RequestExtendedDeviceInformationCommand(device_id=self.id))
        return ops


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

LOGICAL_SELECTOR_BY_PHYSICAL_SOURCE_ID = {
    physical_source_id: logical_selector
    for logical_selector, physical_source_id in PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR.items()
}


class InputState(VersionedState):
    device_id: HexBytes
    selector: int
    assigned_name: str | None = None
    hidden_name: str | None = None
    hardware_name: str | None = None

    def __setattr__(self, field_name: str, value: Any) -> None:
        if field_name == "name":
            self.assigned_name = value
            return
        super().__setattr__(field_name, value)

    @property
    def name(self) -> str:
        if self.assigned_name is not None:
            return self.assigned_name
        if self.hardware_name is not None:
            return self.hardware_name
        return f"Input {self.selector:02X}"

    @name.setter
    def name(self, value: str | None) -> None:
        self.assigned_name = value

    @property
    def name_discovered(self) -> bool:
        return self.assigned_name is not None

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

    @classmethod
    def parse_qualified_name(cls, qualified_name: str) -> tuple[HexBytes, int]:
        device_id_str, selector_str = qualified_name.split(":")
        if selector_str.startswith(("0x", "0X")):
            selector = int(selector_str, 0)
        else:
            physical_source_id = int(selector_str, 10)
            selector = LOGICAL_SELECTOR_BY_PHYSICAL_SOURCE_ID.get(
                physical_source_id, physical_source_id
            )
        return HexBytes(device_id_str), selector

    def update(self, op: SourceNameOptionsCommand) -> None:
        if op.name is None:
            return
        self.assigned_name = op.name
        self.hidden_name = op.hidden_name

    def apply_hardware_name(self, name: str) -> None:
        self.hardware_name = name


class OutputState(VersionedState):
    id: int
    name: str | None = None
    on: bool | None = None
    muted: bool | None = None
    source: int | None = None
    volume: float | None = None
    max_volume: float | None = None

    def needed_update_ops(self) -> list[codec.Command]:
        ops: list[codec.Command] = []
        if self.name is None:
            ops.append(codec.ZoneNameRequestCommand(output=self.id))
        if self.on is None:
            ops.append(codec.StandbyPowerCommand(output=self.id))
        if self.muted is None:
            ops.append(codec.MuteCommand(output=self.id))
        if self.source is None:
            ops.append(codec.SourceSelectionCommand(output=self.id))
        if self.volume is None:
            ops.append(codec.VolumeCommand(output=self.id))
        if self.max_volume is None:
            ops.append(codec.MaximumVolumeCommand(output=self.id))
        return ops

    def update(self, op: codec.OutputCommand) -> None:
        if op.output != self.id and op.output != ALL_OUTPUTS:
            return
        match op:
            case codec.StandbyPowerCommand():
                if op.is_on is not None:
                    self.on = op.is_on.as_bool(self.on)
            case codec.MuteCommand():
                if op.is_muted is not None:
                    self.muted = op.is_muted.as_bool(self.muted)
            case codec.SourceSelectionCommand():
                if op.source is not None:
                    self.source = op.source & 0x7F
            case codec.VolumeCommand():
                if op.volume is not None:
                    self.volume = op.volume
            case codec.MaximumVolumeCommand():
                if op.max_volume is not None:
                    self.max_volume = op.max_volume
            case codec.ZoneNameCommand():
                if op.name is not None:
                    self.name = op.name


class RemoteInput(VersionedState):
    id: int
    present: bool | None = None
    device_guid: UUID | None = None
    source_index: int | None = None
    name: str | None = None

    def update(self, op: codec.DistributedSourceDefinitionSlotCommand) -> bool:
        if op.slot_id != self.id:
            return False
        old_version = self.version
        match op:
            case codec.DistributedSourceDefinitionCommand():
                self.present = True
                self.device_guid = op.backing_device_guid
                self.source_index = op.source_index
                self.name = op.name
            case codec.DistributedSourceDefinitionUnusedCommand():
                self.present = False
                self.device_guid = None
                self.source_index = None
                self.name = None
        return self.version != old_version

    def needed_update_ops(self) -> list[codec.Command]:
        if self.present is None:
            return [codec.DistributedSourceDefinitionRequestCommand(slot_id=self.id)]
        return []


class SystemState(VersionTrackerMixin):
    def __init__(self) -> None:
        super().__init__()
        self.devices = TrackedDict[HexBytes, DeviceState](
            lambda device_id: DeviceState(id=device_id), tracker=self
        )
        self.inputs = TrackedDict[tuple[HexBytes, int], InputState](
            lambda device_and_selector: InputState(
                device_id=device_and_selector[0],
                selector=device_and_selector[1],
            ),
            tracker=self,
        )
        self.outputs = TrackedDict[int, OutputState](
            lambda output_id: OutputState(id=output_id), tracker=self
        )
        self.remote_inputs = TrackedDict[int, RemoteInput](
            lambda remote_input_id: RemoteInput(id=remote_input_id), tracker=self
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "devices": {
                str(device_id): device.model_dump(mode="json", exclude={"id"})
                for device_id, device in self.devices.items()
            },
            "inputs": {
                f"{input.device_id}:0x{input.selector:02X}": input.model_dump(
                    mode="json", exclude={"device_id", "selector"}
                )
                for input in self.inputs.values()
            },
            "outputs": {
                output_id: output.model_dump(mode="json", exclude={"id"})
                for output_id, output in self.outputs.items()
            },
            "remote_inputs": {
                remote_input_id: remote_input.model_dump(mode="json", exclude={"id"})
                for remote_input_id, remote_input in self.remote_inputs.items()
            },
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SystemState":
        state = cls()
        for device_id_hex, device_data in data.get("devices", {}).items():
            device_id = HexBytes(device_id_hex)
            state.devices[device_id].merge(DeviceState(id=device_id, **device_data))
        for qualified_name, input_data in data.get("inputs", {}).items():
            device_id, selector = InputState.parse_qualified_name(qualified_name)
            state.inputs[(device_id, selector)].merge(
                InputState(device_id=device_id, selector=selector, **input_data)
            )
        for output_id_str, output_data in data.get("outputs", {}).items():
            output_id = int(output_id_str)
            if isinstance(output_data, dict) and isinstance(output_data.get("source"), str):
                output_data = {
                    **output_data,
                    "source": HexBytes(output_data["source"]).int(),
                }
            state.outputs[output_id].merge(OutputState(id=output_id, **output_data))
        for remote_input_id_str, remote_input_data in data.get("remote_inputs", {}).items():
            remote_input_id = int(remote_input_id_str)
            state.remote_inputs[remote_input_id].merge(
                RemoteInput(id=remote_input_id, **remote_input_data)
            )
        return state

    async def save_to_file(self, file_path: str) -> None:
        payload = json.dumps(self.to_json(), indent=2)
        Path(file_path).write_text(payload, encoding="utf-8")

    @classmethod
    async def load_from_file(cls, file_path: str) -> "SystemState":
        payload = Path(file_path).read_text(encoding="utf-8")
        data = json.loads(payload)
        return cls.from_json(data)

    def merge(self, other: "SystemState") -> None:
        for device_id, other_device in other.devices.items():
            self.devices[device_id].merge(other_device)
        for input_key, other_input in other.inputs.items():
            self.inputs[input_key].merge(other_input)
        for output_id, other_output in other.outputs.items():
            self.outputs[output_id].merge(other_output)
        for remote_input_id, other_remote_input in other.remote_inputs.items():
            self.remote_inputs[remote_input_id].merge(other_remote_input)


TransportArgument: TypeAlias = Transport[Command] | Iterable[Transport[Command]] | str | Iterable[str]


def _is_transport(value: object) -> TypeGuard[Transport[Command]]:
    return hasattr(value, "send") and hasattr(value, "recv") and hasattr(value, "shutdown")


def _normalize_transport_argument(
    transport_arg: TransportArgument,
    *,
    port: int,
    reconnection_wait_secs: float,
    connection_timeout_secs: float,
    trace: bool,
    read_only: bool,
) -> tuple[Transport[Command], ...]:
    if isinstance(transport_arg, str):
        return (
            codec.connect(
                transport_arg,
                port=port,
                reconnection_wait_secs=reconnection_wait_secs,
                connection_timeout_secs=connection_timeout_secs,
                trace=trace,
                read_only=read_only,
            ),
        )
    elif _is_transport(transport_arg):
        return (transport_arg,)
    else:
        transports: list[Transport[Command]] = []
        for item in cast(Iterable[TransportArgument], transport_arg):
            transports.extend(
                _normalize_transport_argument(
                    item,
                    port=port,
                    reconnection_wait_secs=reconnection_wait_secs,
                    connection_timeout_secs=connection_timeout_secs,
                    trace=trace,
                    read_only=read_only,
                )
            )
        return tuple(transports)


class System(VersionTrackerMixin):
    def __init__(
        self,
        transport: TransportArgument,
        state: SystemState | None = None,
        *,
        port: int = 17037,
        reconnection_wait_secs: float = 5.0,
        connection_timeout_secs: float = 10.0,
        trace: bool = False,
        read_only: bool = True,
    ) -> None:
        super().__init__()
        self.transports = _normalize_transport_argument(
            transport,
            port=port,
            reconnection_wait_secs=reconnection_wait_secs,
            connection_timeout_secs=connection_timeout_secs,
            trace=trace,
            read_only=read_only,
        )
        if not self.transports:
            raise ValueError("System requires at least one transport")
        self.transport = self.transports[0]
        self._transports_by_host = {transport.host: transport for transport in self.transports}
        self._pending_device_host_info: list[codec.UndocumentedHostIdentityCommandResponse] = []
        self.state = state or SystemState()
        self.state._parent_version_tracker = self
        self.apply_hardware_defaults()
        self.tasks = [
            asyncio.create_task(self._handle_events(transport)) for transport in self.transports
        ]

    def shutdown(self) -> None:
        for task in self.tasks:
            task.cancel()
        for transport in self.transports:
            transport.shutdown()

    def __enter__(self) -> "System":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown()

    def dump(self) -> None:
        print(f"Devices ({len(self.state.devices)}):")
        for device in self.state.devices.values():
            print(f"  {device}")
        print(f"Inputs ({len(self.state.inputs)}):")
        for (device_id, selector), input in sorted(
            self.state.inputs.items(),
            key=lambda item: item[1].qualified_name,
        ):
            print(f"  {input.qualified_name}: {input}")
        print(f"Outputs ({len(self.state.outputs)}):")
        for _, output in self.state.outputs.items():
            print(f"  {output}")
        print(f"Remote Inputs ({len(self.state.remote_inputs)}):")
        for _, remote_input in self.state.remote_inputs.items():
            print(f"  {remote_input}")
        print()

    def device_for_output(self, output_id: int) -> DeviceState | None:
        for device in self.state.devices.values():
            if device.outputs is not None and output_id in device.outputs:
                return device
        return None

    def inputs_by_device(
        self, device_id: HexBytes, include_remote: bool = True
    ) -> list[InputState]:
        return [
            input
            for (input_device_id, _), input in self.state.inputs.items()
            if input_device_id == device_id and (include_remote or not input.remote)
        ]

    def discovered_inputs_by_device(
        self, device_id: HexBytes, include_remote: bool = True
    ) -> list[InputState]:
        return [
            input
            for input in self.inputs_by_device(device_id, include_remote=include_remote)
            if input.name_discovered
        ]

    def apply_hardware_defaults(self) -> None:
        for device in self.state.devices.values():
            device.apply_hardware_defaults()
            if device.model_id is None:
                continue
            model = model_by_number(device.model_id)
            if model is None:
                continue
            for source in model.sources:
                self.state.inputs[(device.id, source.selector)].apply_hardware_name(source.name)

    def outputs_by_input(self, source_input: InputState) -> list[OutputState]:
        outputs = []
        for output in self.state.outputs.values():
            if output.source is None:
                continue
            if output.source != source_input.selector:
                continue
            output_device = self.device_for_output(output.id)
            if output_device is not None and output_device.id != source_input.device_id:
                continue
            outputs.append(output)
        return outputs

    def input_for_device_selector(self, device_id: HexBytes, selector: int) -> InputState | None:
        return self.state.inputs.get((device_id, selector))

    def transport_for_device(self, device: DeviceState) -> Transport[Command] | None:
        if device.host is None:
            return None
        return self._transports_by_host.get(device.host)

    def transport_for_device_id(self, device_id: HexBytes) -> Transport[Command] | None:
        device = self.state.devices.get(device_id)
        if device is None:
            return None
        return self.transport_for_device(device)

    def transport_for_output(self, output_id: int) -> Transport[Command]:
        device = self.device_for_output(output_id)
        if device is None:
            return self.transport
        return self.transport_for_device(device) or self.transport

    def _target_transports_for_op(self, op: Command) -> tuple[Transport[Command], ...]:
        match op:
            case codec.OutputCommand(output=output_id) if output_id == ALL_OUTPUTS:
                return self.transports
            case codec.OutputCommand(output=output_id):
                device = self.device_for_output(output_id)
                if device is not None:
                    if transport := self.transport_for_device(device):
                        return (transport,)
                    if not op.is_write():
                        return self.transports
                return (self.transport,)
            case codec.DeviceIdCommand(device_id=device_id):
                if transport := self.transport_for_device_id(device_id):
                    return (transport,)
                return self.transports
            case _:
                return self.transports

    def _outputs_for_all_outputs_event(
        self,
        transport: Transport[Command] | None,
    ) -> tuple[OutputState, ...]:
        if transport is not None:
            output_ids = {
                output_id
                for device in self.state.devices.values()
                if device.host == transport.host
                for output_id in (device.outputs or ())
            }
            if output_ids:
                return tuple(self.state.outputs[output_id] for output_id in sorted(output_ids))
        return tuple(self.state.outputs.values())

    def send_ops(self, *ops: Command, transport: Transport[Command] | None = None) -> None:
        if transport is not None:
            routed_ops = [op for op in ops if transport in self._target_transports_for_op(op)]
            if routed_ops:
                transport.send(*routed_ops)
            return

        ops_by_transport: dict[Transport[Command], list[Command]] = {}
        for op in ops:
            for target_transport in self._target_transports_for_op(op):
                ops_by_transport.setdefault(target_transport, []).append(op)
        for target_transport, transport_ops in ops_by_transport.items():
            target_transport.send(*transport_ops)

    def _apply_pending_device_host_info(self) -> None:
        pending = self._pending_device_host_info
        self._pending_device_host_info = []
        for op in pending:
            if not self._apply_device_host_info(op):
                self._pending_device_host_info.append(op)

    def _apply_device_host_info(self, op: codec.UndocumentedHostIdentityCommandResponse) -> bool:
        candidates = set(op.candidate_guids)
        matched = False
        for device in self.state.devices.values():
            if device.mac == op.mac or (device.guid is not None and device.guid in candidates):
                matched = True
                if device.mac is None:
                    device.mac = op.mac
                # 58FF has been observed in both UUID byte orders for the same
                # device. It can enrich identity, but it is not proof that the
                # matched device is local to the transport that emitted the row.
                if device.guid is None and len(candidates) == 1:
                    device.guid = op.guid
        return matched

    def update(
        self,
        op: Command | ConnectionInterrupted,
        transport: Transport[Command] | None = None,
    ) -> None:
        match op:
            case ConnectionInterrupted():
                self.refresh(transport=transport)
            case codec.RequestZoneAssignmentsCommandResponse():
                device = self.state.devices[op.device_id]
                device.host = transport.host if transport is not None else self.transport.host
                device.update(op)
                self.apply_hardware_defaults()
            case codec.SourceNameOptionsCommand():
                if source_device := self.device_for_output(op.output):
                    self.state.inputs[(source_device.id, op.source_selector)].update(op)
            case codec.DeviceIdCommand():
                self.state.devices[op.device_id].update(op)
                self.apply_hardware_defaults()
                self._apply_pending_device_host_info()
            case codec.UndocumentedHostIdentityCommandResponse():
                if not self._apply_device_host_info(op):
                    self._pending_device_host_info.append(op)
            case codec.DistributedSourceDefinitionSlotCommand(slot_id=int(slot_id)):
                self.state.remote_inputs[slot_id].update(op)
            case codec.OutputCommand():
                if isinstance(op, codec.SourceGainCommand):
                    # Input gain ops are how we can discover the number of expected outputs for a device!
                    gain_device = self.device_for_output(op.output)
                    if (
                        gain_device is not None
                        and gain_device.input_count is None
                        and op.source_selector == 0xFF
                        and op.gains is not None
                    ):
                        gain_device.input_count = len(op.gains)

                if op.output == ALL_OUTPUTS:
                    for output in self._outputs_for_all_outputs_event(transport):
                        output.update(op)
                else:
                    if transport is not None:
                        if output_device := self.device_for_output(op.output):
                            if output_device.host is None:
                                output_device.host = transport.host
                    self.state.outputs[op.output].update(op)

    async def _handle_events(self, transport: Transport[Command]) -> None:
        async for op in transport.recv():
            self.update(op, transport=transport)

    def refresh_outputs(
        self,
        include_names: bool = False,
        *,
        transport: Transport[Command] | None = None,
    ) -> None:
        self.send_ops(
            codec.StandbyPowerCommand(output=ALL_OUTPUTS),
            codec.MuteCommand(output=ALL_OUTPUTS),
            codec.SourceSelectionCommand(output=ALL_OUTPUTS),
            codec.VolumeCommand(output=ALL_OUTPUTS),
            codec.MaximumVolumeCommand(output=ALL_OUTPUTS),
            transport=transport,
        )
        if include_names:
            self.send_ops(codec.ZoneNameRequestCommand(output=ALL_OUTPUTS), transport=transport)

    def refresh(self, *, transport: Transport[Command] | None = None) -> None:
        """Don't block on configuration, but fill gaps and refresh dynamic output info."""
        for device in self.state.devices.values():
            self.send_ops(*device.needed_update_ops(), transport=transport)
        for output in self.state.outputs.values():
            self.send_ops(*output.needed_update_ops(), transport=transport)
        for remote_input in self.state.remote_inputs.values():
            self.send_ops(*remote_input.needed_update_ops(), transport=transport)
        self.refresh_outputs(transport=transport)

    async def discover_devices(
        self,
        target_devices: int = 2,
        *,
        time_between_probes_secs: float = 0.5,
    ) -> None:
        def determine_delta() -> tuple[list[DeviceState], set[codec.Command], set[codec.Command]]:
            incomplete_devices = []
            probe_ops: set[codec.Command] = set()
            best_effort_probe_ops: set[codec.Command] = set()

            def mark_incomplete(device: DeviceState, op: codec.Command) -> None:
                incomplete_devices.append(device)
                probe_ops.add(op)

            for device in self.state.devices.values():
                for op in device.needed_update_ops():
                    mark_incomplete(device, op)
                if device.outputs and device.input_count is None:
                    for output_id in device.outputs:
                        best_effort_probe_ops.add(
                            codec.SourceGainCommand(output=output_id, source_selector=0xFF)
                        )
            return incomplete_devices, probe_ops, best_effort_probe_ops

        def missing_host_transports() -> list[Transport[Command]]:
            if len(self.transports) == 1:
                return []
            return [
                transport
                for transport in self.transports
                if not any(device.host == transport.host for device in self.state.devices.values())
            ]

        while True:
            incomplete_devices, probe_ops, best_effort_probe_ops = determine_delta()
            has_enough_devices = len(self.state.devices) >= target_devices
            host_probe_ops: set[codec.Command] = set()
            missing_hosts = missing_host_transports()
            if missing_hosts:
                host_probe_ops.add(codec.RequestZoneAssignmentsCommand())
                host_probe_ops.add(codec.UndocumentedHostIdentityCommand())

            if has_enough_devices and not incomplete_devices and not missing_hosts:
                if best_effort_probe_ops or host_probe_ops:
                    self.send_ops(*(best_effort_probe_ops | host_probe_ops))
                break

            if not has_enough_devices:
                probe_ops.add(codec.RequestZoneAssignmentsCommand())
                probe_ops.add(codec.RequestDeviceInformationCommand())

            previous_version = self.version
            self.send_ops(*(probe_ops | best_effort_probe_ops | host_probe_ops))

            if not has_enough_devices:
                await self.state.devices.wait_for_change(timeout=time_between_probes_secs)
            elif incomplete_devices:
                await wait_for_any_change(incomplete_devices, time_between_probes_secs)
            else:
                await self.wait_for_change(
                    since_version=previous_version,
                    timeout=time_between_probes_secs,
                )

    async def discover_inputs(
        self,
        *,
        time_between_probes_secs: float = 0.5,
        time_to_wait_for_devices_with_unknown_inputs: float = 2.0,
    ) -> None:
        devices_with_outputs = [device for device in self.state.devices.values() if device.outputs]

        probed_outputs_by_device: dict[HexBytes, set[int]] = {}
        unknown_input_count_deadlines: dict[HexBytes, float] = {}

        def unknown_input_count_deadline(device: DeviceState) -> float | None:
            if device.input_count is not None:
                return None
            return unknown_input_count_deadlines.get(device.id)

        def infer_unknown_input_count_after_deadline(device: DeviceState) -> None:
            if device.input_count is not None:
                return
            deadline = unknown_input_count_deadline(device)
            if deadline is None or asyncio.get_running_loop().time() < deadline:
                return
            detected_inputs = len(self.discovered_inputs_by_device(device.id, include_remote=False))
            if detected_inputs > 0:
                device.input_count = detected_inputs

        def determine_probe_ops() -> tuple[list[codec.Command], bool]:
            probe_ops: list[codec.Command] = []
            any_devices_with_unknown_input_count = False
            for device in devices_with_outputs:
                infer_unknown_input_count_after_deadline(device)
                detected_inputs = len(
                    self.discovered_inputs_by_device(device.id, include_remote=False)
                )
                if device.input_count is None:
                    any_devices_with_unknown_input_count = True
                elif detected_inputs >= device.input_count:
                    continue

                probed_outputs = probed_outputs_by_device.setdefault(device.id, set())
                if (
                    device.input_count is not None
                    and detected_inputs < device.input_count
                    and device.outputs is not None
                    and all(output_id in probed_outputs for output_id in device.outputs)
                ):
                    probed_outputs.clear()
                for output_id in device.outputs or ():
                    if output_id in probed_outputs:
                        continue
                    probed_outputs.add(output_id)
                    if (
                        device.input_count is None
                        and device.id not in unknown_input_count_deadlines
                    ):
                        unknown_input_count_deadlines[device.id] = (
                            asyncio.get_running_loop().time()
                            + time_to_wait_for_devices_with_unknown_inputs
                        )
                    probe_ops.append(codec.SourceNameOptionsRequestCommand(output=output_id))
            return probe_ops, any_devices_with_unknown_input_count

        def input_tables_ready() -> bool:
            for device in devices_with_outputs:
                infer_unknown_input_count_after_deadline(device)
                detected_inputs = len(
                    self.discovered_inputs_by_device(device.id, include_remote=False)
                )
                if device.input_count is None:
                    deadline = unknown_input_count_deadline(device)
                    if deadline is None or asyncio.get_running_loop().time() < deadline:
                        return False
                    if detected_inputs > 0:
                        device.input_count = detected_inputs
                    else:
                        continue
                elif detected_inputs < device.input_count:
                    return False
            return True

        while True:
            probe_ops, any_devices_with_unknown_input_count = determine_probe_ops()
            if not probe_ops:
                break

            self.send_ops(*probe_ops)

            probe_time = time_between_probes_secs
            if any_devices_with_unknown_input_count:
                probe_time = time_to_wait_for_devices_with_unknown_inputs
            await self.wait_for_ready(input_tables_ready, probe_time)

    async def discover_remote_inputs(
        self,
        *,
        slot_ids: Iterable[int] | None = None,
        time_between_probes_secs: float = 0.5,
    ) -> None:
        slot_ids = REMOTE_INPUT_SLOT_IDS if slot_ids is None else tuple(slot_ids)
        for slot_id in slot_ids:
            _ = self.state.remote_inputs[slot_id]

        def needed_probe_ops() -> list[codec.Command]:
            probe_ops: list[codec.Command] = []
            for slot_id in slot_ids:
                probe_ops.extend(self.state.remote_inputs[slot_id].needed_update_ops())
            return probe_ops

        while True:
            probe_ops = needed_probe_ops()
            if not probe_ops:
                break
            self.send_ops(*probe_ops)
            await self.wait_for_ready(
                lambda: len(needed_probe_ops()) == 0,
                time_between_probes_secs,
            )

    async def discover_outputs(self, *, time_between_probes_secs: float = 0.5) -> None:
        for device in self.state.devices.values():
            for output_id in device.outputs or ():
                _ = self.state.outputs[
                    output_id
                ]  # Ensure the output exists in our dict so we can update it.

        self.refresh_outputs(include_names=True)

        def needed_probe_ops() -> set[codec.Command]:
            probe_ops: set[codec.Command] = set()
            for output in self.state.outputs.values():
                probe_ops.update(output.needed_update_ops())
            return probe_ops

        while True:
            await self.wait_for_ready(
                lambda: len(needed_probe_ops()) == 0,
                time_between_probes_secs,
            )
            probe_ops = list(needed_probe_ops())
            if len(probe_ops) == 0:
                break
            if len(probe_ops) > 10:
                # Reduce spam.
                probe_ops = probe_ops[:10]
            self.send_ops(*probe_ops)

    async def discover(
        self,
        *,
        target_devices: int = 2,
        time_between_probes_secs: float = 0.5,
        time_to_wait_for_devices_with_unknown_inputs: float = 2.0,
    ) -> None:
        await self.discover_devices(
            target_devices=target_devices,
            time_between_probes_secs=time_between_probes_secs,
        )
        await asyncio.gather(
            self.discover_inputs(
                time_between_probes_secs=time_between_probes_secs,
                time_to_wait_for_devices_with_unknown_inputs=time_to_wait_for_devices_with_unknown_inputs,
            ),
            self.discover_outputs(
                time_between_probes_secs=time_between_probes_secs,
            ),
            self.discover_remote_inputs(time_between_probes_secs=time_between_probes_secs),
        )

    def device(self, device_id: HexBytes) -> "DeviceSelector":
        device = self.state.devices.get(device_id)
        if device is None:
            raise ValueError(f"Device {device_id} not found")
        if len(device.needed_update_ops()) > 0:
            raise ValueError(
                f"Device {device_id} is missing information: {device.needed_update_ops()}"
            )
        return DeviceSelector(self, device_id)

    def device_by_id(self, device_id: HexBytes) -> "DeviceSelector":
        return DeviceSelector(self, device_id)

    def output(self, output_id: int) -> "OutputSelector":
        output = self.state.outputs.get(output_id)
        if output is None:
            raise ValueError(f"Output {output_id} not found")
        if len(output.needed_update_ops()) > 0:
            raise ValueError(
                f"Output {output_id} is missing information: {output.needed_update_ops()}"
            )
        return OutputSelector(self, output_id)

    def output_by_name(self, name: str) -> "OutputSelector":
        normalized_name = _normalize_name(name)
        for output in self.state.outputs.values():
            if output.name is not None and _normalize_name(output.name) == normalized_name:
                return OutputSelector(self, output.id)
        raise ValueError(f"Output with name {name} not found")

    def all_outputs(self) -> "OutputSelector":
        return OutputSelector(self, ALL_OUTPUTS)

    def input_by_name(self, name: str, *, prefer_remote: bool = True) -> "InputSelector":
        normalized_name = _normalize_name(name)
        for input in self.state.inputs.values():
            if prefer_remote and not input.remote:
                continue
            if _normalize_name(input.name) == normalized_name:
                return InputSelector(self, input.device_id, input.selector)
        if prefer_remote:
            return self.input_by_name(name, prefer_remote=False)
        raise ValueError(f"Input with name {name} not found")

    def all_inputs(self) -> "tuple[InputSelector, ...]":
        return tuple(
            InputSelector(self, input.device_id, input.selector)
            for input in self.state.inputs.values()
        )


class DeviceSelector:
    def __init__(self, system: System, device_id: HexBytes) -> None:
        self.system = system
        self.device = self.system.state.devices[device_id]

    @property
    def firmware(self) -> int:
        assert self.device.firmware is not None
        return self.device.firmware

    @property
    def model_id(self) -> HexBytes:
        assert self.device.model_id is not None
        return self.device.model_id

    @property
    def mac(self) -> HexBytes:
        assert self.device.mac is not None
        return self.device.mac

    @property
    def guid(self) -> UUID:
        assert self.device.guid is not None
        return self.device.guid

    @property
    def outputs(self) -> "tuple[OutputSelector, ...]":
        return tuple(
            OutputSelector(self.system, output_id) for output_id in self.device.outputs or ()
        )

    @property
    def inputs(self) -> "tuple[InputSelector, ...]":
        return tuple(
            InputSelector(self.system, self.device.id, input.selector)
            for input in self.system.inputs_by_device(self.device.id)
        )


class OutputSelector:
    def __init__(self, system: System, output_id: int) -> None:
        self.system = system
        self.output_id = output_id

    def _target_output_ids(self) -> tuple[int, ...]:
        if not self.is_all_outputs:
            return (self.output_id,)

        return (ALL_OUTPUTS,)

    def _send_output_ops(self, ops: Iterable[codec.OutputCommand]) -> None:
        ops = tuple(ops)
        if ops:
            self.system.send_ops(*ops)

    def _output_value(
        self, read: Callable[[OutputState], OutputValueT | None]
    ) -> OutputValueT | None:
        output = self.output
        if output is not None:
            return read(output)

        values: set[OutputValueT] = set()
        for output in self.system.state.outputs.values():
            value = read(output)
            if value is not None:
                values.add(value)
        if len(values) == 1:
            return values.pop()
        return None

    @property
    def is_all_outputs(self) -> bool:
        return self.output_id == ALL_OUTPUTS

    @property
    def output(self) -> OutputState | None:
        if self.output_id == ALL_OUTPUTS:
            return None
        return self.system.state.outputs[self.output_id]

    @property
    def name(self) -> str | None:
        output = self.output
        if output is not None:
            return output.name
        return None

    @property
    def on(self) -> bool | None:
        return self._output_value(lambda output: output.on)

    @property
    def muted(self) -> bool | None:
        return self._output_value(lambda output: output.muted)

    @property
    def source(self) -> int | None:
        return self._output_value(lambda output: output.source)

    @property
    def volume(self) -> float | None:
        return self._output_value(lambda output: output.volume)

    @property
    def max_volume(self) -> float | None:
        return self._output_value(lambda output: output.max_volume)

    @property
    def device(self) -> DeviceSelector:
        if self.is_all_outputs:
            raise ValueError("Cannot determine device for ALL_OUTPUTS selector")
        device = self.system.device_for_output(self.output_id)
        if device is None:
            raise ValueError(f"Cannot find device for output {self.output_id}")
        return self.system.device(device.id)

    @property
    def input(self) -> "InputSelector | None":
        if self.is_all_outputs:
            raise ValueError("Cannot determine input for ALL_OUTPUTS selector")
        source = self.source
        if source is None:
            return None
        device = self.system.device_for_output(self.output_id)
        if device is None:
            raise ValueError(f"Cannot find device for output {self.output_id}")
        input = self.system.input_for_device_selector(device.id, source)
        if input is None:
            raise ValueError(
                f"Cannot find input for output {self.output_id} on device {device.id} with source {source}"
            )
        return InputSelector(self.system, input.device_id, input.selector)

    def enable(self, on: bool = True) -> None:
        self._send_output_ops(
            codec.StandbyPowerCommand(
                output=output_id,
                is_on=ToggleBool.On if on else ToggleBool.Off,
            )
            for output_id in self._target_output_ids()
        )

    def disable(self) -> None:
        self.enable(on=False)

    def mute(self, muted: bool = True) -> None:
        self._send_output_ops(
            codec.MuteCommand(
                output=output_id,
                is_muted=ToggleBool.On if muted else ToggleBool.Off,
            )
            for output_id in self._target_output_ids()
        )

    def unmute(self) -> None:
        self.mute(muted=False)

    def set_volume(self, volume: float) -> None:
        self._send_output_ops(
            codec.VolumeCommand(output=output_id, volume=volume)
            for output_id in self._target_output_ids()
        )

    def set_max_volume(self, max_volume: float) -> None:
        self._send_output_ops(
            codec.MaximumVolumeCommand(output=output_id, max_volume=max_volume)
            for output_id in self._target_output_ids()
        )

    def set_input(self, input: "InputSelector") -> None:
        self._send_output_ops(
            codec.SourceSelectionCommand(
                output=output_id,
                source=input.selector,
            )
            for output_id in self._target_output_ids()
        )


class InputSelector:
    def __init__(self, system: System, device_id: HexBytes, selector: int) -> None:
        self.system = system
        self.input = self.system.state.inputs[(device_id, selector)]

    @property
    def selector(self) -> int:
        return self.input.selector

    @property
    def name(self) -> str:
        return self.input.name

    @property
    def device(self) -> DeviceSelector:
        return self.system.device(self.input.device_id)

    @property
    def outputs(self) -> "tuple[OutputSelector, ...]":
        return tuple(
            OutputSelector(self.system, output.id)
            for output in self.system.outputs_by_input(self.input)
        )
