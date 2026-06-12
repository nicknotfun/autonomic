import json
from pathlib import Path
from typing import Any
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.codec import ALL_OUTPUTS, SourceNameOptionsCommand
import amp.codec as codec
from amp.hardware import SourceModelInfo, model_by_number
from amp.versioned import TrackedDict, VersionTrackerMixin, VersionedState


REMOTE_INPUT_SLOT_IDS = tuple(range(0x20))
AUDIO_ONLY_SOURCE_FLAG = 0x40
SOURCE_TURN_ON_FLAG = 0x80
REMOTE_SOURCE_SELECTOR_MIN = 0x20
REMOTE_SOURCE_SELECTOR_MAX = 0x3F


def _guid_candidates(guid: UUID) -> set[UUID]:
    """Build GUID byte-order candidates observed on the wire.

    Returns:
        Set containing the GUID as reported and its Windows wire-order variant.
    """
    return {guid, UUID(bytes_le=guid.bytes)}


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
        """Apply model-derived input and output metadata to this device.

        Returns:
            True when the device state changed, otherwise False.
        """
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
        """Merge a device-scoped protocol response into this device.

        Returns:
            True when the command applied and changed state, otherwise False.
        """
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
        """Build read commands needed to complete this device state.

        Returns:
            Commands for any missing device identity, GUID, or MAC fields.
        """
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
    hardware_kind: str | None = None
    hardware_physical_source_id: int | None = None

    def __setattr__(self, field_name: str, value: Any) -> None:
        """Redirect legacy name assignment to the assigned name field.

        Args:
            field_name: Attribute being assigned.
            value: Value to assign to the attribute.
        """
        if field_name == "name":
            self.assigned_name = value
            return
        super().__setattr__(field_name, value)

    @property
    def name(self) -> str:
        """Return the best available display name for this input.

        Returns:
            Assigned name, hardware name, or generated selector label.
        """
        if self.assigned_name is not None:
            return self.assigned_name
        if self.hardware_name is not None:
            return self.hardware_name
        return f"Input {self.selector:02X}"

    @name.setter
    def name(self, value: str | None) -> None:
        """Set the user-assigned display name for this input."""
        self.assigned_name = value

    @property
    def name_discovered(self) -> bool:
        """Report whether a user-facing name was discovered from the device.

        Returns:
            True when an assigned source name has been read.
        """
        return self.assigned_name is not None

    @property
    def remote(self) -> bool:
        """Report whether this input selector is a distributed source selector.

        Returns:
            True for remote source selectors, otherwise False.
        """
        return OutputState.is_remote_source_selector(self.selector)

    @property
    def physical_source_id(self) -> int | None:
        """Translate the logical selector to the documented physical input id.

        Returns:
            One-based physical source id for local inputs, or None for remote inputs.
        """
        if self.remote:
            return None
        if self.hardware_kind is not None:
            return self.hardware_physical_source_id
        return PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR.get(self.selector)

    @property
    def qualified_name(self) -> str:
        """Return a stable device-qualified input name.

        Returns:
            Name in device-id:physical-id form when possible, otherwise device-id:selector.
        """
        if self.physical_source_id is not None:
            return f"{self.device_id}:{self.physical_source_id}"
        else:
            return f"{self.device_id}:0x{self.selector:02X}"

    @classmethod
    def parse_qualified_name(cls, qualified_name: str) -> tuple[HexBytes, int]:
        """Parse a device-qualified input name into state keys.

        Args:
            qualified_name: Input name in device-id:physical-id or device-id:selector form.

        Returns:
            Device id and logical source selector.
        """
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
        """Merge a source-name response into this input."""
        if op.name is None:
            return
        self.assigned_name = op.name
        self.hidden_name = op.hidden_name

    def apply_hardware_name(self, name: str) -> None:
        """Apply a model-derived input name."""
        self.hardware_name = name

    def apply_hardware_source(self, source: SourceModelInfo) -> None:
        """Apply hardware catalog metadata for this input."""
        self.hardware_name = source.name
        self.hardware_kind = source.kind
        self.hardware_physical_source_id = source.physical_source_id


class OutputState(VersionedState):
    id: int
    name: str | None = None
    on: bool | None = None
    muted: bool | None = None
    source_raw: int | None = None
    source_detail: tuple[int, ...] = ()
    volume: float | None = None
    max_volume: float | None = None

    @staticmethod
    def normalize_source_selector(source: int) -> int:
        """Strip protocol flags from a source selector.

        Returns:
            Logical selector without turn-on or audio-only flags.
        """
        source_without_turn_on = source & ~SOURCE_TURN_ON_FLAG
        if 0x40 <= source_without_turn_on <= 0x4F:
            return source_without_turn_on & ~AUDIO_ONLY_SOURCE_FLAG
        return source_without_turn_on

    @staticmethod
    def is_remote_source_selector(selector: int) -> bool:
        """Report whether a selector addresses a distributed source slot.

        Returns:
            True when the selector falls in the remote source range.
        """
        return REMOTE_SOURCE_SELECTOR_MIN <= selector <= REMOTE_SOURCE_SELECTOR_MAX

    @staticmethod
    def is_local_source_selector(selector: int) -> bool:
        """Report whether a selector addresses a local input.

        Returns:
            True when the selector is below the distributed source range.
        """
        return selector < REMOTE_SOURCE_SELECTOR_MIN

    @property
    def reported_sources(self) -> tuple[int, ...]:
        """Return normalized source selectors reported by output state.

        Returns:
            Ordered, de-duplicated selectors from source_raw and source_detail.
        """
        if self.source_raw is None:
            return ()
        selectors = []
        seen = set()
        for raw_source in (self.source_raw, *self.source_detail):
            selector = self.normalize_source_selector(raw_source)
            if selector not in seen:
                selectors.append(selector)
                seen.add(selector)
        return tuple(selectors)

    @property
    def remote_source_selector(self) -> int | None:
        """Return the reported distributed source selector, if any.

        Returns:
            First remote selector in reported source state, or None.
        """
        return next(
            (
                source
                for source in self.reported_sources
                if self.is_remote_source_selector(source)
            ),
            None,
        )

    @property
    def local_source_selector(self) -> int | None:
        """Return the reported local source selector, if any.

        Returns:
            First local selector in reported source state, or None.
        """
        return next(
            (
                source
                for source in self.reported_sources
                if self.is_local_source_selector(source)
            ),
            None,
        )

    @property
    def selected_reported_source_selector(self) -> int | None:
        """Return the preferred reported selector for this output.

        Returns:
            Local selector when present, otherwise remote selector or first reported selector.
        """
        if self.local_source_selector is not None:
            return self.local_source_selector
        if self.remote_source_selector is not None:
            return self.remote_source_selector
        if self.reported_sources:
            return self.reported_sources[0]
        return None

    def needed_update_ops(self) -> list[codec.Command]:
        """Build read commands needed to complete this output state.

        Returns:
            Commands for missing name, power, mute, source, volume, or max volume.
        """
        ops: list[codec.Command] = []
        if self.name is None:
            ops.append(codec.ZoneNameRequestCommand(output=self.id))
        if self.on is None:
            ops.append(codec.StandbyPowerCommand(output=self.id))
        if self.muted is None:
            ops.append(codec.MuteCommand(output=self.id))
        if self.source_raw is None:
            ops.append(codec.SourceSelectionCommand(output=self.id))
        if self.volume is None:
            ops.append(codec.VolumeCommand(output=self.id))
        if self.max_volume is None:
            ops.append(codec.MaximumVolumeCommand(output=self.id))
        return ops

    def update(self, op: codec.OutputCommand) -> None:
        """Merge an output-scoped command or response into this output."""
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
                    self.source_raw = op.source
                    self.source_detail = op.detail
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
        """Merge a distributed source slot response into this remote input.

        Returns:
            True when the command applied and changed state, otherwise False.
        """
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
        """Build read commands needed to determine this remote input slot.

        Returns:
            Slot-definition request when presence is unknown, otherwise an empty list.
        """
        if self.present is None:
            return [codec.DistributedSourceDefinitionRequestCommand(slot_id=self.id)]
        return []


class SystemState(VersionTrackerMixin):
    def __init__(self) -> None:
        """Initialize canonical discovered devices, inputs, outputs, and remote inputs."""
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
        """Serialize system state to a JSON-compatible dictionary.

        Returns:
            Dictionary containing devices, inputs, outputs, and remote input slots.
        """
        return {
            "devices": {
                str(device_id): device.model_dump(mode="json", exclude={"id"})
                for device_id, device in self.devices.items()
            },
            "inputs": {
                f"{input_state.device_id}:0x{input_state.selector:02X}": input_state.model_dump(
                    mode="json", exclude={"device_id", "selector"}
                )
                for input_state in self.inputs.values()
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
        """Load canonical system state from decoded JSON data.

        Returns:
            Populated SystemState with hardware defaults applied.
        """
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
            state.outputs[output_id].merge(OutputState(id=output_id, **output_data))
        for remote_input_id_str, remote_input_data in data.get("remote_inputs", {}).items():
            remote_input_id = int(remote_input_id_str)
            state.remote_inputs[remote_input_id].merge(
                RemoteInput(id=remote_input_id, **remote_input_data)
            )
        state.apply_hardware_defaults()
        return state

    async def save_to_file(self, file_path: str) -> None:
        """Write this state to a JSON file."""
        payload = json.dumps(self.to_json(), indent=2)
        Path(file_path).write_text(payload, encoding="utf-8")

    @classmethod
    async def load_from_file(cls, file_path: str) -> "SystemState":
        """Read system state from a JSON file.

        Returns:
            Populated SystemState loaded from the file.
        """
        payload = Path(file_path).read_text(encoding="utf-8")
        data = json.loads(payload)
        return cls.from_json(data)

    def merge(self, other: "SystemState") -> None:
        """Merge another state snapshot into this one."""
        for device_id, other_device in other.devices.items():
            self.devices[device_id].merge(other_device)
        for input_key, other_input in other.inputs.items():
            self.inputs[input_key].merge(other_input)
        for output_id, other_output in other.outputs.items():
            self.outputs[output_id].merge(other_output)
        for remote_input_id, other_remote_input in other.remote_inputs.items():
            self.remote_inputs[remote_input_id].merge(other_remote_input)

    def apply_hardware_defaults(self) -> None:
        """Apply model catalog defaults to devices and local inputs."""
        for device in self.devices.values():
            device.apply_hardware_defaults()
            if device.model_id is None:
                continue
            model = model_by_number(device.model_id)
            if model is None:
                continue
            for source in model.sources:
                self.inputs[(device.id, source.selector)].apply_hardware_source(source)

    def dump(self) -> None:
        """Print a human-readable snapshot of canonical system state."""
        print(f"Devices ({len(self.devices)}):")
        for device in self.devices.values():
            print(f"  {device}")
        inputs = list(self.listed_inputs())
        print(f"Inputs ({len(inputs)}):")
        for input_state in inputs:
            print(f"  {input_state.qualified_name}: {input_state}")
        print(f"Outputs ({len(self.outputs)}):")
        for _, output in self.outputs.items():
            print(f"  {output}")
        print(f"Remote Inputs ({len(self.remote_inputs)}):")
        for _, remote_input in self.remote_inputs.items():
            print(f"  {remote_input}")
        print()

    def device_for_output(self, output_id: int) -> DeviceState | None:
        """Find the canonical device that owns an output id.

        Returns:
            Matching device state, or None when unknown.
        """
        for device in self.devices.values():
            if device.outputs is not None and output_id in device.outputs:
                return device
        return None

    def device_for_guid(self, guid: UUID | None) -> DeviceState | None:
        """Find a device by GUID, accounting for observed byte-order variants.

        Returns:
            Matching device state, or None when no GUID is available or matched.
        """
        if guid is None:
            return None
        candidate_guids = _guid_candidates(guid)
        for device in self.devices.values():
            if device.guid in candidate_guids:
                return device
        return None

    def inputs_by_device(
        self, device_id: HexBytes, include_remote: bool = False
    ) -> list[InputState]:
        """List known inputs for one device.

        Args:
            device_id: Device id whose inputs should be listed.
            include_remote: Whether distributed source selectors should be included.

        Returns:
            Inputs sorted by physical source order, then selector.
        """
        return sorted(
            [
                input_state
                for (input_device_id, _), input_state in self.inputs.items()
                if input_device_id == device_id
                and (include_remote or not input_state.remote)
            ],
            key=self._input_sort_key,
        )

    def discovered_inputs_by_device(
        self, device_id: HexBytes, include_remote: bool = False
    ) -> list[InputState]:
        """List named inputs discovered for one device.

        Args:
            device_id: Device id whose discovered inputs should be listed.
            include_remote: Whether distributed source selectors should be included.

        Returns:
            Inputs whose source names have been discovered.
        """
        return [
            input_state
            for input_state in self.inputs_by_device(device_id, include_remote=include_remote)
            if input_state.name_discovered
        ]

    def _input_sort_key(self, input_state: InputState) -> tuple[int, int]:
        """Build the physical-order sort key for an input.

        Returns:
            Tuple ordering physical inputs before selector-only inputs.
        """
        physical_source_id = input_state.physical_source_id
        if physical_source_id is not None:
            return physical_source_id, input_state.selector
        return 0x100 + input_state.selector, input_state.selector

    def listed_inputs(self) -> tuple[InputState, ...]:
        """Build the public input listing without duplicate remote mappings.

        Returns:
            Local physical inputs plus orphan local inputs, sorted for presentation.
        """
        inputs: list[InputState] = []
        for device in self.devices.values():
            inputs.extend(self.inputs_by_device(device.id))

        listed_keys = {(input_state.device_id, input_state.selector) for input_state in inputs}
        for input_state in self.inputs.values():
            key = (input_state.device_id, input_state.selector)
            if input_state.remote or key in listed_keys:
                continue
            inputs.append(input_state)

        return tuple(
            sorted(
                inputs,
                key=lambda input_state: (
                    str(input_state.device_id),
                    *self._input_sort_key(input_state),
                ),
            )
        )

    def selector_for_remote_input(
        self,
        backing_device: DeviceState | None,
        remote_input: RemoteInput,
    ) -> int | None:
        """Map a remote input slot's backing source to a local logical selector.

        Args:
            backing_device: Device that owns the backing source, if known.
            remote_input: Remote input slot state to interpret.

        Returns:
            Local logical source selector, or None when the backing source is unknown.
        """
        if remote_input.source_index is None:
            return None
        physical_source_id = remote_input.source_index + 1
        if backing_device is not None and backing_device.model_id is not None:
            model = model_by_number(backing_device.model_id)
            if model is not None:
                for source in model.sources:
                    if source.physical_source_id == physical_source_id:
                        return source.selector
        return LOGICAL_SELECTOR_BY_PHYSICAL_SOURCE_ID.get(physical_source_id)

    def output_remote_source(self, output: OutputState) -> RemoteInput | None:
        """Resolve an output's active remote source selector to remote slot state.

        Returns:
            Active remote input state, or None when no present remote source is active.
        """
        remote_source_selector = output.remote_source_selector
        if remote_source_selector is None:
            return None
        remote_slot_id = remote_source_selector - REMOTE_SOURCE_SELECTOR_MIN
        remote_input = self.remote_inputs.get(remote_slot_id)
        if remote_input is None or not remote_input.present:
            return None
        return remote_input

    def source_selection_command_for_input(
        self,
        output_id: int,
        source_input: InputState,
    ) -> codec.SourceSelectionCommand:
        """Build the best source-selection command for one output/input pair.

        Args:
            output_id: Target output id.
            source_input: Desired input state, local or remote.

        Returns:
            Source selection command encoded for the target output's device.
        """
        if output_id not in self.outputs:
            raise ValueError(f"Output {output_id} not found")
        target_device = self.device_for_output(output_id)
        if target_device is None:
            raise ValueError(f"Cannot find target device for output {output_id}")

        if source_input.remote:
            return self._source_selection_command_for_remote_input(
                output_id,
                target_device,
                source_input,
            )

        if target_device.id == source_input.device_id:
            return codec.SourceSelectionCommand(output=output_id, source=source_input.selector)

        remote_selector = self.remote_selector_for_input(
            output_id,
            target_device,
            source_input,
        )
        return codec.SourceSelectionCommand(
            output=output_id,
            source=remote_selector,
        )

    def source_selection_commands_for_input(
        self,
        output_id: int,
        source_input: InputState,
    ) -> tuple[codec.SourceSelectionCommand, ...]:
        """Build source-selection commands for one output or all known outputs.

        Args:
            output_id: Target output id, or ALL_OUTPUTS for every canonical output.
            source_input: Desired input state, local or remote.

        Returns:
            One command per concrete output that should be set.
        """
        if output_id != ALL_OUTPUTS:
            return (self.source_selection_command_for_input(output_id, source_input),)

        output_ids = tuple(self.outputs.keys())
        if not output_ids:
            raise ValueError(
                f"Cannot set input {source_input.qualified_name} for ALL_OUTPUTS without known outputs"
            )
        return tuple(
            self.source_selection_command_for_input(target_output_id, source_input)
            for target_output_id in output_ids
        )

    def _source_selection_command_for_remote_input(
        self,
        output_id: int,
        target_device: DeviceState,
        source_input: InputState,
    ) -> codec.SourceSelectionCommand:
        """Build a source-selection command for an already-remote input selector.

        Args:
            output_id: Target output id.
            target_device: Device that owns the target output.
            source_input: Remote input selector state.

        Returns:
            Local-source command when the target device owns the backing source,
            otherwise remote-source command using the distributed selector.
        """
        remote_slot_id = source_input.selector - REMOTE_SOURCE_SELECTOR_MIN
        remote_input = self.remote_inputs.get(remote_slot_id)
        if remote_input is None or not remote_input.present:
            raise ValueError(
                f"Cannot route remote input {source_input.qualified_name} ({source_input.name}) "
                f"to output {output_id}: distributed source slot {remote_slot_id} is not defined"
            )

        backing_device = self.device_for_guid(remote_input.device_guid)
        backing_source = self.selector_for_remote_input(backing_device, remote_input)
        if backing_source is None:
            raise ValueError(
                f"Cannot route remote input {source_input.qualified_name} ({source_input.name}) "
                f"to output {output_id}: backing source is unknown"
            )
        if backing_device is not None and target_device.id == backing_device.id:
            return codec.SourceSelectionCommand(output=output_id, source=backing_source)

        return codec.SourceSelectionCommand(
            output=output_id,
            source=source_input.selector,
        )

    def remote_selector_for_input(
        self,
        output_id: int,
        target_device: DeviceState,
        source_input: InputState,
    ) -> int:
        """Find the distributed source selector that exposes a local input remotely.

        Args:
            output_id: Target output id used for error context.
            target_device: Device that owns the target output.
            source_input: Local source input to route across devices.

        Returns:
            Remote source selector for the backing input.
        """
        source_device = self.devices.get(source_input.device_id)
        if source_device is None:
            raise ValueError(
                f"Cannot route input {source_input.qualified_name} ({source_input.name}) to output "
                f"{output_id} on device {target_device.id}: source device is unknown"
            )
        if source_device.guid is None:
            raise ValueError(
                f"Cannot route input {source_input.qualified_name} ({source_input.name}) to output "
                f"{output_id} on device {target_device.id}: source device {source_device.id} "
                "has no GUID"
            )
        physical_source_id = source_input.physical_source_id
        if physical_source_id is None:
            raise ValueError(
                f"Cannot route input {source_input.qualified_name} ({source_input.name}) to output "
                f"{output_id} on device {target_device.id}: input has no physical source id"
            )
        source_index = physical_source_id - 1
        source_guids = _guid_candidates(source_device.guid)
        for remote_slot_id, remote_input in sorted(self.remote_inputs.items()):
            if (
                remote_input.present
                and remote_input.source_index == source_index
                and remote_input.device_guid in source_guids
            ):
                return REMOTE_SOURCE_SELECTOR_MIN + remote_slot_id

        raise ValueError(
            f"Cannot route input {source_input.qualified_name} ({source_input.name}) to output "
            f"{output_id} on device {target_device.id}: no distributed source mapping "
            f"for source device {source_device.id} physical input {physical_source_id}"
        )
