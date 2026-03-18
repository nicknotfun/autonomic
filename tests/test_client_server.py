from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path

from autonomic_ma6 import MA6Client, VirtualMA6Device


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ServerThread:
    def __init__(self, state_file: Path, host: str = "127.0.0.1"):
        self.loop = asyncio.new_event_loop()
        self.amscp_port = _find_free_port()
        self.mrad_port = _find_free_port()
        self.discovery_port = _find_free_port()
        self.server = VirtualMA6Device(
            state_file=state_file,
            host=host,
            amscp_port=self.amscp_port,
            mrad_port=self.mrad_port,
            discovery_port=self.discovery_port,
        )
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.start())
        self.loop.run_forever()

    def start(self):
        self.thread.start()
        time.sleep(0.2)

    def stop(self):
        stop_future = asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop)
        stop_future.result(timeout=3)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)


def test_guid_zones_xml_and_status(tmp_path: Path):
    state_file = tmp_path / "ma6_state.json"
    runner = ServerThread(state_file)
    runner.start()

    try:
        client = MA6Client(host="127.0.0.1", amscp_port=runner.amscp_port, mrad_port=runner.mrad_port)
        client.initialize()

        zones_resp = client.list_zones()
        assert len(zones_resp.zones) == 6
        assert all(len(str(z.zoneGuid)) == 36 for z in zones_resp.zones)

        sources_resp = client.list_sources()
        assert len(sources_resp.sources) >= 3

        client.select_zone(guid=zones_resp.zones[0].zoneGuid)
        client.select_source(guid=sources_resp.sources[1].sourceGuid)
        client.volume(47)
        client.play()

        status = client.get_status()
        assert str(status.ZoneGuid) == str(zones_resp.zones[0].zoneGuid)
        assert str(status.SourceGuid) == str(sources_resp.sources[1].sourceGuid)
        assert status.Volume == "47"
        assert status.PlayState == "Playing"

        mrad_status = client.mrad_get_status()
        assert len(str(mrad_status.ZoneGuid)) == 36
    finally:
        runner.stop()

    on_disk = json.loads(state_file.read_text())
    zone = next(z for z in on_disk["zones"].values() if z["zone_guid"] == str(zones_resp.zones[0].zoneGuid))
    assert zone["volume"] == 47
    assert zone["play_state"] == "Playing"


def test_discovery_and_mute_toggle(tmp_path: Path):
    state_file = tmp_path / "ma6_state.json"
    runner = ServerThread(state_file)
    runner.start()

    try:
        client = MA6Client.discover(
            timeout=0.5,
            discovery_port=runner.discovery_port,
            amscp_port=runner.amscp_port,
            network="127.0.0.0/30",
        )
        client.mrad_port = runner.mrad_port
        client.initialize()
        zone_guid = client.list_zones().zones[1].zoneGuid
        client.select_zone(guid=zone_guid)
        client.mute("toggle")
        assert client.get_status().Mute == "True"
    finally:
        runner.stop()
