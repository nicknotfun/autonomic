# Tests for common command formatting, event parsing, and XML browse handling.
from __future__ import annotations

import unittest

from autonomic import MirageAudioSystem, format_assignment, format_command, parse_event, parse_xml_list
from autonomic.exceptions import CommandError
from autonomic.protocol import frame_command, is_xml_response, parse_legacy_list
from helpers import ScriptedConnection


class ProtocolTests(unittest.TestCase):
    def test_formats_commands_and_assignments(self):
        self.assertEqual(format_command("SetSource", "Living Room Input", False, "Zone_1"), 'SetSource "Living Room Input" false Zone_1')
        self.assertEqual(format_assignment("Artist", "Peter Frampton"), 'Artist="Peter Frampton"')
        self.assertEqual(format_command("SetMusicFilter", format_assignment("Artist", "Peter Frampton")), 'SetMusicFilter Artist="Peter Frampton"')
        self.assertEqual(frame_command("GetStatus"), b"GetStatus\r\n")

    def test_parses_events(self):
        event = parse_event("MRAD.StateChanged Zone_1 Volume=24")
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.namespace, "MRAD")
        self.assertEqual(event.reason, "StateChanged")
        self.assertEqual(event.source, "Zone_1")
        self.assertEqual(event.name, "Volume")
        self.assertEqual(event.value, "24")

    def test_parses_xml_browse_list(self):
        response = parse_xml_list(
            '<Zones total="1" start="1" more="false">'
            '<Zone guid="00010000-a259-4cda-a715-94915436337e" name="Kitchen" id="Zone_1" isOn="True" />'
            "</Zones>"
        )
        self.assertEqual(response.kind, "Zones")
        self.assertEqual(response.total, 1)
        self.assertEqual(response.more, False)
        self.assertEqual(response.items[0].guid, "00010000-a259-4cda-a715-94915436337e")
        self.assertEqual(response.items[0].name, "Kitchen")
        self.assertEqual(response.items[0].get_bool("isOn"), True)

    def test_xml_detection_ignores_help_syntax_placeholders(self):
        self.assertTrue(is_xml_response('<Zones total="0" start="1" more="false" />'))
        self.assertFalse(is_xml_response("<Off,On,Toggle>"))

    def test_parses_legacy_list(self):
        response = parse_legacy_list(
            [
                "BeginAlbums Total=2",
                'Album {abc} "Kind of Blue"',
                'Album {def} "Blue Train"',
                "EndAlbums NoMore",
            ]
        )
        self.assertEqual(response.kind, "Albums")
        self.assertEqual(response.total, 2)
        self.assertEqual(response.terminator, "NoMore")
        self.assertEqual(response.items[1].name, "Blue Train")

    def test_client_accepts_padded_responses_and_mixed_case_errors(self):
        conn = ScriptedConnection({"Ping": ["Pong   "], "Bad": ["eRrOr: unknown   "]})
        client = MirageAudioSystem("mrad.local", connection=conn)

        self.assertEqual(client.command("Ping").first_line, "Pong")
        with self.assertRaises(CommandError):
            client.command("Bad")

    def test_client_skips_padded_mrad_banner_lines(self):
        conn = ScriptedConnection(
            {
                "MRAD.BrowseAllZones": [
                    "   ",
                    "Autonomic Controls MRAD Bridge version 8.3.20260518.1 Release.   ",
                    "",
                    "More info found on the Web http://www.autonomic-controls.com   ",
                    "Type '?' for help or 'help <command>' for help on <command>.   ",
                    "Server=ACE14F006012   ",
                    '<Zones total="1" start="1" more="false"><Zone guid="zg1" name="Office" /></Zones>   ',
                ]
            }
        )
        client = MirageAudioSystem("mrad.local", connection=conn)

        response = client.command("MRAD.BrowseAllZones")
        self.assertIsNotNone(response.payload)
        assert response.payload is not None
        self.assertEqual(response.payload.items[0].name, "Office")

    def test_client_can_collect_banner_lines_when_requested(self):
        conn = ScriptedConnection(
            {
                "Banner": [
                    "Autonomic Controls MRAD Bridge version 8.3.20260518.1 Release.",
                    "More info found on the Web http://www.autonomic-controls.com",
                    "Type '?' for help or 'help <command>' for help on <command>.",
                    "Server=ACE14F006012",
                ]
            }
        )
        client = MirageAudioSystem("mrad.local", connection=conn)

        response = client.command("Banner", collect_until_idle=True, include_banners=True)

        self.assertEqual(len(response.lines), 4)
        self.assertEqual(response.lines[0], "Autonomic Controls MRAD Bridge version 8.3.20260518.1 Release.")
        self.assertEqual(response.lines[-1], "Server=ACE14F006012")

    def test_client_drains_browse_ok_tail_before_next_command(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones 1 3": [
                    '<Zones total="1" start="1" more="false"><Zone guid="zg1" name="Kitchen" /></Zones>',
                    "Zones Ok",
                ],
                "BrowseAllSources 1 2": [
                    '<Sources total="0" start="1" more="false" />',
                    "Sources Ok",
                ],
            }
        )
        client = MirageAudioSystem("mrad.local", connection=conn)

        zones = client.browse_all_zones(1, 3)
        sources = client.browse_all_sources(1, 2)

        self.assertEqual(zones.kind, "Zones")
        self.assertEqual(sources.kind, "Sources")
        self.assertEqual(conn.sent, ["BrowseAllZones 1 3", "BrowseAllSources 1 2"])

    def test_client_collects_plain_text_until_idle_when_requested(self):
        single_conn = ScriptedConnection({"help": ["Commands:", "BrowseAllZones - List all zones"]})
        single_client = MirageAudioSystem("mrad.local", connection=single_conn)

        self.assertEqual(single_client.command("help").lines, ["Commands:"])

        collect_conn = ScriptedConnection(
            {
                "help": [
                    "Commands:",
                    "BrowseAllZones       - Returns a list of all zones",
                    "SetZoneGroup         - Grouping and ungrouping zones.",
                    "<zoneNameOrIdOrGuidOrGroupGuid> <commaDelimGuidsSelected> <optionalTargetSource>",
                ]
            }
        )
        collect_client = MirageAudioSystem("mrad.local", connection=collect_conn)

        response = collect_client.command("help", collect_until_idle=True)

        self.assertEqual(
            response.lines,
            [
                "Commands:",
                "BrowseAllZones       - Returns a list of all zones",
                "SetZoneGroup         - Grouping and ungrouping zones.",
                "<zoneNameOrIdOrGuidOrGroupGuid> <commaDelimGuidsSelected> <optionalTargetSource>",
            ],
        )


if __name__ == "__main__":
    unittest.main()
