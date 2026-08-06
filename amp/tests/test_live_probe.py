from amp.byte_utils import HexBytes
from amp.codec import (
    RequestZoneAssignmentsCommandResponse,
    SourceNameOptionsCommand,
    SourceSelectionCommand,
)
from amp_live_probe import discovered_device_ids, discovered_outputs, discovered_selectors


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
