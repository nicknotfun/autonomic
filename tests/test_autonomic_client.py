from __future__ import annotations

import unittest
from unittest.mock import patch

from autonomic import (
    AutonomicClient,
    AutonomicError,
    AutonomicOutput,
    AutonomicOutputGroup,
    AutonomicSource,
    MirageAmplifier,
    MirageAudioSystem,
)

from helpers import ScriptedConnection


class AutonomicClientTests(unittest.TestCase):
    def test_unified_client_exposes_high_level_controls(self):
        client = AutonomicClient("mms.local", auto_initialize=False)
        self.assertEqual(client.host, "mms.local")
        self.assertTrue(hasattr(client, "media"))
        self.assertTrue(hasattr(client, "audio"))
        self.assertTrue(hasattr(client, "amplifier"))
        self.assertTrue(hasattr(client, "assign_source_to_output"))
        self.assertTrue(hasattr(client, "assign_output_sources"))
        self.assertTrue(hasattr(client, "assign_matrix"))
        self.assertTrue(hasattr(client, "set_output_volume"))
        self.assertTrue(hasattr(client, "set_output_mute"))
        self.assertTrue(hasattr(client, "set_output_power"))
        self.assertTrue(hasattr(client, "set_output_is_on"))
        self.assertFalse(hasattr(client, "enable_output"))
        self.assertFalse(hasattr(client, "disable_output"))
        self.assertFalse(hasattr(client, "enable_all_outputs"))
        self.assertFalse(hasattr(client, "disable_all_outputs"))
        self.assertFalse(client._initialized)

    def test_constructor_auto_initializes_by_default(self):
        with patch.object(MirageAmplifier, "get_device_id", return_value="00D4") as get_device_id:
            client = AutonomicClient("amp.local", mode="amplifier")

        self.assertEqual(client.detect_mode(), "amplifier")
        self.assertTrue(client._initialized)
        get_device_id.assert_called_once_with()

    def test_unified_source_listing_only_browses_and_preserves_source_names(self):
        conn = ScriptedConnection(
            {
                "BrowseAllSources": [
                    '<Sources total="2" start="1" more="false">'
                    '<Source guid="sg-alpha" name="Alpha" sId="10107" />'
                    '<Source guid="sg-beta" name="Beta Input" sId="10108" />'
                    "</Sources>"
                ]
            }
        )
        client = AutonomicClient("mms.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mms.local", connection=conn)

        sources = client.list_sources(include_disabled=True)

        self.assertEqual([source.name for source in sources], ["Alpha", "Beta Input"])
        self.assertEqual(conn.sent, ["BrowseAllSources"])
        self.assertFalse(any("SourceName" in command for command in conn.sent))
        self.assertFalse(any("Name" in command and command != "BrowseAllSources" for command in conn.sent))

    def test_unified_client_applies_source_aliases_without_renaming_sources(self):
        conn = ScriptedConnection(
            {
                "BrowseAllSources": [
                    '<Sources total="4" start="1" more="false">'
                    '<Source guid="000027fb-f8a9-f6be-a465-3d0fbee12977" name="COAX2" sId="10107" />'
                    '<Source guid="000027fc-f8a9-f6be-a465-3d0fbee12977" name="Beta" sId="10108" />'
                    '<Source guid="0000008a-f8a9-f6be-a465-3d0fbee12977" name="S10" sId="10" />'
                    '<Source guid="0000008b-f8a9-f6be-a465-3d0fbee12977" name="S11" sId="11" />'
                    "</Sources>"
                ]
            }
        )
        client = AutonomicClient("mms.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mms.local", connection=conn)

        sources = client.list_sources()

        self.assertEqual([source.name for source in sources], ["Alpha", "Beta", "Gamma", "Delta"])
        self.assertEqual(sources[0].attributes["name"], "COAX2")
        self.assertEqual(client.source_by_name("gamma").id, "10")

        client.assign_source_to_output("Alpha", "Zone_1")

        self.assertIn("SetSource 000027fb-f8a9-f6be-a465-3d0fbee12977 false Zone_1", conn.sent)
        self.assertFalse(any("SourceName" in command for command in conn.sent))
        self.assertFalse(any("Name" in command and command != "BrowseAllSources" for command in conn.sent))

    def test_unified_source_aliases_can_be_disabled(self):
        conn = ScriptedConnection(
            {
                "BrowseAllSources": [
                    '<Sources total="1" start="1" more="false">'
                    '<Source guid="000027fb-f8a9-f6be-a465-3d0fbee12977" name="COAX2" sId="10107" />'
                    "</Sources>"
                ]
            }
        )
        client = AutonomicClient("mms.local", mode="mrad", auto_initialize=False, source_aliases=None)
        client.audio = MirageAudioSystem("mms.local", connection=conn)

        self.assertEqual(client.list_sources()[0].name, "COAX2")

    def test_lookup_helpers_find_sources_and_outputs_by_name(self):
        conn = ScriptedConnection(
            {
                "BrowseAllSources": [
                    '<Sources total="2" start="1" more="false">'
                    '<Source guid="sg-alpha" name="Alpha" sId="10107" />'
                    '<Source guid="sg-beta" name="Beta Input" sId="10108" disabled="true" />'
                    "</Sources>"
                ],
                "BrowseAllZones": [
                    '<Zones total="2" start="1" more="false">'
                    '<Zone guid="zg-kitchen" name="Kitchen" id="Zone_1" />'
                    '<Zone guid="zg-patio" name="Patio West" id="Zone_2" disabled="true" />'
                    "</Zones>"
                ],
            }
        )
        client = AutonomicClient("mms.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mms.local", connection=conn)

        self.assertEqual(client.source_by_name(" alpha ").id, "10107")
        self.assertEqual(client.output_by_name("KITCHEN").id, "Zone_1")

        with self.assertRaises(LookupError):
            client.source_by_name("Beta Input")
        with self.assertRaises(LookupError):
            client.output_by_name("Patio West")

        self.assertEqual(client.source_by_name("Beta Input", include_disabled=True).id, "10108")
        self.assertEqual(client.output_by_name("Patio West", include_disabled=True).id, "Zone_2")

    def test_all_outputs_returns_fanout_proxy(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="3" start="1" more="false">'
                    '<Zone guid="zg-office" name="Office" id="Zone_1" />'
                    '<Zone guid="zg-kitchen" name="Kitchen" id="Zone_2" />'
                    '<Zone guid="zg-disabled" name="Disabled" id="Zone_3" disabled="true" />'
                    "</Zones>"
                ],
            }
        )
        client = AutonomicClient("mms.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mms.local", connection=conn)

        outputs = client.all_outputs()

        self.assertIsInstance(outputs, AutonomicOutputGroup)
        self.assertEqual(len(outputs), 2)
        self.assertEqual([output.name for output in outputs], ["Office", "Kitchen"])
        self.assertIsInstance(outputs[0], AutonomicOutput)

        outputs.assign("sg-alpha")
        outputs.set_volume(50)
        outputs.unmute()

        self.assertIn("SetSource sg-alpha false Zone_1", conn.sent)
        self.assertIn("SetSource sg-alpha false Zone_2", conn.sent)
        self.assertNotIn("SetSource sg-alpha false Zone_3", conn.sent)
        self.assertIn("Volume 50 Zone_1", conn.sent)
        self.assertIn("Volume 50 Zone_2", conn.sent)
        self.assertNotIn("Volume 50 Zone_3", conn.sent)
        self.assertIn("Mute false Zone_1", conn.sent)
        self.assertIn("Mute false Zone_2", conn.sent)
        self.assertNotIn("Mute false Zone_3", conn.sent)

    def test_unified_client_sets_mrad_output_power_state_without_enablement_names(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="1" start="1" more="false">'
                    '<Zone guid="zg-kitchen" name="Kitchen" id="Zone_1" />'
                    "</Zones>"
                ],
            }
        )
        client = AutonomicClient("mms.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mms.local", connection=conn)
        output = client.output_by_name("Kitchen")

        client.set_output_is_on(output, True)
        output.set_power(False)

        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power Off Zone_1", conn.sent)
        self.assertFalse(hasattr(client, "enable_output"))
        self.assertFalse(hasattr(output, "enable"))

    def test_unified_client_supports_direct_amplifier_mode_with_objects(self):
        client = AutonomicClient(
            "amp.local",
            mode="amplifier",
            auto_initialize=False,
            amplifier_source_count=12,
            amplifier_source_base=0,
        )
        sent: list[str] = []

        def fake_send_commands(commands, *, timeout=None) -> str:
            normalized = [command.upper().rstrip("\r\n") for command in commands]
            sent.extend(normalized)
            if normalized == ["2FFF"]:
                return "AFFF00D40102030405060708"
            return ""

        def fake_send(command: str) -> str:
            return fake_send_commands([command]).strip()

        def fake_status(commands, *, timeout=None) -> str:
            normalized = [command.upper().rstrip("\r\n") for command in commands]
            sent.extend(normalized)
            if normalized == ["2FFF"]:
                return "AFFF00D40102030405060708"
            responses = {
                "01FF": "",
                "02FF": "",
                "03FF": "",
                "04FF": "",
            }
            return "\n".join(responses.get(command, "") for command in normalized)

        def fake_status_send(command: str) -> str:
            return fake_status([command]).strip()

        client.amplifier.send_commands = fake_status  # type: ignore[method-assign]
        client.amplifier.send_ascii = fake_status_send  # type: ignore[method-assign]

        self.assertEqual(client.detect_mode(), "amplifier")
        self.assertEqual(client.amplifier.get_device_id(), "00D4")
        outputs = client.list_outputs()
        sources = client.list_sources()
        self.assertEqual(len(outputs), 8)
        self.assertEqual(len(sources), 12)
        self.assertIsInstance(outputs[0], AutonomicOutput)
        self.assertIsInstance(sources[6], AutonomicSource)
        self.assertEqual([output.name for output in outputs], [
            "Kitchen",
            "Dining",
            "Living",
            "Master",
            "Bathroom",
            "Foyer",
            "Sitting",
            "Passthrough",
        ])
        self.assertEqual(sources[6].name, "Alpha")
        self.assertEqual(sources[7].name, "Beta")
        self.assertEqual(sources[6].attributes["address"], "02")
        self.assertEqual(sources[11].attributes["address"], "0B")
        self.assertEqual(client.source_by_name("Alpha").id, "6")
        self.assertEqual(client.output_by_name("Kitchen").id, "1")

        sent.clear()
        client.amplifier.send_commands = fake_send_commands  # type: ignore[method-assign]
        client.amplifier.send_ascii = fake_send  # type: ignore[method-assign]

        client.select_output(outputs[0])
        client.select_source(sources[6])
        outputs[0].set_power(True)
        outputs[0].set_volume(50)
        outputs[0].unmute()
        outputs[0].mute()
        sources[7].assign_to(outputs[1])
        outputs[1].assign(sources[7])
        client.assign_source_to_outputs(sources[6], outputs[:2])
        client.assign_matrix({outputs[0]: sources[6], outputs[1]: sources[7]})
        client.assign_source_to_output("Alpha", "Kitchen")
        client.set_output_volume("Patio West", 50)
        client.set_output_mute("Patio West", False)
        client.set_output_power("Patio West", True)

        self.assertEqual(
            sent,
            [
                "030102",
                "010101",
                "040150",
                "020101",
                "020100",
                "030204",
                "030204",
                "030102",
                "030202",
                "030102",
                "030204",
                "030102",
                "040A50",
                "020A01",
                "010A01",
            ],
        )

        with self.assertRaises(AutonomicError):
            client.play()

    def test_unified_client_reads_direct_amplifier_output_status(self):
        client = AutonomicClient("amp.local", mode="amplifier", auto_initialize=False, amplifier_output_count=2)
        sent: list[str] = []

        def fake_send_commands(commands, *, timeout=None) -> str:
            normalized = [command.upper().rstrip("\r\n") for command in commands]
            sent.extend(normalized)
            if normalized == ["2FFF"]:
                return "AFFF00D40102030405060708"
            responses = {
                "01FF": "010101\n010200",
                "02FF": "020101\n020200",
                "03FF": "030102\n0302A2",
                "04FF": "04015064\n040240",
            }
            return "\n".join(responses.get(command, "") for command in normalized)

        def fake_send(command: str) -> str:
            return fake_send_commands([command]).strip()

        client.amplifier.send_commands = fake_send_commands  # type: ignore[method-assign]
        client.amplifier.send_ascii = fake_send  # type: ignore[method-assign]

        outputs = client.list_outputs()

        self.assertEqual(sent, ["01FF", "02FF", "03FF", "04FF"])
        self.assertEqual(
            [(output.is_on, output.muted, output.volume, output.source_name) for output in outputs],
            [
                (True, False, 50, "Alpha"),
                (False, True, 40, "Remote 3"),
            ],
        )
        self.assertEqual([output.name for output in outputs], ["Kitchen", "Dining"])


if __name__ == "__main__":
    unittest.main()
