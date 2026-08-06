from amp.byte_utils import HexBytes
from amp.codec import (
    RequestDeviceInformationCommandResponse,
    SourceNameOptionsCommand,
    SourceSelectionCommand,
)
from amp.state import InputState, OutputState, SystemState


def test_output_state_interprets_local_and_remote_source_selectors() -> None:
    output = OutputState(id=1)

    output.update(SourceSelectionCommand(output=1, source=0x82, detail=(0x20,)))

    assert output.source_raw == 0x82
    assert output.source_detail == (0x20,)
    assert output.reported_sources == (0x02, 0x20)
    assert output.local_source_selector == 0x02
    assert output.remote_source_selector == 0x20
    assert output.selected_reported_source_selector == 0x02


def test_system_state_applies_hardware_defaults_to_devices_and_inputs() -> None:
    state = SystemState()
    state.devices[HexBytes("00D4")].update(
        RequestDeviceInformationCommandResponse(
            firmware=6,
            model_id=HexBytes("B0"),
            device_id=HexBytes("00D4"),
            zones=(1, 2),
        )
    )

    state.apply_hardware_defaults()

    device = state.devices[HexBytes("00D4")]
    input_state = state.inputs[(HexBytes("00D4"), 0x02)]
    assert device.input_count == 8
    assert device.output_count == 8
    assert input_state.name == "OPT1"
    assert input_state.physical_source_id == 7


def test_system_state_json_round_trips_inputs_outputs_and_devices() -> None:
    state = SystemState()
    state.devices[HexBytes("00D4")].update(
        RequestDeviceInformationCommandResponse(
            firmware=6,
            model_id=HexBytes("B0"),
            device_id=HexBytes("00D4"),
            zones=(1,),
        )
    )
    state.inputs[(HexBytes("00D4"), 0x02)].update(
        SourceNameOptionsCommand(
            output=1,
            source_selector=0x02,
            options=HexBytes("000001"),
            name="W1",
        )
    )
    state.outputs[1].update(SourceSelectionCommand(output=1, source=0x02))

    reloaded = SystemState.from_json(state.to_json())

    assert reloaded.devices[HexBytes("00D4")].outputs == (1,)
    reloaded_input = reloaded.inputs[(HexBytes("00D4"), 0x02)]
    assert reloaded_input.name == "W1"
    assert reloaded_input.options == HexBytes("000001")
    assert reloaded.outputs[1].source_raw == 0x02


def test_input_state_qualified_name_round_trips_physical_source_ids() -> None:
    input_state = InputState(device_id=HexBytes("00D4"), selector=0x02)

    assert input_state.qualified_name == "00D4:7"
    assert InputState.parse_qualified_name("00D4:7") == (HexBytes("00D4"), 0x02)
    assert InputState.parse_qualified_name("00D4:0x20") == (HexBytes("00D4"), 0x20)
