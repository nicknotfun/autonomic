from __future__ import annotations

import unittest

from autonomic import MirageAmplifier, ProtocolError, decode_output_address, encode_output_address


class FakeMirageAmplifier(MirageAmplifier):
    def __init__(self, **kwargs):
        super().__init__("127.0.0.1", **kwargs)
        self.sent: list[str] = []

    def send_ascii(self, command: str) -> str:
        self.sent.append(command)
        return "OK"


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
        amp.set_output_volume(10, 0x40)
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
                "040A40",
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
            amp.set_output_volume(1, 0xA1)
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


if __name__ == "__main__":
    unittest.main()
