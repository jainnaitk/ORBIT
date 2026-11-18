"""Unit tests for audio resolver and player with mocked mpv and yt-dlp."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metrolite.audio.player import AudioPlayer
from metrolite.audio.resolver import StreamResolutionError, StreamResolver
from metrolite.store.db import SCHEMA_SQL
from metrolite.ytm.client import Track


@pytest.fixture
def mock_db_with_local_track(tmp_path: Path) -> tuple[Path, Path]:
    """Create a temporary SQLite database with a downloaded local track."""
    db_file = tmp_path / "test_resolver.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.executescript(SCHEMA_SQL)
        audio_file = tmp_path / "song123.opus"
        audio_file.write_bytes(b"dummy audio content")
        conn.execute(
            """
            INSERT INTO tracks (video_id, title, artist, duration_s, local_path)
            VALUES (?, ?, ?, ?, ?);
            """,
            ("song123", "Offline Song", "Local Artist", 180, str(audio_file)),
        )
    conn.close()
    return db_file, audio_file


@pytest.mark.asyncio
async def test_resolver_offline_local_path(mock_db_with_local_track: tuple[Path, Path]) -> None:
    """Test instant resolution of local files from SQLite without yt-dlp."""
    db_file, audio_file = mock_db_with_local_track
    resolver = StreamResolver(db_path=db_file)

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        resolved = await resolver.resolve("song123")
        assert resolved == str(audio_file.resolve())
        # yt-dlp should NOT be called when local file exists
        mock_ydl.assert_not_called()


@pytest.mark.asyncio
async def test_resolver_cache_and_ttl(tmp_path: Path) -> None:
    """Test stream URL memory caching and TTL expiry."""
    db_file = tmp_path / "empty.db"
    resolver = StreamResolver(db_path=db_file, stream_url_ttl_s=1)

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"url": "https://stream.googlevideo.com/test1"}
        mock_ydl_cls.return_value.__enter__.return_value = mock_instance

        # 1. First resolution should call yt-dlp
        url1 = await resolver.resolve("stream_vid")
        assert url1 == "https://stream.googlevideo.com/test1"
        assert mock_instance.extract_info.call_count == 1
        assert resolver.is_cached("stream_vid")

        # 2. Second resolution within TTL should hit cache
        url2 = await resolver.resolve("stream_vid")
        assert url2 == "https://stream.googlevideo.com/test1"
        assert mock_instance.extract_info.call_count == 1

        # 3. Simulate TTL expiry
        resolver._cache["stream_vid"] = (
            "https://stream.googlevideo.com/test1",
            time.monotonic() - 10,
        )
        assert not resolver.is_cached("stream_vid")

        # 4. Third resolution after expiry should call yt-dlp again
        mock_instance.extract_info.return_value = {"url": "https://stream.googlevideo.com/test2"}
        url3 = await resolver.resolve("stream_vid")
        assert url3 == "https://stream.googlevideo.com/test2"
        assert mock_instance.extract_info.call_count == 2


@pytest.mark.asyncio
async def test_resolver_extraction_failure(tmp_path: Path) -> None:
    """Test yt-dlp extraction failure raises StreamResolutionError."""
    resolver = StreamResolver(db_path=tmp_path / "none.db")

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_instance = MagicMock()
        mock_instance.extract_info.side_effect = Exception("YouTube blocked IP")
        mock_ydl_cls.return_value.__enter__.return_value = mock_instance

        with pytest.raises(StreamResolutionError, match="yt-dlp extraction failed"):
            await resolver.resolve("failed_vid")


@pytest.mark.asyncio
async def test_player_load_and_preload() -> None:
    """Test AudioPlayer load_and_play and preload_next with mocked MPV."""
    mock_mpv = MagicMock()
    mock_resolver = MagicMock()

    mock_resolver.resolve = pytest.importorskip("unittest.mock").AsyncMock(
        side_effect=lambda vid: f"https://mock-stream/{vid}"
    )

    player = AudioPlayer(resolver=mock_resolver, mpv_instance=mock_mpv)

    track1 = Track(video_id="vid1", title="Track 1", artist="Artist 1")
    track2 = Track(video_id="vid2", title="Track 2", artist="Artist 2")

    # Load and play track1
    await player.load_and_play(track1)
    mock_mpv.loadfile.assert_called_with("https://mock-stream/vid1", mode="replace")
    assert player.current_track == track1
    assert mock_mpv.pause is False

    # Preload track2 for gapless transition
    await player.preload_next(track2)
    mock_mpv.loadfile.assert_called_with("https://mock-stream/vid2", mode="append")
    assert player.next_track == track2


def test_player_controls() -> None:
    """Test pause, resume, seek, set_volume, and stop."""
    mock_mpv = MagicMock()
    player = AudioPlayer(resolver=MagicMock(), mpv_instance=mock_mpv)

    player.pause()
    assert mock_mpv.pause is True

    player.resume()
    assert mock_mpv.pause is False

    player.seek(42.5)
    mock_mpv.seek.assert_called_with(42.5, reference="absolute")

    # Volume clamping (0 to 100)
    player.set_volume(120)
    assert player.volume == 100
    assert mock_mpv.volume == 100.0

    player.set_volume(-10)
    assert player.volume == 0
    assert mock_mpv.volume == 0.0

    player.set_volume(75)
    assert player.volume == 75
    assert mock_mpv.volume == 75.0

    player.stop()
    mock_mpv.stop.assert_called_once()
    assert player.current_track is None


def test_player_callbacks() -> None:
    """Test registered callbacks for state changes and time position."""
    mock_mpv = MagicMock()
    mock_mpv.idle_active = False
    mock_mpv.pause = False
    mock_mpv.core_idle = False

    time_pos_captured: list[float] = []
    state_captured: list[str] = []

    player = AudioPlayer(
        resolver=MagicMock(),
        mpv_instance=mock_mpv,
        on_time_pos=lambda pos: time_pos_captured.append(pos),
        on_state_changed=lambda st: state_captured.append(st),
    )

    # Set current track to transition out of stopped state
    player.current_track = Track(video_id="v1", title="Title")
    player._update_state()
    assert player.state == "playing"
    assert "playing" in state_captured

    player.pause()
    assert player.state == "paused"
    assert "paused" in state_captured
