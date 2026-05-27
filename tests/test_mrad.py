# Tests for MRAD/MAS browse, status, grouping, and control commands.
from __future__ import annotations

import unittest

from autonomic import AutonomicOutput, AutonomicSource, AutonomicZoneGroup, MRADCommandHelp, MirageAudioSystem

from helpers import ScriptedConnection


class MirageAudioSystemTests(unittest.TestCase):
    def test_initialize_and_zone_source_controls(self):
        conn = ScriptedConnection()
        mas = MirageAudioSystem("mrad.local", connection=conn)

        mas.initialize(client_type="UnitTest", host_hint="mrad.example", subscribe=["Power", "Volume"])
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
                "SetHost mrad.example",
                "SubscribeEvents Power,Volume",
            ],
        )
        self.assertIn("SetZone 00010000-a259-4cda-a715-94915436337e", conn.sent)
        self.assertIn("SetSource 00000007-a259-4cda-a715-94915436337e", conn.sent)
        self.assertIn("Volume 24", conn.sent)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("VolumeUp Zone_1", conn.sent)
        self.assertIn("Mute toggle Zone_1", conn.sent)

    def test_session_configuration_commands_use_mrad_syntax(self):
        conn = ScriptedConnection(
            {
                "Banner": [
                    "Autonomic Controls MRAD Bridge version 8.3.20260518.1 Release.",
                    "Server=ACE14F006012",
                ]
            }
        )
        mas = MirageAudioSystem("mrad.local", connection=conn)

        mas.set_client_type("UnitTest")
        mas.set_client_version("1.2.3")
        mas.set_encoding(65001)
        mas.set_host("mrad.example")
        mas.set_xml_mode("Lists")
        mas.set_xml_mode(False)
        eol_response = mas.set_response_eol_zero()
        banner = mas.banner()

        self.assertEqual(eol_response.lines, [])
        self.assertEqual(conn.response_delimiter, b"\x00")
        self.assertEqual(banner[-1], "Server=ACE14F006012")
        self.assertEqual(
            conn.sent,
            [
                "SetClientType UnitTest",
                "SetClientVersion 1.2.3",
                "SetEncoding 65001",
                "SetHost mrad.example",
                "SetXmlMode Lists",
                "SetXmlMode None",
                "SetResponseEolZero",
                "Banner",
            ],
        )
        with self.assertRaises(ValueError):
            mas.set_xml_mode("xml please")  # type: ignore[arg-type]

    def test_session_mode_commands_use_mrad_help_catalog_names(self):
        conn = ScriptedConnection()
        mas = MirageAudioSystem("mrad.local", connection=conn)

        mas.enter_command_mode()
        mas.toggle_passthrough_mode()
        mas.enter_passthrough_mode()
        mas.clear_terminal()
        mas.exit_session()

        self.assertEqual(
            conn.sent,
            [
                "!Autonomic",
                "*Autonomic",
                "@Autonomic",
                "cls",
                "Exit",
            ],
        )

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
        mas = MirageAudioSystem("mrad.local", connection=conn)

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
        mas = MirageAudioSystem("mrad.local", connection=conn)
        output = mas.list_outputs()[0]

        mas.set_output_power(output, True)
        output.set_is_on(False)

        self.assertEqual(output.is_on, False)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power Off Zone_1", conn.sent)
        self.assertFalse(hasattr(mas, "enable_output"))
        self.assertFalse(hasattr(output, "enable"))

    def test_versions_identify_party_mode_and_group_helpers(self):
        conn = ScriptedConnection(
            {
                "GetVersions": [
                    'Versions "AMP:ACE14F0055B4 SKU:M6250 FW:6.3.2.0,AMP:ACE14F006012 SKU:MA6 FW:8.3.166.0"'
                ],
                "BrowseZoneGroups": [
                    '<ZoneGroups total="1" start="1" more="false">'
                    '<ZoneGroup guid="gg1" name="ZG_1" sId="sg7" sGuid="source-guid">'
                    '<vol><zone eventId="Zone_1" guid="zg1" name="Office" on="1" volume="22" mute="0" /></vol>'
                    '<src><zone eventId="Zone_1" guid="zg1" name="Office" on="1" /></src>'
                    '<Sources><Source guid="sg7" name="Main" sId="7" /></Sources>'
                    "</ZoneGroup>"
                    "</ZoneGroups>"
                ],
                "BrowseZoneGroup Zone_1": [
                    '<ZoneGroups total="1" start="1" more="false">'
                    '<ZoneGroup guid="gg1" name="ZG_1"><vol><zone eventId="Zone_1" guid="zg1" name="Office" /></vol></ZoneGroup>'
                    "</ZoneGroups>"
                ],
                "BrowseZonesForGroup gg1": [
                    '<Zones total="1" start="1" more="false">'
                    '<Zone guid="zg1" name="Office" id="Zone_1" isOn="True" />'
                    "</Zones>"
                ],
                "BrowsePartyModeInclude": [
                    '<PartyModeInclude total="1" start="1" more="false">'
                    '<PartyModeInfo guid="zg1" name="Office" enabled="true" hardGroupGuid="hg1" />'
                    "</PartyModeInclude>"
                ],
            }
        )
        mas = MirageAudioSystem("mrad.local", connection=conn)

        versions = mas.get_versions()
        groups = mas.list_zone_groups()
        selected_groups = mas.list_zone_group("Zone_1")
        group_zones = mas.list_zones_for_group(groups[0])
        party_rows = mas.list_party_mode_include()
        mas.identify_zone("Zone_1")
        mas.set_party_mode(True)
        mas.party_mode("toggle", "Zone_1")

        self.assertEqual([(item.identifier, item.sku, item.firmware) for item in versions], [
            ("ACE14F0055B4", "M6250", "6.3.2.0"),
            ("ACE14F006012", "MA6", "8.3.166.0"),
        ])
        self.assertIsInstance(groups[0], AutonomicZoneGroup)
        self.assertEqual(groups[0].guid, "gg1")
        self.assertEqual(groups[0].volume_outputs[0].id, "Zone_1")
        self.assertTrue(groups[0].volume_outputs[0].is_on)
        self.assertEqual(groups[0].sources[0].name, "Main")
        self.assertEqual(selected_groups[0].name, "ZG_1")
        self.assertEqual(group_zones[0].name, "Office")
        self.assertEqual(party_rows[0].name, "Office")
        self.assertTrue(party_rows[0].enabled)
        self.assertEqual(party_rows[0].hard_group_guid, "hg1")
        self.assertIn("BrowseZoneGroup Zone_1", conn.sent)
        self.assertIn("BrowseZonesForGroup gg1", conn.sent)
        self.assertIn("IdentifyZone Zone_1", conn.sent)
        self.assertIn("PartyMode On", conn.sent)
        self.assertIn("PartyMode Toggle Zone_1", conn.sent)

    def test_help_and_command_catalog_parse_wrapped_mrad_entries(self):
        conn = ScriptedConnection(
            {
                "help": [
                    "Autonomic Controls MRAD Bridge version 8.3.20260518.1 Release.",
                    "Commands:",
                    "BrowseZoneGroups       - Returns a list of active Zone groups",
                    "SetPartyModeInclude    - Include or Exclude Zone from partymode [True |",
                    "False]",
                    "<ToggleState> <optionalZoneNameOrIdOrGuidOrGroupGuid>",
                    "SetSourceForGroup      - Selects the active source by GUID or Name or Id",
                    "and sets all zones in the active zone's zone",
                    "group to that source.",
                    "<guidOrNameOrId>",
                    "SetZoneGroup           - Grouping and ungrouping zones.",
                    "<zoneNameOrIdOrGuidOrGroupGuid> <commaDelimGuidsSelected> <optionalTargetSource>",
                ],
                "help SetZoneGroup": [
                    "SetZoneGroup           - Grouping and ungrouping zones.",
                    "<zoneNameOrIdOrGuidOrGroupGuid> <commaDelimGuidsSelected> <optionalTargetSource>",
                ],
            }
        )
        mas = MirageAudioSystem("mrad.local", connection=conn)

        lines = mas.help()
        catalog = mas.command_catalog()
        specific_help = mas.help_text("SetZoneGroup")
        specific_entry = mas.command_help("SetZoneGroup")
        by_command = {item.command: item for item in catalog}

        self.assertEqual(lines[0], "Commands:")
        self.assertIsInstance(catalog[0], MRADCommandHelp)
        self.assertEqual(by_command["BrowseZoneGroups"].description, "Returns a list of active Zone groups")
        self.assertEqual(
            by_command["SetPartyModeInclude"].description,
            "Include or Exclude Zone from partymode [True | False]",
        )
        self.assertEqual(
            by_command["SetPartyModeInclude"].usage,
            ("<ToggleState> <optionalZoneNameOrIdOrGuidOrGroupGuid>",),
        )
        self.assertEqual(
            by_command["SetSourceForGroup"].description,
            "Selects the active source by GUID or Name or Id and sets all zones in the active zone's zone group to that source.",
        )
        self.assertEqual(
            by_command["SetZoneGroup"].usage,
            ("<zoneNameOrIdOrGuidOrGroupGuid> <commaDelimGuidsSelected> <optionalTargetSource>",),
        )
        self.assertEqual(specific_entry.command, "SetZoneGroup")
        self.assertEqual(
            specific_entry.usage,
            ("<zoneNameOrIdOrGuidOrGroupGuid> <commaDelimGuidsSelected> <optionalTargetSource>",),
        )
        self.assertIn("SetZoneGroup", specific_help)
        self.assertEqual(conn.sent, ["help", "help", "help SetZoneGroup", "help SetZoneGroup"])

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
        mas = MirageAudioSystem("mrad.local", connection=conn)

        response = mas.set_output_volume("Zone_1", 18)

        self.assertEqual(response.first_line, "OK")
        self.assertEqual(conn.sent, ["Power On Zone_1", "Volume 18 Zone_1", "Power On Zone_1", "Volume 18 Zone_1"])

    def test_active_zone_controls_power_selected_zone(self):
        conn = ScriptedConnection()
        mas = MirageAudioSystem("mrad.local", connection=conn)

        mas.set_zone("Zone_9")
        mas.set_source("sg-main")
        mas.max_volume(80)

        self.assertEqual(
            conn.sent,
            [
                "SetZone Zone_9",
                "Power On Zone_9",
                "SetSource sg-main",
                "Power On Zone_9",
                "MaxVolume 80",
            ],
        )

    def test_output_audio_controls_use_mrad_commands_and_ranges(self):
        conn = ScriptedConnection()
        mas = MirageAudioSystem("mrad.local", connection=conn)

        mas.set_output_bass("Zone_1", -3)
        mas.output_bass_up("Zone_1")
        mas.output_bass_down("Zone_1")
        mas.set_output_treble("Zone_1", 4)
        mas.output_treble_up("Zone_1")
        mas.output_treble_down("Zone_1")
        mas.set_output_balance("Zone_1", -10)
        mas.output_balance_left("Zone_1")
        mas.output_balance_right("Zone_1")
        mas.set_output_gain("Zone_1", 5)
        mas.set_output_max_volume("Zone_1", 90)
        mas.set_output_loudness("Zone_1", True)
        mas.set_output_mono_downmix("Zone_1", False)
        mas.set_output_power_on_volume("Zone_1", 12)
        mas.set_output_name("Zone_1", "Office West")
        mas.set_output_icon("Zone_1", "Kitchen")
        mas.output_gain_up("Zone_1")
        mas.output_gain_down("Zone_1")
        mas.get_output_volume("Zone_1")
        mas.get_output_mute("Zone_1")
        mas.bass(-1)
        mas.treble(2)
        mas.balance(0)
        mas.zone_gain(3)
        mas.max_volume(70)
        mas.loudness("toggle")
        mas.mono_downmix(True)
        mas.power_on_volume(0)

        self.assertEqual(
            conn.sent,
            [
                "Power On Zone_1",
                "Bass -3 Zone_1",
                "Power On Zone_1",
                "BassUp Zone_1",
                "Power On Zone_1",
                "BassDown Zone_1",
                "Power On Zone_1",
                "Treble 4 Zone_1",
                "Power On Zone_1",
                "TrebleUp Zone_1",
                "Power On Zone_1",
                "TrebleDown Zone_1",
                "Power On Zone_1",
                "Balance -10 Zone_1",
                "Power On Zone_1",
                "BalanceLeft Zone_1",
                "Power On Zone_1",
                "BalanceRight Zone_1",
                "Power On Zone_1",
                "ZoneGain 5 Zone_1",
                "Power On Zone_1",
                "MaxVolume 90 Zone_1",
                "Power On Zone_1",
                "Loudness True Zone_1",
                "Power On Zone_1",
                "MonoDownmix False Zone_1",
                "Power On Zone_1",
                "PowerOnVolume 12 Zone_1",
                'ZoneName "Office West" Zone_1',
                "ZoneIcon Kitchen Zone_1",
                "Power On Zone_1",
                "ZoneGainUp Zone_1",
                "Power On Zone_1",
                "ZoneGainDown Zone_1",
                "GetVolume Zone_1",
                "GetMute Zone_1",
                "Bass -1",
                "Treble 2",
                "Balance 0",
                "ZoneGain 3",
                "MaxVolume 70",
                "Loudness Toggle",
                "MonoDownmix True",
                "PowerOnVolume 0",
            ],
        )

        with self.assertRaises(ValueError):
            mas.set_output_bass("Zone_1", -13)
        with self.assertRaises(ValueError):
            mas.set_output_balance("Zone_1", 21)
        with self.assertRaises(ValueError):
            mas.set_output_gain("Zone_1", 13)
        with self.assertRaises(ValueError):
            mas.set_output_loudness("Zone_1", "sometimes")

    def test_source_diagnostics_and_paging_commands_use_mrad_syntax(self):
        conn = ScriptedConnection(
            {
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
        mas = MirageAudioSystem("mrad.local", connection=conn)

        mas.ping()
        mas.echo("hello there")
        mas.uptime()
        mas.time("s")
        mas.sync_time()
        mas.log_comment("unit test")
        mas.set_client_version("1.2.3")
        mas.set_source_by_name("engine-source")
        mas.set_source_name("sg-main", "Main Input")
        mas.set_active_source_name("Active Input")
        mas.set_source_icon("sg-main", "Disc")
        mas.set_active_source_icon("Radio")
        page_up = mas.browse_page_up()
        page_down = mas.browse_page_down()

        self.assertTrue(mas.ping_ok())
        self.assertEqual(mas.echo_text("hello there"), "hello there")
        self.assertEqual(mas.uptime_text(), "0.13:19:03")
        self.assertEqual(mas.time_text("s"), "2026-05-26T12:49:45")
        self.assertEqual(page_up.kind, "Sources")
        self.assertEqual(page_down.kind, "Zones")
        self.assertIn("Ping", conn.sent)
        self.assertIn("Echo \"hello there\"", conn.sent)
        self.assertIn("Uptime", conn.sent)
        self.assertIn("Time s", conn.sent)
        self.assertIn("SyncTime", conn.sent)
        self.assertIn("LogComment \"unit test\"", conn.sent)
        self.assertIn("SetClientVersion 1.2.3", conn.sent)
        self.assertIn("SetSourceByName engine-source", conn.sent)
        self.assertIn("SourceName \"Main Input\" sg-main", conn.sent)
        self.assertIn("SourceName \"Active Input\"", conn.sent)
        self.assertIn("SourceIcon Disc sg-main", conn.sent)
        self.assertIn("SourceIcon Radio", conn.sent)
        self.assertIn("BrowsePageUp", conn.sent)
        self.assertIn("BrowsePageDown", conn.sent)

    def test_group_party_and_zone_setting_commands_use_mrad_syntax(self):
        conn = ScriptedConnection()
        mas = MirageAudioSystem("mrad.local", connection=conn)

        mas.set_party_mode_include(True, "Zone_1")
        mas.set_party_mode_include("toggle")
        mas.set_source_for_group("sg7")
        mas.set_zone_group("Zone_1", ["zg1", "zg2"], "sg7")
        mas.set_zone_group("gg1", "zg1,zg2")
        mas.set_zone_group_timer("23:30", "gg1")

        self.assertEqual(
            conn.sent,
            [
                "SetPartyModeInclude True Zone_1",
                "SetPartyModeInclude Toggle",
                "SetSourceForGroup sg7",
                "SetZoneGroup Zone_1 zg1,zg2 sg7",
                "SetZoneGroup gg1 zg1,zg2",
                "SetZoneGroupTimer 23:30 gg1",
            ],
        )

    def test_output_audio_control_retries_after_zone_off_error(self):
        class ZoneOffOnceConnection(ScriptedConnection):
            def send_line(self, command: str) -> None:
                self.sent.append(command)
                self.calls[command] += 1
                if command == "Bass 1 Zone_1" and self.calls[command] == 1:
                    self._pending.append('Bass Error "Zone 1 is off."')
                    return
                self._pending.append("OK")

        conn = ZoneOffOnceConnection()
        mas = MirageAudioSystem("mrad.local", connection=conn)

        response = mas.set_output_bass("Zone_1", 1)

        self.assertEqual(response.first_line, "OK")
        self.assertEqual(conn.sent, ["Power On Zone_1", "Bass 1 Zone_1", "Power On Zone_1", "Bass 1 Zone_1"])

    def test_list_outputs_can_hydrate_mrad_status(self):
        conn = ScriptedConnection(
            {
                "BrowseAllZones": [
                    '<Zones total="1" start="1" more="false">'
                    '<Zone guid="zg-browse" name="Office" id="Zone_1" sourceName="Old" />'
                    "</Zones>"
                ],
                "GetStatus": [
                    "MRAD.ReportState Zone_1 ZoneId=1",
                    "MRAD.ReportState Zone_1 ZoneName=Office Live",
                    "MRAD.ReportState Zone_1 ZoneGuid=zg-live",
                    "MRAD.ReportState Zone_1 PowerOn=True",
                    "MRAD.ReportState Zone_1 Mute=False",
                    "MRAD.ReportState Zone_1 Volume=42",
                    "MRAD.ReportState Zone_1 MinVolume=0",
                    "MRAD.ReportState Zone_1 MinMinVolume=0",
                    "MRAD.ReportState Zone_1 MaxVolume=88",
                    "MRAD.ReportState Zone_1 MaxMaxVolume=100",
                    "MRAD.ReportState Zone_1 Bass=-2",
                    "MRAD.ReportState Zone_1 Treble=3",
                    "MRAD.ReportState Zone_1 Balance=-4",
                    "MRAD.ReportState Zone_1 ZoneGain=5",
                    "MRAD.ReportState Zone_1 LoudnessEnabled=True",
                    "MRAD.ReportState Zone_1 MonoDownmix=False",
                    "MRAD.ReportState Zone_1 PowerOnVolume=10",
                    "MRAD.ReportState Zone_1 AdjustingVolume=False",
                    "MRAD.ReportState Zone_1 DeviceType=MA6",
                    "MRAD.ReportState Zone_1 DoNotDisturb=False",
                    "MRAD.ReportState Zone_1 GainMode=Variable",
                    "MRAD.ReportState Zone_1 IconId=Zone",
                    "MRAD.ReportState Zone_1 PartyMode=Off",
                    "MRAD.ReportState Zone_1 SourceId=7",
                    "MRAD.ReportState Zone_1 SourceName=COAX1",
                    "MRAD.ReportState Zone_1 QualifiedSourceName=COAX1@ACE14F006012",
                    "MRAD.ReportState Zone_1 ZoneExclusiveSource=False",
                    "MRAD.ReportState Zone_1 ZoneGroupId=group-1",
                    "MRAD.ReportState Zone_1 ZoneGroupName=ZG_1",
                    "MRAD.ReportState Zone_1 ZoneGroupPower=True",
                    "MRAD.ReportState Zone_1 ZoneGroupSource=False",
                    "MRAD.ReportState Zone_1 ZoneGroupVolume=True",
                    "MRAD.ReportState Zone_1 ZoneIsLocked=False",
                    "OK",
                ],
            }
        )
        mas = MirageAudioSystem("mrad.local", connection=conn)

        output = mas.list_outputs(include_status=True)[0]

        self.assertEqual(output.id, "Zone_1")
        self.assertEqual(output.guid, "zg-live")
        self.assertEqual(output.name, "Office Live")
        self.assertTrue(output.is_on)
        self.assertFalse(output.muted)
        self.assertEqual(output.volume, 42)
        self.assertEqual(output.min_volume, 0)
        self.assertEqual(output.min_min_volume, 0)
        self.assertEqual(output.max_volume, 88)
        self.assertEqual(output.max_max_volume, 100)
        self.assertEqual(output.bass, -2)
        self.assertEqual(output.treble, 3)
        self.assertEqual(output.balance, -4)
        self.assertEqual(output.gain, 5)
        self.assertTrue(output.loudness)
        self.assertFalse(output.mono_downmix)
        self.assertEqual(output.power_on_volume, 10)
        self.assertFalse(output.adjusting_volume)
        self.assertEqual(output.device_type, "MA6")
        self.assertFalse(output.do_not_disturb)
        self.assertEqual(output.gain_mode, "Variable")
        self.assertEqual(output.icon_id, "Zone")
        self.assertEqual(output.party_mode, "Off")
        self.assertEqual(output.source_id, "7")
        self.assertEqual(output.source_name, "COAX1")
        self.assertEqual(output.qualified_source_name, "COAX1@ACE14F006012")
        self.assertFalse(output.zone_exclusive_source)
        self.assertEqual(output.zone_group_id, "group-1")
        self.assertEqual(output.zone_group_name, "ZG_1")
        self.assertTrue(output.zone_group_power)
        self.assertFalse(output.zone_group_source)
        self.assertTrue(output.zone_group_volume)
        self.assertFalse(output.zone_is_locked)

    def test_get_output_status_and_typed_reads_use_mrad_status_snapshot(self):
        conn = ScriptedConnection(
            {
                "GetStatus": [
                    "MRAD.ReportState Zone_1 ZoneId=1",
                    "MRAD.ReportState Zone_1 ZoneName=Office",
                    "MRAD.ReportState Zone_1 PowerOn=True",
                    "MRAD.ReportState Zone_1 Mute=False",
                    "MRAD.ReportState Zone_1 Volume=42",
                    "MRAD.ReportState Zone_1 MaxVolume=88",
                    "MRAD.ReportState Zone_1 Bass=-2",
                    "MRAD.ReportState Zone_1 Treble=3",
                    "MRAD.ReportState Zone_1 Balance=-4",
                    "MRAD.ReportState Zone_1 ZoneGain=5",
                    "MRAD.ReportState Zone_1 LoudnessEnabled=True",
                    "MRAD.ReportState Zone_1 MonoDownmix=False",
                    "MRAD.ReportState Zone_1 PowerOnVolume=10",
                    "MRAD.ReportState Zone_1 SourceId=7",
                    "MRAD.ReportState Zone_1 SourceName=COAX1",
                    "OK",
                ],
            }
        )
        mas = MirageAudioSystem("mrad.local", connection=conn)

        output = mas.get_output_status("Zone_1")

        self.assertEqual(output.id, "Zone_1")
        self.assertEqual(output.name, "Office")
        self.assertTrue(output.is_on)
        self.assertFalse(output.muted)
        self.assertEqual(output.volume, 42)
        self.assertEqual(mas.read_output_volume("Zone_1"), 42)
        self.assertFalse(mas.read_output_mute("Zone_1"))
        self.assertTrue(mas.read_output_power("Zone_1"))
        self.assertEqual(mas.read_output_max_volume("Zone_1"), 88)
        self.assertEqual(mas.read_output_bass("Zone_1"), -2)
        self.assertEqual(mas.read_output_treble("Zone_1"), 3)
        self.assertEqual(mas.read_output_balance("Zone_1"), -4)
        self.assertEqual(mas.read_output_gain("Zone_1"), 5)
        self.assertTrue(mas.read_output_loudness("Zone_1"))
        self.assertFalse(mas.read_output_mono_downmix("Zone_1"))
        self.assertEqual(mas.read_output_power_on_volume("Zone_1"), 10)
        self.assertEqual(mas.read_output_source_id("Zone_1"), "7")
        self.assertEqual(mas.read_output_source_name("Zone_1"), "COAX1")

        conn.sent.clear()
        mas.output_max_volume_up("Zone_1")
        mas.output_max_volume_down("Zone_1", step=2)

        self.assertEqual(
            conn.sent,
            [
                "GetStatus",
                "Power On Zone_1",
                "MaxVolume 89 Zone_1",
                "GetStatus",
                "Power On Zone_1",
                "MaxVolume 86 Zone_1",
            ],
        )

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
        mas = MirageAudioSystem("mrad.local", connection=conn)

        mas.set_all_output_volume(18)
        mas.set_all_output_gain(2)
        mas.mute_all_outputs(True)
        mas.assign_source_to_all_outputs("sg7")

        self.assertIn("Volume 18 Zone_1", conn.sent)
        self.assertIn("Volume 18 Zone_2", conn.sent)
        self.assertNotIn("Volume 18 Zone_3", conn.sent)
        self.assertIn("ZoneGain 2 Zone_1", conn.sent)
        self.assertIn("ZoneGain 2 Zone_2", conn.sent)
        self.assertNotIn("ZoneGain 2 Zone_3", conn.sent)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power On Zone_2", conn.sent)
        self.assertNotIn("Power On Zone_3", conn.sent)
        self.assertIn("MuteAll On", conn.sent)
        self.assertIn("SetSource sg7 false Zone_1", conn.sent)
        self.assertIn("SetSource sg7 false Zone_2", conn.sent)
        self.assertNotIn("SetSource sg7 false Zone_3", conn.sent)
        self.assertNotIn("AllOff", conn.sent)

        conn.sent.clear()
        mas.set_all_output_power(False)
        mas.set_all_output_power(True)

        self.assertIn("AllOff", conn.sent)
        self.assertIn("Power On Zone_1", conn.sent)
        self.assertIn("Power On Zone_2", conn.sent)
        self.assertNotIn("Power On Zone_3", conn.sent)

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
        mas = MirageAudioSystem("mrad.local", connection=conn)

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
        mas = MirageAudioSystem("mrad.local", connection=conn)

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
        mas = MirageAudioSystem("mrad.local", connection=conn)

        zones = mas.browse_all_zones()
        sources = mas.browse_all_sources()
        groups = mas.browse_zone_groups()

        self.assertEqual(zones.items[0].name, "Office")
        self.assertEqual(sources.items[0].guid, "sg7")
        self.assertEqual(groups.attributes["srceId"], "sg7")
        self.assertEqual(groups.items[0].children["vol"][0]["name"], "Office")

if __name__ == "__main__":
    unittest.main()
