# Tests for direct amplifier command encoding, discovery, and typed controls.
from __future__ import annotations

import unittest

from autonomic import (
    AmplifierDeviceInfo,
    AmplifierNetworkInfo,
    AmplifierResetDefaults,
    MirageAmplifier,
    ProtocolError,
    decode_output_address,
    encode_output_address,
)


class FakeMirageAmplifier(MirageAmplifier):
    def __init__(self, responses: dict[str, str] | None = None, **kwargs):
        super().__init__("127.0.0.1", **kwargs)
        self.sent: list[str] = []
        self.responses = responses or {}

    def send_commands(self, commands, *, timeout=None) -> str:
        normalized = [command.upper().rstrip("\r\n") for command in commands]
        self.sent.extend(normalized)
        return "\n".join(self.responses.get(command, "OK") for command in normalized)

    def send_ascii(self, command: str) -> str:
        return self.send_commands([command]).strip()


class MirageAmplifierDeviceInfoTests(unittest.TestCase):
    def test_parse_device_id_from_support_response(self):
        self.assertEqual(MirageAmplifier.parse_device_id("AFFF01A80102030405060708"), "01A8")
        with self.assertRaises(ProtocolError):
            MirageAmplifier.parse_device_id("NOPE")

    def test_direct_output_control_command_builders(self):
        self.assertEqual(MirageAmplifier.build_data_command("03", "1F", "02"), "031F02")
        self.assertEqual(MirageAmplifier.build_data_command(3, 31, 2), "031F02")
        self.assertEqual(MirageAmplifier.source_data(7), "02")
        self.assertEqual(MirageAmplifier.expected_response_keys(["110A", "120A", "010A04"]), {(0x04, 10), (0x01, 10)})

        amp = FakeMirageAmplifier()
        amp.set_output_mute(10, True)
        amp.set_output_mute(10, False)
        amp.set_output_mute(10, "toggle")
        amp.set_output_power(10, True)
        amp.set_output_power(10, False)
        amp.set_output_power(10, "toggle")
        amp.set_all_output_power(True)
        amp.set_all_output_power(False)
        amp.set_output_volume(10, 50)
        amp.output_volume_up(10)
        amp.output_volume_down(10)
        amp.set_output_max_volume(10, 75)
        amp.set_output_bass(10, -3)
        amp.set_output_treble(10, 4)
        amp.set_output_balance(10, -10)
        amp.set_output_gain(10, 2)
        amp.set_output_delay(10, 125)
        amp.set_output_loudness(10, True)
        amp.set_output_name(10, "Kitchen")
        amp.set_input_gain(7, 10, 50)
        amp.set_source_metadata(6, 0, "Artist")
        amp.set_source_metadata(6, 3, "Title", refresh=False)
        amp.set_source_metadata_fields(6, {1: "Album", 2: "Track"})
        amp.refresh_source_metadata(6)
        amp.assign_source_to_output(7, 31)
        amp.assign_source_to_output(12, 31)
        amp.assign_source_to_output(32, 31)
        amp.assign_source_to_all_outputs(1)
        amp.assign_output_sources({1: 1, 2: 8})
        amp.request_all_parameters(1)
        amp.request_device_model_info()
        amp.request_extended_device_info("00:D4")
        amp.request_network_info("00D4")
        amp.request_system_id("00D4")
        amp.request_device_guid("00D4")
        amp.query_device_status_info("6012")
        amp.query_device_link_info("6012", "4D")
        amp.query_device_links("00D4")
        amp.query_device_state_info("00D4")
        amp.query_output_names()
        amp.ping_device()

        self.assertEqual(
            amp.sent,
            [
                "020A00",
                "020A01",
                "020A02",
                "010A01",
                "010A00",
                "010A04",
                "01FF01",
                "01FF00",
                "040A50",
                "110A",
                "120A",
                "0D0A78",
                "050AFD",
                "060A04",
                "070AF6",
                "440A02",
                "310A19",
                "0C0A01",
                "1C0A4B69746368656E",
                "320A0209",
                "32FF",
                "46FF0100417274697374",
                "47FF0100",
                "46FF01035469746C65",
                "46FF0101416C62756D",
                "47FF0101",
                "46FF0102547261636B",
                "47FF0102",
                "47FF0100",
                "47FF0101",
                "47FF0102",
                "47FF0103",
                "031F02",
                "031F0B",
                "031F20",
                "03FF05",
                "030105",
                "030204",
                "090100",
                "14FF06",
                "39FF00D4",
                "3AFF00D483",
                "3AFF00D486",
                "3AFF00D485",
                "3AFF601287",
                "4DFF6012",
                "4DFF00D4",
                "CDFF00D4",
                "4AFF00D4",
                "38FF",
                "14FF06",
            ],
        )

    def test_direct_output_control_validation(self):
        amp = FakeMirageAmplifier()
        with self.assertRaises(ValueError):
            amp.set_output_volume(1, 101)
        with self.assertRaises(ValueError):
            amp.set_output_power(1, "cycle")
        with self.assertRaises(ValueError):
            amp.assign_source_to_output(64, 1)
        with self.assertRaises(ValueError):
            amp.set_output_bass(1, -13)
        with self.assertRaises(ValueError):
            amp.set_output_balance(1, 21)
        with self.assertRaises(ValueError):
            amp.set_output_delay(1, 127)
        with self.assertRaises(ValueError):
            amp.set_input_gain(1, 1, 101)
        with self.assertRaises(ValueError):
            amp.set_output_name(1, "x" * 26)
        with self.assertRaises(ValueError):
            MirageAmplifier.guid_to_wire("not-a-guid")
        with self.assertRaises(ValueError):
            MirageAmplifier.build_device_link_query_command("00D4", "4E")
        with self.assertRaises(ValueError):
            amp.set_source_metadata(1, 4, "Nope")
        with self.assertRaises(ValueError):
            amp.set_source_metadata(1, 0, "x" * 101)

    def test_direct_reset_defaults_builds_safe_all_output_batch(self):
        amp = FakeMirageAmplifier(output_count=2)

        response = amp.reset_all_to_defaults()

        self.assertEqual(response, "\n".join(["OK"] * 22))
        self.assertEqual(
            amp.sent,
            [
                "02FF00",
                "0DFFA0",
                "04FF00",
                "03FF05",
                "05FF00",
                "06FF00",
                "07FF00",
                "44FF00",
                "31FF00",
                "0CFF00",
                "1C015A6F6E652031",
                "1C025A6F6E652032",
                "32FF0500",
                "32FF0600",
                "32FF0700",
                "32FF0300",
                "32FF0000",
                "32FF0100",
                "32FF0200",
                "32FF0400",
                "02FF01",
                "01FF00",
            ],
        )

    def test_direct_reset_defaults_can_be_customized_and_validated(self):
        amp = FakeMirageAmplifier()
        defaults = AmplifierResetDefaults(
            source=32,
            volume=25,
            max_volume=80,
            bass=-2,
            treble=3,
            balance=4,
            gain=-1,
            input_gain=50,
            delay_ms=50,
            loudness=True,
            muted=True,
            is_on=True,
        )

        commands = amp.reset_all_to_defaults_commands(defaults, safety_mute=False, reset_output_names=False)

        self.assertEqual(
            commands,
            [
                "0DFF80",
                "04FF28",
                "03FF20",
                "05FFFE",
                "06FF03",
                "07FF04",
                "44FFFF",
                "31FF0A",
                "0CFF01",
                "32FF0509",
                "32FF0609",
                "32FF0709",
                "32FF0309",
                "32FF0009",
                "32FF0109",
                "32FF0209",
                "32FF0409",
                "02FF00",
                "01FF01",
            ],
        )
        with self.assertRaises(ValueError):
            amp.reset_all_to_defaults_commands(AmplifierResetDefaults(volume=50, max_volume=25))
        with self.assertRaises(ValueError):
            amp.reset_all_to_defaults_commands(AmplifierResetDefaults(input_gain=101))

    def test_direct_source_name_helpers_build_low_level_label_commands(self):
        amp = FakeMirageAmplifier(source_base=0)
        devices = [
            AmplifierDeviceInfo(amp_id="00D4", model_byte=0xB0, zones=(1, 2)),
            AmplifierDeviceInfo(amp_id="6012", model_byte=0xE9, zones=(9,)),
        ]

        self.assertEqual(
            MirageAmplifier.build_source_name_command(1, 0x01, "COAX1"),
            "290101000001434F415831",
        )

        commands = amp.low_level_source_label_commands(devices=devices)

        self.assertIn("2901050000014131", commands)
        self.assertIn("2902060000014132", commands)
        self.assertIn("2901040000014F505432", commands)
        self.assertIn("290901000001416E616C6F672033", commands)
        self.assertIn("29090B00000143617374696E675F31", commands)
        self.assertNotIn("29095200000143617374696E675F38", commands)

        response = amp.rename_sources_to_low_level_input_labels(devices=devices)

        self.assertEqual(response, "\n".join("OK" for _command in commands))
        self.assertEqual(amp.sent, commands)

    def test_direct_source_metadata_query_and_write_helpers_use_module_opcodes(self):
        amp = FakeMirageAmplifier(
            responses={
                "47FF0100": "46FF0100417274697374",
                "47FF0101": "46FF0101416C62756D",
                "47FF0102": "46FF0102547261636B",
                "47FF0103": "46FF01035469746C65",
            }
        )

        self.assertEqual(
            MirageAmplifier.build_source_metadata_command(0x01, 3, "Title"),
            "46FF01035469746C65",
        )
        self.assertEqual(MirageAmplifier.build_source_metadata_query_command(0x01, 3), "47FF0103")
        self.assertEqual(
            MirageAmplifier.build_source_metadata_query_command(0x01, 3, output=10),
            "470A0103",
        )

        metadata = amp.discover_source_metadata(6)

        self.assertEqual(amp.sent, ["47FF0100", "47FF0101", "47FF0102", "47FF0103"])
        self.assertEqual(
            [(item.source_id, item.position, item.value) for item in metadata],
            [(6, 0, "Artist"), (6, 1, "Album"), (6, 2, "Track"), (6, 3, "Title")],
        )

    def test_direct_source_details_query_combines_name_and_metadata_module_opcodes(self):
        amp = FakeMirageAmplifier(
            source_base=0,
            responses={
                "290105": "2901050000014131\n29100500000143617374696E675F38",
                "47010500": "46010500417274697374",
                "47010501": "46010501416C62756D",
                "47010502": "46010502547261636B",
                "47010503": "460105035469746C65",
            },
        )

        self.assertEqual(MirageAmplifier.build_source_name_query_command(0x05, output=1), "290105")

        names = amp.refresh_source_name(0, output=1)
        details = amp.refresh_source_details(0, output=1)

        self.assertEqual(
            amp.sent,
            ["290105", "290105", "47010500", "47010501", "47010502", "47010503"],
        )
        self.assertEqual([(item.output, item.source_id, item.name) for item in names], [(1, 0, "A1")])
        self.assertEqual(details.output, 1)
        self.assertEqual(details.source_id, 0)
        self.assertIsNotNone(details.name)
        self.assertEqual(details.name.name, "A1")
        self.assertEqual(
            [(item.output, item.source_id, item.position, item.value) for item in details.metadata],
            [(1, 0, 0, "Artist"), (1, 0, 1, "Album"), (1, 0, 2, "Track"), (1, 0, 3, "Title")],
        )

    def test_direct_source_metadata_can_query_output_scoped_and_bulk_fields(self):
        amp = FakeMirageAmplifier(
            source_base=0,
            source_count=2,
            responses={
                "47010500": "46010500417274697374",
                "47010501": "46010501416C62756D",
                "47010502": "46010502547261636B",
                "47010503": "460105035469746C65",
                "47FF0500": "46FF0500417274697374",
                "47FF0601": "46FF0601416C62756D",
            },
        )

        scoped = amp.refresh_source_metadata(0, output=1)
        bulk = amp.refresh_all_source_metadata(sources=[0, 1])

        self.assertEqual(amp.sent[:4], ["47010500", "47010501", "47010502", "47010503"])
        self.assertEqual(
            amp.sent[4:],
            ["47FF0500", "47FF0501", "47FF0502", "47FF0503", "47FF0600", "47FF0601", "47FF0602", "47FF0603"],
        )
        self.assertEqual(
            [(item.output, item.source_id, item.position) for item in scoped],
            [(1, 0, 0), (1, 0, 1), (1, 0, 2), (1, 0, 3)],
        )
        self.assertEqual(
            [(item.output, item.source_id, item.position, item.value) for item in bulk],
            [(None, 0, 0, "Artist"), (None, 1, 1, "Album")],
        )

    def test_direct_input_gain_query_parses_module_opcode(self):
        amp = FakeMirageAmplifier(responses={"3201": "3201FF0800000000000000"})

        self.assertEqual(MirageAmplifier.build_input_gain_command(1, 0x05, 9), "32010509")

        gains = amp.query_input_gains(1, include_source_names=False)

        self.assertEqual(amp.sent, ["3201"])
        self.assertEqual(len(gains), 8)
        self.assertEqual(gains[0].output, 1)
        self.assertEqual(gains[0].source_id, 1)
        self.assertEqual(gains[0].logical_source, 5)
        self.assertEqual(gains[0].raw_gain, 8)
        self.assertEqual(gains[0].gain_percent, 44)

    def test_direct_source_delay_query_parses_extended_module_payload(self):
        amp = FakeMirageAmplifier(source_base=0, responses={"3101": "31010506070400010203"})

        delays = amp.query_output_source_delays(1)

        self.assertEqual(amp.sent, ["3101"])
        self.assertEqual(
            [(delay.output, delay.logical_source, delay.source_id, delay.delay_ms, delay.raw_delay) for delay in delays],
            [
                (1, 0, 4, 25, 5),
                (1, 1, 5, 30, 6),
                (1, 2, 6, 35, 7),
                (1, 3, 3, 20, 4),
                (1, 4, 7, 0, 0),
                (1, 5, 0, 5, 1),
                (1, 6, 1, 10, 2),
                (1, 7, 2, 15, 3),
            ],
        )

    def test_direct_preset_group_queries_parse_control4_opcode(self):
        amp = FakeMirageAmplifier(
            responses={
                "4EFF0000": "4EFF000000000102",
                "4EFF0001": "4EFF000100",
                "4EFF0002": "4EFF8002",
                "4EFF0003": "4EFF0003000082074B69746368656E0101",
            }
        )

        self.assertEqual(MirageAmplifier.build_preset_group_query_command(3), "4EFF0003")

        preset_map = amp.query_preset_group_map()
        empty_group = amp.query_preset_group(1)
        unavailable_group = amp.query_preset_group(2)
        named_group = amp.query_preset_group(3)

        self.assertEqual(amp.sent, ["4EFF0000", "4EFF0001", "4EFF0002", "4EFF0003"])
        self.assertIsNotNone(preset_map)
        self.assertTrue(preset_map.available)
        self.assertEqual(preset_map.map_data, "0102")
        self.assertIsNone(preset_map.signature)
        self.assertEqual(preset_map.available_slots, (1, 10))
        self.assertIsNotNone(empty_group)
        self.assertTrue(empty_group.available)
        self.assertTrue(empty_group.empty)
        self.assertIsNotNone(unavailable_group)
        self.assertFalse(unavailable_group.available)
        self.assertTrue(unavailable_group.empty)
        self.assertIsNotNone(named_group)
        self.assertFalse(named_group.empty)
        self.assertTrue(named_group.read_only)
        self.assertEqual(named_group.preset_id, 2)
        self.assertEqual(named_group.name, "Kitchen")
        self.assertEqual(named_group.raw_name, "4B69746368656E")
        self.assertEqual(named_group.member_zones, (1, 9))

        amp.sent.clear()
        self.assertEqual([group.slot for group in amp.discover_preset_groups(slots=[1, 2, 3])], [3])
        self.assertEqual(
            [group.slot for group in amp.discover_preset_groups(slots=[1, 2, 3], include_empty=True, include_unavailable=True)],
            [1, 2, 3],
        )

        discovery = amp.discover()
        self.assertIsNotNone(discovery.preset_group_map)
        self.assertEqual(discovery.preset_group_map.available_slots, (1, 10))
        self.assertEqual([group.slot for group in discovery.preset_groups], [1])

    def test_direct_loudness_accepts_string_boolean_states(self):
        amp = FakeMirageAmplifier()

        amp.set_output_loudness(1, "off")
        amp.set_output_loudness(2, "on")
        amp.set_all_output_loudness("false")

        self.assertEqual(amp.sent, ["0C0100", "0C0201", "0CFF00"])
        with self.assertRaises(ValueError):
            amp.set_output_loudness(1, "toggle")

    def test_direct_max_volume_relative_helpers_read_then_set_clamped_value(self):
        amp = FakeMirageAmplifier(
            responses={
                "0D0A": "0D0A50",
                "0D0B": "0D0BA0",
                "0D0C": "0D0C00",
                "0D0D": "",
            }
        )

        amp.output_max_volume_up(10)
        amp.output_max_volume_down(10)
        amp.output_max_volume_up(11)
        amp.output_max_volume_down(12)

        self.assertEqual(amp.sent, ["0D0A", "0D0A52", "0D0A", "0D0A4E", "0D0B", "0D0BA0", "0D0C", "0D0C00"])
        with self.assertRaises(ProtocolError):
            amp.output_max_volume_up(13)

    def test_direct_relative_tone_gain_and_delay_helpers_read_then_set_clamped_value(self):
        amp = FakeMirageAmplifier(
            responses={
                "050A": "050A0B",
                "060A": "060AF5",
                "070A": "070A01",
                "440A": "440A0B",
                "310A": "310A78",
                "310B": "310B01",
                "050C": "",
            }
        )

        amp.output_bass_up(10)
        amp.output_bass_down(10)
        amp.output_treble_up(10)
        amp.output_treble_down(10)
        amp.output_balance_left(10)
        amp.output_balance_right(10)
        amp.output_gain_up(10)
        amp.output_gain_down(10)
        amp.output_delay_up(10)
        amp.output_delay_down(11)
        amp.output_delay_up(11, step_ms=10)

        self.assertEqual(
            amp.sent,
            [
                "050A",
                "050A0C",
                "050A",
                "050A0A",
                "060A",
                "060AF6",
                "060A",
                "060AF4",
                "070A",
                "070A00",
                "070A",
                "070A02",
                "440A",
                "440A0C",
                "440A",
                "440A0A",
                "310A",
                "310A78",
                "310B",
                "310B00",
                "310B",
                "310B03",
            ],
        )
        with self.assertRaises(ValueError):
            amp.output_delay_up(11, step_ms=3)
        with self.assertRaises(ProtocolError):
            amp.output_bass_up(12)

    def test_direct_device_sub_info_and_link_queries_parse_observed_stack_rows(self):
        amp = FakeMirageAmplifier(
            responses={
                "3AFF601287": "3AFF60120700000000",
                "4DFF6012": "CDFF00D400",
                "CDFF00D4": "4DFF6012",
                "4DFF00D4": "",
                "4AFF00D4": "4AFF00D4FFFFFFFFFFFF",
            }
        )

        self.assertEqual(MirageAmplifier.build_device_sub_info_command("60:12", 0x87), "3AFF601287")
        self.assertEqual(MirageAmplifier.build_device_link_query_command("6012", "4D"), "4DFF6012")
        self.assertEqual(MirageAmplifier.build_device_state_query_command("00D4"), "4AFF00D4")

        sub_info = amp.query_device_status_info("6012")
        link_info = amp.query_device_link_info("6012", "4D")
        links = amp.query_device_links("00D4")
        state_info = amp.query_device_state_info("00D4")

        self.assertEqual(amp.sent, ["3AFF601287", "4DFF6012", "4DFF00D4", "CDFF00D4", "4AFF00D4"])
        self.assertEqual(len(sub_info), 1)
        self.assertEqual(sub_info[0].amp_id, "6012")
        self.assertEqual(sub_info[0].response_type, 0x07)
        self.assertEqual(sub_info[0].payload, (0, 0, 0, 0))
        self.assertEqual(sub_info[0].payload_hex, "00000000")
        self.assertEqual(sub_info[0].value, 0)
        self.assertEqual(len(link_info), 1)
        self.assertEqual(link_info[0].command, 0xCD)
        self.assertEqual(link_info[0].amp_id, "00D4")
        self.assertEqual(link_info[0].status, 0)
        self.assertEqual([(link.command, link.amp_id, link.status) for link in links], [(0x4D, "6012", None)])
        self.assertEqual(len(state_info), 1)
        self.assertEqual(state_info[0].amp_id, "00D4")
        self.assertEqual(state_info[0].payload, (255, 255, 255, 255, 255, 255))
        self.assertEqual(state_info[0].payload_hex, "FFFFFFFFFFFF")

    def test_direct_output_name_query_uses_legacy_refresh_opcode(self):
        amp = FakeMirageAmplifier(
            output_count=2,
            responses={
                "38FF": "1C015A6F6E652031\n1C02",
            },
        )

        names = amp.query_output_names()

        self.assertEqual(amp.sent, ["38FF"])
        self.assertEqual([(name.output, name.name) for name in names], [(1, "Zone 1"), (2, "Zone 2")])

        amp = FakeMirageAmplifier(
            output_count=2,
            responses={
                "38FF": "1C015A6F6E652031\n1C02",
            },
        )

        outputs = amp.list_outputs(include_names=True, include_status=False)

        self.assertEqual(amp.sent, ["38FF"])
        self.assertEqual(outputs[0].name, "Zone 1")
        self.assertEqual(outputs[1].name, "Zone 2")

    def test_direct_reset_defaults_can_clear_remote_source_slots_when_requested(self):
        amp = FakeMirageAmplifier()

        commands = amp.reset_all_to_defaults_commands(clear_remote_sources=True, reset_output_names=False)

        self.assertEqual(commands[18:21], ["4FFF0000", "4FFF0100", "4FFF0200"])
        self.assertEqual(commands[49], "4FFF1F00")
        self.assertEqual(commands[-2:], ["02FF01", "01FF00"])
        with self.assertRaises(ValueError):
            amp.reset_all_to_defaults_commands(
                AmplifierResetDefaults(source=32),
                clear_remote_sources=True,
                reset_output_names=False,
            )

    def test_http_poll_helpers_support_stagey_matrix_shape(self):
        self.assertEqual(MirageAmplifier.build_data_command("04", 1, [0x20, 0x40]), "04012040")
        self.assertEqual(encode_output_address(64), 192)
        self.assertEqual(decode_output_address(192), 64)

        rows = MirageAmplifier.parse_response("04012040   \n030184\nignored\n")
        self.assertEqual(rows[0].command, 4)
        self.assertEqual(rows[0].output, 1)
        self.assertEqual(rows[0].data, [0x20, 0x40])
        self.assertEqual(rows[1].data, [0x84])
        self.assertEqual(MirageAmplifier.decode_matrix_source(rows[1].data[0] & 0x7F), 7)

        amp = FakeMirageAmplifier(source_base=0)
        amp.assign_source_to_output(7, 1)
        amp.assign_source_to_output(33, 1)
        self.assertEqual(amp.sent, ["030104", "030121"])

    def test_all_output_status_queries_expect_per_output_responses(self):
        amp = MirageAmplifier("127.0.0.1", output_count=2)

        keys = amp._expected_all_output_response_keys(["01FF", "02FF", "38FF", "2FFF"])

        self.assertEqual(keys, {(0x01, 1), (0x01, 2), (0x02, 1), (0x02, 2), (0x1C, 1), (0x1C, 2)})

        stacked_amp = MirageAmplifier("127.0.0.1", output_count=2, native_output_start=9)

        self.assertEqual(stacked_amp._expected_all_output_response_keys(["01FF"]), {(0x01, 9), (0x01, 10)})
        self.assertEqual(
            [item.id for item in stacked_amp.list_outputs(include_status=False)],
            ["9", "10"],
        )

    def test_direct_source_names_prefer_this_device_output_rows(self):
        rows = "\n".join(
            [
                "290905000001506C617965725F41",
                "290902000001416E616C6F672034",
                "2901050000014131",
                "2901020000014F505431",
            ]
        )
        m6250 = FakeMirageAmplifier(
            output_count=8,
            source_count=8,
            source_base=0,
            native_output_start=1,
            responses={"29FF": rows, "4FFF": ""},
        )
        ma6 = FakeMirageAmplifier(
            output_count=8,
            source_count=12,
            source_base=0,
            native_output_start=9,
            responses={"29FF": rows, "4FFF": ""},
        )

        m6250_sources = m6250.list_sources(include_disabled=True)
        ma6_sources = ma6.list_sources(include_disabled=True)

        self.assertEqual(m6250_sources[0].name, "A1")
        self.assertEqual(m6250_sources[6].name, "OPT1")
        self.assertEqual(ma6_sources[0].name, "Player_A")
        self.assertEqual(ma6_sources[6].name, "Analog 4")

    def test_direct_output_status_polling_merges_power_mute_source_and_scaled_volume(self):
        amp = FakeMirageAmplifier(
            output_count=2,
            responses={
                "01FF": "010101\n010200",
                "02FF": "020101\n020200",
                "03FF": "030102\n03028522",
                "04FF": "04015064\n040240",
                "05FF": "0501FD\n050202",
                "06FF": "060104\n0602FC",
                "07FF": "0701F6\n07020A",
                "0CFF": "0C0101\n0C0200",
                "0DFF": "0D0180A0\n0D02A0",
                "31FF": "31011900000000000000\n310200",
                "44FF": "440102\n4402FE",
                "29FF": "290102000001434F415832",
                "4FFF": "",
            },
        )

        outputs = amp.list_outputs()

        self.assertEqual(amp.sent, ["29FF", "4FFF", "01FF", "02FF", "03FF", "04FF", "05FF", "06FF", "07FF", "0CFF", "0DFF", "31FF", "44FF"])
        self.assertEqual(len(outputs), 2)
        self.assertTrue(outputs[0].is_on)
        self.assertFalse(outputs[0].muted)
        self.assertEqual(outputs[0].source_id, "7")
        self.assertEqual(outputs[0].source_name, "COAX2")
        self.assertEqual(outputs[0].volume, 50)
        self.assertEqual(outputs[0].bass, -3)
        self.assertEqual(outputs[0].treble, 4)
        self.assertEqual(outputs[0].balance, -10)
        self.assertTrue(outputs[0].loudness)
        self.assertEqual(outputs[0].max_volume, 80)
        self.assertEqual(outputs[0].attributes["maxVolumeStatusData"], "80A0")
        self.assertEqual(outputs[0].delay_ms, 125)
        self.assertEqual(outputs[0].attributes["sourceDelayData"], "1900000000000000")
        self.assertEqual(outputs[0].attributes["sourceDelayMsBySource"], "5:125,6:0,7:0,4:0,8:0,1:0,2:0,3:0")
        self.assertEqual(outputs[0].gain, 2)
        self.assertFalse(outputs[1].is_on)
        self.assertTrue(outputs[1].muted)
        self.assertEqual(outputs[1].source_id, "1")
        self.assertEqual(outputs[1].source_name, "S1")
        self.assertEqual(outputs[1].attributes["sourceStatusData"], "8522")
        self.assertEqual(outputs[1].volume, 40)
        self.assertEqual(outputs[1].bass, 2)
        self.assertEqual(outputs[1].treble, -4)
        self.assertEqual(outputs[1].balance, 10)
        self.assertFalse(outputs[1].loudness)
        self.assertEqual(outputs[1].max_volume, 100)
        self.assertEqual(outputs[1].delay_ms, 0)
        self.assertEqual(outputs[1].gain, -2)

    def test_direct_output_status_polling_can_be_disabled(self):
        amp = FakeMirageAmplifier(output_count=2)

        outputs = amp.list_outputs(include_status=False)

        self.assertEqual(amp.sent, [])
        self.assertEqual([output.volume for output in outputs], [None, None])

    def test_direct_typed_output_reads_use_status_snapshot(self):
        amp = FakeMirageAmplifier(
            output_count=1,
            responses={
                "01FF": "010101",
                "02FF": "020101",
                "03FF": "030102",
                "04FF": "040150",
                "05FF": "0501FD",
                "06FF": "060104",
                "07FF": "0701F6",
                "0CFF": "0C0101",
                "0DFF": "0D0180",
                "31FF": "310119",
                "44FF": "440102",
                "29FF": "290102000001434F415832",
                "4FFF": "",
            },
        )

        self.assertTrue(amp.read_output_power(1))
        self.assertFalse(amp.read_output_mute(1))
        self.assertEqual(amp.read_output_volume(1), 50)
        self.assertEqual(amp.read_output_max_volume(1), 80)
        self.assertEqual(amp.read_output_bass(1), -3)
        self.assertEqual(amp.read_output_treble(1), 4)
        self.assertEqual(amp.read_output_balance(1), -10)
        self.assertEqual(amp.read_output_gain(1), 2)
        self.assertEqual(amp.read_output_delay(1), 125)
        self.assertTrue(amp.read_output_loudness(1))
        self.assertEqual(amp.read_output_source_id(1), "7")
        self.assertEqual(amp.read_output_source_name(1), "COAX2")

    def test_direct_discovery_parses_module_opcodes(self):
        text = "\n".join(
            [
                "94FF0006B000D40102030405060708",
                "B9FF000000D4060300393A0A0100C8ACE14F0055B41907150202",
                "3AFF00D403030A0100C8FFFF00000A0100010A010001",
                "3AFF00D40601",
                "3AFF00D40500194E67A9F8BEF6A4653D0FBEE12977",
                "29090500000115506C617965725F4140414345313446303036303132506C617965725F41",
                "4FFF008768126C88DF41BDABBD079C4E7436940A4F70746963616C2032",
                "46FF05034469737472616374696F6E73",
                "3000070102",
            ]
        )
        rows = MirageAmplifier.parse_response(text)

        source_name = MirageAmplifier.parse_source_name(rows[5])
        remote_source = MirageAmplifier.parse_remote_source(rows[6])
        metadata = MirageAmplifier.parse_source_metadata(rows[7])
        group = MirageAmplifier.parse_zone_group(rows[8])

        self.assertIsNotNone(source_name)
        self.assertEqual(source_name.source_id, 1)
        self.assertEqual(source_name.name, "Player_A")
        self.assertEqual(source_name.hidden_name, "Player_A@ACE14F006012")
        self.assertIsNotNone(remote_source)
        self.assertEqual(remote_source.source_id, 32)
        self.assertEqual(remote_source.name, "Optical 2")
        self.assertEqual(remote_source.guid, "6c126887-df88-bd41-abbd-079c4e743694")
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.source_id, 1)
        self.assertEqual(metadata.position, 3)
        self.assertEqual(metadata.value, "Distractions")
        self.assertIsNotNone(group)
        self.assertEqual(group.zones, (1, 2))
        self.assertTrue(group.source_linked)
        self.assertTrue(group.volume_linked)
        self.assertTrue(group.power_linked)

    def test_direct_device_discovery_merges_model_network_guid_and_system_id(self):
        amp = FakeMirageAmplifier(
            responses={
                "14FF06": "94FF0006B000D40102030405060708",
                "39FF00D4": "B9FF000000D4060300393A0A0100C8ACE14F0055B41907150202",
                "3AFF00D483": "3AFF00D403030A0100C8FFFF00000A0100010A010001",
                "3AFF00D486": "3AFF00D40601",
                "3AFF00D485": "3AFF00D40500194E67A9F8BEF6A4653D0FBEE12977",
            }
        )

        devices = amp.discover_devices()

        self.assertEqual(amp.sent, ["14FF06", "39FF00D4", "3AFF00D483", "3AFF00D486", "3AFF00D485"])
        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.amp_id, "00D4")
        self.assertEqual(device.model_byte, 0xB0)
        self.assertEqual(device.model_name, "M-6250")
        self.assertEqual(device.zones, (1, 2, 3, 4, 5, 6, 7, 8))
        self.assertEqual(device.mac, "ACE14F0055B4")
        self.assertEqual(device.guid, "674e1900-f8a9-f6be-a465-3d0fbee12977")
        self.assertEqual(device.system_id, 1)
        self.assertIsInstance(device.network, AmplifierNetworkInfo)
        self.assertEqual(device.network.ip_address, "10.1.0.200")
        self.assertEqual(device.network.subnet_mask, "255.255.0.0")

    def test_direct_layout_is_inferred_from_discovery_and_cached(self):
        amp = FakeMirageAmplifier(
            responses={
                "14FF06": "\n".join(
                    [
                        "94FF0006B000D40102030405060708",
                        "94FF0006E96012090A0B0C0D0E0F10",
                    ]
                ),
                "39FF00D4": "B9FF000000D4060300393A0A0100C8ACE14F0055B41907150202",
                "3AFF00D483": "3AFF00D403030A0100C8FFFF00000A0100010A010001",
                "3AFF00D486": "3AFF00D40601",
                "3AFF00D485": "3AFF00D40500194E67A9F8BEF6A4653D0FBEE12977",
                "39FF6012": "B9FF00006012060300393A0A0100C9ACE14F0060121907150202",
                "3AFF601283": "3AFF601203030A0100C9FFFF00000A0100010A010001",
                "3AFF601286": "3AFF60120601",
                "3AFF601285": "3AFF6012058768126C88DF41BDABBD079C4E743694",
                "2FFF": "AFFF60120102030405060708",
            }
        )

        layout = amp.infer_layout()
        first_probe = list(amp.sent)
        devices = amp.discover_devices()
        cached_layout = amp.infer_layout()

        self.assertEqual(layout.output_count, 16)
        self.assertEqual(layout.source_count, 12)
        self.assertEqual(layout.source_base, 0)
        self.assertEqual(layout.device_id, "6012")
        self.assertEqual(layout.model_byte, 0xE9)
        self.assertEqual(cached_layout, layout)
        self.assertEqual(len(devices), 2)
        self.assertEqual(amp.sent, first_probe)


if __name__ == "__main__":
    unittest.main()
