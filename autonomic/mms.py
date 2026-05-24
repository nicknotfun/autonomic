from __future__ import annotations

from collections.abc import Iterable

from .base import AutonomicClient, ProtocolConnection
from .models import BrowseResponse, CommandResponse, StatusSnapshot
from .protocol import events_to_snapshot, format_assignment, format_command

MMS_PORT = 5004


class MirageMediaServer(AutonomicClient):
    """Client for the Mirage Media Server control protocol on TCP port 5004."""

    def __init__(
        self,
        host: str,
        port: int = MMS_PORT,
        *,
        timeout: float = 3.0,
        connection: ProtocolConnection | None = None,
    ):
        super().__init__(host, port, timeout=timeout, connection=connection)

    def initialize(
        self,
        *,
        instance: str | None = None,
        client_type: str = "PythonSDK",
        client_version: str = "0.1.0.0",
        host_hint: str | None = None,
        xml_lists: bool = True,
        encoding: int = 65001,
        subscribe: bool | Iterable[str] = True,
        options: dict[str, object] | None = None,
    ) -> None:
        self.set_client_type(client_type)
        self.set_client_version(client_version)
        if host_hint:
            self.set_host(host_hint)
        self.set_xml_mode("Lists" if xml_lists else "None")
        self.set_encoding(encoding)
        if instance:
            self.set_instance(instance)
        for name, value in (options or {}).items():
            self.set_option(name, value)
        self.subscribe_events(subscribe)

    def command(self, name: str, *args: object, **request_options: object) -> CommandResponse:
        return self.request(format_command(name, *args), **request_options)

    def set_client_type(self, client_type: str) -> CommandResponse:
        return self.command("SetClientType", client_type)

    def set_client_version(self, version: str) -> CommandResponse:
        return self.command("SetClientVersion", version)

    def set_host(self, host: str) -> CommandResponse:
        return self.command("SetHost", host)

    def set_xml_mode(self, mode: str = "Lists") -> CommandResponse:
        return self.command("SetXmlMode", mode)

    def set_encoding(self, encoding: int = 65001) -> CommandResponse:
        return self.command("SetEncoding", encoding)

    def set_instance(self, instance: str) -> CommandResponse:
        return self.command("SetInstance", instance)

    def set_option(self, name: str, value: object = True) -> CommandResponse:
        return self.command("SetOption", format_assignment(name, value))

    def subscribe_events(self, events: bool | Iterable[str] = True) -> CommandResponse:
        if isinstance(events, bool):
            return self.command("SubscribeEvents", events)
        return self.command("SubscribeEvents", ",".join(events))

    def get_status(self, *, timeout: float | None = None, idle_timeout: float = 0.15) -> StatusSnapshot:
        response = self.command("GetStatus", timeout=timeout, idle_timeout=idle_timeout, collect_events=True)
        return StatusSnapshot(events=response.events, by_source=events_to_snapshot(response.events), raw_lines=response.lines)

    def play(self) -> CommandResponse:
        return self.command("Play")

    def pause(self) -> CommandResponse:
        return self.command("Pause")

    def stop(self) -> CommandResponse:
        return self.command("Stop")

    def play_pause(self) -> CommandResponse:
        return self.command("PlayPause")

    def skip_next(self) -> CommandResponse:
        return self.command("SkipNext")

    def skip_previous(self) -> CommandResponse:
        return self.command("SkipPrevious")

    def seek(self, seconds: int) -> CommandResponse:
        return self.command("Seek", seconds)

    def thumbs_up(self) -> CommandResponse:
        return self.command("ThumbsUp")

    def thumbs_down(self) -> CommandResponse:
        return self.command("ThumbsDown")

    def set_stars(self, stars: int) -> CommandResponse:
        if not -1 <= stars <= 5:
            raise ValueError("stars must be between -1 and 5")
        return self.command("SetStars", stars)

    def set_volume(self, volume: int) -> CommandResponse:
        if not 0 <= volume <= 50:
            raise ValueError("MMS output volume must be between 0 and 50")
        return self.command("SetVolume", volume)

    def mute(self, state: bool | str = "toggle") -> CommandResponse:
        return self.command("Mute", state)

    def browse(self, container: str, start: int | None = None, count: int | None = None, *extra: object) -> BrowseResponse:
        command_name = container if container.startswith("Browse") else f"Browse{container}"
        response = self.command(command_name, start, count, *extra)
        if response.payload is None:
            raise ValueError(f"{command_name} did not return a browse response")
        return response.payload

    def browse_albums(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Albums", start, count)

    def browse_artists(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Artists", start, count)

    def browse_composers(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Composers", start, count)

    def browse_favorites(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Favorites", start, count)

    def browse_genres(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Genres", start, count)

    def browse_now_playing(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("NowPlaying", start, count)

    def browse_picklist(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Picklist", start, count)

    def browse_playlists(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Playlists", start, count)

    def browse_radio_sources(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("RadioSources", start, count)

    def browse_titles(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Titles", start, count)

    def browse_top_menu(
        self,
        start: int | None = None,
        count: int | None = None,
        *,
        item_guid: str | None = None,
    ) -> BrowseResponse:
        if item_guid is not None:
            return self.browse("TopMenu", start, count, format_assignment("itemGuid", item_guid))
        return self.browse("TopMenu", start, count)

    def browse_service_accounts(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("ServiceAccounts", start, count)

    def browse_instances(self, start: int | None = None, count: int | None = None) -> BrowseResponse:
        return self.browse("Instances", start, count)

    def ack_pick_item(self, guid: str) -> CommandResponse:
        return self.command("AckPickItem", guid)

    def ack_button(self, guid: str, button: str, value: str | None = None) -> CommandResponse:
        return self.command("AckButton", guid, button, value)

    def back(self, pages: int = 1) -> CommandResponse:
        return self.command("Back", pages)

    def set_music_filter(
        self,
        tag: str | None = None,
        value: str | None = None,
        *,
        search: str | None = None,
        clear: bool = False,
    ) -> CommandResponse:
        if clear:
            return self.command("SetMusicFilter", "Clear")
        if search is not None:
            return self.command("SetMusicFilter", format_assignment("Search", search))
        if tag is None or value is None:
            raise ValueError("provide tag and value, search, or clear=True")
        return self.command("SetMusicFilter", format_assignment(tag, value))

    def clear_music_filter(self) -> CommandResponse:
        return self.set_music_filter(clear=True)

    def set_radio_filter(self, tag: str | None = None, value: str | None = None, *, clear: bool = False) -> CommandResponse:
        if clear:
            return self.command("SetRadioFilter", "Clear")
        if tag is None or value is None:
            raise ValueError("provide tag and value or clear=True")
        return self.command("SetRadioFilter", format_assignment(tag, value))

    def clear_radio_filter(self) -> CommandResponse:
        return self.set_radio_filter(clear=True)

    def set_service_account(self, service: str, account: str, *, latch_to_output: bool | None = None) -> CommandResponse:
        return self.command("SetServiceAccount", service, account, latch_to_output)

    def clear_service_account(self, service: str = "Clear", *, latch_to_output: bool = False) -> CommandResponse:
        return self.command("SetServiceAccount", service, "Clear", latch_to_output)

    def store_preset(self, name: str | None = None) -> CommandResponse:
        return self.command("StorePreset", name)

    def recall_preset(self, name_or_guid: str) -> CommandResponse:
        return self.command("RecallPreset", name_or_guid)

    def play_album(self, guid_or_name: str, enqueue: bool = False) -> CommandResponse:
        return self.command("PlayAlbum", guid_or_name, enqueue)

    def play_artist(self, guid_or_name: str, enqueue: bool = False) -> CommandResponse:
        return self.command("PlayArtist", guid_or_name, enqueue)

    def play_genre(self, guid_or_name: str, enqueue: bool = False) -> CommandResponse:
        return self.command("PlayGenre", guid_or_name, enqueue)

    def play_playlist(self, guid_or_name: str, enqueue: bool = False, start_guid: str | None = None) -> CommandResponse:
        return self.command("PlayPlaylist", guid_or_name, enqueue, start_guid)

    def play_title(self, guid_or_name: str, enqueue: bool = False) -> CommandResponse:
        return self.command("PlayTitle", guid_or_name, enqueue)

    def play_radio_station(self, guid_or_name: str) -> CommandResponse:
        return self.command("PlayRadioStation", guid_or_name)

    def jump_to_now_playing_item(self, guid_or_index: str | int) -> CommandResponse:
        return self.command("JumpToNowPlayingItem", guid_or_index)

    def remove_now_playing_item(self, guid_or_index: str | int) -> CommandResponse:
        return self.command("RemoveNowPlayingItem", guid_or_index)

    def send_keys(self, ir_key: str | int) -> CommandResponse:
        return self.command("SendKeys", ir_key)
