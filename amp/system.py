import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from os import PathLike
from typing import Any, Literal, TypeAlias, TypeVar, cast, overload, override
from uuid import UUID

from amp.byte_utils import HexBytes
from amp.codec import ALL_OUTPUTS, Command
import amp.codec as codec
from amp.state import (
    AUDIO_ONLY_SOURCE_FLAG as AUDIO_ONLY_SOURCE_FLAG,
    LOGICAL_SELECTOR_BY_PHYSICAL_SOURCE_ID as LOGICAL_SELECTOR_BY_PHYSICAL_SOURCE_ID,
    PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR as PHYSICAL_SOURCE_ID_BY_LOGICAL_SELECTOR,
    REMOTE_INPUT_SLOT_IDS as REMOTE_INPUT_SLOT_IDS,
    REMOTE_SOURCE_SELECTOR_MAX as REMOTE_SOURCE_SELECTOR_MAX,
    REMOTE_SOURCE_SELECTOR_MIN as REMOTE_SOURCE_SELECTOR_MIN,
    SOURCE_TURN_ON_FLAG as SOURCE_TURN_ON_FLAG,
    DeviceState as DeviceState,
    InputState as InputState,
    OutputState as OutputState,
    RemoteInput as RemoteInput,
    SystemState as SystemState,
)
from amp.toggle_bool import ToggleBool
from amp.transport import (
    DEFAULT_CONNECTION_TIMEOUT_SECS,
    BaseTransport,
    ConnectionInterrupted,
)
from amp.versioned import VersionTrackerMixin, wait_for_any_change

logger = logging.getLogger(__name__)

OutputValueT = TypeVar("OutputValueT", bound=Hashable)


def _normalize_name(name: str) -> str:
    """Normalize a user-facing name for loose lookup comparisons.

    Returns:
        Whitespace-free, case-folded name text.
    """
    return "".join(name.split()).casefold()


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

    async def save_state(self, file_path: str | PathLike[str]) -> None:
        """Save the currently discovered system state as JSON.

        Call :meth:`discover` first when the file will be used as a configuration
        backup so names, source options, and output settings are complete.

        Args:
            file_path: Destination JSON path.
        """
        await self.state.save_to_file(file_path)

    async def restore_state(
        self,
        file_path: str | PathLike[str],
        *,
        neutral_volume: float = 0.5,
    ) -> None:
        """Restore tracked configuration and leave outputs in a neutral state.

        The restore whitelist contains source names/options, zone names, and
        maximum volume. Saved power, source, mute, and volume are deliberately
        ignored. Each restored output is muted during the operation, set to the
        requested neutral volume, and finally unmuted. The protocol has no safe
        no-source value, so source selection is left unchanged.

        Device identity fields and distributed-source slots are retained in the
        JSON snapshot for discovery/reference but are deliberately outside the
        restore whitelist, including snapshots with per-device slot ownership.

        Args:
            file_path: Source JSON path previously written by :meth:`save_state`.
            neutral_volume: Final output volume in the inclusive range 0.0 to 1.0.

        Raises:
            ValueError: If the snapshot cannot be safely applied to the current
                discovered topology or contains incomplete write data.
        """
        saved_state = await SystemState.load_from_file(file_path)
        restore_ops = self._state_restore_ops(
            saved_state,
            neutral_volume=neutral_volume,
        )
        # Validate with the production encoder even when custom transports do
        # not encode. A malformed later row must not leave an earlier mute or
        # rename queued without the final unmute.
        encoder = codec.CommandEncoder(read_only=False)
        for op in restore_ops:
            encoder.encode(op)
        self.send_ops(*restore_ops)

    def _state_restore_ops(
        self,
        saved_state: SystemState,
        *,
        neutral_volume: float,
    ) -> tuple[Command, ...]:
        """Validate a saved state and build its complete write plan."""
        errors: list[str] = []
        if not 0.0 <= neutral_volume <= 1.0:
            errors.append("neutral_volume must be between 0.0 and 1.0")

        current_devices: dict[HexBytes, DeviceState] = {}
        for device_id, saved_device in saved_state.devices.items():
            current_device = self.state.devices.get(device_id)
            if current_device is None:
                errors.append(f"saved device {device_id} is not present")
                continue
            current_devices[device_id] = current_device
            if (
                saved_device.guid is not None
                and current_device.guid is not None
                and saved_device.guid != current_device.guid
            ):
                errors.append(f"saved device {device_id} has a different GUID")
            if len(self.transports) > 1 and self.transport_for_device(current_device) is None:
                errors.append(f"current device {device_id} has no transport mapping")

        target_outputs: dict[int, OutputState] = {}
        for output_id, saved_output in saved_state.outputs.items():
            current_output = self.state.outputs.get(output_id)
            if current_output is None:
                errors.append(f"saved output {output_id} is not present")
                continue
            saved_output_device = saved_state.device_for_output(output_id)
            current_output_device = self.state.device_for_output(output_id)
            if saved_output_device is None:
                errors.append(f"saved output {output_id} has no owning device")
                continue
            if current_output_device is None:
                errors.append(f"current output {output_id} has no owning device")
                continue
            if saved_output_device.id != current_output_device.id:
                errors.append(
                    f"saved output {output_id} belongs to device {saved_output_device.id}, "
                    f"not current device {current_output_device.id}"
                )
                continue
            if saved_output.max_volume is not None and not 0.0 <= saved_output.max_volume <= 1.0:
                errors.append(f"saved output {output_id} has invalid maximum volume")
            target_outputs[output_id] = current_output

        source_restore_ops: list[Command] = []
        for input_key, saved_input in saved_state.inputs.items():
            has_write_data = any(
                value is not None
                for value in (
                    saved_input.options,
                    saved_input.hidden_name,
                    saved_input.assigned_name,
                )
            )
            if not has_write_data:
                continue
            if saved_input.options is None and (
                saved_input.hidden_name is not None or saved_input.assigned_name is not None
            ):
                errors.append(
                    f"saved input {saved_input.qualified_name} has a name but no options bytes"
                )
                continue
            if saved_input.options is not None and len(saved_input.options) != 3:
                errors.append(
                    f"saved input {saved_input.qualified_name} option bytes must be 3 bytes"
                )
                continue
            if input_key not in self.state.inputs:
                errors.append(f"saved input {saved_input.qualified_name} is not present")
                continue
            current_device = current_devices.get(saved_input.device_id)
            if current_device is None:
                errors.append(
                    f"saved input {saved_input.qualified_name} has no current device"
                )
                continue
            representative_output = next(
                (
                    output_id
                    for output_id in current_device.outputs or ()
                    if output_id in self.state.outputs
                ),
                None,
            )
            if representative_output is None:
                errors.append(
                    f"saved input {saved_input.qualified_name} has no current device output"
                )
                continue
            source_restore_ops.append(
                codec.SourceNameOptionsCommand(
                    output=representative_output,
                    source_selector=saved_input.selector,
                    options=saved_input.options,
                    hidden_name=saved_input.hidden_name,
                    name=saved_input.assigned_name,
                )
            )

        if errors:
            raise ValueError("Cannot restore state: " + "; ".join(errors))

        output_ids = tuple(target_outputs)
        mute_first_ops: list[Command] = [
            codec.MuteCommand(output=output_id, is_muted=ToggleBool.On)
            for output_id in output_ids
        ]
        output_configuration_ops: list[Command] = []
        for output_id, saved_output in saved_state.outputs.items():
            if saved_output.name is not None:
                output_configuration_ops.append(
                    codec.ZoneNameCommand(output=output_id, name=saved_output.name)
                )
            if saved_output.max_volume is not None:
                output_configuration_ops.append(
                    codec.MaximumVolumeCommand(
                        output=output_id,
                        max_volume=saved_output.max_volume,
                    )
                )

        neutral_volume_ops: list[Command] = []
        for output_id, current_output in target_outputs.items():
            detail: tuple[int, ...] = ()
            if current_output.volume_detail:
                detail = (round(neutral_volume * 200),)
            neutral_volume_ops.append(
                codec.VolumeCommand(
                    output=output_id,
                    volume=neutral_volume,
                    detail=detail,
                )
            )

        unmute_last_ops: list[Command] = [
            codec.MuteCommand(output=output_id, is_muted=ToggleBool.Off)
            for output_id in output_ids
        ]
        return (
            *mute_first_ops,
            *source_restore_ops,
            *output_configuration_ops,
            *neutral_volume_ops,
            *unmute_last_ops,
        )

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
                transport.validate_send(*routed_ops)
                transport.send(*routed_ops)
            return

        ops_by_transport: defaultdict[BaseTransport[Command], list[Command]] = defaultdict(list)
        for op in ops:
            for target_transport in self._target_transports_for_op(op):
                ops_by_transport[target_transport].append(op)
        for target_transport, transport_ops in ops_by_transport.items():
            target_transport.validate_send(*transport_ops)
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
        if transport is None or device.host is not None:
            return
        if any(
            other_device.id != device.id and other_device.host == transport.host
            for other_device in self.state.devices.values()
        ):
            return
        device.host = transport.host

    def _devices_for_transport_event(
        self,
        transport: BaseTransport[Command],
    ) -> tuple[DeviceState, ...]:
        """Resolve devices likely associated with a transport-originated event.

        Returns:
            Devices assigned to the transport, or the sole device in a
            single-transport system when its host is not yet known.
        """
        devices = tuple(
            device for device in self.state.devices.values() if device.host == transport.host
        )
        if devices:
            return devices

        unhosted_devices = tuple(
            device for device in self.state.devices.values() if device.host is None
        )
        if len(self.transports) == 1 and len(self.state.devices) == 1 and len(unhosted_devices) == 1:
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
            if len(self.transports) > 1:
                return ()

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
                self._note_device_host(device, transport or self.transport)
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
                if transport is None:
                    self.state.remote_inputs[slot_id].update(op)
                else:
                    owners = self._devices_for_transport_event(transport)
                    if len(owners) == 1:
                        self.state.update_remote_input(owners[0].id, op)
                        if isinstance(op, codec.DistributedSourceDefinitionUnusedCommand):
                            key = (owners[0].id, REMOTE_SOURCE_SELECTOR_MIN + slot_id)
                            if key in self.state.inputs:
                                del self.state.inputs[key]
                                self.state.inputs.mark_updated()
                    elif not owners and len(self.transports) == 1 and not self.state.remote_inputs_by_device:
                        self.state.remote_inputs[slot_id].update(op)
                    else:
                        logger.warning("Ignoring remote slot %s with ambiguous owner on %s", slot_id, transport.host)
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
        """Refresh runtime names, distributed routing, and current output state.

        Args:
            transport: Optional transport to target or filter the requests.
        """
        for device in self.state.devices.values():
            self.send_ops(*device.needed_update_ops(), transport=transport)
        for output in self.state.outputs.values():
            self.send_ops(*output.needed_update_ops(), transport=transport)
        for device in self.state.devices.values():
            if transport is not None and self.transport_for_device(device) is not transport:
                continue
            if device.outputs:
                self.send_ops(
                    codec.SourceNameOptionsRequestCommand(output=device.outputs[0]),
                    transport=transport,
                )
            if self.transport_for_device(device) is not None:
                for slot_id in REMOTE_INPUT_SLOT_IDS:
                    self._invalidate_remote_slot(device.id, slot_id)
                self.send_ops(
                    *(codec.DistributedSourceDefinitionRequestCommand(slot_id=slot_id)
                      for slot_id in REMOTE_INPUT_SLOT_IDS),
                    transport=self.transport_for_device(device),
                )
        if not self.state.remote_inputs_by_device:
            for remote_input in self.state.remote_inputs.values():
                remote_input.present = None
                remote_input.device_guid = None
                remote_input.source_index = None
                remote_input.name = None
                self.send_ops(*remote_input.needed_update_ops(), transport=transport)
        self.refresh_outputs(include_names=bool(self.state.outputs), transport=transport)

    def _invalidate_remote_slot(self, device_id: HexBytes, slot_id: int) -> None:
        """Prevent use of a cached route until a fresh device reply arrives."""
        slot = self.state.remote_inputs_by_device[(device_id, slot_id)]
        slot.present = None
        slot.device_guid = None
        slot.source_index = None
        slot.name = None
        self.state._update_remote_consensus(slot_id)

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
                    # A completed identity table is not sufficient for safe
                    # per-amplifier routing without all transport owners.
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
        if any(slot_id not in REMOTE_INPUT_SLOT_IDS for slot_id in slot_ids):
            raise ValueError("Remote slot ids must be between 0 and 31")
        targets: list[tuple[BaseTransport[Command], HexBytes | None]] = []
        for transport in self.transports:
            owners = self._devices_for_transport_event(transport)
            if len(owners) == 1:
                targets.append((transport, owners[0].id))
            elif len(self.transports) == 1 and not owners and not self.state.remote_inputs_by_device:
                targets.append((transport, None))
            else:
                raise ValueError(f"Cannot discover remote slots without an owner for {transport.host}")
        for _, device_id in targets:
            for slot_id in slot_ids:
                if device_id is not None:
                    self._invalidate_remote_slot(device_id, slot_id)
                else:
                    slot = self.state.remote_inputs[slot_id]
                    slot.present = None
                    slot.device_guid = None
                    slot.source_index = None
                    slot.name = None

        def needed_probe_ops(device_id: HexBytes | None) -> list[codec.Command]:
            """Build remote-input slot requests still needed.

            Returns:
                Distributed source definition requests for unknown slots.
            """
            probe_ops: list[codec.Command] = []
            for slot_id in slot_ids:
                slot = self.state.remote_input_for_device(device_id, slot_id)
                if slot is None or slot.present is None:
                    probe_ops.append(codec.DistributedSourceDefinitionRequestCommand(slot_id=slot_id))
            return probe_ops

        while True:
            pending = [(transport, needed_probe_ops(device_id)) for transport, device_id in targets]
            if not any(ops for _, ops in pending):
                break
            for transport, ops in pending:
                self.send_ops(*ops, transport=transport)
            await self.wait_for_ready(
                lambda: all(not needed_probe_ops(device_id) for _, device_id in targets),
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
        matches = tuple(
            output
            for output in self.state.outputs.values()
            if output.name and _normalize_name(output.name) == normalized_name
        )
        if len(matches) > 1:
            candidates = ", ".join(str(output.id) for output in matches)
            raise ValueError(
                f"Output name {name!r} is ambiguous; candidate output ids: {candidates}"
            )
        if matches:
            return OutputSelector(self, matches[0].id)
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
            input_groups: tuple[tuple[InputState, ...], ...] = (
                (remote_inputs, listed_inputs)
                if prefer_remote
                else (listed_inputs, remote_inputs)
            )
        else:
            input_groups = (listed_inputs,)

        for input_group in input_groups:
            matches = tuple(
                input_state
                for input_state in input_group
                if _normalize_name(input_state.name) == normalized_name
            )
            if not matches and include_hardware_names:
                matches = tuple(
                    input_state
                    for input_state in input_group
                    if input_state.hardware_name is not None
                    and _normalize_name(input_state.hardware_name) == normalized_name
                )
            if len(matches) > 1:
                candidates = ", ".join(
                    input_state.qualified_name for input_state in matches
                )
                raise ValueError(
                    f"Input name {name!r} is ambiguous; candidates: {candidates}. "
                    "Use input_by_id(device_id, selector) to choose one."
                )
            if matches:
                input_state = matches[0]
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
    def __init__(
        self, system: System, remote_source_id: int, *, device_id: HexBytes | None = None
    ) -> None:
        """Create a selector for a distributed source slot.

        Args:
            system: System that owns the canonical state.
            remote_source_id: Zero-based distributed source slot id.
        """
        self.system = system
        remote_source = (
            self.system.state.remote_input_for_device(device_id, remote_source_id)
            if device_id is not None
            else self.system.state.remote_inputs.get(remote_source_id)
        )
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
        device = self.system.state.device_for_output(output.id)
        return RemoteSourceSelector(
            self.system, remote_source.id, device_id=None if device is None else device.id
        )

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
        output = self.output
        if output is not None:
            return self._remote_source_for_output(output)
        selected: RemoteSourceSelector | None = None
        for target in self.system.state.outputs.values():
            candidate = self._remote_source_for_output(target)
            if candidate is None:
                return None
            if selected is not None and (
                candidate.id,
                candidate.remote_source.device_guid,
                candidate.remote_source.source_index,
                candidate.name,
            ) != (
                selected.id,
                selected.remote_source.device_guid,
                selected.remote_source.source_index,
                selected.name,
            ):
                return None
            selected = candidate
        return selected

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
