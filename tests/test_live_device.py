from __future__ import annotations

import os
import socket
import unittest

from autonomic import MA6Client, MirageAmplifierDiagnostics, MirageAudioSystem, MirageMediaServer


HOST = os.environ.get("AUTONOMIC_TEST_HOST")


@unittest.skipUnless(HOST, "set AUTONOMIC_TEST_HOST to run live Autonomic device tests")
class LiveAutonomicDeviceTests(unittest.TestCase):
    def test_live_auto_client_detects_supported_backend(self):
        assert HOST is not None
        client = MA6Client(HOST, timeout=5)
        try:
            mode = client.detect_mode()
            self.assertIn(mode, {"mrad", "amplifier"})

            client.initialize(host_hint=HOST)
            outputs = client.list_outputs()
            sources = client.list_sources()
            self.assertGreater(len(outputs.items), 0)
            self.assertGreater(len(sources.items), 0)

            if mode == "amplifier":
                response = client.amplifier.request_all_parameters(1)
                self.assertIsInstance(response, str)
        finally:
            client.close()

    def test_live_mrad_outputs_sources_and_state_preserving_controls(self):
        assert HOST is not None
        if not _port_open(HOST, 5006):
            self.skipTest("MRAD port 5006 is not available on this device")

        with MirageAudioSystem(HOST, timeout=5) as mas:
            mas.initialize(host_hint=HOST, subscribe=False)
            outputs = mas.list_outputs()
            sources = mas.list_sources()
            groups = mas.browse_zone_groups()

            self.assertGreater(len(outputs.items), 0)
            self.assertGreater(len(sources.items), 0)
            self.assertIsNotNone(groups.total)

            status = mas.get_status(timeout=6, idle_timeout=0.2)
            output_item = outputs.items[0]
            output = output_item.id or output_item.guid or output_item.name
            self.assertIsNotNone(output)
            assert output is not None

            source = (
                output_item.attributes.get("sGuid")
                or output_item.attributes.get("sId")
                or output_item.attributes.get("sourceName")
            )
            if source:
                mas.assign_source_to_output(source, output)

            output_state = status.by_source.get(str(output), {})
            if "Volume" in output_state:
                mas.set_output_volume(output, int(output_state["Volume"]))
            if "Mute" in output_state:
                mas.set_output_mute(output, output_state["Mute"])
            if "PowerOn" in output_state:
                mas.set_output_enabled(output, output_state["PowerOn"].lower() == "true")

    def test_live_mms_browse_and_single_socket_mrad(self):
        assert HOST is not None
        if not _port_open(HOST, 5004):
            self.skipTest("MMS port 5004 is not available on this device")

        with MirageMediaServer(HOST, timeout=5) as mms:
            mms.set_client_type("PythonSDKLiveTest")
            mms.set_encoding(65001)
            mms.set_xml_mode("Lists")

            albums = mms.browse_albums(1, 1)
            self.assertGreaterEqual(len(albums.items), 0)
            self.assertEqual(albums.kind, "Albums")

            mms.set_option("Supports_SingleSocket", True)
            single_socket_mrad = MirageAudioSystem(HOST, mms_client=mms, single_socket=True, timeout=5)
            outputs = single_socket_mrad.list_outputs()
            self.assertGreater(len(outputs.items), 0)

    def test_live_amplifier_device_id_read(self):
        assert HOST is not None
        if not _port_open(HOST, 17037):
            self.skipTest("Amplifier port 17037 is not available on this device")

        device_id = MirageAmplifierDiagnostics(HOST, timeout=2).get_device_id()
        self.assertRegex(device_id, r"^[0-9A-F]{4}$")


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False
