from __future__ import annotations

import unittest

from autonomic import AutonomicError
from autonomic_ma6 import MA6Client


class CompatibilityTests(unittest.TestCase):
    def test_autonomic_ma6_exports_client(self):
        client = MA6Client("mms.local")
        self.assertEqual(client.host, "mms.local")
        self.assertTrue(hasattr(client, "media"))
        self.assertTrue(hasattr(client, "audio"))
        self.assertTrue(hasattr(client, "assign_source_to_output"))
        self.assertTrue(hasattr(client, "assign_output_sources"))
        self.assertTrue(hasattr(client, "assign_matrix"))
        self.assertTrue(hasattr(client, "set_output_volume"))
        self.assertTrue(hasattr(client, "set_output_mute"))
        self.assertTrue(hasattr(client, "enable_output"))

    def test_ma6_client_supports_direct_amplifier_mode(self):
        client = MA6Client("amp.local", mode="amplifier")
        sent: list[str] = []

        def fake_send(command: str) -> str:
            sent.append(command)
            if command == "2FFF":
                return "AFFF00D40102030405060708"
            return "OK"

        client.amplifier.send_ascii = fake_send  # type: ignore[method-assign]

        self.assertEqual(client.detect_mode(), "amplifier")
        self.assertEqual(client.amplifier.get_device_id(), "00D4")
        self.assertEqual(client.list_outputs().total, 8)
        self.assertEqual(client.list_sources().items[6].attributes["address"], "02")

        client.select_output(1)
        client.select_source(7)
        client.set_output_volume(1, 0x40)
        client.set_output_mute(1, False)
        client.enable_output(1)
        client.assign_matrix({1: 7, 2: 8})

        self.assertEqual(
            sent,
            [
                "2FFF",
                "030102",
                "040140",
                "020101",
                "010100",
                "030102",
                "030204",
            ],
        )

        with self.assertRaises(AutonomicError):
            client.play()


if __name__ == "__main__":
    unittest.main()
