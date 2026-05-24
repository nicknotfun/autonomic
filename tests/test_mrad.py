from __future__ import annotations

import unittest

from autonomic import MirageAudioSystem, MirageMediaServer

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
        self.assertEqual(conn.sent[-5:], [
            "SetZone 00010000-a259-4cda-a715-94915436337e",
            "SetSource 00000007-a259-4cda-a715-94915436337e",
            "Volume 24",
            "VolumeUp Zone_1",
            "Mute toggle Zone_1",
        ])

    def test_output_aliases_assignment_and_enable_controls(self):
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
        mas.assign_source_to_output(sources.items[0].guid or "sg7", outputs.items[0].guid or "zg1")
        mas.assign_output_sources({"zg1": "sg7", "zg2": "sg8"})
        mas.set_output_volume("zg1", 24)
        mas.set_output_mute("zg1", False)
        mas.enable_output("zg1")
        mas.disable_output("zg2")

        self.assertEqual(outputs.items[0].name, "Office")
        self.assertEqual(sources.items[1].name, "Aux")
        self.assertGreaterEqual(conn.sent.count("SetSource sg7 false zg1"), 2)
        self.assertIn("SetSource sg8 false zg2", conn.sent)
        self.assertIn("Volume 24 zg1", conn.sent)
        self.assertIn("Mute false zg1", conn.sent)
        self.assertIn("Power On zg1", conn.sent)
        self.assertIn("Power Off zg2", conn.sent)

    def test_batch_output_helpers_use_all_browsed_outputs(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="2" start="1" more="false">'
                    '<Zone guid="zg1" name="Office" id="Zone_1" />'
                    '<Zone guid="zg2" name="Kitchen" id="Zone_2" />'
                    "</Zones>"
                ],
            }
        )
        mas = MirageAudioSystem("mms.local", connection=conn)

        mas.set_all_output_volume(18)
        mas.mute_all_outputs(True)
        mas.enable_all_outputs()
        mas.assign_source_to_all_outputs("sg7")
        mas.disable_all_outputs()
        mas.party_mode("Toggle")

        self.assertIn("Volume 18 zg1", conn.sent)
        self.assertIn("Volume 18 zg2", conn.sent)
        self.assertIn("MuteAll On", conn.sent)
        self.assertIn("Power On zg1", conn.sent)
        self.assertIn("Power On zg2", conn.sent)
        self.assertIn("SetSource sg7 false zg1", conn.sent)
        self.assertIn("SetSource sg7 false zg2", conn.sent)
        self.assertIn("AllOff", conn.sent)
        self.assertIn("PartyMode Toggle", conn.sent)

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

    def test_set_zone_group(self):
        conn = ScriptedConnection()
        mas = MirageAudioSystem("mms.local", connection=conn)

        mas.set_zone_group("zg1", ["zg1", "zg2"], "sg7")
        mas.set_zone_group("gg1", ["zg1"])

        self.assertEqual(conn.sent[0], "SetZoneGroup zg1 zg1,zg2 sg7")
        self.assertEqual(conn.sent[1], "SetZoneGroup gg1 zg1")

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
