from __future__ import annotations

import unittest

from autonomic import MirageAmplifier, MirageAmplifierDiagnostics, ProtocolError


class FakeMirageAmplifier(MirageAmplifier):
    def __init__(self):
        super().__init__("127.0.0.1")
        self.sent: list[str] = []

    def send_ascii(self, command: str) -> str:
        self.sent.append(command)
        return "OK"


class MirageAmplifierDiagnosticsTests(unittest.TestCase):
    def test_parse_device_id_from_support_response(self):
        self.assertEqual(MirageAmplifierDiagnostics.parse_device_id("AFFF01A80102030405060708"), "01A8")
        with self.assertRaises(ProtocolError):
            MirageAmplifierDiagnostics.parse_device_id("NOPE")

    def test_build_factory_reset_command(self):
        self.assertEqual(MirageAmplifierDiagnostics.build_factory_reset_command("01a8"), "42FF01A80355AA")
        with self.assertRaises(ValueError):
            MirageAmplifierDiagnostics.build_factory_reset_command("XYZ")

    def test_factory_reset_requires_confirmation(self):
        diag = MirageAmplifierDiagnostics("127.0.0.1")
        with self.assertRaises(ValueError):
            diag.factory_reset(device_id="01A8")

    def test_direct_output_control_command_builders(self):
        self.assertEqual(MirageAmplifier.build_data_command("03", "1F", "02"), "031F02")
        self.assertEqual(MirageAmplifier.build_data_command(3, 31, 2), "031F02")
        self.assertEqual(MirageAmplifier.source_data(7), "02")

        amp = FakeMirageAmplifier()
        amp.enable_output(10)
        amp.disable_output(10)
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
                "010A00",
                "010A01",
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


if __name__ == "__main__":
    unittest.main()
