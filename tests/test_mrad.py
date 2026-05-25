from __future__ import annotations

import unittest

from autonomic import AutonomicOutput, AutonomicSource, MirageAudioSystem, MirageMediaServer

from helpers import ScriptedConnection


class MirageAudioSystemTests(unittest.TestCase):
    def test_initialize_and_zone_source_controls(self):
        conn = ScriptedConnection()
        mas = MirageAudioSystem("mms.local", connection=conn)

        mas.initialize(client_type="UnitTest", host_hint="mms.example", subscribe=["Power", "Volume"])
        mas.set_zone("00010000-a259-4cda-a715-94915436337e")
        mas.set_source("00000007-a259-4cda-a715-94915436337e")
        mas.volume(24)
        mas.volume_up("Zone_1")
        mas.mute("toggle", "Zone_1")

        self.assertEqual(
            conn.sent[:6],
            [
                "*",
                "SetClientType UnitTest",
                "SetEncoding 65001",
                "SetXmlMode Lists",
                "SetHost mms.example",
                "SubscribeEvents Power,Volume",
            ],
        )
        self.assertIn("SetZone 00010000-a259-4cda-a715-94915436337e", conn.sent)
        self.assertIn("SetSource 00000007-a259-4cda-a715-94915436337e", conn.sent)
        self.assertIn("Volume 24", conn.sent)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("VolumeUp Zone_1", conn.sent)
        self.assertIn("Mute toggle Zone_1", conn.sent)

    def test_output_aliases_assignment_mute_and_volume_controls(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="2" start="1" more="false">'
                    '<Zone guid="zg1" name="Office" id="Zone_1" />'
                    '<Zone guid="zg2" name="Kitchen" id="Zone_2" />'
                    "</Zones>"
                ],
                "BrowseAllSources": [
                    '<Sources total="2" start="1" more="false">'
                    '<Source guid="sg7" name="Main" sId="7" />'
                    '<Source guid="sg8" name="Aux" sId="8" />'
                    "</Sources>"
                ],
            }
        )
        mas = MirageAudioSystem("mms.local", connection=conn)

        outputs = mas.list_outputs()
        sources = mas.list_sources()
        mas.assign_source_to_output(sources[0], outputs[0])
        mas.assign_output_sources({"zg1": "sg7", "zg2": "sg8"})
        outputs[0].set_volume(24)
        outputs[0].unmute()
        sources[1].assign_to(outputs[1])
        outputs[1].assign(sources[1])

        self.assertIsInstance(outputs[0], AutonomicOutput)
        self.assertIsInstance(sources[0], AutonomicSource)
        self.assertEqual(outputs[0].name, "Office")
        self.assertEqual(sources[1].name, "Aux")
        self.assertIn("SetSource sg7 false zg1", conn.sent)
        self.assertIn("SetSource sg7 false Zone_1", conn.sent)
        self.assertIn("SetSource sg8 false zg2", conn.sent)
        self.assertGreaterEqual(conn.sent.count("SetSource sg8 false Zone_2"), 2)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power On Zone_2", conn.sent)
        self.assertIn("Volume 24 Zone_1", conn.sent)
        self.assertIn("Mute false Zone_1", conn.sent)
        self.assertNotIn("AllOff", conn.sent)

    def test_output_power_state_is_runtime_zone_control(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="1" start="1" more="false">'
                    '<Zone guid="zg1" name="Office" id="Zone_1" isOn="False" />'
                    "</Zones>"
                ],
            }
        )
        mas = MirageAudioSystem("mms.local", connection=conn)
        output = mas.list_outputs()[0]

        mas.set_output_power(output, True)
        output.set_is_on(False)

        self.assertEqual(output.is_on, False)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power Off Zone_1", conn.sent)
        self.assertFalse(hasattr(mas, "enable_output"))
        self.assertFalse(hasattr(output, "enable"))

    def test_output_control_retries_after_zone_off_error(self):
        class ZoneOffOnceConnection(ScriptedConnection):
            def send_line(self, command: str) -> None:
                self.sent.append(command)
                self.calls[command] += 1
                if command == "Volume 18 Zone_1" and self.calls[command] == 1:
                    self._pending.append('Volume Error "Zone 1 is off."')
                    return
                self._pending.append("OK")

        conn = ZoneOffOnceConnection()
        mas = MirageAudioSystem("mms.local", connection=conn)

        response = mas.set_output_volume("Zone_1", 18)

        self.assertEqual(response.first_line, "OK")
        self.assertEqual(conn.sent, ["Power On Zone_1", "Volume 18 Zone_1", "Power On Zone_1", "Volume 18 Zone_1"])

    def test_batch_output_helpers_use_all_browsed_outputs(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="3" start="1" more="false">'
                    '<Zone guid="zg1" name="Office" id="Zone_1" />'
                    '<Zone guid="zg2" name="Kitchen" id="Zone_2" />'
                    '<Zone guid="zg3" name="Disabled" id="Zone_3" disabled="true" />'
                    "</Zones>"
                ],
            }
        )
        mas = MirageAudioSystem("mms.local", connection=conn)

        mas.set_all_output_volume(18)
        mas.mute_all_outputs(True)
        mas.assign_source_to_all_outputs("sg7")

        self.assertIn("Volume 18 Zone_1", conn.sent)
        self.assertIn("Volume 18 Zone_2", conn.sent)
        self.assertNotIn("Volume 18 Zone_3", conn.sent)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power On Zone_2", conn.sent)
        self.assertNotIn("Power On Zone_3", conn.sent)
        self.assertIn("MuteAll On", conn.sent)
        self.assertIn("SetSource sg7 false Zone_1", conn.sent)
        self.assertIn("SetSource sg7 false Zone_2", conn.sent)
        self.assertNotIn("SetSource sg7 false Zone_3", conn.sent)
        self.assertNotIn("AllOff", conn.sent)

    def test_list_helpers_omit_disabled_outputs_and_sources_by_default(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="4" start="1" more="false">'
                    '<Zone guid="zg1" name="Office" id="Zone_1" />'
                    '<Zone guid="zg2" name="Kitchen" id="Zone_2" disabled="True" />'
                    '<Zone guid="zg3" name="Patio" id="Zone_3" enabled="false" />'
                    '<Zone guid="zg4" name="Pool" id="Zone_4" available="0" />'
                    "</Zones>"
                ],
                "BrowseAllSources": [
                    '<Sources total="4" start="1" more="false">'
                    '<Source guid="sg7" name="Main" sId="7" />'
                    '<Source guid="sg8" name="Aux" sId="8" isDisabled="true" />'
                    '<Source guid="sg9" name="Hidden" sId="9" isHidden="yes" />'
                    '<Source guid="sg10" name="Unavailable" sId="10" isAvailable="False" />'
                    "</Sources>"
                ],
            }
        )
        mas = MirageAudioSystem("mms.local", connection=conn)

        outputs = mas.list_outputs()
        sources = mas.list_sources()
        all_outputs = mas.list_outputs(include_disabled=True)
        all_sources = mas.list_sources(include_disabled=True)

        self.assertEqual([output.name for output in outputs], ["Office"])
        self.assertEqual([source.name for source in sources], ["Main"])
        self.assertEqual([output.disabled for output in all_outputs], [None, True, True, True])
        self.assertEqual([source.disabled for source in all_sources], [None, True, True, True])

    def test_source_listing_only_browses_and_preserves_source_names(self):
        conn = ScriptedConnection(
            {
                "BrowseAllSources": [
                    '<Sources total="3" start="1" more="false">'
                    '<Source guid="sg-alpha" name="Alpha" sId="10107" />'
                    '<Source guid="sg-beta" name="Beta Input" sId="10108" />'
                    '<Source guid="sg-disabled" name="Disabled Source" sId="10109" disabled="true" />'
                    "</Sources>"
                ]
            }
        )
        mas = MirageAudioSystem("mms.local", connection=conn)

        sources = mas.list_sources(include_disabled=True)

        self.assertEqual([source.name for source in sources], ["Alpha", "Beta Input", "Disabled Source"])
        self.assertEqual(conn.sent, ["BrowseAllSources"])
        self.assertFalse(any("SourceName" in command for command in conn.sent))
        self.assertFalse(any("Name" in command and command != "BrowseAllSources" for command in conn.sent))

    def test_browse_zones_sources_and_groups(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="1" start="1" more="false">'
                    '<Zone guid="zg1" name="Office" id="Zone_1" isOn="True" sourceId="7" />'
                    "</Zones>"
                ],
                "BrowseAllSources": [
                    '<Sources total="1" start="1" more="false">'
                    '<Source guid="sg7" name="Main" smart="1" sId="7" />'
                    "</Sources>"
                ],
                "BrowseZoneGroups": [
                    '<ZoneGroups total="1" start="1" more="false" srceAvail="1" srceId="sg7">'
                    '<ZoneGroup guid="gg1" name="ZG_1"><vol><zone eventId="Zone_1" guid="zg1" name="Office" /></vol></ZoneGroup>'
                    "</ZoneGroups>"
                ],
            }
        )
        mas = MirageAudioSystem("mms.local", connection=conn)

        zones = mas.browse_all_zones()
        sources = mas.browse_all_sources()
        groups = mas.browse_zone_groups()

        self.assertEqual(zones.items[0].name, "Office")
        self.assertEqual(sources.items[0].guid, "sg7")
        self.assertEqual(groups.attributes["srceId"], "sg7")
        self.assertEqual(groups.items[0].children["vol"][0]["name"], "Office")

    def test_single_socket_prefixes_mrad_commands_on_mms_connection(self):
        mms_conn = ScriptedConnection(
            {
                "MRAD.BrowseAllZones": [
                    '<Zones total="1" start="1" more="false"><Zone guid="zg1" name="Office" /></Zones>'
                ]
            }
        )
        mms = MirageMediaServer("mms.local", connection=mms_conn)
        mas = MirageAudioSystem("mms.local", mms_client=mms, single_socket=True)

        zones = mas.browse_all_zones()

        self.assertEqual(zones.items[0].name, "Office")
        self.assertEqual(mms_conn.sent, ["MRAD.BrowseAllZones"])


if __name__ == "__main__":
    unittest.main()
