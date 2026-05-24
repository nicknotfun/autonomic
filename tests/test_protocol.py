from __future__ import annotations

import unittest

from autonomic import MirageMediaServer, album_art_url, format_assignment, format_command, parse_event, parse_xml_list
from autonomic.exceptions import CommandError
from autonomic.protocol import frame_command, parse_legacy_list
from helpers import ScriptedConnection


class ProtocolTests(unittest.TestCase):
    def test_formats_commands_and_assignments(self):
        self.assertEqual(format_command("PlayAlbum", "Yellow Brick Road", True), 'PlayAlbum "Yellow Brick Road" true')
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

    def test_album_art_url(self):
        url = album_art_url("http://192.168.1.20:5005", guid="{abc}", width=300, height=300, constrain=True, fmt="jpg")
        self.assertEqual(url, "http://192.168.1.20:5005/GetArt?guid=%7Babc%7D&w=300&h=300&c=1&fmt=jpg")

    def test_client_accepts_padded_responses_and_mixed_case_errors(self):
        conn = ScriptedConnection({"Ping": ["Pong   "], "Bad": ["eRrOr: unknown   "]})
        client = MirageMediaServer("mms.local", connection=conn)

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
        client = MirageMediaServer("mms.local", connection=conn)

        response = client.command("MRAD.BrowseAllZones")
        self.assertIsNotNone(response.payload)
        assert response.payload is not None
        self.assertEqual(response.payload.items[0].name, "Office")


if __name__ == "__main__":
    unittest.main()
