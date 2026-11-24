"""Unit tests for FastAPI bridge server, WebSocket broadcasts, and media keys."""

from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from metrolite.audio.media_keys import MediaKeysListener
from metrolite.audio.player import AudioPlayer
from metrolite.audio.queue import QueueManager
from metrolite.bridge.server import (
    BridgeController,
    BridgeServer,
    WebSocketManager,
    create_app,
    find_available_port,
    remove_bridge_file,
    write_bridge_file,
)
from metrolite.library.downloader import DownloadManager
from metrolite.library.manager import LibraryManager
from metrolite.store.db import SCHEMA_SQL
from metrolite.ytm.client import Track, YTMusicClient


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    """Initialize a temporary SQLite database for bridge tests."""
    db_file = tmp_path / "bridge_test.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.executescript(SCHEMA_SQL)
    conn.close()
    return db_file


@pytest.fixture
def mock_controller(test_db_path: Path) -> BridgeController:
    """Create a BridgeController with mocked external audio and network dependencies."""
    mock_mpv = MagicMock()
    mock_mpv.pause = False
    mock_mpv.idle_active = False
    mock_mpv.core_idle = False
    mock_mpv.time_pos = 0.0
    mock_mpv.duration = 180.0
    mock_mpv.volume = 80.0

    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock(side_effect=lambda vid: f"https://mock-stream/{vid}")

    mock_player = AudioPlayer(resolver=mock_resolver, mpv_instance=mock_mpv)

    mock_ytm = MagicMock(spec=YTMusicClient)
    mock_ytm.get_home_sections = AsyncMock(
        return_value=[
            {
                "title": "Quick Picks",
                "contents": [{"video_id": "t1", "title": "Track 1", "artist": "Artist 1"}],
            }
        ]
    )
    mock_ytm.search = AsyncMock(
        return_value=[
            Track(video_id="t2", title="Track 2", artist="Artist 2"),
        ]
    )
    mock_ytm.get_watch_radio = AsyncMock(
        return_value=[
            Track(video_id="radio1", title="Radio 1", artist="Radio Artist"),
        ]
    )

    mock_downloader = MagicMock(spec=DownloadManager)
    mock_downloader.db_path = test_db_path
    mock_downloader.queue_download = MagicMock()
    mock_downloader.delete_download = AsyncMock(return_value=True)
    mock_downloader.list_downloads = AsyncMock(return_value=[])
    mock_downloader.stop = MagicMock()

    mock_media_keys = MediaKeysListener(enabled=False)

    controller = BridgeController(
        player=mock_player,
        queue=QueueManager(db_path=test_db_path, ytm_client=mock_ytm, auto_persist=False),
        library=LibraryManager(db_path=test_db_path),
        downloader=mock_downloader,
        ytm=mock_ytm,
        media_keys=mock_media_keys,
        db_path=test_db_path,
        ws_manager=WebSocketManager(),
    )
    return controller


@pytest.fixture
def test_token() -> str:
    return "test-secret-token-xyz-12345"


@pytest.fixture
def client(mock_controller: BridgeController, test_token: str) -> TestClient:
    """Create a FastAPI TestClient with the configured controller and token."""
    app = create_app(controller=mock_controller, token=test_token)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Port and Bridge File Utility Tests
# ---------------------------------------------------------------------------


def test_find_available_port() -> None:
    """Test port finding and collision avoidance by incrementing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        busy_port = s.getsockname()[1]

        # Starting exactly at busy_port should return busy_port + 1 or higher
        avail = find_available_port("127.0.0.1", start_port=busy_port)
        assert avail > busy_port


def test_write_and_remove_bridge_file(tmp_path: Path) -> None:
    """Test atomic writing and cleanup of bridge.json."""
    bridge_file = tmp_path / "metrolite" / "bridge.json"
    write_bridge_file(bridge_file, "127.0.0.1", 8766, "token_abc")

    assert bridge_file.is_file()
    data = json.loads(bridge_file.read_text(encoding="utf-8"))
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 8766
    assert data["token"] == "token_abc"

    remove_bridge_file(bridge_file)
    assert not bridge_file.is_file()


# ---------------------------------------------------------------------------
# Authentication & Security Tests
# ---------------------------------------------------------------------------


def test_health_check_exempt_from_token(client: TestClient) -> None:
    """Test GET /health succeeds without requiring authentication token."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["app"] == "MetroLite"
    assert data["version"] == "0.1.0"


def test_protected_endpoints_require_token(client: TestClient, test_token: str) -> None:
    """Test protected endpoints reject unauthenticated or wrongly authenticated requests."""
    # 1. No token -> 401
    res1 = client.get("/queue")
    assert res1.status_code == 401

    # 2. Invalid token -> 401
    res2 = client.get("/queue", headers={"X-Bridge-Token": "bad-token"})
    assert res2.status_code == 401

    # 3. Valid token via X-Bridge-Token header -> 200
    res3 = client.get("/queue", headers={"X-Bridge-Token": test_token})
    assert res3.status_code == 200

    # 4. Valid token via Authorization: Bearer -> 200
    res4 = client.get("/queue", headers={"Authorization": f"Bearer {test_token}"})
    assert res4.status_code == 200

    # 5. Valid token via ?token= query param -> 200
    res5 = client.get(f"/queue?token={test_token}")
    assert res5.status_code == 200


# ---------------------------------------------------------------------------
# WebSocket Tests
# ---------------------------------------------------------------------------


def test_websocket_auth_rejection(client: TestClient) -> None:
    """Test WebSocket connection without valid token is rejected."""
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws"):
        pass

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws?token=invalid"):
        pass


def test_websocket_connection_and_tick(client: TestClient, test_token: str) -> None:
    """Test WebSocket connects with token and receives state updates and ping/pong."""
    with client.websocket_connect(f"/ws?token={test_token}") as ws:
        msg1 = ws.receive_json()
        assert msg1["event"] == "playback_tick"
        assert "time_pos" in msg1["data"]

        msg2 = ws.receive_json()
        assert msg2["event"] == "queue_changed"
        assert "items" in msg2["data"]

        # Test ping -> pong
        ws.send_text("ping")
        pong = ws.receive_json()
        assert pong["event"] == "pong"


# ---------------------------------------------------------------------------
# Player Control REST Endpoints
# ---------------------------------------------------------------------------


def test_player_endpoints(client: TestClient, test_token: str) -> None:
    """Test play, pause, next, prev, seek, and volume REST endpoints."""
    headers = {"X-Bridge-Token": test_token}

    # 1. POST /player/play with video_id
    res_play = client.post("/player/play", json={"video_id": "test_vid_1"}, headers=headers)
    assert res_play.status_code == 200
    assert "current_track" in res_play.json()

    # 2. GET /player/play
    res_get_play = client.get("/player/play", headers=headers)
    assert res_get_play.status_code == 200

    # 3. POST /player/pause
    res_pause = client.post("/player/pause", headers=headers)
    assert res_pause.status_code == 200

    # 4. POST /player/seek
    res_seek = client.post("/player/seek?seconds=45.0", headers=headers)
    assert res_seek.status_code == 200

    # 5. POST /player/volume
    res_vol = client.post("/player/volume?volume=60", headers=headers)
    assert res_vol.status_code == 200
    assert res_vol.json()["volume"] == 60

    # 6. POST /player/next and /player/prev
    res_next = client.post("/player/next", headers=headers)
    assert res_next.status_code == 200

    res_prev = client.post("/player/prev", headers=headers)
    assert res_prev.status_code == 200


# ---------------------------------------------------------------------------
# Queue REST Endpoints
# ---------------------------------------------------------------------------


def test_queue_endpoints(client: TestClient, test_token: str) -> None:
    """Test queue fetch and manipulation actions (set, append, add_next, move, remove, clear)."""
    headers = {"X-Bridge-Token": test_token}

    track_a = {"video_id": "a", "title": "Track A", "artist": "Artist A"}
    track_b = {"video_id": "b", "title": "Track B", "artist": "Artist B"}
    track_c = {"video_id": "c", "title": "Track C", "artist": "Artist C"}

    # 1. Action: set
    res_set = client.post(
        "/queue",
        json={"action": "set", "items": [track_a, track_b], "start_index": 0},
        headers=headers,
    )
    assert res_set.status_code == 200
    assert len(res_set.json()["items"]) == 2

    # 2. GET /queue
    res_get = client.get("/queue", headers=headers)
    assert res_get.status_code == 200
    assert len(res_get.json()["items"]) == 2

    # 3. Action: append
    res_app = client.post(
        "/queue",
        json={"action": "append", "item": track_c},
        headers=headers,
    )
    assert res_app.status_code == 200
    assert len(res_app.json()["items"]) == 3

    # 4. Action: shuffle
    res_shuf = client.post(
        "/queue",
        json={"action": "shuffle", "shuffle": True},
        headers=headers,
    )
    assert res_shuf.status_code == 200
    assert res_shuf.json()["shuffle"] is True

    # 5. Action: repeat
    res_rep = client.post(
        "/queue",
        json={"action": "repeat", "repeat_mode": "all"},
        headers=headers,
    )
    assert res_rep.status_code == 200
    assert res_rep.json()["repeat_mode"] == "all"

    # 6. Action: remove
    res_rem = client.post(
        "/queue",
        json={"action": "remove", "index": 1},
        headers=headers,
    )
    assert res_rem.status_code == 200
    assert len(res_rem.json()["items"]) == 2

    # 7. Action: clear
    res_clr = client.post(
        "/queue",
        json={"action": "clear"},
        headers=headers,
    )
    assert res_clr.status_code == 200
    assert len(res_clr.json()["items"]) == 0


# ---------------------------------------------------------------------------
# Library & Liked Songs REST Endpoints
# ---------------------------------------------------------------------------


def test_liked_endpoints(client: TestClient, test_token: str) -> None:
    """Test liking and unliking tracks and listing liked tracks."""
    headers = {"X-Bridge-Token": test_token}

    track_meta = {
        "video_id": "fav_1",
        "title": "Favorite Song",
        "artist": "Great Artist",
        "duration_s": 210,
    }

    # 1. Toggle like -> True
    res1 = client.post("/liked/fav_1", json={"track_meta": track_meta}, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["is_liked"] is True

    # 2. GET /liked
    res_list = client.get("/liked", headers=headers)
    assert res_list.status_code == 200
    liked_songs = res_list.json()
    assert len(liked_songs) == 1
    assert liked_songs[0]["video_id"] == "fav_1"

    # 3. Toggle like again -> False
    res2 = client.post("/liked/fav_1", headers=headers)
    assert res2.status_code == 200
    assert res2.json()["is_liked"] is False


# ---------------------------------------------------------------------------
# Playlists REST Endpoints
# ---------------------------------------------------------------------------


def test_playlists_endpoints(client: TestClient, test_token: str) -> None:
    """Test playlist creation, track addition, retrieval, and deletion."""
    headers = {"X-Bridge-Token": test_token}

    # 1. POST /playlists
    res_create = client.post(
        "/playlists",
        json={"name": "Night Vibes", "description": "Chill music"},
        headers=headers,
    )
    assert res_create.status_code == 200
    pl = res_create.json()
    pl_id = pl["id"]
    assert pl["name"] == "Night Vibes"

    # 2. GET /playlists
    res_list = client.get("/playlists", headers=headers)
    assert res_list.status_code == 200
    assert any(p["id"] == pl_id for p in res_list.json())

    # 3. POST /playlists/{id}/tracks
    track_to_add = {"video_id": "pl_track_1", "title": "Track 1", "artist": "Artist 1"}
    res_add = client.post(
        f"/playlists/{pl_id}/tracks",
        json={"tracks": [track_to_add]},
        headers=headers,
    )
    assert res_add.status_code == 200
    assert res_add.json()["added"] == 1

    # 4. GET /playlists/{id}/tracks
    res_tracks = client.get(f"/playlists/{pl_id}/tracks", headers=headers)
    assert res_tracks.status_code == 200
    tracks = res_tracks.json()
    assert len(tracks) == 1
    assert tracks[0]["video_id"] == "pl_track_1"

    # 5. DELETE /playlists/{id}/tracks
    res_del = client.delete(
        f"/playlists/{pl_id}/tracks?video_id=pl_track_1",
        headers=headers,
    )
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Downloads REST Endpoints
# ---------------------------------------------------------------------------


def test_downloads_endpoints(
    client: TestClient, test_token: str, mock_controller: BridgeController
) -> None:
    """Test offline download queueing, listing, and deletion."""
    headers = {"X-Bridge-Token": test_token}

    # 1. POST /downloads/{video_id}
    res_post = client.post(
        "/downloads/offline_song_1",
        json={"track_meta": {"title": "Offline Song"}},
        headers=headers,
    )
    assert res_post.status_code == 200
    assert res_post.json()["status"] == "queued"
    mock_controller.downloader.queue_download.assert_called_once()

    # 2. GET /downloads
    res_get = client.get("/downloads", headers=headers)
    assert res_get.status_code == 200
    assert isinstance(res_get.json(), list)

    # 3. DELETE /downloads/{video_id}
    res_del = client.delete("/downloads/offline_song_1", headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "deleted"


# ---------------------------------------------------------------------------
# Settings REST Endpoints
# ---------------------------------------------------------------------------


def test_settings_endpoints(client: TestClient, test_token: str) -> None:
    """Test updating and reading SQLite-backed settings."""
    headers = {"X-Bridge-Token": test_token}

    # 1. PUT /settings
    new_settings = {"theme": "oled", "autoplay_radio": True, "volume": 75}
    res_put = client.put("/settings", json=new_settings, headers=headers)
    assert res_put.status_code == 200
    assert res_put.json() == new_settings

    # 2. GET /settings
    res_get = client.get("/settings", headers=headers)
    assert res_get.status_code == 200
    saved = res_get.json()
    assert saved["theme"] == "oled"
    assert saved["autoplay_radio"] is True
    assert saved["volume"] == 75


# ---------------------------------------------------------------------------
# YouTube Music Metadata Endpoints
# ---------------------------------------------------------------------------


def test_ytm_endpoints(client: TestClient, test_token: str) -> None:
    """Test home sections and search endpoints."""
    headers = {"X-Bridge-Token": test_token}

    # 1. GET /home
    res_home = client.get("/home", headers=headers)
    assert res_home.status_code == 200
    home_data = res_home.json()
    assert len(home_data) > 0
    assert home_data[0]["title"] == "Quick Picks"

    # 2. GET /search
    res_search = client.get("/search?q=coldplay&filter=songs", headers=headers)
    assert res_search.status_code == 200
    results = res_search.json()
    assert len(results) > 0
    assert results[0]["title"] == "Track 2"


# ---------------------------------------------------------------------------
# Media Keys Listener Tests
# ---------------------------------------------------------------------------


def test_media_keys_listener_callbacks() -> None:
    """Test that media keys listener dispatches appropriate callbacks on key events."""
    play_called = False
    next_called = False
    prev_called = False

    def on_play() -> None:
        nonlocal play_called
        play_called = True

    def on_next() -> None:
        nonlocal next_called
        next_called = True

    def on_prev() -> None:
        nonlocal prev_called
        prev_called = True

    listener = MediaKeysListener(
        on_play_pause=on_play,
        on_next=on_next,
        on_prev=on_prev,
        enabled=True,
    )

    with patch("metrolite.audio.media_keys.keyboard") as mock_kb:
        mock_kb.Key.media_play_pause = "key_play_pause"
        mock_kb.Key.media_next = "key_next"
        mock_kb.Key.media_previous = "key_previous"

        listener._on_press("key_play_pause")
        assert play_called is True

        listener._on_press("key_next")
        assert next_called is True

        listener._on_press("key_previous")
        assert prev_called is True


def test_media_keys_start_stop() -> None:
    """Test starting and stopping media keys listener with context manager."""
    listener = MediaKeysListener(enabled=True)
    with listener:
        assert listener.is_alive()
    assert not listener.is_alive()


# ---------------------------------------------------------------------------
# BridgeServer Lifecycle Test
# ---------------------------------------------------------------------------


def test_bridge_server_lifecycle(
    mock_controller: BridgeController, tmp_path: Path, test_token: str
) -> None:
    """Test BridgeServer initialization, port auto-selection, and stop cleanup."""
    bridge_file = tmp_path / "bridge.json"
    server = BridgeServer(
        host="127.0.0.1",
        port=8766,
        token=test_token,
        controller=mock_controller,
        bridge_file_path=bridge_file,
    )

    assert server.port >= 8766
    assert server.token == test_token
    assert server.app is not None

    server.stop()
    assert not bridge_file.is_file()


@pytest.mark.asyncio
async def test_bridge_consecutive_error_skips_and_websocket(
    mock_controller: BridgeController,
) -> None:
    """Test max 2 consecutive error skips before pausing and sending WebSocket error event."""
    tracks = [
        Track(video_id=f"err_t{i}", title=f"Track {i}", artist="Artist")
        for i in range(1, 6)
    ]
    mock_controller.queue.set_queue(tracks, start_index=0)

    # Mock ws_manager to capture broadcast events
    broadcast_events: list[tuple[str, Any]] = []

    async def fake_broadcast(event: str, data: Any) -> None:
        broadcast_events.append((event, data))

    mock_controller.ws_manager.broadcast = fake_broadcast  # type: ignore[method-assign]

    # Error skip 1: should advance to track 2
    mock_controller.player.record_error()
    await mock_controller.handle_track_ended(reason="error")
    assert mock_controller.queue.current_index == 1
    assert mock_controller.player.consecutive_errors == 1
    assert mock_controller.queue.consecutive_errors == 1

    # Error skip 2: should advance to track 3
    mock_controller.player.record_error()
    await mock_controller.handle_track_ended(reason="error")
    assert mock_controller.queue.current_index == 2
    assert mock_controller.player.consecutive_errors == 2
    assert mock_controller.queue.consecutive_errors == 2

    # Error skip 3: should stop auto-advancing, pause player, and emit WebSocket error event
    mock_controller.player.record_error()
    await mock_controller.handle_track_ended(reason="error")
    assert mock_controller.queue.current_index == 2  # did NOT advance
    assert mock_controller.player.consecutive_errors == 3
    mock_controller.player._mpv.pause = True

    # Verify "error" event was broadcast
    error_broadcasts = [e for e in broadcast_events if e[0] == "error"]
    assert len(error_broadcasts) >= 1
    err_event, err_data = error_broadcasts[-1]
    assert err_event == "error"
    assert "consecutive_errors" in err_data
    assert err_data["consecutive_errors"] == 3

    # Normal completion (eof) resets consecutive errors and advances
    await mock_controller.handle_track_ended(reason="eof")
    assert mock_controller.player.consecutive_errors == 0
    assert mock_controller.queue.consecutive_errors == 0
    assert mock_controller.queue.current_index == 3


@pytest.mark.asyncio
async def test_bridge_aborted_reason_no_advance(
    mock_controller: BridgeController,
) -> None:
    """Test that end-file reason 'aborted' does not trigger auto-advance."""
    tracks = [
        Track(video_id=f"abt_t{i}", title=f"Track {i}", artist="Artist")
        for i in range(1, 4)
    ]
    mock_controller.queue.set_queue(tracks, start_index=1)
    assert mock_controller.queue.current_index == 1

    await mock_controller.handle_track_ended(reason="aborted")
    assert mock_controller.queue.current_index == 1


@pytest.mark.asyncio
async def test_ctrl_play_catches_stream_resolution_error(
    mock_controller: BridgeController,
) -> None:
    """Test that ctrl.play() catches StreamResolutionError and broadcasts WebSocket error event."""
    from metrolite.audio.resolver import StreamResolutionError

    mock_controller.player.resolver.resolve = AsyncMock(
        side_effect=StreamResolutionError("yt-dlp extraction failed for bad_vid: Requested format is not available")
    )

    broadcast_events: list[tuple[str, Any]] = []

    async def fake_broadcast(event: str, data: Any) -> None:
        broadcast_events.append((event, data))

    mock_controller.ws_manager.broadcast = fake_broadcast  # type: ignore[method-assign]

    res = await mock_controller.play("bad_vid")
    assert res["status"] == "error"
    assert "Requested format is not available" in res["error"]
    assert res["video_id"] == "bad_vid"

    # Verify WebSocket error event broadcast
    err_events = [e for e in broadcast_events if e[0] == "error"]
    assert len(err_events) == 1
    _, err_payload = err_events[0]
    assert "Requested format is not available" in err_payload["message"]
    assert err_payload["video_id"] == "bad_vid"
    assert mock_controller.player.consecutive_errors >= 1


def test_player_play_endpoint_stream_resolution_error(
    client: TestClient,
    mock_controller: BridgeController,
    test_token: str,
) -> None:
    """Test that /player/play returns a clean JSON error response instead of ASGI 500 when resolution fails."""
    from metrolite.audio.resolver import StreamResolutionError

    mock_controller.player.resolver.resolve = AsyncMock(
        side_effect=StreamResolutionError("yt-dlp extraction failed for fail_vid: Requested format is not available")
    )
    headers = {"X-Bridge-Token": test_token}

    res_post = client.post("/player/play", json={"video_id": "fail_vid"}, headers=headers)
    assert res_post.status_code == 200
    data = res_post.json()
    assert data["status"] == "error"
    assert "Requested format is not available" in data["error"]
    assert data["video_id"] == "fail_vid"

    res_get = client.get("/player/play?video_id=fail_vid", headers=headers)
    assert res_get.status_code == 200
    get_data = res_get.json()
    assert get_data["status"] == "error"
    assert "Requested format is not available" in get_data["error"]


def test_queue_set_autoplay_stream_resolution_error(
    client: TestClient,
    mock_controller: BridgeController,
    test_token: str,
) -> None:
    """Test that /queue action 'set' with auto_play=True does not crash with ASGI 500 if resolution fails."""
    from metrolite.audio.resolver import StreamResolutionError

    mock_controller.player.resolver.resolve = AsyncMock(
        side_effect=StreamResolutionError("yt-dlp extraction failed: Requested format is not available")
    )
    headers = {"X-Bridge-Token": test_token}

    track = {"video_id": "unplayable_vid", "title": "Unplayable", "artist": "Artist"}
    res = client.post(
        "/queue",
        json={"action": "set", "items": [track], "auto_play": True},
        headers=headers,
    )
    assert res.status_code == 200
    assert len(res.json()["items"]) == 1


def test_player_toggle_and_shutdown_endpoints(
    client: TestClient,
    mock_controller: BridgeController,
    test_token: str,
) -> None:
    """Test /player/toggle, /shutdown, and /player/quit endpoints."""
    headers = {"X-Bridge-Token": test_token}

    # Set up mock track so pause transitions to 'paused'
    mock_track = Track(video_id="toggle_vid", title="Toggle Track", artist="Artist")
    mock_controller.player.current_track = mock_track
    mock_controller.player.mpv.idle_active = False

    # Test toggle when playing -> pauses
    mock_controller.player.state = "playing"
    mock_controller.player.mpv.pause = False
    res = client.post("/player/toggle", headers=headers)
    assert res.status_code == 200
    assert mock_controller.player.state == "paused"

    # Test toggle when paused -> attempts play
    res2 = client.post("/player/toggle", headers=headers)
    assert res2.status_code == 200

    # Test /shutdown
    res_shutdown = client.post("/shutdown", headers=headers)
    assert res_shutdown.status_code == 200
    assert res_shutdown.json() == {"status": "shutting_down"}

    # Test /player/quit
    res_quit = client.post("/player/quit", headers=headers)
    assert res_quit.status_code == 200
    assert res_quit.json() == {"status": "shutting_down"}


def test_player_seek_idle_buffering_no_500(
    client: TestClient,
    mock_controller: BridgeController,
    test_token: str,
) -> None:
    """Test seeking while idle or buffering does not crash with HTTP 500."""
    headers = {"X-Bridge-Token": test_token}

    # Simulate idle/stopped mpv
    mock_controller.player.mpv.idle_active = True
    res = client.post("/player/seek?seconds=120.0", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"time_pos": 0.0}

    # Simulate buffering state
    mock_controller.player.mpv.idle_active = False
    mock_controller.player._loading_new_track = True
    res2 = client.post("/player/seek", json={"seconds": 65.0}, headers=headers)
    assert res2.status_code == 200
    assert res2.json() == {"time_pos": 0.0}

    # Simulate libmpv throwing SystemError (-12)
    mock_controller.player._loading_new_track = False
    mock_controller.player.mpv.seek.side_effect = SystemError("libmpv MPV_ERROR_COMMAND (-12)")
    res3 = client.post("/player/seek", json={"seconds": 30.0}, headers=headers)
    assert res3.status_code == 200
    assert res3.json() == {"time_pos": 0.0}
