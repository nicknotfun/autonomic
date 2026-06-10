import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypeVar, cast, overload, override
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.codec import ALL_OUTPUTS, Command, SourceNameOptionsCommand
import amp.codec as codec
from amp.hardware import SourceModelInfo, model_by_number
from amp.toggle_bool import ToggleBool
from amp.transport import (
    DEFAULT_CONNECTION_TIMEOUT_SECS,
    BaseTransport,
    ConnectionInterrupted,
)
from amp.versioned import (
    TrackedDict,
    VersionTrackerMixin,
    VersionedState,
    wait_for_any_change,
)

logger = logging.getLogger(__name__)

REMOTE_INPUT_SLOT_IDS = tuple(range(0x20))
OutputValueT = TypeVar("OutputValueT", bound=Hashable)
AUDIO_ONLY_SOURCE_FLAG = 0x40
SOURCE_TURN_ON_FLAG = 0x80
REMOTE_SOURCE_SELECTOR_MIN = 0x20
REMOTE_SOURCE_SELECTOR_MAX = 0x3F


def _normalize_name(name: str) -> str:
    """Normalize a user-facing name for loose lookup comparisons.

    Returns:
        Whitespace-free, case-folded name text.
    """
    return "".join(name.split()).casefold()


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


TransportArgument: TypeAlias = (
    BaseTransport[Command] | Iterable[BaseTransport[Command]] | str | Iterable[str]
)


def _normalize_transport_argument(
    transport_arg: TransportArgument,
    *,
    port: int,
    reconnection_wait_secs: float,
    connection_timeout_secs: float,
    trace: bool,
    read_only: bool,
) -> tuple[BaseTransport[Command], ...]:
    """Normalize hosts and transports into concrete transport instances.

    Args:
        transport_arg: Host, transport, or iterable of hosts/transports.
        port: TCP port used when constructing transports from hosts.
        reconnection_wait_secs: Delay between reconnect attempts for new transports.
        connection_timeout_secs: Connect timeout for new transports.
        trace: Whether constructed transports should trace protocol traffic.
        read_only: Whether constructed transports should block write commands.

    Returns:
        Tuple of transport instances.
    """
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
    elif isinstance(transport_arg, BaseTransport):
        return (transport_arg,)
    else:
        transports: list[BaseTransport[Command]] = []
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
        connection_timeout_secs: float = DEFAULT_CONNECTION_TIMEOUT_SECS,
        trace: bool = False,
        read_only: bool = True,
    ) -> None:
        """Create a system controller around one or more transports.

        Args:
            transport: Host, transport instance, or iterable of hosts/transports.
            state: Existing canonical state to use, or None for a new state.
            port: TCP port used for host-based transports.
            reconnection_wait_secs: Delay between reconnect attempts.
            connection_timeout_secs: Connect timeout for host-based transports.
            trace: Whether host-based transports should trace protocol traffic.
            read_only: Whether host-based transports should reject write commands.
        """
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
        """Synchronously cancel event tasks and shut down all transports."""
        for task in self.tasks:
            task.cancel()
        for transport in self.transports:
            transport.shutdown()

    async def _close_transport(self, transport: BaseTransport[Command]) -> None:
        """Close a single transport asynchronously."""
        await transport.aclose()

    async def aclose(self) -> None:
        """Cancel event tasks and asynchronously close all transports."""
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()

        close_results = await asyncio.gather(
            *(self._close_transport(transport) for transport in self.transports),
            return_exceptions=True,
        )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for result in close_results:
            if isinstance(result, BaseException):
                raise result

    def __enter__(self) -> "System":
        """Enter a synchronous context manager.

        Returns:
            This System instance.
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit a synchronous context manager by shutting down transports.

        Args:
            exc_type: Exception type raised inside the context, if any.
            exc_val: Exception value raised inside the context, if any.
            exc_tb: Exception traceback raised inside the context, if any.
        """
        self.shutdown()

    async def __aenter__(self) -> "System":
        """Enter an asynchronous context manager.

        Returns:
            This System instance.
        """
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit an asynchronous context manager by closing transports.

        Args:
            exc_type: Exception type raised inside the context, if any.
            exc_val: Exception value raised inside the context, if any.
            exc_tb: Exception traceback raised inside the context, if any.
        """
        await self.aclose()

    def apply_hardware_defaults(self) -> None:
        """Apply model catalog defaults to canonical system state."""
        self.state.apply_hardware_defaults()

    def transport_for_device(self, device: DeviceState) -> BaseTransport[Command] | None:
        """Find the transport associated with a discovered device.

        Returns:
            Matching transport, or None when host information is unavailable.
        """
        if device.host is None:
            return None
        return self._transports_by_host.get(device.host)

    def transport_for_device_id(
        self, device_id: HexBytes
    ) -> BaseTransport[Command] | None:
        """Find the transport associated with a device id.

        Returns:
            Matching transport, or None when the device or host mapping is unknown.
        """
        device = self.state.devices.get(device_id)
        if device is None:
            return None
        return self.transport_for_device(device)

    def transport_for_output(self, output_id: int) -> BaseTransport[Command]:
        """Select the preferred transport for an output command.

        Returns:
            Device-local transport when known, otherwise the primary transport.
        """
        device = self.state.device_for_output(output_id)
        if device is None:
            return self.transport
        return self.transport_for_device(device) or self.transport

    def _target_transports_for_op(
        self, op: Command
    ) -> tuple[BaseTransport[Command], ...]:
        """Determine which transports should receive a command.

        Returns:
            Transports that should receive the command.
        """
        match op:
            case codec.OutputCommand(output=output_id) if output_id == ALL_OUTPUTS:
                return self.transports
            case codec.OutputCommand(output=output_id):
                device = self.state.device_for_output(output_id)
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
        transport: BaseTransport[Command] | None,
    ) -> tuple[OutputState, ...]:
        """Resolve concrete outputs affected by an ALL_OUTPUTS event.

        Returns:
            Outputs local to the emitting transport when resolvable, otherwise all outputs.
        """
        if transport is not None:
            devices = self._devices_for_transport_event(transport)
            device_ids = {device.id for device in devices}
            return tuple(
                output
                for output in self.state.outputs.values()
                if (device := self.state.device_for_output(output.id)) is not None
                and device.id in device_ids
            )
        return tuple(self.state.outputs.values())

    def send_ops(
        self, *ops: Command, transport: BaseTransport[Command] | None = None
    ) -> None:
        """Send commands through their appropriate transport or a requested transport.

        Args:
            *ops: Commands to send.
            transport: Optional transport filter/target.
        """
        if transport is not None:
            routed_ops = [op for op in ops if transport in self._target_transports_for_op(op)]
            if routed_ops:
                transport.send(*routed_ops)
            return

        ops_by_transport: defaultdict[BaseTransport[Command], list[Command]] = defaultdict(list)
        for op in ops:
            for target_transport in self._target_transports_for_op(op):
                ops_by_transport[target_transport].append(op)
        for target_transport, transport_ops in ops_by_transport.items():
            target_transport.send(*transport_ops)

    def _apply_pending_device_host_info(self) -> None:
        """Retry host-identity rows that could not yet be matched to devices."""
        pending = self._pending_device_host_info
        self._pending_device_host_info = []
        for op in pending:
            if not self._apply_device_host_info(op):
                self._pending_device_host_info.append(op)

    def _apply_device_host_info(self, op: codec.UndocumentedHostIdentityCommandResponse) -> bool:
        """Apply an undocumented host identity response to matching devices.

        Returns:
            True when at least one device matched the response.
        """
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

    def _note_device_host(
        self,
        device: DeviceState,
        transport: BaseTransport[Command] | None,
    ) -> None:
        """Record the emitting transport host for a device when not already known.

        Args:
            device: Device state to update.
            transport: Transport that produced related device data, if known.
        """
        if transport is not None and device.host is None:
            device.host = transport.host

    def _devices_for_transport_event(
        self,
        transport: BaseTransport[Command],
    ) -> tuple[DeviceState, ...]:
        """Resolve devices likely associated with a transport-originated event.

        Returns:
            Devices assigned to the transport, or a single inferred unhosted device.
        """
        devices = tuple(
            device for device in self.state.devices.values() if device.host == transport.host
        )
        if devices:
            return devices

        unhosted_devices = tuple(
            device for device in self.state.devices.values() if device.host is None
        )
        if len(unhosted_devices) == 1:
            device = unhosted_devices[0]
            device.host = transport.host
            return (device,)

        return ()

    def _devices_for_source_name_event(
        self,
        op: codec.SourceNameOptionsCommand,
        transport: BaseTransport[Command] | None,
    ) -> tuple[DeviceState, ...]:
        """Resolve devices affected by a source-name response.

        Args:
            op: Source name/options response.
            transport: Transport that emitted the response, if known.

        Returns:
            Candidate devices whose input table should receive the response.
        """
        if op.output != ALL_OUTPUTS:
            device = self.state.device_for_output(op.output)
            if device is not None:
                self._note_device_host(device, transport)
                return (device,)
            return ()

        if transport is not None:
            devices = self._devices_for_transport_event(transport)
            if devices:
                return devices

        devices = tuple(self.state.devices.values())
        if len(devices) == 1:
            return devices
        return ()

    def update(
        self,
        op: Command | ConnectionInterrupted,
        transport: BaseTransport[Command] | None = None,
    ) -> None:
        """Merge an incoming command/event into canonical system state.

        Args:
            op: Decoded protocol command/event or connection-interruption marker.
            transport: Transport that emitted the event, if known.
        """
        match op:
            case ConnectionInterrupted():
                self.refresh(transport=transport)
            case codec.RequestZoneAssignmentsCommandResponse():
                device = self.state.devices[op.device_id]
                device.host = transport.host if transport is not None else self.transport.host
                device.update(op)
                self.apply_hardware_defaults()
            case codec.SourceNameOptionsCommand():
                for source_device in self._devices_for_source_name_event(op, transport):
                    self.state.inputs[(source_device.id, op.source_selector)].update(op)
            case codec.DeviceIdCommand():
                device = self.state.devices[op.device_id]
                device.update(op)
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
                    gain_device = self.state.device_for_output(op.output)
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
                    output_device = self.state.device_for_output(op.output)
                    if output_device is None:
                        return
                    if transport is not None:
                        self._note_device_host(output_device, transport)
                    output = self.state.outputs[op.output]
                    output.update(op)

    async def _handle_events(self, transport: BaseTransport[Command]) -> None:
        """Consume transport events and apply them to system state."""
        async for op in transport.recv():
            self.update(op, transport=transport)

    def refresh_outputs(
        self,
        include_names: bool = False,
        *,
        transport: BaseTransport[Command] | None = None,
    ) -> None:
        """Request dynamic output state for all outputs.

        Args:
            include_names: Whether zone names should also be requested.
            transport: Optional transport to target or filter the requests.
        """
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

    def refresh(self, *, transport: BaseTransport[Command] | None = None) -> None:
        """Request missing configuration state and current output state.

        Args:
            transport: Optional transport to target or filter the requests.
        """
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
        """Discover device identity, output ownership, and host mapping.

        Args:
            target_devices: Expected number of devices before discovery can settle.
            time_between_probes_secs: Delay or wait timeout between probe rounds.
        """
        def determine_delta() -> tuple[list[DeviceState], set[codec.Command], set[codec.Command]]:
            """Find incomplete device fields and commands for the next probe round.

            Returns:
                Incomplete devices, required probe commands, and best-effort probe commands.
            """
            incomplete_devices = []
            probe_ops: set[codec.Command] = set()
            best_effort_probe_ops: set[codec.Command] = set()

            def mark_incomplete(device: DeviceState, op: codec.Command) -> None:
                """Record one missing device field and its probe command.

                Args:
                    device: Device missing state.
                    op: Command that can read the missing state.
                """
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

        def missing_host_transports() -> list[BaseTransport[Command]]:
            """Find transports that do not yet have an associated device.

            Returns:
                Transports whose host has not been matched to discovered devices.
            """
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

            if has_enough_devices and not incomplete_devices:
                if best_effort_probe_ops or host_probe_ops:
                    self.send_ops(*(best_effort_probe_ops | host_probe_ops))
                if missing_hosts:
                    previous_version = self.version
                    await self.wait_for_change(
                        since_version=previous_version,
                        timeout=time_between_probes_secs,
                    )
                    if self.version != previous_version:
                        continue
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
        """Discover local input names and infer unknown input counts.

        Args:
            time_between_probes_secs: Delay or wait timeout between probe rounds.
            time_to_wait_for_devices_with_unknown_inputs: Maximum wait before inferring
                input count from discovered input names.
        """
        devices_with_outputs = [device for device in self.state.devices.values() if device.outputs]

        probed_outputs_by_device: defaultdict[HexBytes, set[int]] = defaultdict(set)
        unknown_input_count_deadlines: dict[HexBytes, float] = {}

        def unknown_input_count_deadline(device: DeviceState) -> float | None:
            """Return the input-count inference deadline for a device.

            Returns:
                Event-loop timestamp deadline, or None when no deadline is active.
            """
            if device.input_count is not None:
                return None
            return unknown_input_count_deadlines.get(device.id)

        def infer_unknown_input_count_after_deadline(device: DeviceState) -> None:
            """Infer an unknown input count once the device deadline has elapsed."""
            if device.input_count is not None:
                return
            deadline = unknown_input_count_deadline(device)
            if deadline is None or asyncio.get_running_loop().time() < deadline:
                return
            detected_inputs = len(
                self.state.discovered_inputs_by_device(device.id, include_remote=False)
            )
            if detected_inputs > 0:
                device.input_count = detected_inputs

        def determine_probe_ops() -> tuple[list[codec.Command], bool]:
            """Build source-name probes for the next input discovery round.

            Returns:
                Probe commands and whether any device still has an unknown input count.
            """
            probe_ops: list[codec.Command] = []
            any_devices_with_unknown_input_count = False
            for device in devices_with_outputs:
                infer_unknown_input_count_after_deadline(device)
                detected_inputs = len(
                    self.state.discovered_inputs_by_device(device.id, include_remote=False)
                )
                if device.input_count is None:
                    any_devices_with_unknown_input_count = True
                elif detected_inputs >= device.input_count:
                    continue

                probed_outputs = probed_outputs_by_device[device.id]
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
            """Report whether all device input tables are complete enough.

            Returns:
                True when input discovery can stop, otherwise False.
            """
            for device in devices_with_outputs:
                infer_unknown_input_count_after_deadline(device)
                detected_inputs = len(
                    self.state.discovered_inputs_by_device(device.id, include_remote=False)
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
        """Discover distributed source slot definitions.

        Args:
            slot_ids: Remote input slot ids to probe, or None for the default slot range.
            time_between_probes_secs: Delay or wait timeout between probe rounds.
        """
        slot_ids = REMOTE_INPUT_SLOT_IDS if slot_ids is None else tuple(slot_ids)
        for slot_id in slot_ids:
            _ = self.state.remote_inputs[slot_id]

        def needed_probe_ops() -> list[codec.Command]:
            """Build remote-input slot requests still needed.

            Returns:
                Distributed source definition requests for unknown slots.
            """
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
        """Discover output state for all outputs assigned to known devices.

        Args:
            time_between_probes_secs: Delay or wait timeout between probe rounds.
        """
        for device in self.state.devices.values():
            for output_id in device.outputs or ():
                _ = self.state.outputs[
                    output_id
                ]  # Ensure the output exists in our dict so we can update it.

        self.refresh_outputs(include_names=True)

        def needed_probe_ops() -> set[codec.Command]:
            """Build output-state requests still needed.

            Returns:
                Set of commands needed to complete known output state.
            """
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
        timeout_secs: float | None = None,
    ) -> None:
        """Run full device, input, output, and remote-input discovery.

        Args:
            target_devices: Expected number of devices before device discovery can settle.
            time_between_probes_secs: Delay or wait timeout between probe rounds.
            time_to_wait_for_devices_with_unknown_inputs: Maximum wait before inferring
                input count from discovered input names.
            timeout_secs: Optional total timeout for the full discovery workflow.
        """
        async def run_discovery() -> None:
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

        async with asyncio.timeout(timeout_secs):
            await run_discovery()

    def device_by_id(self, device_id: HexBytes) -> "DeviceSelector":
        """Create a selector for a known device id.

        Returns:
            DeviceSelector for the canonical device.
        """
        return DeviceSelector(self, device_id)

    def output(self, output_id: int) -> "OutputSelector":
        """Create a selector for a fully discovered output.

        Returns:
            OutputSelector for the canonical output.
        """
        output = self.state.outputs.get(output_id)
        if output is None:
            raise ValueError(f"Output {output_id} not found")
        if len(output.needed_update_ops()) > 0:
            raise ValueError(
                f"Output {output_id} is missing information: {output.needed_update_ops()}"
            )
        return OutputSelector(self, output_id)

    def output_by_name(self, name: str) -> "OutputSelector":
        """Find an output by display name.

        Args:
            name: Output name to match, ignoring case and whitespace.

        Returns:
            OutputSelector for the matching output.
        """
        normalized_name = _normalize_name(name)
        for output in self.state.outputs.values():
            if output.name is not None and _normalize_name(output.name) == normalized_name:
                return OutputSelector(self, output.id)
        raise ValueError(f"Output with name {name} not found")

    @overload
    def all_outputs(self, expand: Literal[True]) -> "tuple[OutputSelector, ...]":
        """Type overload for expanded concrete output selectors.

        Args:
            expand: True to request one selector per canonical output.

        Returns:
            Tuple of OutputSelector instances.
        """
        ...

    @overload
    def all_outputs(self, expand: Literal[False] = False) -> "OutputSelector":
        """Type overload for the aggregate ALL_OUTPUTS selector.

        Args:
            expand: False or omitted to request the aggregate selector.

        Returns:
            OutputSelector targeting ALL_OUTPUTS.
        """
        ...

    def all_outputs(self, expand: bool = False) -> "OutputSelector | tuple[OutputSelector, ...]":
        """Create a selector for all outputs or expand to concrete selectors.

        Args:
            expand: When True, return one selector per canonical output.

        Returns:
            ALL_OUTPUTS selector, or a tuple of concrete OutputSelector instances.
        """
        if expand:
            return tuple(OutputSelector(self, output_id) for output_id in self.state.outputs.keys())
        return OutputSelector(self, ALL_OUTPUTS)

    def input_by_name(
        self,
        name: str,
        *,
        prefer_remote: bool = False,
        include_remote: bool = False,
        include_hardware_names: bool = True,
    ) -> "InputSelector":
        """Find an input by display or hardware name.

        Args:
            name: Input name to match, ignoring case and whitespace.
            prefer_remote: When remote inputs are included, search them before local inputs.
            include_remote: Whether distributed source selectors should be searchable.
            include_hardware_names: Whether model-derived hardware names should match.

        Returns:
            InputSelector for the matching input.
        """
        normalized_name = _normalize_name(name)
        listed_inputs = self.state.listed_inputs()
        if include_remote:
            remote_inputs = tuple(
                input_state for input_state in self.state.inputs.values() if input_state.remote
            )
            inputs = (
                (*remote_inputs, *listed_inputs)
                if prefer_remote
                else (*listed_inputs, *remote_inputs)
            )
        else:
            inputs = listed_inputs
        for input_state in inputs:
            if _normalize_name(input_state.name) == normalized_name or (
                include_hardware_names
                and input_state.hardware_name is not None
                and _normalize_name(input_state.hardware_name) == normalized_name
            ):
                return InputSelector(self, input_state.device_id, input_state.selector)
        raise ValueError(f"Input with name {name} not found")

    def all_inputs(self, *, include_remote: bool = False) -> "tuple[InputSelector, ...]":
        """Create selectors for known inputs.

        Args:
            include_remote: Whether distributed source selectors should be included.

        Returns:
            Tuple of InputSelector instances in listing order.
        """
        inputs = self.state.inputs.values() if include_remote else self.state.listed_inputs()
        return tuple(
            InputSelector(self, input_state.device_id, input_state.selector)
            for input_state in inputs
        )

    def input_by_id(self, device_id: HexBytes, selector: int) -> "InputSelector":
        """Create a selector for a known input device id and source selector.

        Args:
            device_id: Device id that owns the input.
            selector: Logical source selector.

        Returns:
            InputSelector for the matching input.
        """
        return InputSelector(self, device_id, selector)


class Selector:
    def __str__(self) -> str:
        """Return the friendly selector representation.

        Returns:
            String containing selector properties and salient referenced ids.
        """
        return self._format()

    @classmethod
    def _format_value(cls, value: object) -> str:
        """Format a property value for selector string output.

        Returns:
            Human-readable string representation.
        """
        if isinstance(value, Selector):
            return value._reference()
        if isinstance(value, HexBytes | UUID):
            return str(value)
        if isinstance(value, tuple):
            if not value:
                return "()"
            items = ", ".join(cls._format_value(item) for item in value)
            suffix = "," if len(value) == 1 else ""
            return f"({items}{suffix})"
        if isinstance(value, list):
            return "[" + ", ".join(cls._format_value(item) for item in value) + "]"
        if isinstance(value, set):
            return "{" + ", ".join(sorted(cls._format_value(item) for item in value)) + "}"
        if isinstance(value, dict):
            return (
                "{"
                + ", ".join(
                    f"{cls._format_value(key)}: {cls._format_value(item)}"
                    for key, item in value.items()
                )
                + "}"
            )
        return repr(value)

    @staticmethod
    def _format_unavailable_property(exc: Exception) -> str:
        """Format an unavailable property exception for selector output.

        Returns:
            Placeholder string explaining the unavailable value.
        """
        message = str(exc)
        if message:
            return f"<unavailable: {message}>"
        return "<unavailable>"

    @classmethod
    def _property_names(cls) -> tuple[str, ...]:
        """List property names declared by the selector class.

        Returns:
            Tuple of property names in class declaration order.
        """
        return tuple(name for name, value in vars(cls).items() if isinstance(value, property))

    def _format_property(self, name: str, value: object) -> str:
        """Format one named property for selector string output.

        Args:
            name: Property name being formatted.
            value: Property value being formatted.

        Returns:
            Human-readable value string.
        """
        return self._format_value(value)

    def _reference(self) -> str:
        """Return the short representation used by other selectors.

        Returns:
            Selector class name with salient identifying fields.
        """
        return f"{type(self).__name__}()"

    def _format(self) -> str:
        """Build the full selector representation from declared properties.

        Returns:
            String containing all readable selector properties.
        """
        properties = []
        for name in type(self)._property_names():
            try:
                value = getattr(self, name)
            except Exception as exc:
                formatted_value = self._format_unavailable_property(exc)
            else:
                formatted_value = self._format_property(name, value)
            properties.append(f"{name}={formatted_value}")
        return f"{type(self).__name__}(" + ", ".join(properties) + ")"


class DeviceSelector(Selector):
    def __init__(self, system: System, device_id: HexBytes) -> None:
        """Create a selector for a canonical device.

        Args:
            system: System that owns the canonical state.
            device_id: Device id to select.
        """
        self.system = system
        device = self.system.state.devices.get(device_id)
        if device is None:
            raise ValueError(f"Device {device_id} not found")
        self.device = device

    @override
    def _reference(self) -> str:
        """Return a compact device selector reference.

        Returns:
            Reference string containing the device id.
        """
        return f"DeviceSelector(id={self.id})"

    @property
    def id(self) -> HexBytes:
        """Return the selected device id.

        Returns:
            Device id.
        """
        return self.device.id

    @property
    def firmware(self) -> int:
        """Return the discovered firmware version.

        Returns:
            Firmware version integer.
        """
        assert self.device.firmware is not None
        return self.device.firmware

    @property
    def model_id(self) -> HexBytes:
        """Return the discovered hardware model id.

        Returns:
            Model id bytes.
        """
        assert self.device.model_id is not None
        return self.device.model_id

    @property
    def mac(self) -> HexBytes:
        """Return the discovered network MAC address.

        Returns:
            MAC address bytes.
        """
        assert self.device.mac is not None
        return self.device.mac

    @property
    def guid(self) -> UUID:
        """Return the discovered device GUID.

        Returns:
            Device GUID.
        """
        assert self.device.guid is not None
        return self.device.guid

    @property
    def outputs(self) -> "tuple[OutputSelector, ...]":
        """Return output selectors owned by this device.

        Returns:
            Tuple of OutputSelector instances for canonical outputs on this device.
        """
        return tuple(
            OutputSelector(self.system, output.id)
            for output in self.system.state.outputs.values()
            if (device := self.system.state.device_for_output(output.id)) is not None
            and device.id == self.device.id
        )

    @property
    def inputs(self) -> "tuple[InputSelector, ...]":
        """Return local input selectors owned by this device.

        Returns:
            Tuple of InputSelector instances in device input order.
        """
        return tuple(
            InputSelector(self.system, self.device.id, input_state.selector)
            for input_state in self.system.state.inputs_by_device(self.device.id)
        )


class RemoteSourceSelector(Selector):
    def __init__(self, system: System, remote_source_id: int) -> None:
        """Create a selector for a distributed source slot.

        Args:
            system: System that owns the canonical state.
            remote_source_id: Zero-based distributed source slot id.
        """
        self.system = system
        remote_source = self.system.state.remote_inputs.get(remote_source_id)
        if remote_source is None:
            raise ValueError(f"Remote source {remote_source_id} not found")
        self.remote_source = remote_source

    @override
    def _reference(self) -> str:
        """Return a compact remote source selector reference.

        Returns:
            Reference string containing slot id and protocol selector.
        """
        return f"RemoteSourceSelector(id={self.id}, selector=0x{self.selector:02X})"

    @override
    def _format_property(self, name: str, value: object) -> str:
        """Format remote source properties for selector string output.

        Args:
            name: Property name being formatted.
            value: Property value being formatted.

        Returns:
            Human-readable value string.
        """
        if name == "selector":
            assert isinstance(value, int)
            return f"0x{value:02X}"
        if name == "remote_source" and isinstance(value, RemoteInput):
            return f"RemoteInput(id={value.id})"
        return super()._format_property(name, value)

    @property
    def id(self) -> int:
        """Return the distributed source slot id.

        Returns:
            Zero-based remote source slot id.
        """
        return self.remote_source.id

    @property
    def selector(self) -> int:
        """Return the protocol selector for this remote source slot.

        Returns:
            Logical selector in the distributed source range.
        """
        return REMOTE_SOURCE_SELECTOR_MIN + self.id

    @property
    def present(self) -> bool | None:
        """Return whether this remote source slot is defined.

        Returns:
            True for defined slots, False for unused slots, or None when unknown.
        """
        return self.remote_source.present

    @property
    def name(self) -> str | None:
        """Return the distributed source name.

        Returns:
            Remote source name, or None when unknown or unused.
        """
        return self.remote_source.name

    @property
    def backing_device(self) -> "DeviceSelector | None":
        """Return the device selector for the source backing this remote slot.

        Returns:
            DeviceSelector for the backing device, or None when unresolved.
        """
        device = self.system.state.device_for_guid(self.remote_source.device_guid)
        if device is None:
            return None
        return self.system.device_by_id(device.id)

    @property
    def backing_source_selector(self) -> int | None:
        """Return the local source selector backing this remote slot.

        Returns:
            Local logical selector, or None when the backing source is unresolved.
        """
        backing_device = self.system.state.device_for_guid(self.remote_source.device_guid)
        return self.system.state.selector_for_remote_input(
            backing_device,
            self.remote_source,
        )

    @property
    def backing_input(self) -> "InputSelector | None":
        """Return the input selector backing this remote slot.

        Returns:
            InputSelector for the backing input, or None when unresolved.
        """
        backing_device = self.system.state.device_for_guid(self.remote_source.device_guid)
        backing_source = self.backing_source_selector
        if backing_device is None or backing_source is None:
            return None
        input_state = self.system.state.inputs.get((backing_device.id, backing_source))
        if input_state is None:
            return None
        return self.system.input_by_id(input_state.device_id, input_state.selector)


class OutputSelector(Selector):
    def __init__(self, system: System, output_id: int) -> None:
        """Create a selector for one output or the ALL_OUTPUTS sentinel.

        Args:
            system: System that owns the canonical state.
            output_id: Concrete output id or ALL_OUTPUTS.
        """
        self.system = system
        self.output_id = output_id
        if output_id != ALL_OUTPUTS and output_id not in self.system.state.outputs:
            raise ValueError(f"Output {output_id} not found")

    @staticmethod
    def _format_output_id(output_id: int) -> str:
        """Format an output id for selector string output.

        Args:
            output_id: Concrete output id or ALL_OUTPUTS.

        Returns:
            Human-readable output id string.
        """
        return "ALL_OUTPUTS" if output_id == ALL_OUTPUTS else str(output_id)

    @override
    def _reference(self) -> str:
        """Return a compact output selector reference.

        Returns:
            Reference string containing the output id.
        """
        return f"OutputSelector(id={self._format_output_id(self.id)})"

    @override
    def _format_property(self, name: str, value: object) -> str:
        """Format output selector properties for string output.

        Args:
            name: Property name being formatted.
            value: Property value being formatted.

        Returns:
            Human-readable value string.
        """
        if name == "id":
            assert isinstance(value, int)
            return self._format_output_id(value)
        if name == "output" and isinstance(value, OutputState):
            return f"OutputState(id={value.id})"
        return super()._format_property(name, value)

    @property
    def id(self) -> int:
        """Return the selected output id.

        Returns:
            Concrete output id or ALL_OUTPUTS.
        """
        return self.output_id

    def _target_output_ids(self) -> tuple[int, ...]:
        """Resolve this selector to concrete output ids for write commands.

        Returns:
            One concrete output id, or all canonical output ids for ALL_OUTPUTS.
        """
        if not self.is_all_outputs:
            return (self.output_id,)

        return tuple(self.system.state.outputs.keys())

    def _send_output_ops(self, ops: Iterable[codec.OutputCommand]) -> None:
        """Send output commands when the generated command set is not empty."""
        ops = tuple(ops)
        if ops:
            self.system.send_ops(*ops)

    def _output_value(
        self, read: Callable[[OutputState], OutputValueT | None]
    ) -> OutputValueT | None:
        """Read a property from one output or a unanimous ALL_OUTPUTS set.

        Args:
            read: Function that extracts a value from OutputState.

        Returns:
            Concrete output value, unanimous all-output value, or None.
        """
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
        """Return whether this selector targets ALL_OUTPUTS.

        Returns:
            True for the ALL_OUTPUTS sentinel, otherwise False.
        """
        return self.output_id == ALL_OUTPUTS

    @property
    def output(self) -> OutputState | None:
        """Return the canonical output state for a concrete selector.

        Returns:
            OutputState for concrete outputs, or None for ALL_OUTPUTS.
        """
        if self.output_id == ALL_OUTPUTS:
            return None
        output = self.system.state.outputs.get(self.output_id)
        if output is None:
            raise ValueError(f"Output {self.output_id} not found")
        return output

    @property
    def name(self) -> str | None:
        """Return the output display name.

        Returns:
            Output name for concrete outputs, or None for ALL_OUTPUTS/unknown names.
        """
        output = self.output
        if output is not None:
            return output.name
        return None

    @property
    def on(self) -> bool | None:
        """Return output power state.

        Returns:
            Power state for a concrete output, unanimous all-output state, or None.
        """
        return self._output_value(lambda output: output.on)

    @property
    def muted(self) -> bool | None:
        """Return output mute state.

        Returns:
            Mute state for a concrete output, unanimous all-output state, or None.
        """
        return self._output_value(lambda output: output.muted)

    def _remote_source_for_output(self, output: OutputState) -> RemoteSourceSelector | None:
        """Resolve an output's remote source to a selector.

        Returns:
            RemoteSourceSelector for the active remote source, or None.
        """
        remote_source = self.system.state.output_remote_source(output)
        if remote_source is None:
            return None
        return RemoteSourceSelector(self.system, remote_source.id)

    def _local_source_selector_for_output(self, output: OutputState) -> int | None:
        """Infer the active local source selector for an output.

        Returns:
            Local selector reported by the output or inferred from a same-device remote source.
        """
        if output.local_source_selector is not None:
            return output.local_source_selector

        remote_source = self._remote_source_for_output(output)
        if remote_source is None:
            return None
        backing_device = remote_source.backing_device
        backing_source = remote_source.backing_source_selector
        output_device = self.system.state.device_for_output(output.id)
        if (
            output_device is not None
            and backing_device is not None
            and output_device.id == backing_device.id
        ):
            return backing_source
        return None

    def _active_sources_for_output(self, output: OutputState) -> tuple[int, ...]:
        """Return all interpreted active source selectors for an output.

        Returns:
            Ordered, de-duplicated selectors including inferred local and reported sources.
        """
        sources = []
        seen = set()
        for source in (
            self._local_source_selector_for_output(output),
            *output.reported_sources,
        ):
            if source is not None and source not in seen:
                sources.append(source)
                seen.add(source)
        return tuple(sources)

    def _selected_source_for_output(self, output: OutputState) -> int | None:
        """Return the preferred active source selector for an output.

        Returns:
            First interpreted active selector, or None when no source is known.
        """
        active_sources = self._active_sources_for_output(output)
        if active_sources:
            return active_sources[0]
        return None

    @property
    def source(self) -> int | None:
        """Return the preferred active source selector.

        Returns:
            Concrete or unanimous active selector, or None when ambiguous or unknown.
        """
        return self._output_value(self._selected_source_for_output)

    @property
    def source_raw(self) -> int | None:
        """Return the raw source byte reported by output state.

        Returns:
            Concrete or unanimous raw source byte, or None.
        """
        return self._output_value(lambda output: output.source_raw)

    @property
    def source_detail(self) -> tuple[int, ...] | None:
        """Return preserved source-selection detail bytes.

        Returns:
            Concrete or unanimous detail byte tuple, or None.
        """
        return self._output_value(lambda output: output.source_detail)

    @property
    def reported_sources(self) -> tuple[int, ...] | None:
        """Return normalized selectors reported by output state.

        Returns:
            Concrete or unanimous reported source tuple, or None.
        """
        return self._output_value(lambda output: output.reported_sources)

    @property
    def active_sources(self) -> tuple[int, ...] | None:
        """Return interpreted active source selectors.

        Returns:
            Concrete or unanimous active selector tuple, or None.
        """
        return self._output_value(self._active_sources_for_output)

    @property
    def local_source_selector(self) -> int | None:
        """Return the interpreted local source selector.

        Returns:
            Concrete or unanimous local selector, or None.
        """
        return self._output_value(self._local_source_selector_for_output)

    @property
    def local_source(self) -> int | None:
        """Return the interpreted local source selector alias.

        Returns:
            Same value as local_source_selector.
        """
        return self.local_source_selector

    @property
    def remote_source_selector(self) -> int | None:
        """Return the reported remote source selector.

        Returns:
            Concrete or unanimous remote selector, or None.
        """
        return self._output_value(lambda output: output.remote_source_selector)

    @property
    def remote_source(self) -> RemoteSourceSelector | None:
        """Return the active remote source selector object.

        Returns:
            RemoteSourceSelector for the active present remote source, or None.
        """
        remote_source_selector = self.remote_source_selector
        if remote_source_selector is None:
            return None
        remote_source_id = remote_source_selector - REMOTE_SOURCE_SELECTOR_MIN
        remote_source = self.system.state.remote_inputs.get(remote_source_id)
        if remote_source is None or not remote_source.present:
            return None
        return RemoteSourceSelector(self.system, remote_source_id)

    @property
    def remote_backing_device(self) -> "DeviceSelector | None":
        """Return the device backing the active remote source.

        Returns:
            DeviceSelector for the remote backing device, or None.
        """
        remote_source = self.remote_source
        if remote_source is None:
            return None
        return remote_source.backing_device

    @property
    def remote_backing_input(self) -> "InputSelector | None":
        """Return the input backing the active remote source.

        Returns:
            InputSelector for the remote backing input, or None.
        """
        remote_source = self.remote_source
        if remote_source is None:
            return None
        return remote_source.backing_input

    @property
    def volume(self) -> float | None:
        """Return output volume.

        Returns:
            Concrete or unanimous output volume, or None.
        """
        return self._output_value(lambda output: output.volume)

    @property
    def max_volume(self) -> float | None:
        """Return output maximum volume.

        Returns:
            Concrete or unanimous maximum volume, or None.
        """
        return self._output_value(lambda output: output.max_volume)

    @property
    def device(self) -> DeviceSelector:
        """Return the device that owns this concrete output.

        Returns:
            DeviceSelector for the owning device.
        """
        if self.is_all_outputs:
            raise ValueError("Cannot determine device for ALL_OUTPUTS selector")
        device = self.system.state.device_for_output(self.output_id)
        if device is None:
            raise ValueError(f"Cannot find device for output {self.output_id}")
        return self.system.device_by_id(device.id)

    @property
    def input(self) -> "InputSelector | None":
        """Return the input currently selected by this concrete output.

        Returns:
            InputSelector for the active input, or None when no input is known.
        """
        if self.is_all_outputs:
            raise ValueError("Cannot determine input for ALL_OUTPUTS selector")
        output = self.output
        if output is None:
            return None

        output_device = self.system.state.device_for_output(self.output_id)
        local_source = self._local_source_selector_for_output(output)
        if local_source is not None and output_device is not None:
            input_state = self.system.state.inputs.get((output_device.id, local_source))
            if input_state is not None:
                return self.system.input_by_id(input_state.device_id, input_state.selector)

        remote_source = self._remote_source_for_output(output)
        if remote_source is not None:
            backing_input = remote_source.backing_input
            if backing_input is not None:
                return backing_input

        remote_source_selector = output.remote_source_selector
        if remote_source_selector is None:
            return None
        device = self.system.state.device_for_output(self.output_id)
        if device is None:
            raise ValueError(f"Cannot find device for output {self.output_id}")
        input_state = self.system.state.inputs.get((device.id, remote_source_selector))
        if input_state is None:
            raise ValueError(
                f"Cannot find input for output {self.output_id} on device {device.id} with source {remote_source_selector}"
            )
        return self.system.input_by_id(input_state.device_id, input_state.selector)

    def enable(self, on: bool = True) -> None:
        """Set output power state.

        Args:
            on: True to power on, False to power off.
        """
        self._send_output_ops(
            codec.StandbyPowerCommand(
                output=output_id,
                is_on=ToggleBool.On if on else ToggleBool.Off,
            )
            for output_id in self._target_output_ids()
        )

    def disable(self) -> None:
        """Power off the selected output or outputs."""
        self.enable(on=False)

    def mute(self, muted: bool = True) -> None:
        """Set output mute state.

        Args:
            muted: True to mute, False to unmute.
        """
        self._send_output_ops(
            codec.MuteCommand(
                output=output_id,
                is_muted=ToggleBool.On if muted else ToggleBool.Off,
            )
            for output_id in self._target_output_ids()
        )

    def unmute(self) -> None:
        """Unmute the selected output or outputs."""
        self.mute(muted=False)

    def set_volume(self, volume: float) -> None:
        """Set output volume."""
        self._send_output_ops(
            codec.VolumeCommand(output=output_id, volume=volume)
            for output_id in self._target_output_ids()
        )

    def set_max_volume(self, max_volume: float) -> None:
        """Set output maximum volume."""
        self._send_output_ops(
            codec.MaximumVolumeCommand(output=output_id, max_volume=max_volume)
            for output_id in self._target_output_ids()
        )

    def set_input(self, input: "InputSelector") -> None:
        """Route an input to this output or to all known outputs."""
        self._send_output_ops(
            self.system.state.source_selection_commands_for_input(
                self.output_id,
                input.input,
            )
        )


class InputSelector(Selector):
    def __init__(self, system: System, device_id: HexBytes, selector: int) -> None:
        """Create a selector for a canonical input.

        Args:
            system: System that owns the canonical state.
            device_id: Device id that owns the input.
            selector: Logical source selector.
        """
        self.system = system
        input_state = self.system.state.inputs.get((device_id, selector))
        if input_state is None:
            raise ValueError(f"Input {device_id}:0x{selector:02X} not found")
        self.input = input_state

    @override
    def _reference(self) -> str:
        """Return a compact input selector reference.

        Returns:
            Reference string containing device id, selector, and qualified name.
        """
        return (
            "InputSelector("
            f"device_id={self.device_id}, "
            f"selector=0x{self.selector:02X}, "
            f"qualified_name={self.qualified_name}"
            ")"
        )

    @override
    def _format_property(self, name: str, value: object) -> str:
        """Format input selector properties for string output.

        Args:
            name: Property name being formatted.
            value: Property value being formatted.

        Returns:
            Human-readable value string.
        """
        if name == "selector":
            assert isinstance(value, int)
            return f"0x{value:02X}"
        return super()._format_property(name, value)

    @property
    def device_id(self) -> HexBytes:
        """Return the id of the device that owns this input.

        Returns:
            Device id.
        """
        return self.input.device_id

    @property
    def selector(self) -> int:
        """Return the logical source selector for this input.

        Returns:
            Source selector byte.
        """
        return self.input.selector

    @property
    def qualified_name(self) -> str:
        """Return the stable device-qualified input name.

        Returns:
            Qualified input name from InputState.
        """
        return self.input.qualified_name

    @property
    def name(self) -> str:
        """Return the best available display name for this input.

        Returns:
            Input display name.
        """
        return self.input.name

    @property
    def device(self) -> DeviceSelector:
        """Return the device selector for this input's device.

        Returns:
            DeviceSelector for the owning device.
        """
        return self.system.device_by_id(self.input.device_id)

    @property
    def outputs(self) -> "tuple[OutputSelector, ...]":
        """Return outputs currently selecting this input.

        Returns:
            Tuple of OutputSelector instances whose active input resolves to this input.
        """
        outputs = []
        for output in self.system.state.outputs.values():
            output_selector = OutputSelector(self.system, output.id)
            output_device = self.system.state.device_for_output(output.id)
            if (
                output_device is not None
                and output_device.id == self.input.device_id
                and self.input.selector in output.reported_sources
            ):
                outputs.append(output_selector)
                continue

            try:
                selected_input = output_selector.input
            except ValueError:
                continue
            if selected_input is not None and selected_input.input == self.input:
                outputs.append(output_selector)
        return tuple(outputs)
