# Optional live-device smoke tests for MRAD and direct amplifier backends.
from __future__ import annotations

import os
import socket
import unittest

from autonomic import AutonomicClient, MirageAmplifier, MirageAudioSystem


HOST = os.environ.get("AUTONOMIC_TEST_HOST")
HOSTS = tuple(
    host.strip()
    for host in os.environ.get("AUTONOMIC_TEST_HOSTS", "").split(",")
    if host.strip()
)


@unittest.skipUnless(HOST, "set AUTONOMIC_TEST_HOST to run live Autonomic device tests")
class LiveAutonomicDeviceTests(unittest.TestCase):
    def test_live_auto_client_detects_supported_backend(self):
        assert HOST is not None
        client = AutonomicClient(HOST)
        try:
            mode = client.detect_mode()
            self.assertIn(mode, {"mrad", "amplifier"})

            outputs = client.list_outputs()
            sources = client.list_sources()
            self.assertGreater(len(outputs), 0)
            self.assertGreater(len(sources), 0)

            if mode == "amplifier":
                response = client.amplifier.request_all_parameters(1)
                self.assertIsInstance(response, str)
        finally:
            client.close()

    def test_live_mrad_outputs_sources_and_control_plane_roundtrip(self):
        assert HOST is not None
        if not _port_open(HOST, 5006):
            self.skipTest("MRAD port 5006 is not available on this device")

        with MirageAudioSystem(HOST) as mas:
            mas.initialize(host_hint=HOST, subscribe=False)
            outputs = mas.list_outputs()
            sources = mas.list_sources()
            groups = mas.browse_zone_groups()

            self.assertGreater(len(outputs), 0)
            self.assertGreater(len(sources), 0)
            self.assertIsNotNone(groups.total)

            status = mas.get_status(timeout=6, idle_timeout=0.2)
            output_item = outputs[0]
            output = output_item

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

    def test_live_amplifier_device_id_read(self):
        assert HOST is not None
        if not _port_open(HOST, 17037):
            self.skipTest("Amplifier port 17037 is not available on this device")

        amplifier = MirageAmplifier(HOST, timeout=2)
        device_id = amplifier.get_device_id()
        devices = amplifier.discover_devices()
        sources = amplifier.list_sources()
        outputs = amplifier.list_outputs()
        preset_group_map = amplifier.query_preset_group_map()
        preset_group = amplifier.query_preset_group(1)
        source_delays = amplifier.query_output_source_delays(1)
        device_status_info = []
        device_link_info = []
        device_state_info = []
        for device in devices:
            device_status_info.extend(amplifier.query_device_status_info(device.amp_id))
            device_link_info.extend(amplifier.query_device_links(device.amp_id))
            device_state_info.extend(amplifier.query_device_state_info(device.amp_id))
        reset_response = amplifier.reset_all_to_defaults()

        self.assertRegex(device_id, r"^[0-9A-F]{4}$")
        self.assertTrue(any(device.amp_id == device_id for device in devices))
        self.assertGreater(len(sources), 0)
        self.assertGreater(len(outputs), 0)
        self.assertIsNotNone(preset_group_map)
        self.assertTrue(preset_group is None or preset_group.slot == 1)
        self.assertIsInstance(source_delays, list)
        self.assertIsInstance(device_status_info, list)
        self.assertIsInstance(device_link_info, list)
        self.assertIsInstance(device_state_info, list)
        self.assertIsInstance(reset_response, str)


@unittest.skipUnless(len(HOSTS) >= 2, "set AUTONOMIC_TEST_HOSTS to run multi-device live Autonomic tests")
class LiveAutonomicMultiDeviceTests(unittest.TestCase):
    def test_live_multi_direct_client_lists_device_qualified_sources_and_outputs(self):
        primary = HOSTS[0]
        if not all(_port_open(host, 17037) for host in HOSTS[:2]):
            self.skipTest("Amplifier port 17037 is not available on both devices")

        with AutonomicClient(primary, mode="amplifier") as client:
            outputs = client.list_outputs(include_status=False)
            sources = client.list_sources(include_disabled=True)

        self.assertTrue(any(output.attributes.get("deviceId") for output in outputs))
        self.assertTrue(any(source.id and ":" in source.id for source in sources))
        self.assertGreaterEqual(len(outputs), 2)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False
