from __future__ import annotations

import unittest

from autonomic import MirageAmplifier, ProtocolError, decode_output_address, encode_output_address


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

        amp = FakeMirageAmplifier()
        amp.set_output_mute(10, True)
        amp.set_output_mute(10, False)
        amp.set_output_mute(10, "toggle")
        amp.set_output_power(10, True)
        amp.set_output_power(10, False)
        amp.set_output_volume(10, 50)
        amp.output_volume_up(10)
        amp.output_volume_down(10)
        amp.assign_source_to_output(7, 31)
        amp.assign_source_to_all_outputs(1)
        amp.assign_output_sources({1: 1, 2: 8})
        amp.request_all_parameters(1)

        self.assertEqual(
            amp.sent,
            [
                "020A00",
                "020A01",
                "020A02",
                "010A01",
                "010A00",
                "040A50",
                "110A00",
                "120A00",
                "031F02",
                "03FF05",
                "030105",
                "030204",
                "090100",
            ],
        )

    def test_direct_output_control_validation(self):
        amp = FakeMirageAmplifier()
        with self.assertRaises(ValueError):
            amp.set_output_volume(1, 101)
        with self.assertRaises(ValueError):
            amp.set_output_power(1, "toggle")
        with self.assertRaises(ValueError):
            amp.assign_source_to_output(9, 1)

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

        keys = amp._expected_all_output_response_keys(["01FF", "02FF", "2FFF"])

        self.assertEqual(keys, {(0x01, 1), (0x01, 2), (0x02, 1), (0x02, 2)})

    def test_direct_output_status_polling_merges_power_mute_source_and_scaled_volume(self):
        amp = FakeMirageAmplifier(
            output_count=2,
            responses={
                "01FF": "010101\n010200",
                "02FF": "020101\n020200",
                "03FF": "030102\n030204",
                "04FF": "040150\n040240",
            },
        )

        outputs = amp.list_outputs()

        self.assertEqual(amp.sent, ["01FF", "02FF", "03FF", "04FF"])
        self.assertEqual(len(outputs), 2)
        self.assertTrue(outputs[0].is_on)
        self.assertFalse(outputs[0].muted)
        self.assertEqual(outputs[0].source_id, "7")
        self.assertEqual(outputs[0].source_name, "S7")
        self.assertEqual(outputs[0].volume, 50)
        self.assertFalse(outputs[1].is_on)
        self.assertTrue(outputs[1].muted)
        self.assertEqual(outputs[1].source_id, "8")
        self.assertEqual(outputs[1].source_name, "S8")
        self.assertEqual(outputs[1].volume, 40)

    def test_direct_output_status_polling_can_be_disabled(self):
        amp = FakeMirageAmplifier(output_count=2)

        outputs = amp.list_outputs(include_status=False)

        self.assertEqual(amp.sent, [])
        self.assertEqual([output.volume for output in outputs], [None, None])


if __name__ == "__main__":
    unittest.main()
