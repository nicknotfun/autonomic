# Model catalog for observed and reverse-engineered Autonomic hardware traits.
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HardwareModel:
    key: str
    name: str
    model_byte: int
    output_count: int | None = None
    amplified_output_count: int | None = None
    preamp_output_count: int | None = None
    source_count: int | None = None
    source_base: int = 0
    physical_source_offset: int = 0
    analog_inputs: int | None = None
    coax_inputs: int | None = None
    optical_inputs: int | None = None
    streaming_source_count: int = 0
    is_mms: bool = False
    is_integrated_mms: bool = False
    unstackable: bool = False
    mrad_volume_max: int = 100
    watts_8_ohm: int | None = None
    watts_4_ohm: int | None = None
    bridged_watts_8_ohm: int | None = None
    source_labels_by_data: tuple[tuple[int, str], ...] = ()
    observed_extra_source_labels_by_data: tuple[tuple[int, str], ...] = ()
    notes: tuple[str, ...] = ()


HARDWARE_MODELS: dict[int, HardwareModel] = {
    0x87: HardwareModel(
        key="M400",
        name="M-400 Mirage Multi-Zone Amplifier",
        model_byte=0x87,
        output_count=4,
        amplified_output_count=4,
        source_count=6,
        source_base=0,
        notes=(
            "Control4 amp utility model byte M400_TYPE_ID=0x87.",
            "Control4 amp utility reports SourceCount=6.",
            "Per-device output count follows the M400/M401e four-zone model family.",
        ),
    ),
    0x88: HardwareModel(
        key="M800",
        name="M-800 Mirage Multi-Zone Amplifier",
        model_byte=0x88,
        output_count=8,
        amplified_output_count=6,
        preamp_output_count=2,
        source_count=8,
        source_base=0,
        notes=(
            "Control4 amp utility model byte M800_TYPE_ID=0x88.",
            "Control4 amp utility reports SourceCount=8.",
            "Per-device output count follows the M800/M801e eight-zone model family.",
        ),
    ),
    0x8D: HardwareModel(
        key="M401e",
        name="M-401e eSeries Digital Amplifier",
        model_byte=0x8D,
        output_count=4,
        amplified_output_count=4,
        source_count=6,
        source_base=0,
        notes=(
            "Control4 amp utility model byte M401E_TYPE_ID=0x8D.",
            "Control4 amp utility reports SourceCount=6.",
            "Product documentation describes four independent zones.",
        ),
    ),
    0x8E: HardwareModel(
        key="M801e",
        name="M-801e eSeries Digital Amplifier",
        model_byte=0x8E,
        output_count=8,
        amplified_output_count=6,
        preamp_output_count=2,
        source_count=12,
        source_base=0,
        notes=(
            "Control4 amp utility model byte M801E_TYPE_ID=0x8E.",
            "Control4 amp utility reports SourceCount=12.",
            "Product documentation describes eight zone outputs.",
        ),
    ),
    0x93: HardwareModel(
        key="M120e",
        name="M-120e eSeries Digital Amplifier",
        model_byte=0x93,
        output_count=4,
        amplified_output_count=4,
        source_count=2,
        source_base=0,
        unstackable=True,
        watts_8_ohm=30,
        notes=(
            "Control4 amp utility model byte M120E_TYPE_ID=0x93.",
            "Control4 amp utility reports SourceCount=2 and marks M120e unstackable.",
            "Product documentation describes four rooms/zones and two local source inputs.",
        ),
    ),
    0x98: HardwareModel(
        key="M120e",
        name="M-120e eSeries Digital Amplifier Gen 2",
        model_byte=0x98,
        output_count=4,
        amplified_output_count=4,
        source_count=2,
        source_base=0,
        unstackable=True,
        watts_8_ohm=30,
        notes=(
            "Control4 amp utility model byte M120E2_TYPE_ID=0x98.",
            "Control4 amp utility reports SourceCount=2 and treats gen 2 as M120e.",
        ),
    ),
    0x9B: HardwareModel(
        key="M250e",
        name="M-250e eSeries Amplifier",
        model_byte=0x9B,
        source_count=3,
        source_base=0,
        notes=(
            "Control4 amp utility model byte M250E_TYPE_ID=0x9B.",
            "Control4 amp utility reports SourceCount=3.",
        ),
    ),
    0xB0: HardwareModel(
        key="M-6250",
        name="M-6250 Matrix Amplifier",
        model_byte=0xB0,
        output_count=8,
        amplified_output_count=6,
        preamp_output_count=2,
        source_count=8,
        source_base=0,
        mrad_volume_max=80,
        analog_inputs=4,
        coax_inputs=2,
        optical_inputs=2,
        watts_8_ohm=55,
        watts_4_ohm=85,
        source_labels_by_data=(
            (0x05, "A1"),
            (0x06, "A2"),
            (0x07, "A3"),
            (0x03, "A4"),
            (0x00, "COAX1"),
            (0x01, "COAX2"),
            (0x02, "OPT1"),
            (0x04, "OPT2"),
        ),
        notes=(
            "Observed as model byte 0xB0 on device 00D4 at 10.1.0.200.",
            "Probe reported zones 1-8.",
            "MRAD bridge on 10.1.0.201 reports an 80-step volume expression for these zones.",
        ),
    ),
    0xE1: HardwareModel(
        key="MMS-1e",
        name="MMS-1e Mirage Music Streamer",
        model_byte=0xE1,
        output_count=0,
        source_count=0,
        is_mms=True,
        unstackable=True,
        notes=(
            "Control4 amp utility model byte MMS1E_TYPE_ID=0xE1.",
            "Tracked only for direct-amplifier stack discovery; MMS control is intentionally not exposed.",
        ),
    ),
    0xE2: HardwareModel(
        key="MSB-20e",
        name="MSB-20e Mirage Soundbar",
        model_byte=0xE2,
        output_count=1,
        source_count=5,
        source_base=0,
        physical_source_offset=1,
        is_mms=True,
        is_integrated_mms=True,
        unstackable=True,
        notes=(
            "Control4 amp utility model byte MSB20E_TYPE_ID=0xE2.",
            "Control4 amp utility reports SourceCount=5 and a physical source offset of 1.",
            "Tracked only for direct-amplifier stack discovery; MMS control is intentionally not exposed.",
        ),
    ),
    0xE3: HardwareModel(
        key="MMS-3e",
        name="MMS-3e Mirage Music Streamer",
        model_byte=0xE3,
        output_count=0,
        source_count=0,
        is_mms=True,
        unstackable=True,
        notes=(
            "Control4 amp utility model byte MMS3E_TYPE_ID=0xE3.",
            "Tracked only for direct-amplifier stack discovery; MMS control is intentionally not exposed.",
        ),
    ),
    0xE5: HardwareModel(
        key="MMS-5e",
        name="MMS-5e Mirage Music Streamer",
        model_byte=0xE5,
        output_count=0,
        source_count=0,
        is_mms=True,
        unstackable=True,
        notes=(
            "Control4 amp utility model byte MMS5E_TYPE_ID=0xE5.",
            "Tracked only for direct-amplifier stack discovery; MMS control is intentionally not exposed.",
        ),
    ),
    0xE9: HardwareModel(
        key="MA6",
        name="MA6 Streaming Amplifier",
        model_byte=0xE9,
        output_count=8,
        amplified_output_count=6,
        preamp_output_count=2,
        source_count=12,
        source_base=0,
        mrad_volume_max=80,
        analog_inputs=4,
        coax_inputs=2,
        optical_inputs=2,
        streaming_source_count=3,
        watts_8_ohm=120,
        watts_4_ohm=180,
        bridged_watts_8_ohm=350,
        source_labels_by_data=(
            (0x05, "Player_A"),
            (0x06, "Player_B"),
            (0x07, "Player_C"),
            (0x03, "Analog 1"),
            (0x00, "Analog 2"),
            (0x01, "Analog 3"),
            (0x02, "Analog 4"),
            (0x04, "Coaxial 1"),
            (0x08, "Coaxial 2"),
            (0x09, "Optical 1"),
            (0x0A, "Optical 2"),
            (0x0B, "Casting_1"),
        ),
        observed_extra_source_labels_by_data=(
            (0x0C, "Casting_2"),
            (0x0D, "Casting_3"),
            (0x0E, "Casting_4"),
            (0x0F, "Casting_5"),
            (0x50, "Casting_6"),
            (0x51, "Casting_7"),
            (0x52, "Casting_8"),
        ),
        notes=(
            "Observed as model byte 0xE9 on device 6012 at 10.1.0.201.",
            "Probe reported zones 9-16 when discovered through 10.1.0.200.",
            "The Control4 module names additional casting selectors, but probing reported 12 assignable local sources; extra casting labels are tracked separately.",
        ),
    ),
}

LEGACY_MODEL_NAMES: dict[int, str] = {model_byte: model.key for model_byte, model in HARDWARE_MODELS.items()}


def model_for_byte(model_byte: int | None) -> HardwareModel | None:
    if model_byte is None:
        return None
    return HARDWARE_MODELS.get(int(model_byte))


def model_name_for_byte(model_byte: int | None) -> str | None:
    model = model_for_byte(model_byte)
    if model is not None:
        return model.key
    if model_byte is None:
        return None
    return LEGACY_MODEL_NAMES.get(int(model_byte))
