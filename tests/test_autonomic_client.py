# Tests for the unified high-level client across MRAD and direct amp backends.
from __future__ import annotations

import unittest
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from autonomic import (
    AutonomicClient,
    AutonomicError,
    AutonomicOutput,
    AutonomicOutputGroup,
    AutonomicSource,
    DEFAULT_HOST,
    AmplifierDeviceInfo,
    AmplifierLayout,
    AmplifierResetDefaults,
    HARDWARE_MODELS,
    MirageAmplifier,
    MirageAudioSystem,
    load_config,
    model_name_for_byte,
)

from helpers import ScriptedConnection


class AutonomicClientTests(unittest.TestCase):
    def test_unified_client_exposes_high_level_controls(self):
        client = AutonomicClient("mrad.local", auto_initialize=False)
        self.assertEqual(client.host, "mrad.local")
        self.assertTrue(hasattr(client, "audio"))
        self.assertTrue(hasattr(client, "amplifier"))
        self.assertTrue(hasattr(client, "assign_source_to_output"))
        self.assertTrue(hasattr(client, "assign_output_sources"))
        self.assertTrue(hasattr(client, "assign_matrix"))
        self.assertTrue(hasattr(client, "set_output_volume"))
        self.assertTrue(hasattr(client, "read_output_volume"))
        self.assertTrue(hasattr(client, "read_output_max_volume"))
        self.assertTrue(hasattr(client, "set_output_mute"))
        self.assertTrue(hasattr(client, "set_output_power"))
        self.assertTrue(hasattr(client, "set_output_is_on"))
        self.assertTrue(hasattr(client, "set_output_max_volume"))
        self.assertTrue(hasattr(client, "output_max_volume_up"))
        self.assertTrue(hasattr(client, "output_max_volume_down"))
        self.assertTrue(hasattr(client, "set_output_bass"))
        self.assertTrue(hasattr(client, "output_bass_up"))
        self.assertTrue(hasattr(client, "set_output_treble"))
        self.assertTrue(hasattr(client, "output_treble_down"))
        self.assertTrue(hasattr(client, "set_output_balance"))
        self.assertTrue(hasattr(client, "output_balance_left"))
        self.assertTrue(hasattr(client, "output_gain_up"))
        self.assertTrue(hasattr(client, "output_delay_up"))
        self.assertTrue(hasattr(client, "set_output_loudness"))
        self.assertTrue(hasattr(client, "set_output_mono_downmix"))
        self.assertTrue(hasattr(client, "set_output_power_on_volume"))
        self.assertTrue(hasattr(client, "enter_command_mode"))
        self.assertTrue(hasattr(client, "toggle_passthrough_mode"))
        self.assertTrue(hasattr(client, "set_source_name"))
        self.assertTrue(hasattr(client, "set_source_icon"))
        self.assertTrue(hasattr(client, "set_all_output_power"))
        self.assertTrue(hasattr(client, "all_off"))
        self.assertTrue(hasattr(client, "reset_all_to_defaults"))
        self.assertTrue(hasattr(client, "set_input_gain"))
        self.assertTrue(hasattr(client, "set_source_metadata"))
        self.assertTrue(hasattr(client, "define_eaudiocast_source"))
        self.assertTrue(hasattr(client, "delete_eaudiocast_source"))
        self.assertFalse(hasattr(client, "enable_output"))
        self.assertFalse(hasattr(client, "disable_output"))
        self.assertFalse(hasattr(client, "enable_all_outputs"))
        self.assertFalse(hasattr(client, "disable_all_outputs"))
        self.assertFalse(hasattr(client, "media"))
        self.assertFalse(hasattr(client, "play"))
        self.assertFalse(client._initialized)

    def test_unified_client_defaults_to_primary_direct_amp_host(self):
        client = AutonomicClient(auto_initialize=False)

        self.assertEqual(DEFAULT_HOST, "10.1.0.200")
        self.assertEqual(client.host, "10.1.0.200")
        self.assertEqual(client.audio.host, "10.1.0.200")
        self.assertEqual(client.amplifier.host, "10.1.0.200")
        self.assertEqual(client.amplifier.output_count, 8)
        self.assertEqual(client.amplifier.source_count, 8)
        self.assertEqual(client.amplifier.source_base, 0)

        ma6_client = AutonomicClient("10.1.0.201", mode="amplifier", auto_initialize=False)

        self.assertEqual(ma6_client.amplifier.output_count, 8)
        self.assertEqual(ma6_client.amplifier.native_output_start, 9)
        self.assertEqual(ma6_client.amplifier.source_count, 12)
        self.assertEqual(ma6_client.amplifier.source_base, 0)
        endpoint_by_host = {endpoint.host: endpoint for endpoint in ma6_client._direct_endpoints()}
        self.assertEqual(endpoint_by_host["10.1.0.201"].output_start, 9)
        self.assertEqual(endpoint_by_host["10.1.0.201"].source_count, 12)

    def test_auto_detection_prefers_direct_amplifier_when_multiple_ports_are_available(self):
        client = AutonomicClient("hybrid.local", auto_initialize=False)

        with patch("autonomic.client._can_connect", return_value=True) as can_connect:
            self.assertEqual(client.detect_mode(), "amplifier")

        can_connect.assert_called_once_with("hybrid.local", client.amplifier.port, client.amplifier.timeout)

    def test_constructor_auto_initializes_by_default(self):
        with (
            patch.object(
                MirageAmplifier,
                "infer_layout",
                return_value=AmplifierLayout(output_count=8, source_count=8, source_base=0, device_id="00D4"),
            ) as infer_layout,
            patch.object(MirageAmplifier, "get_device_id", return_value="00D4") as get_device_id,
        ):
            client = AutonomicClient("amp.local", mode="amplifier")

        self.assertEqual(client.detect_mode(), "amplifier")
        self.assertTrue(client._initialized)
        self.assertEqual(client._amplifier_device_id, "00D4")
        infer_layout.assert_called_once_with()
        get_device_id.assert_not_called()

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
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

        sources = client.list_sources(include_disabled=True)

        self.assertEqual([source.name for source in sources], ["Alpha", "Beta Input"])
        self.assertEqual(conn.sent, ["BrowseAllSources"])
        self.assertFalse(any("SourceName" in command for command in conn.sent))
        self.assertFalse(any("Name" in command and command != "BrowseAllSources" for command in conn.sent))

    def test_unified_client_caches_sources_and_updates_mrad_source_writes(self):
        conn = ScriptedConnection(
            {
                "BrowseAllSources": [
                    '<Sources total="2" start="1" more="false">'
                    '<Source guid="sg-alpha" name="Alpha" sId="10107" />'
                    '<Source guid="sg-beta" name="Beta" sId="10108" />'
                    "</Sources>"
                ]
            }
        )
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

        sources = client.list_sources(include_disabled=True)
        sources[0].name = "Caller Mutation"
        self.assertEqual(client.list_sources(include_disabled=True)[0].name, "Alpha")

        client.set_source_name("sg-alpha", "Renamed Alpha")
        client.set_source_icon("sg-alpha", "Disc")
        cached = client.list_sources(include_disabled=True)

        self.assertEqual([source.name for source in cached], ["Renamed Alpha", "Beta"])
        self.assertEqual(cached[0].attributes["icon"], "Disc")
        self.assertEqual(conn.sent.count("BrowseAllSources"), 1)
        self.assertIn('SourceName "Renamed Alpha" sg-alpha', conn.sent)
        self.assertIn("SourceIcon Disc sg-alpha", conn.sent)

    def test_unified_client_applies_explicit_source_aliases_without_renaming_sources(self):
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
        client = AutonomicClient(
            "mrad.local",
            mode="mrad",
            auto_initialize=False,
            source_aliases={
                "000027fb-f8a9-f6be-a465-3d0fbee12977": "Alpha",
                "000027fc-f8a9-f6be-a465-3d0fbee12977": "Beta",
                "0000008a-f8a9-f6be-a465-3d0fbee12977": "Gamma",
                "0000008b-f8a9-f6be-a465-3d0fbee12977": "Delta",
            },
        )
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

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
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False, source_aliases=None)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

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
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

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
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

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
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)
        output = client.output_by_name("Kitchen")

        client.set_output_is_on(output, True)
        output.set_power(False)

        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power Off Zone_1", conn.sent)
        self.assertFalse(hasattr(client, "enable_output"))
        self.assertFalse(hasattr(output, "enable"))

    def test_unified_client_supports_mrad_output_audio_controls(self):
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
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

        client.set_output_max_volume("Zone_1", 90)
        client.set_output_bass("Zone_1", 1)
        client.output_bass_up("Zone_1")
        client.output_bass_down("Zone_1")
        client.set_output_treble("Zone_1", -1)
        client.output_treble_up("Zone_1")
        client.output_treble_down("Zone_1")
        client.set_output_balance("Zone_1", 2)
        client.output_balance_left("Zone_1")
        client.output_balance_right("Zone_1")
        client.set_output_loudness("Zone_1", True)
        client.set_output_mono_downmix("Zone_1", False)
        client.set_output_power_on_volume("Zone_1", 12)
        client.set_all_output_bass(0)
        client.all_outputs().set_power_on_volume(0)

        self.assertIn("MaxVolume 90 Zone_1", conn.sent)
        self.assertIn("Bass 1 Zone_1", conn.sent)
        self.assertIn("BassUp Zone_1", conn.sent)
        self.assertIn("BassDown Zone_1", conn.sent)
        self.assertIn("Treble -1 Zone_1", conn.sent)
        self.assertIn("TrebleUp Zone_1", conn.sent)
        self.assertIn("TrebleDown Zone_1", conn.sent)
        self.assertIn("Balance 2 Zone_1", conn.sent)
        self.assertIn("BalanceLeft Zone_1", conn.sent)
        self.assertIn("BalanceRight Zone_1", conn.sent)
        self.assertIn("Loudness True Zone_1", conn.sent)
        self.assertIn("MonoDownmix False Zone_1", conn.sent)
        self.assertIn("PowerOnVolume 12 Zone_1", conn.sent)
        self.assertIn("Bass 0 Zone_1", conn.sent)
        self.assertIn("Bass 0 Zone_2", conn.sent)
        self.assertIn("PowerOnVolume 0 Zone_1", conn.sent)
        self.assertIn("PowerOnVolume 0 Zone_2", conn.sent)

    def test_unified_client_exposes_mrad_versions_groups_and_party_helpers(self):
        conn = ScriptedConnection(
            {
                "GetVersions": ['Versions "AMP:ACE14F0055B4 SKU:M6250 FW:6.3.2.0"'],
                "BrowseZoneGroups": [
                    '<ZoneGroups total="1" start="1" more="false">'
                    '<ZoneGroup guid="gg1" name="ZG_1"><vol><zone eventId="Zone_1" guid="zg1" name="Office" /></vol></ZoneGroup>'
                    "</ZoneGroups>"
                ],
                "BrowseZonesForGroup gg1": [
                    '<Zones total="1" start="1" more="false"><Zone guid="zg1" name="Office" id="Zone_1" /></Zones>'
                ],
                "BrowsePartyModeInclude": [
                    '<PartyModeInclude total="1" start="1" more="false">'
                    '<PartyModeInfo guid="zg1" name="Office" enabled="false" />'
                    "</PartyModeInclude>"
                ],
                "help SetZoneGroup": [
                    "SetZoneGroup           - Grouping and ungrouping zones.",
                    "<zoneNameOrIdOrGuidOrGroupGuid> <commaDelimGuidsSelected> <optionalTargetSource>",
                ],
            }
        )
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

        versions = client.get_versions()
        groups = client.list_zone_groups()
        zones = client.list_zones_for_group(groups[0])
        party_rows = client.list_party_mode_include()
        client.identify_output(zones[0])
        client.set_party_mode("toggle", zones[0])
        client.set_party_mode_include(False, zones[0])
        client.set_source_for_group("sg-main")
        client.set_zone_group(zones[0], ["Zone_1", "Zone_2"], "sg-main")
        client.set_zone_group_timer("22:00", zones[0])
        client.set_output_name(zones[0], "Office")
        client.set_output_icon(zones[0], "Kitchen")
        client.set_output_gain(zones[0], 3)
        command_help = client.command_help("SetZoneGroup")

        self.assertEqual(versions[0].sku, "M6250")
        self.assertEqual(groups[0].guid, "gg1")
        self.assertEqual(zones[0].id, "Zone_1")
        self.assertFalse(party_rows[0].enabled)
        self.assertEqual(
            command_help.usage,
            ("<zoneNameOrIdOrGuidOrGroupGuid> <commaDelimGuidsSelected> <optionalTargetSource>",),
        )
        self.assertIn("IdentifyZone Zone_1", conn.sent)
        self.assertIn("PartyMode Toggle Zone_1", conn.sent)
        self.assertIn("SetPartyModeInclude False Zone_1", conn.sent)
        self.assertIn("SetSourceForGroup sg-main", conn.sent)
        self.assertIn("SetZoneGroup Zone_1 Zone_1,Zone_2 sg-main", conn.sent)
        self.assertIn("SetZoneGroupTimer 22:00 Zone_1", conn.sent)
        self.assertIn("ZoneName Office Zone_1", conn.sent)
        self.assertIn("ZoneIcon Kitchen Zone_1", conn.sent)
        self.assertIn("ZoneGain 3 Zone_1", conn.sent)

    def test_unified_client_lists_direct_amplifier_zone_groups(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp.local",
                        "output_count": 3,
                        "source_count": 8,
                        "source_base": 0,
                    }
                ],
                "output_names": {
                    "1": "Kitchen",
                    "2": "Dining",
                    "3": "Living",
                }
            }
        }
        client = AutonomicClient(
            "amp.local",
            mode="amplifier",
            auto_initialize=False,
            config=config,
        )
        sent: list[str] = []

        def fake_send_commands(commands, *, timeout=None) -> str:
            normalized = [command.upper().rstrip("\r\n") for command in commands]
            sent.extend(normalized)
            return "\n".join("3000070102" if command == "30FF20" else "" for command in normalized)

        client.amplifier.send_commands = fake_send_commands  # type: ignore[method-assign]

        groups = client.list_zone_groups()
        matching_groups = client.list_zone_group("Kitchen")
        zones = client.list_zones_for_group(groups[0])

        self.assertEqual(groups[0].id, "DirectGroup_1_2")
        self.assertEqual(groups[0].attributes["sourceLinked"], "true")
        self.assertEqual(groups[0].attributes["volumeLinked"], "true")
        self.assertEqual(groups[0].attributes["powerLinked"], "true")
        self.assertEqual([output.name for output in groups[0].volume_outputs], ["Kitchen", "Dining"])
        self.assertEqual([output.id for output in groups[0].source_outputs], ["1", "2"])
        self.assertEqual(matching_groups, groups)
        self.assertEqual([zone.name for zone in zones], ["Kitchen", "Dining"])
        self.assertEqual(sent, ["30FF20", "30FF20", "30FF20"])

    def test_unified_client_forwards_mrad_source_and_utility_helpers(self):
        conn = ScriptedConnection(
            {
                "Banner": [
                    "Autonomic Controls MRAD Bridge version 8.3.20260518.1 Release.",
                    "Server=ACE14F006012",
                ],
                "Ping": ['Ping "Pong"'],
                "Echo \"hello there\"": ['Echo "hello there"'],
                "Uptime": ['Uptime "0.13:19:03"'],
                "Time s": ['Time "2026-05-26T12:49:45"'],
                "BrowsePageUp": [
                    '<Sources total="1" start="1" more="false"><Source guid="sg1" name="Main" sId="1" /></Sources>'
                ],
                "BrowsePageDown": [
                    '<Zones total="1" start="1" more="false"><Zone guid="zg1" name="Office" id="Zone_1" /></Zones>'
                ],
            }
        )
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

        client.echo("hello there")
        client.uptime()
        client.time("s")
        client.sync_time()
        client.log_comment("unit test")
        client.set_client_version("1.2.3")
        client.set_client_type("UnitTest")
        client.set_encoding(65001)
        client.set_host("mrad.example")
        client.set_xml_mode(False)
        client.set_response_eol_zero()
        banner = client.banner()
        client.set_source_by_name("engine-source")
        client.set_source_name("sg-main", "Main Input")
        client.set_source_icon("sg-main", "Disc")
        page_up = client.browse_page_up()
        page_down = client.browse_page_down()

        self.assertTrue(client.ping_ok())
        self.assertEqual(client.echo_text("hello there"), "hello there")
        self.assertEqual(client.uptime_text(), "0.13:19:03")
        self.assertEqual(client.time_text("s"), "2026-05-26T12:49:45")
        self.assertEqual(banner[-1], "Server=ACE14F006012")
        self.assertEqual(page_up.kind, "Sources")
        self.assertEqual(page_down.kind, "Zones")
        self.assertIn("Echo \"hello there\"", conn.sent)
        self.assertIn("Uptime", conn.sent)
        self.assertIn("Time s", conn.sent)
        self.assertIn("SyncTime", conn.sent)
        self.assertIn("LogComment \"unit test\"", conn.sent)
        self.assertIn("SetClientVersion 1.2.3", conn.sent)
        self.assertIn("SetClientType UnitTest", conn.sent)
        self.assertIn("SetEncoding 65001", conn.sent)
        self.assertIn("SetHost mrad.example", conn.sent)
        self.assertIn("SetXmlMode None", conn.sent)
        self.assertIn("SetResponseEolZero", conn.sent)
        self.assertIn("Banner", conn.sent)
        self.assertEqual(conn.response_delimiter, b"\x00")
        self.assertIn("SetSourceByName engine-source", conn.sent)
        self.assertIn("SourceName \"Main Input\" sg-main", conn.sent)
        self.assertIn("SourceIcon Disc sg-main", conn.sent)

    def test_unified_client_reads_typed_output_status_and_scales_mrad_volume(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "6012",
                        "host": "10.1.0.201",
                        "output_start": 9,
                        "native_output_start": 9,
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                        "model_byte": "0xE9",
                    }
                ]
            }
        }
        conn = ScriptedConnection(
            {
                "GetStatus": [
                    "MRAD.ReportState Zone_9 ZoneId=9",
                    "MRAD.ReportState Zone_9 ZoneName=Patio",
                    "MRAD.ReportState Zone_9 PowerOn=True",
                    "MRAD.ReportState Zone_9 Mute=False",
                    "MRAD.ReportState Zone_9 Volume=40",
                    "MRAD.ReportState Zone_9 MinVolume=0",
                    "MRAD.ReportState Zone_9 MinMinVolume=0",
                    "MRAD.ReportState Zone_9 MaxVolume=80",
                    "MRAD.ReportState Zone_9 MaxMaxVolume=80",
                    "MRAD.ReportState Zone_9 Bass=-2",
                    "MRAD.ReportState Zone_9 Treble=3",
                    "MRAD.ReportState Zone_9 Balance=-1",
                    "MRAD.ReportState Zone_9 ZoneGain=4",
                    "MRAD.ReportState Zone_9 LoudnessEnabled=True",
                    "MRAD.ReportState Zone_9 MonoDownmix=False",
                    "MRAD.ReportState Zone_9 PowerOnVolume=20",
                    "MRAD.ReportState Zone_9 DeviceType=MA6",
                    "MRAD.ReportState Zone_9 GainMode=Variable",
                    "MRAD.ReportState Zone_9 PartyMode=Off",
                    "MRAD.ReportState Zone_9 QualifiedSourceName=Player_A@ACE14F006012",
                    "MRAD.ReportState Zone_9 ZoneGroupName=ZG_9",
                    "MRAD.ReportState Zone_9 ZoneGroupPower=False",
                    "MRAD.ReportState Zone_9 ZoneIsLocked=False",
                    "OK",
                ],
            }
        )
        client = AutonomicClient("10.1.0.201", mode="mrad", auto_initialize=False, config=config)
        client.audio = MirageAudioSystem("10.1.0.201", connection=conn)

        output = client.get_output_status("Zone_9")

        self.assertEqual(output.id, "Zone_9")
        self.assertEqual(output.name, "Patio")
        self.assertTrue(output.is_on)
        self.assertFalse(output.muted)
        self.assertEqual(output.volume, 50.0)
        self.assertEqual(output.attributes["raw_volume"], "40")
        self.assertEqual(output.min_volume, 0.0)
        self.assertEqual(output.min_min_volume, 0.0)
        self.assertEqual(output.max_max_volume, 100.0)
        self.assertEqual(output.attributes["raw_max_max_volume"], "80")
        self.assertEqual(output.device_type, "MA6")
        self.assertEqual(output.gain_mode, "Variable")
        self.assertEqual(output.party_mode, "Off")
        self.assertEqual(output.qualified_source_name, "Player_A@ACE14F006012")
        self.assertEqual(output.zone_group_name, "ZG_9")
        self.assertFalse(output.zone_group_power)
        self.assertFalse(output.zone_is_locked)
        self.assertEqual(client.read_output_volume("Zone_9"), 50.0)
        self.assertEqual(client.read_output_max_volume("Zone_9"), 100.0)
        self.assertFalse(client.read_output_mute("Zone_9"))
        self.assertTrue(client.read_output_power("Zone_9"))
        self.assertEqual(client.read_output_bass("Zone_9"), -2)
        self.assertEqual(client.read_output_treble("Zone_9"), 3)
        self.assertEqual(client.read_output_balance("Zone_9"), -1)
        self.assertEqual(client.read_output_gain("Zone_9"), 4)
        self.assertTrue(client.read_output_loudness("Zone_9"))
        self.assertFalse(client.read_output_mono_downmix("Zone_9"))
        self.assertEqual(client.read_output_power_on_volume("Zone_9"), 25.0)
        self.assertFalse(output.read_mono_downmix())
        self.assertEqual(output.read_power_on_volume(), 25.0)

    def test_unified_client_routes_local_sources_by_target_direct_device(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "D001",
                        "host": "amp-one.local",
                        "output_start": 1,
                        "output_count": 2,
                        "source_count": 2,
                        "source_base": 0,
                        "model_byte": "0xB0",
                    },
                    {
                        "device_id": "D002",
                        "host": "amp-two.local",
                        "output_start": 3,
                        "native_output_start": 1,
                        "output_count": 2,
                        "source_count": 2,
                        "source_base": 0,
                        "model_byte": "0xE9",
                    },
                ],
                "output_names": {
                    "1": "Kitchen",
                    "3": "Patio",
                },
                "local_sources_by_device_id": {
                    "D001": [
                        {"slot": 1, "name": "A1"},
                    ],
                    "D002": [
                        {"slot": 1, "name": "A1", "aliases": ["Analog 1"]},
                    ],
                },
            }
        }
        client = AutonomicClient("amp-one.local", mode="amplifier", auto_initialize=False, config=config)
        sent_by_host: dict[str, list[str]] = {"amp-one.local": [], "amp-two.local": []}
        for endpoint in client._direct_endpoints():
            endpoint.amplifier.send_commands = (  # type: ignore[method-assign]
                lambda commands, timeout=None, host=endpoint.host: sent_by_host[host].extend(
                    command.upper().rstrip("\r\n") for command in commands
                )
                or ""
            )

        outputs = client.list_outputs(include_status=False)
        client.assign_source_to_output("A1", "Kitchen")
        client.assign_source_to_output("A1", "Patio")
        client.assign_source_to_output("Analog 1", outputs[2])

        self.assertEqual([output.id for output in outputs], ["1", "2", "3", "4"])
        self.assertEqual(outputs[2].attributes["deviceId"], "D002")
        self.assertEqual(outputs[2].attributes["nativeId"], "1")
        self.assertEqual(sent_by_host["amp-one.local"], ["030105"])
        self.assertEqual(sent_by_host["amp-two.local"], ["030105", "030105"])

    def test_unified_client_routes_all_model_local_sources_by_target_device(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp-one.local",
                        "output_start": 1,
                        "native_output_start": 1,
                        "output_count": 8,
                        "source_count": 8,
                        "source_base": 0,
                        "model_byte": "0xB0",
                    },
                    {
                        "device_id": "6012",
                        "host": "amp-two.local",
                        "output_start": 9,
                        "native_output_start": 9,
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                        "model_byte": "0xE9",
                    },
                ]
            }
        }
        client = AutonomicClient("amp-one.local", mode="amplifier", auto_initialize=False, config=config)
        sent_by_host: dict[str, list[str]] = {"amp-one.local": [], "amp-two.local": []}
        for endpoint in client._direct_endpoints():
            endpoint.amplifier.send_commands = (  # type: ignore[method-assign]
                lambda commands, timeout=None, host=endpoint.host: sent_by_host[host].extend(
                    command.upper().rstrip("\r\n") for command in commands
                )
                or ""
            )

        for source_name in ("A1", "A2", "A3", "A4", "COAX1", "COAX2", "OPT1", "OPT2"):
            client.assign_source_to_output(source_name, "Zone_1")
        client.assign_source_to_output("00D4:OPT1", "Zone_1")
        for source_name in (
            "Player_A",
            "Player_B",
            "Player_C",
            "Analog 1",
            "Analog 2",
            "Analog 3",
            "Analog 4",
            "Coaxial 1",
            "Coaxial 2",
            "Optical 1",
            "Optical 2",
            "Casting_1",
        ):
            client.assign_source_to_output(source_name, "Zone_9")
        client.assign_source_to_output("6012:Optical 1", "Zone_9")
        client.assign_source_to_output("S12", "Zone_9")

        self.assertEqual(
            sent_by_host["amp-one.local"],
            ["030105", "030106", "030107", "030103", "030100", "030101", "030102", "030104", "030102"],
        )
        self.assertEqual(
            sent_by_host["amp-two.local"],
            [
                "030905",
                "030906",
                "030907",
                "030903",
                "030900",
                "030901",
                "030902",
                "030904",
                "030908",
                "030909",
                "03090A",
                "03090B",
                "030909",
                "03090B",
            ],
        )

    def test_unified_client_defines_eaudiocast_sources_between_direct_devices(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp-one.local",
                        "output_start": 1,
                        "native_output_start": 1,
                        "output_count": 8,
                        "model_byte": "0xB0",
                    },
                    {
                        "device_id": "6012",
                        "host": "amp-two.local",
                        "output_start": 9,
                        "native_output_start": 9,
                        "output_count": 8,
                        "model_byte": "0xE9",
                    },
                ]
            }
        }
        client = AutonomicClient("amp-one.local", mode="amplifier", auto_initialize=False, config=config)
        devices = [
            AmplifierDeviceInfo(amp_id="00D4", guid="m6250-guid"),
            AmplifierDeviceInfo(amp_id="6012", guid="ma6-guid"),
        ]
        sent: dict[str, list[tuple[int, str, int, str]]] = {"amp-one.local": [], "amp-two.local": []}
        deleted: dict[str, list[int]] = {"amp-one.local": [], "amp-two.local": []}

        for endpoint in client._direct_endpoints():
            endpoint.amplifier.discover_devices = lambda refresh=False, devices=devices: devices  # type: ignore[method-assign]
            endpoint.amplifier.define_remote_source = (  # type: ignore[method-assign]
                lambda slot, guid, source_position, name="", host=endpoint.host: sent[host].append(
                    (slot, guid, source_position, name)
                )
                or "OK"
            )
            endpoint.amplifier.delete_remote_source = (  # type: ignore[method-assign]
                lambda slot, host=endpoint.host: deleted[host].append(slot) or "OK"
            )

        self.assertEqual(
            client.define_eaudiocast_source(
                target_device_id="6012",
                slot=0,
                source="00D4:OPT1",
                name="M6250 OPT1",
            ),
            "OK",
        )
        self.assertEqual(
            client.define_eaudiocast_source(
                target_device_id="00D4",
                slot=2,
                source="6012:3",
                name="MA6 Analog 1",
            ),
            "OK",
        )
        self.assertEqual(client.delete_eaudiocast_source(target_device_id="6012", slot=0), "OK")

        self.assertEqual(sent["amp-two.local"], [(0, "m6250-guid", 6, "M6250 OPT1")])
        self.assertEqual(sent["amp-one.local"], [(2, "ma6-guid", 3, "MA6 Analog 1")])
        self.assertEqual(deleted["amp-two.local"], [0])

        mrad_client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        with self.assertRaises(AutonomicError):
            mrad_client.define_eaudiocast_source(target_device_id="6012", slot=0, source="00D4:6")

    def test_unified_client_keeps_remote_source_slots_device_qualified(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "D001",
                        "host": "amp-one.local",
                        "output_start": 1,
                        "native_output_start": 1,
                        "output_count": 2,
                        "source_count": 2,
                        "source_base": 0,
                    },
                    {
                        "device_id": "D002",
                        "host": "amp-two.local",
                        "output_start": 3,
                        "native_output_start": 1,
                        "output_count": 2,
                        "source_count": 2,
                        "source_base": 0,
                    },
                ],
                "remote_sources": [
                    {
                        "target_device_id": "D001",
                        "source_device_id": "D002",
                        "source_id": 32,
                        "name": "D002 Cast",
                        "guid": "d002-guid",
                    },
                    {
                        "target_device_id": "D002",
                        "source_device_id": "D001",
                        "source_id": 32,
                        "name": "D001 Cast",
                        "guid": "d001-guid",
                    },
                ],
            }
        }
        client = AutonomicClient("amp-one.local", mode="amplifier", auto_initialize=False, config=config)
        sent_by_host: dict[str, list[str]] = {"amp-one.local": [], "amp-two.local": []}
        for endpoint in client._direct_endpoints():
            endpoint.amplifier.list_sources = lambda include_disabled=False: []  # type: ignore[method-assign]
            endpoint.amplifier.send_commands = (  # type: ignore[method-assign]
                lambda commands, timeout=None, host=endpoint.host: sent_by_host[host].extend(
                    command.upper().rstrip("\r\n") for command in commands
                )
                or ""
            )

        sources = client.list_sources(include_disabled=True)
        client.assign_source_to_output("D001:32", "Zone_1")
        client.assign_source_to_output("D002:32", "Zone_3")

        self.assertEqual([(source.id, source.name) for source in sources], [("D001:32", "D002 Cast"), ("D002:32", "D001 Cast")])
        self.assertEqual(sent_by_host["amp-one.local"], ["030120"])
        self.assertEqual(sent_by_host["amp-two.local"], ["030120"])

    def test_unified_client_filters_stacked_source_rows_per_device(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp-one.local",
                        "output_start": 1,
                        "native_output_start": 1,
                        "output_count": 8,
                        "source_count": 8,
                        "source_base": 0,
                        "model_byte": "0xB0",
                    },
                    {
                        "device_id": "6012",
                        "host": "amp-two.local",
                        "output_start": 9,
                        "native_output_start": 9,
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                        "model_byte": "0xE9",
                    },
                ]
            }
        }
        client = AutonomicClient("amp-one.local", mode="amplifier", auto_initialize=False, config=config)
        for endpoint in client._direct_endpoints():
            if endpoint.device_id == "00D4":
                endpoint.amplifier.list_sources = (  # type: ignore[method-assign]
                    lambda include_disabled=False: [
                        AutonomicSource(id=str(index), name=f"M6250 {index}", kind="Source")
                        for index in range(13)
                    ]
                )
            else:
                endpoint.amplifier.list_sources = (  # type: ignore[method-assign]
                    lambda include_disabled=False: [
                        AutonomicSource(id=str(index), name=f"MA6 {index}", kind="Source")
                        for index in range(12)
                    ]
                )

        sources = client.list_sources(include_disabled=True)
        sources_by_id = {source.id: source for source in sources}

        self.assertIn("00D4:7", sources_by_id)
        self.assertNotIn("00D4:8", sources_by_id)
        self.assertNotIn("00D4:12", sources_by_id)
        self.assertIn("6012:8", sources_by_id)
        self.assertIn("6012:11", sources_by_id)
        self.assertEqual(len([source for source in sources if source.attributes.get("deviceId") == "00D4"]), 8)
        self.assertEqual(len([source for source in sources if source.attributes.get("deviceId") == "6012"]), 12)

    def test_unified_client_collapses_whole_device_source_assignment_to_all_outputs(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp-one.local",
                        "output_start": 1,
                        "native_output_start": 1,
                        "output_count": 8,
                        "source_count": 8,
                        "source_base": 0,
                        "model_byte": "0xB0",
                    },
                    {
                        "device_id": "6012",
                        "host": "amp-two.local",
                        "output_start": 9,
                        "native_output_start": 9,
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                        "model_byte": "0xE9",
                    },
                ]
            }
        }
        client = AutonomicClient("amp-one.local", mode="amplifier", auto_initialize=False, config=config)
        sent_by_host: dict[str, list[str]] = {"amp-one.local": [], "amp-two.local": []}
        for endpoint in client._direct_endpoints():
            endpoint.amplifier.send_commands = (  # type: ignore[method-assign]
                lambda commands, timeout=None, host=endpoint.host: sent_by_host[host].extend(
                    command.upper().rstrip("\r\n") for command in commands
                )
                or ""
            )

        outputs = client.list_outputs(include_status=False)
        m6250_outputs = [output for output in outputs if output.attributes.get("deviceId") == "00D4"]
        ma6_outputs = [output for output in outputs if output.attributes.get("deviceId") == "6012"]

        client.assign_source_to_outputs("00D4:OPT1", m6250_outputs)
        client.assign_source_to_outputs("6012:32", ma6_outputs)

        self.assertEqual(sent_by_host["amp-one.local"], ["03FF02"])
        self.assertEqual(sent_by_host["amp-two.local"], ["03FF20"])

    def test_unified_client_caches_direct_sources_and_uses_cache_for_status_names(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "D001",
                        "host": "amp.local",
                        "output_start": 1,
                        "native_output_start": 1,
                        "output_count": 2,
                        "source_count": 2,
                        "source_base": 0,
                    }
                ]
            }
        }
        client = AutonomicClient("amp.local", mode="amplifier", auto_initialize=False, config=config)
        endpoint = client._direct_endpoints()[0]
        list_source_calls = 0
        observed_source_names: list[dict[int, str]] = []

        def fake_list_sources(include_disabled: bool = False) -> list[AutonomicSource]:
            nonlocal list_source_calls
            list_source_calls += 1
            return [
                AutonomicSource(id="0", name="Input 1", kind="Source"),
                AutonomicSource(id="1", name="Input 2", kind="Source"),
            ]

        def fake_list_outputs(
            *,
            include_disabled: bool = False,
            include_status: bool = True,
            include_names: bool = False,
            include_source_names: bool = True,
            source_names: dict[int, str] | None = None,
        ) -> list[AutonomicOutput]:
            observed_source_names.append(dict(source_names or {}))
            return [AutonomicOutput(id="1", name="Output 1", kind="Output", source_id="0", source_name=(source_names or {}).get(0))]

        endpoint.amplifier.list_sources = fake_list_sources  # type: ignore[method-assign]
        endpoint.amplifier.list_outputs = fake_list_outputs  # type: ignore[method-assign]
        endpoint.amplifier.set_source_name = lambda source, name: "OK"  # type: ignore[method-assign]
        endpoint.amplifier.rename_sources_to_low_level_input_labels = lambda: "OK"  # type: ignore[method-assign]

        sources = client.list_sources(include_disabled=True)
        sources[0].name = "Caller Mutation"
        self.assertEqual(client.list_sources(include_disabled=True)[0].name, "Input 1")

        client.set_source_name("D001:0", "Renamed Input")
        client.list_outputs(include_status=True)
        self.assertEqual(client.list_sources(include_disabled=True)[0].name, "Renamed Input")
        self.assertEqual(observed_source_names, [{0: "Renamed Input", 1: "Input 2"}])
        self.assertEqual(list_source_calls, 1)

        client.rename_sources_to_low_level_input_labels()
        self.assertEqual(client.list_sources(include_disabled=True)[0].name, "S1")

    def test_unified_client_updates_source_cache_for_direct_remote_source_writes(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "D001",
                        "host": "amp-one.local",
                        "output_start": 1,
                        "native_output_start": 1,
                        "output_count": 2,
                        "source_count": 2,
                        "source_base": 0,
                    },
                    {
                        "device_id": "D002",
                        "host": "amp-two.local",
                        "output_start": 3,
                        "native_output_start": 1,
                        "output_count": 2,
                        "source_count": 2,
                        "source_base": 0,
                    },
                ]
            }
        }
        client = AutonomicClient("amp-one.local", mode="amplifier", auto_initialize=False, config=config)
        devices = [
            AmplifierDeviceInfo(amp_id="D001", guid="d001-guid"),
            AmplifierDeviceInfo(amp_id="D002", guid="d002-guid"),
        ]
        list_source_calls = 0

        for endpoint in client._direct_endpoints():
            endpoint.amplifier.discover_devices = lambda refresh=False, devices=devices: devices  # type: ignore[method-assign]
            endpoint.amplifier.define_remote_source = lambda slot, guid, source_position, name="": "OK"  # type: ignore[method-assign]
            endpoint.amplifier.delete_remote_source = lambda slot: "OK"  # type: ignore[method-assign]

            def fake_list_sources(include_disabled: bool = False) -> list[AutonomicSource]:
                nonlocal list_source_calls
                list_source_calls += 1
                return []

            endpoint.amplifier.list_sources = fake_list_sources  # type: ignore[method-assign]

        self.assertEqual(client.list_sources(include_disabled=True), [])
        client.define_eaudiocast_source(target_device_id="D002", slot=0, source="D001:0", name="Shared Input")
        self.assertEqual([(source.id, source.name) for source in client.list_sources(include_disabled=True)], [("D002:32", "Shared Input")])

        client.delete_eaudiocast_source(target_device_id="D002", slot=0)
        self.assertEqual(client.list_sources(include_disabled=True), [])
        self.assertEqual(list_source_calls, 2)

    def test_unified_client_prefers_direct_amp_for_percent_volume(self):
        client = AutonomicClient("hybrid.local", mode="auto", auto_initialize=False)
        sent: list[str] = []
        client.amplifier.send_commands = (  # type: ignore[method-assign]
            lambda commands, timeout=None: sent.extend(command.upper().rstrip("\r\n") for command in commands) or ""
        )
        client.audio = MirageAudioSystem("hybrid.local", connection=ScriptedConnection())

        with patch("autonomic.client._can_connect", return_value=True):
            client.set_output_volume(1, 12.5)

        self.assertEqual(sent, ["040114"])
        self.assertEqual(client.audio._connection.sent, [])

    def test_unified_client_uses_direct_amp_for_relative_tone_controls(self):
        client = AutonomicClient("amp.local", mode="amplifier", auto_initialize=False)
        sent: list[str] = []
        responses = {
            "0501": "050101",
            "0601": "0601FE",
            "0701": "070103",
            "4401": "4401FF",
            "3101": "310119",
        }

        def fake_send_commands(commands, *, timeout=None) -> str:
            normalized = [command.upper().rstrip("\r\n") for command in commands]
            sent.extend(normalized)
            return "\n".join(responses.get(command, "") for command in normalized)

        client.amplifier.send_commands = fake_send_commands  # type: ignore[method-assign]

        client.output_bass_up(1)
        client.output_bass_down(1)
        client.output_treble_up(1)
        client.output_treble_down(1)
        client.output_balance_left(1)
        client.output_balance_right(1)
        client.output_gain_up(1)
        client.output_gain_down(1)
        client.output_delay_up(1)
        client.output_delay_down(1)

        self.assertEqual(
            sent,
            [
                "0501",
                "050102",
                "0501",
                "050100",
                "0601",
                "0601FF",
                "0601",
                "0601FD",
                "0701",
                "070102",
                "0701",
                "070104",
                "4401",
                "440100",
                "4401",
                "4401FE",
                "3101",
                "31011A",
                "3101",
                "310118",
            ],
        )

        mrad_client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        with self.assertRaises(AutonomicError):
            mrad_client.output_delay_up(1)

    def test_unified_client_scales_mrad_volume_for_device_expression_range(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "6012",
                        "host": "10.1.0.201",
                        "output_start": 9,
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                        "model_byte": "0xE9",
                    }
                ]
            }
        }
        conn = ScriptedConnection()
        client = AutonomicClient("10.1.0.201", mode="amplifier", auto_initialize=False, config=config)
        client._mrad_client_cache = MirageAudioSystem("10.1.0.201", connection=conn)

        with patch("autonomic.client._can_connect", return_value=False):
            client.set_output_power_on_volume("Zone_9", 50.0)

        self.assertEqual(
            conn.sent,
            [
                "*",
                "SetClientType PythonSDK",
                "SetEncoding 65001",
                "SetXmlMode Lists",
                "SetHost 10.1.0.201",
                "SubscribeEvents false",
                "Power On Zone_9",
                "PowerOnVolume 40 Zone_9",
            ],
        )

    def test_unified_client_scales_mrad_max_volume_relative_helpers(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "6012",
                        "host": "10.1.0.201",
                        "output_start": 9,
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                        "model_byte": "0xE9",
                    }
                ]
            }
        }
        conn = ScriptedConnection(
            {
                "GetStatus": [
                    "MRAD.ReportState Zone_9 ZoneId=9",
                    "MRAD.ReportState Zone_9 MaxVolume=40",
                    "OK",
                ],
            }
        )
        client = AutonomicClient("10.1.0.201", mode="mrad", auto_initialize=False, config=config)
        client.audio = MirageAudioSystem("10.1.0.201", connection=conn)

        client.output_max_volume_up("Zone_9", step=5)
        client.output_max_volume_down("Zone_9", step=10)

        self.assertEqual(
            conn.sent,
            [
                "GetStatus",
                "Power On Zone_9",
                "MaxVolume 44 Zone_9",
                "GetStatus",
                "Power On Zone_9",
                "MaxVolume 32 Zone_9",
            ],
        )

    def test_unified_client_scales_all_mrad_volume_writes_per_device(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "10.1.0.200",
                        "output_start": 1,
                        "output_count": 8,
                        "source_count": 8,
                        "source_base": 0,
                        "model_byte": "0xB0",
                    },
                    {
                        "device_id": "6012",
                        "host": "10.1.0.201",
                        "output_start": 9,
                        "native_output_start": 9,
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                        "model_byte": "0xE9",
                    },
                ]
            }
        }
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="2" start="1" more="false">'
                    '<Zone guid="zg1" name="Kitchen" id="Zone_1" isOn="True" />'
                    '<Zone guid="zg9" name="Patio" id="Zone_9" isOn="True" />'
                    "</Zones>"
                ]
            }
        )
        client = AutonomicClient("10.1.0.200", mode="mrad", auto_initialize=False, config=config)
        client.audio = MirageAudioSystem("10.1.0.200", connection=conn)

        client.set_all_output_volume(50.0)
        client.set_all_output_max_volume(75.0)
        client.volume(12.5)

        self.assertEqual(
            conn.sent,
            [
                "BrowseAllZones",
                "Volume 40 Zone_1",
                "Volume 40 Zone_9",
                "BrowseAllZones",
                "MaxVolume 60 Zone_1",
                "MaxVolume 60 Zone_9",
                "Volume 12",
            ],
        )

    def test_unified_client_supports_direct_amplifier_mode_with_objects(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp.local",
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                    }
                ],
                "output_names": {
                    "1": "Kitchen",
                    "2": "Dining",
                    "3": "Living",
                    "4": "Master",
                    "5": "Bathroom",
                    "6": "Foyer",
                    "7": "Sitting",
                    "8": "Passthrough",
                },
            }
        }
        client = AutonomicClient(
            "amp.local",
            mode="amplifier",
            auto_initialize=False,
            config=config,
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
        self.assertEqual(sources[6].name, "S7")
        self.assertEqual(sources[7].name, "S8")
        self.assertEqual(sources[6].attributes["address"], "02")
        self.assertEqual(sources[11].attributes["address"], "0B")
        self.assertEqual(client.source_by_name("S7").id, "00D4:6")
        self.assertEqual(client.source_by_name("S7").attributes["nativeId"], "6")
        self.assertEqual(client.output_by_name("Kitchen").id, "1")

        sent.clear()
        client.amplifier.send_commands = fake_send_commands  # type: ignore[method-assign]
        client.amplifier.send_ascii = fake_send  # type: ignore[method-assign]

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
        client.assign_source_to_output("S7", "Kitchen")
        client.set_output_volume("Passthrough", 50)
        client.set_output_mute("Passthrough", False)
        client.set_output_power("Passthrough", True)
        client.set_output_power("Passthrough", "toggle")
        client.set_output_loudness("Passthrough", "off")
        client.set_all_output_power(False)
        client.set_all_output_loudness("false")
        client.all_on()
        client.set_input_gain("S7", "Passthrough", 50, refresh=False)
        client.assign_source_to_output("S8", "Passthrough")

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
                "040850",
                "020801",
                "010801",
                "010804",
                "0C0800",
                "01FF00",
                "0CFF00",
                "01FF01",
                "32080209",
                "030804",
            ],
        )

    def test_unified_client_sets_all_mrad_output_power(self):
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
        client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        client.audio = MirageAudioSystem("mrad.local", connection=conn)

        client.all_off()
        client.all_on()

        self.assertIn("AllOff", conn.sent)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power On Zone_2", conn.sent)

        self.assertFalse(hasattr(client, "play"))

    def test_unified_client_defaults_to_hardware_source_names_without_alias_config(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "6012",
                        "host": "amp.local",
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                    }
                ]
            }
        }
        client = AutonomicClient(
            "amp.local",
            mode="amplifier",
            auto_initialize=False,
            config=config,
        )
        client._amplifier_device_id = "6012"

        sources = client.list_sources()

        self.assertEqual(sources[3].name, "S4")
        self.assertIsNone(sources[3].guid)
        self.assertEqual(sources[6].name, "S7")
        self.assertIsNone(sources[6].guid)
        self.assertEqual(sources[7].name, "S8")
        self.assertIsNone(sources[7].guid)
        self.assertEqual(client.source_by_name("S4").id, "6012:3")

    def test_unified_client_reads_direct_amplifier_output_status(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp.local",
                        "output_count": 2,
                        "source_count": 8,
                        "source_base": 0,
                    }
                ],
                "output_names": {
                    "1": "Kitchen",
                    "2": "Dining",
                },
            }
        }
        client = AutonomicClient("amp.local", mode="amplifier", auto_initialize=False, config=config)
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
                "0DFF": "0D0180\n0D0240",
                "0D01": "0D0180",
                "31FF": "310119\n310200",
            }
            return "\n".join(responses.get(command, "") for command in normalized)

        def fake_send(command: str) -> str:
            return fake_send_commands([command]).strip()

        client.amplifier.send_commands = fake_send_commands  # type: ignore[method-assign]
        client.amplifier.send_ascii = fake_send  # type: ignore[method-assign]

        outputs = client.list_outputs()

        self.assertEqual(sent, ["29FF", "4FFF", "01FF", "02FF", "03FF", "04FF", "05FF", "06FF", "07FF", "0CFF", "0DFF", "31FF", "44FF"])
        self.assertEqual(
            [(output.is_on, output.muted, output.volume, output.source_name) for output in outputs],
            [
                (True, False, 50, "S7"),
                (False, True, 40, "Remote 3"),
            ],
        )
        self.assertEqual([output.name for output in outputs], ["Kitchen", "Dining"])

        sent.clear()
        self.assertEqual(client.read_output_max_volume("Kitchen"), 80)
        self.assertEqual(client.read_output_delay(outputs[0]), 125)
        self.assertEqual(outputs[0].read_max_volume(), 80)
        self.assertEqual(outputs[0].read_delay(), 125)

        sent.clear()
        client.output_max_volume_up("Kitchen", step=2)
        outputs[0].max_volume_down(5)

        self.assertEqual(sent, ["0D01", "0D0183", "0D01", "0D0178"])

    def test_client_uses_human_readable_json_config_for_static_mappings(self):
        config_text = """
{
  "source_aliases": {
    "sg-alpha": "Study"
  },
  "direct_amplifier": {
    "devices": [
      {
        "device_id": "00D4",
        "host": "amp.local",
        "output_count": 8,
        "source_count": 8,
        "source_base": 0
      }
    ],
    "output_names": {
      "1": "Library"
    },
    "source_name_aliases": {},
    "local_sources_by_device_id": {
      "00D4": [
        {
          "slot": 7,
          "name": "Desk",
          "guid": "desk-guid"
        }
      ]
    },
    "remote_sources": []
  }
}
"""
        with NamedTemporaryFile("w", suffix=".json") as file:
            file.write(config_text)
            file.flush()
            config = load_config(file.name)
            client = AutonomicClient(
                "amp.local",
                mode="amplifier",
                auto_initialize=False,
                config=config,
            )
        client._amplifier_device_id = "00D4"
        client.amplifier.send_commands = lambda commands, timeout=None: ""  # type: ignore[method-assign]

        outputs = client.list_outputs(include_status=False)
        sources = client.list_sources(include_disabled=True)

        self.assertEqual(outputs[0].name, "Library")
        self.assertEqual(sources[6].name, "Desk")
        self.assertEqual(sources[6].id, "00D4:6")
        self.assertEqual(client.source_aliases, {"sg-alpha": "Study"})

    def test_unified_client_prefers_direct_output_name_writes_per_device(self):
        config_text = """
{
  "direct_amplifier": {
    "devices": [
      {
        "device_id": "00D4",
        "host": "amp-a.local",
        "output_start": 1,
        "native_output_start": 1,
        "output_count": 8,
        "source_count": 8,
        "source_base": 0
      },
      {
        "device_id": "6012",
        "host": "amp-b.local",
        "output_start": 9,
        "native_output_start": 9,
        "output_count": 8,
        "source_count": 12,
        "source_base": 0
      }
    ],
    "output_names": {
      "9": "Grill"
    }
  }
}
"""
        with NamedTemporaryFile("w", suffix=".json") as file:
            file.write(config_text)
            file.flush()
            config = load_config(file.name)

        client = AutonomicClient("amp-a.local", mode="amplifier", auto_initialize=False, config=config)
        endpoints = client._direct_endpoints()
        sent: dict[str, list[str]] = {endpoint.host: [] for endpoint in endpoints}

        for endpoint in endpoints:
            endpoint.amplifier.send_ascii = (  # type: ignore[method-assign]
                lambda command, host=endpoint.host: sent[host].append(command) or "OK"
            )

        self.assertEqual(client.set_output_name(1, "Kitchen"), "OK")
        self.assertEqual(client.set_output_name("Grill", "Patio"), "OK")

        self.assertEqual(sent["amp-a.local"], ["1C014B69746368656E"])
        self.assertEqual(sent["amp-b.local"], ["1C09506174696F"])

    def test_hardware_catalog_contains_observed_direct_amp_models(self):
        self.assertEqual(model_name_for_byte(0x87), "M400")
        self.assertEqual(model_name_for_byte(0x8E), "M801e")
        self.assertEqual(model_name_for_byte(0x93), "M120e")
        self.assertEqual(model_name_for_byte(0xB0), "M-6250")
        self.assertEqual(model_name_for_byte(0xE9), "MA6")
        self.assertEqual(HARDWARE_MODELS[0x87].output_count, 4)
        self.assertEqual(HARDWARE_MODELS[0x87].source_count, 6)
        self.assertEqual(HARDWARE_MODELS[0x88].output_count, 8)
        self.assertEqual(HARDWARE_MODELS[0x88].source_count, 8)
        self.assertEqual(HARDWARE_MODELS[0x8D].source_count, 6)
        self.assertEqual(HARDWARE_MODELS[0x8E].source_count, 12)
        self.assertTrue(HARDWARE_MODELS[0x93].unstackable)
        self.assertEqual(HARDWARE_MODELS[0x93].output_count, 4)
        self.assertEqual(HARDWARE_MODELS[0x93].source_count, 2)
        self.assertEqual(HARDWARE_MODELS[0x9B].source_count, 3)
        self.assertTrue(HARDWARE_MODELS[0xE1].is_mms)
        self.assertTrue(HARDWARE_MODELS[0xE2].is_integrated_mms)
        self.assertEqual(HARDWARE_MODELS[0xE2].physical_source_offset, 1)
        self.assertEqual(HARDWARE_MODELS[0xB0].output_count, 8)
        self.assertEqual(HARDWARE_MODELS[0xB0].source_count, 8)
        self.assertEqual(HARDWARE_MODELS[0xB0].mrad_volume_max, 80)
        self.assertEqual(HARDWARE_MODELS[0xE9].streaming_source_count, 3)
        self.assertEqual(HARDWARE_MODELS[0xE9].source_count, 12)
        self.assertIn((0x52, "Casting_8"), HARDWARE_MODELS[0xE9].observed_extra_source_labels_by_data)

    def test_unified_client_resets_direct_amp_defaults_only_in_direct_mode(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp.local",
                        "output_count": 8,
                        "source_count": 12,
                        "source_base": 0,
                    }
                ]
            }
        }
        client = AutonomicClient(
            "amp.local",
            mode="amplifier",
            auto_initialize=False,
            config=config,
        )
        client._amplifier_device_id = "00D4"
        sent: list[str] = []

        def fake_send_commands(commands, *, timeout=None) -> str:
            normalized = [command.upper().rstrip("\r\n") for command in commands]
            sent.extend(normalized)
            return "\n".join("OK" for _command in normalized)

        client.amplifier.send_commands = fake_send_commands  # type: ignore[method-assign]

        response = client.reset_all_to_defaults()

        self.assertEqual(response, "\n".join(["OK"] * 32))
        self.assertEqual(sent[0], "02FF00")
        self.assertIn("1C015A6F6E652031", sent)
        self.assertIn("1C085A6F6E652038", sent)
        self.assertIn("32FF0500", sent)
        self.assertIn("32FF0B00", sent)
        self.assertEqual(sent[-1], "01FF00")

        sent.clear()
        client.reset_all_outputs_to_defaults(AmplifierResetDefaults(source=32, muted=True), safety_mute=False)

        self.assertIn("03FF20", sent)
        self.assertIn("02FF00", sent)

        sent.clear()
        client.reset_all_to_defaults(clear_remote_sources=True)

        self.assertIn("4FFF0000", sent)
        self.assertIn("4FFF1F00", sent)

        mrad_client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        with self.assertRaises(AutonomicError):
            mrad_client.reset_all_to_defaults()

    def test_unified_client_reset_uses_initialized_mrad_fallback_for_mrad_only_defaults(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp.local",
                        "output_count": 2,
                        "source_count": 2,
                        "source_base": 0,
                    }
                ]
            }
        }
        client = AutonomicClient("amp.local", mode="amplifier", auto_initialize=False, config=config)
        conn = ScriptedConnection()
        client._mrad_client_cache = MirageAudioSystem("amp.local", connection=conn)
        client.amplifier.send_commands = lambda commands, timeout=None: "DIRECT"  # type: ignore[method-assign]

        with patch("autonomic.client._can_connect", return_value=False):
            client.reset_all_to_defaults(AmplifierResetDefaults(power_on_volume=50.0, mono_downmix=True))

        self.assertEqual(
            conn.sent,
            [
                "*",
                "SetClientType PythonSDK",
                "SetEncoding 65001",
                "SetXmlMode Lists",
                "SetHost amp.local",
                "SubscribeEvents false",
                "Power On Zone_1",
                "MonoDownmix True Zone_1",
                "Power On Zone_1",
                "PowerOnVolume 50 Zone_1",
                "Power On Zone_2",
                "MonoDownmix True Zone_2",
                "Power On Zone_2",
                "PowerOnVolume 50 Zone_2",
            ],
        )

    def test_unified_client_resets_direct_source_names_only_in_direct_mode(self):
        client = AutonomicClient("amp.local", mode="amplifier", auto_initialize=False)
        client.amplifier.rename_sources_to_low_level_input_labels = lambda: "OK"  # type: ignore[method-assign]

        self.assertEqual(client.rename_sources_to_low_level_input_labels(), "OK")

        mrad_client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        with self.assertRaises(AutonomicError):
            mrad_client.rename_sources_to_low_level_input_labels()

    def test_unified_client_sets_direct_source_metadata_only_in_direct_mode(self):
        config = {
            "direct_amplifier": {
                "devices": [
                    {
                        "device_id": "00D4",
                        "host": "amp.local",
                        "output_count": 8,
                        "source_count": 8,
                        "source_base": 1,
                    }
                ]
            }
        }
        client = AutonomicClient("amp.local", mode="amplifier", auto_initialize=False, config=config)
        client._amplifier_device_id = "00D4"
        sent: list[str] = []

        def fake_send_commands(commands, *, timeout=None) -> str:
            normalized = [command.upper().rstrip("\r\n") for command in commands]
            sent.extend(normalized)
            return "\n".join("OK" for _command in normalized)

        client.amplifier.send_commands = fake_send_commands  # type: ignore[method-assign]

        client.set_source_metadata(7, 0, "Artist")
        client.set_source_metadata_fields(7, ["Artist", "Album"], refresh=False)
        client.set_source_metadata(7, 2, "Track", output=1)
        client.refresh_source_metadata(7, output=1)
        client.refresh_all_source_metadata(output=1, sources=[7])
        client.refresh_source_name(7, output=1)
        client.refresh_source_details(7, output=1)

        self.assertEqual(
            sent,
            [
                "46FF0200417274697374",
                "47FF0200",
                "46FF0200417274697374",
                "46FF0201416C62756D",
                "46010202547261636B",
                "47010202",
                "47010200",
                "47010201",
                "47010202",
                "47010203",
                "47010200",
                "47010201",
                "47010202",
                "47010203",
                "290102",
                "290102",
                "47010200",
                "47010201",
                "47010202",
                "47010203",
            ],
        )

        mrad_client = AutonomicClient("mrad.local", mode="mrad", auto_initialize=False)
        with self.assertRaises(AutonomicError):
            mrad_client.set_source_metadata("Alpha", 0, "Artist")
        with self.assertRaises(AutonomicError):
            mrad_client.refresh_source_details("Alpha")

if __name__ == "__main__":
    unittest.main()
