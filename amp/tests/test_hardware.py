from types import MappingProxyType

from amp.byte_utils import HexBytes
from amp.hardware import MA6, M6250, MODELS_BY_MODEL_NUMBER, model_by_number


def test_models_are_keyed_by_model_number() -> None:
    assert isinstance(MODELS_BY_MODEL_NUMBER, MappingProxyType)
    assert MODELS_BY_MODEL_NUMBER[HexBytes("B0")] is M6250
    assert MODELS_BY_MODEL_NUMBER[HexBytes("E9")] is MA6


def test_model_by_number_accepts_hexbytes_strings_bytes_and_ints() -> None:
    assert model_by_number(HexBytes("B0")) is M6250
    assert model_by_number("E9") is MA6
    assert model_by_number(bytes.fromhex("B0")) is M6250
    assert model_by_number(0xE9) is MA6
    assert model_by_number("FF") is None


def test_m6250_model_info_matches_observed_probe_data() -> None:
    assert M6250.name == "M6250"
    assert M6250.output_count == 8
    assert M6250.input_count == 8
    assert M6250.source_count == 8
    assert [(source.selector, source.name) for source in M6250.local_sources] == [
        (0x05, "A1"),
        (0x06, "A2"),
        (0x07, "A3"),
        (0x03, "A4"),
        (0x00, "COAX1"),
        (0x01, "COAX2"),
        (0x02, "OPT1"),
        (0x04, "OPT2"),
    ]
    assert M6250.casting_sources == ()


def test_ma6_model_info_matches_observed_probe_data() -> None:
    assert MA6.name == "MA6"
    assert MA6.output_count == 8
    assert MA6.input_count == 19
    assert MA6.source_count == 19
    assert [(source.selector, source.name) for source in MA6.local_sources] == [
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
    ]
    assert [source.selector for source in MA6.casting_sources] == [
        0x0B,
        0x0C,
        0x0D,
        0x0E,
        0x0F,
        0x50,
        0x51,
        0x52,
    ]


def test_hardware_model_sources_do_not_include_runtime_remote_sources() -> None:
    for model in (M6250, MA6):
        assert all(source.kind in {"local", "casting"} for source in model.sources)
        assert all(not 0x20 <= source.selector < 0x50 for source in model.sources)
