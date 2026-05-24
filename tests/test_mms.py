from __future__ import annotations

import unittest

from autonomic import MirageMediaServer

from helpers import ScriptedConnection


class MirageMediaServerTests(unittest.TestCase):
    def test_initialize_and_transport_commands(self):
        conn = ScriptedConnection()
        mms = MirageMediaServer("mms.local", connection=conn)

        mms.initialize(
            instance="Player_B",
            client_type="UnitTest",
            client_version="1.2.3.4",
            host_hint="mms.example",
            options={"supports_playnow": True, "supports_inputbox": True},
        )
        mms.play()
        mms.seek(-10)
        mms.set_volume(35)

        self.assertEqual(
            conn.sent[:9],
            [
                "SetClientType UnitTest",
                "SetClientVersion 1.2.3.4",
                "SetHost mms.example",
                "SetXmlMode Lists",
                "SetEncoding 65001",
                "SetInstance Player_B",
                "SetOption supports_playnow=true",
                "SetOption supports_inputbox=true",
                "SubscribeEvents true",
            ],
        )
        self.assertEqual(conn.sent[-3:], ["Play", "Seek -10", "SetVolume 35"])

    def test_initialize_does_not_force_default_instance(self):
        conn = ScriptedConnection()
        mms = MirageMediaServer("mms.local", connection=conn)

        mms.initialize(host_hint="mms.example")

        self.assertNotIn("SetInstance Player_A", conn.sent)

    def test_browse_and_filters(self):
        conn = ScriptedConnection(
            {
                "BrowseAlbums 1 2": [
                    '<Albums total="2" start="1" more="false">'
                    '<Album guid="{a}" name="A Love Supreme" button="3" />'
                    '<Album guid="{b}" name="Kind of Blue" button="3" />'
                    "</Albums>"
                ],
                'SetMusicFilter Artist="Peter Frampton"': ["MusicFilter Artist=Peter Frampton"],
                'SetMusicFilter Search="Diana*"': ["MusicFilter Search=Diana*"],
                "SetRadioFilter Source=fbbcedb1-af64-4c3f-bfe5-000000000010": ['RadioFilter Ok "Pandora"'],
            }
        )
        mms = MirageMediaServer("mms.local", connection=conn)

        albums = mms.browse_albums(1, 2)
        self.assertEqual(albums.total, 2)
        self.assertEqual(albums.items[0].name, "A Love Supreme")

        mms.set_music_filter("Artist", "Peter Frampton")
        mms.set_music_filter(search="Diana*")
        mms.set_radio_filter("Source", "fbbcedb1-af64-4c3f-bfe5-000000000010")

        self.assertIn('SetMusicFilter Artist="Peter Frampton"', conn.sent)
        self.assertIn('SetMusicFilter Search="Diana*"', conn.sent)
        self.assertIn("SetRadioFilter Source=fbbcedb1-af64-4c3f-bfe5-000000000010", conn.sent)

    def test_get_status_collects_events_until_idle(self):
        conn = ScriptedConnection(
            {
                "GetStatus": [
                    "ReportState Player_A PlayState=Playing",
                    "ReportState Player_A TrackTime=42",
                    "ReportState Player_A MetaData4=Texas Flood",
                ]
            }
        )
        mms = MirageMediaServer("mms.local", connection=conn)

        snapshot = mms.get_status(idle_timeout=0.001)
        self.assertEqual(snapshot.get("Player_A", "PlayState"), "Playing")
        self.assertEqual(snapshot.get("Player_A", "TrackTime"), "42")
        self.assertEqual(snapshot.get("Player_A", "MetaData4"), "Texas Flood")

    def test_media_playback_wrappers_quote_names(self):
        conn = ScriptedConnection()
        mms = MirageMediaServer("mms.local", connection=conn)

        mms.play_album("Kind of Blue", enqueue=True)
        mms.play_playlist("My Favorites", enqueue=True, start_guid="{track}")
        mms.store_preset("Party Time")
        mms.recall_preset("Party Time")

        self.assertEqual(conn.sent[0], 'PlayAlbum "Kind of Blue" true')
        self.assertEqual(conn.sent[1], 'PlayPlaylist "My Favorites" true {track}')
        self.assertEqual(conn.sent[2], 'StorePreset "Party Time"')
        self.assertEqual(conn.sent[3], 'RecallPreset "Party Time"')

    def test_set_volume_validates_mms_range(self):
        mms = MirageMediaServer("mms.local", connection=ScriptedConnection())
        with self.assertRaises(ValueError):
            mms.set_volume(51)


if __name__ == "__main__":
    unittest.main()
