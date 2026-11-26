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
        # Test unpacking works on offline local track as well
        local_path, headers = resolved
        assert local_path == str(audio_file.resolve())
        assert headers == {}
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
    mock_mpv.play.assert_called_with("https://mock-stream/vid1")
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


def test_player_seek_idle_buffering_and_exception_safety() -> None:
    """Verify AudioPlayer.seek safely ignores idle/buffering states and catches libmpv errors."""
    mock_mpv = MagicMock()
    player = AudioPlayer(resolver=MagicMock(), mpv_instance=mock_mpv)

    # 1. Idle state guard (idle_active is True)
    mock_mpv.idle_active = True
    player.seek(50.0)
    mock_mpv.seek.assert_not_called()
    assert player.time_pos == 0.0

    # 2. Buffering state guard (_loading_new_track is True)
    mock_mpv.idle_active = False
    player._loading_new_track = True
    player.seek(50.0)
    mock_mpv.seek.assert_not_called()
    assert player.time_pos == 0.0

    # 3. Libmpv SystemError (e.g. MPV_ERROR_COMMAND code -12) is caught gracefully
    player._loading_new_track = False
    mock_mpv.seek.side_effect = SystemError("libmpv MPV_ERROR_COMMAND (-12)")
    player.seek(50.0)
    assert player.time_pos == 0.0  # Must not mutate time_pos on failure


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


@pytest.mark.asyncio
async def test_resolver_extractor_args_and_cookies(tmp_path: Path) -> None:
    """Test that StreamResolver includes cookies, extractor_args and js_runtimes."""
    cookies_path = tmp_path / "mock_cookies.txt"
    cookies_path.write_text("# Netscape HTTP Cookie File\n")
    resolver = StreamResolver(
        db_path=tmp_path / "empty.db",
        cookies_file=cookies_path,
        cookies_from_browser="firefox",
    )

    captured_opts: dict = {}

    def fake_ydl_init(opts):
        captured_opts.update(opts)
        mock = MagicMock()
        mock.extract_info.return_value = {
            "url": "https://stream.googlevideo.com/audio1",
            "http_headers": {
                "User-Agent": "Mozilla/5.0 AndroidVR",
                "Accept": "*/*",
            },
        }
        mock.__enter__.return_value = mock
        return mock

    with patch("yt_dlp.YoutubeDL", side_effect=fake_ydl_init):
        res = await resolver.resolve("test_extractor_vid")
        assert res == "https://stream.googlevideo.com/audio1"
        assert getattr(res, "user_agent", None) == "Mozilla/5.0 AndroidVR"
        assert getattr(res, "http_headers", {}) == {
            "User-Agent": "Mozilla/5.0 AndroidVR",
            "Accept": "*/*",
        }
        assert resolver.get_user_agent("test_extractor_vid") == "Mozilla/5.0 AndroidVR"
        assert resolver.get_http_headers("test_extractor_vid") == {
            "User-Agent": "Mozilla/5.0 AndroidVR",
            "Accept": "*/*",
        }

        # Test tuple unpacking
        stream_url, headers = res
        assert stream_url == "https://stream.googlevideo.com/audio1"
        assert headers == {
            "User-Agent": "Mozilla/5.0 AndroidVR",
            "Accept": "*/*",
        }

        assert captured_opts["cookiefile"] == str(cookies_path)
        assert captured_opts["cookiesfrombrowser"] == ("firefox",)
        assert captured_opts["extractor_args"] == {
            "youtube": {"player_client": ["android"]}
        }
        assert captured_opts["js_runtimes"] == {"node": {}}
        assert (
            captured_opts["format"]
            == "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best"
        )


@pytest.mark.asyncio
async def test_resolver_player_client_without_cookies(tmp_path: Path) -> None:
    """Test that StreamResolver configures default -tv_samsung and node js_runtimes without cookies."""
    resolver = StreamResolver(db_path=tmp_path / "empty.db")

    captured_opts: dict = {}

    def fake_ydl_init(opts):
        captured_opts.update(opts)
        mock = MagicMock()
        mock.extract_info.return_value = {
            "url": "https://stream.googlevideo.com/audio_nocookies",
        }
        mock.__enter__.return_value = mock
        return mock

    with patch("yt_dlp.YoutubeDL", side_effect=fake_ydl_init):
        res = await resolver.resolve("test_nocookies_vid")
        assert res == "https://stream.googlevideo.com/audio_nocookies"
        assert captured_opts["extractor_args"] == {
            "youtube": {"player_client": ["android"]}
        }
        assert captured_opts["js_runtimes"] == {"node": {}}
        assert (
            captured_opts["format"]
            == "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best"
        )


@pytest.mark.asyncio
async def test_resolver_retry_on_format_not_available(tmp_path: Path) -> None:
    """Test that StreamResolver retries once with bestaudio*/best if format is not available."""
    from yt_dlp.utils import DownloadError as YtDlpDownloadError

    resolver = StreamResolver(db_path=tmp_path / "empty.db", format_selector="custom_format")
    opts_history: list[dict] = []
    call_count = 0

    def fake_ydl_init(opts):
        nonlocal call_count
        call_count += 1
        opts_history.append(dict(opts))
        mock = MagicMock()
        if call_count == 1:
            mock.extract_info.side_effect = YtDlpDownloadError(
                "ERROR: [youtube] test_retry_vid: Requested format is not available. Use --list-formats"
            )
        else:
            mock.extract_info.return_value = {
                "url": "https://stream.googlevideo.com/fallback_audio",
            }
        mock.__enter__.return_value = mock
        return mock

    with patch("yt_dlp.YoutubeDL", side_effect=fake_ydl_init):
        res = await resolver.resolve("test_retry_vid")
        assert res == "https://stream.googlevideo.com/fallback_audio"
        assert len(opts_history) == 2
        # First attempt: custom format and player_client
        assert opts_history[0]["format"] == "custom_format"
        assert opts_history[0]["extractor_args"] == {
            "youtube": {"player_client": ["android"]}
        }
        assert opts_history[0]["js_runtimes"] == {"node": {}}
        # Second attempt: bestaudio*/best format and player_client
        assert opts_history[1]["format"] == "bestaudio*/best"
        assert opts_history[1]["extractor_args"] == {
            "youtube": {"player_client": ["android"]}
        }
        assert opts_history[1]["js_runtimes"] == {"node": {}}


@pytest.mark.asyncio
async def test_player_user_agent_before_load() -> None:
    """Test that AudioPlayer injects User-Agent and http_header_fields into libmpv before calling play(url)."""
    call_order: list[str] = []
    mock_mpv = MagicMock()

    def set_ua(val):
        call_order.append(f"set_ua:{val}")

    def set_headers(val):
        call_order.append(f"set_headers:{val}")

    def play(source):
        call_order.append(f"play:{source}")

    type(mock_mpv).user_agent = property(
        fget=lambda self: getattr(self, "_ua", None),
        fset=lambda self, val: [setattr(self, "_ua", val), set_ua(val)],
    )
    type(mock_mpv).http_header_fields = property(
        fget=lambda self: getattr(self, "_headers", None),
        fset=lambda self, val: [setattr(self, "_headers", val), set_headers(val)],
    )
    mock_mpv.play.side_effect = play

    from metrolite.audio.resolver import ResolvedSource

    mock_resolver = MagicMock()
    mock_resolver.resolve = pytest.importorskip("unittest.mock").AsyncMock(
        return_value=ResolvedSource(
            "https://googlevideo.com/stream",
            user_agent="Custom-AndroidVR-UA",
            http_headers={"User-Agent": "Custom-AndroidVR-UA", "Accept": "*/*"},
        )
    )

    player = AudioPlayer(resolver=mock_resolver, mpv_instance=mock_mpv)
    track = Track(video_id="ua_vid", title="UA Track")

    await player.load_and_play(track)

    assert "set_ua:Custom-AndroidVR-UA" in call_order
    assert any("set_headers:" in call for call in call_order)
    assert "play:https://googlevideo.com/stream" in call_order
    # User-Agent and headers were set BEFORE play
    ua_idx = call_order.index("set_ua:Custom-AndroidVR-UA")
    headers_idx = next(i for i, c in enumerate(call_order) if "set_headers:" in c)
    play_idx = call_order.index("play:https://googlevideo.com/stream")
    assert ua_idx < play_idx
    assert headers_idx < play_idx
    assert player.current_user_agent == "Custom-AndroidVR-UA"
    assert mock_mpv.user_agent == "Custom-AndroidVR-UA"
    assert "User-Agent: Custom-AndroidVR-UA" in mock_mpv.http_header_fields
    assert "Accept: */*" in mock_mpv.http_header_fields


def test_player_consecutive_errors_and_end_file() -> None:
    """Test AudioPlayer consecutive error tracking and max 2 errors limit on end-file."""
    mock_mpv = MagicMock()
    player = AudioPlayer(resolver=MagicMock(), mpv_instance=mock_mpv)
    assert player.consecutive_errors == 0
    assert player.can_skip_on_error() is True

    # Error 1
    player.record_error()
    assert player.consecutive_errors == 1
    assert player.can_skip_on_error() is True

    # Error 2
    player.record_error()
    assert player.consecutive_errors == 2
    assert player.can_skip_on_error() is True

    # Error 3: limit exceeded
    player.record_error()
    assert player.consecutive_errors == 3
    assert player.can_skip_on_error() is False

    # Reset
    player.reset_errors()
    assert player.consecutive_errors == 0
    assert player.can_skip_on_error() is True


def test_downloader_extractor_args_and_cookies(tmp_path: Path) -> None:
    """Test that _download_worker includes cookies, extractor_args, and js_runtimes."""
    from metrolite.library.downloader import _download_worker

    captured_opts: dict = {}

    def fake_ydl(opts):
        captured_opts.update(opts)
        mock = MagicMock()
        fake_file = tmp_path / "dl_vid.opus"
        fake_file.write_bytes(b"audio data")
        mock.extract_info.return_value = {
            "title": "Downloaded Song",
            "artist": "DL Artist",
            "duration": 200,
        }
        mock.__enter__.return_value = mock
        return mock

    cookies_file = tmp_path / "dl_cookies.txt"
    cookies_file.write_text("# Netscape HTTP Cookie File\n")

    with patch("yt_dlp.YoutubeDL", side_effect=fake_ydl):
        target, _meta = _download_worker(
            video_id="dl_vid",
            downloads_dir=tmp_path,
            preferred_ext="opus",
            format_selector="bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best",
            embed_metadata=False,
            embed_thumbnail=False,
            meta={},
            progress_cb=lambda pct, st: None,
            cookies_file=cookies_file,
            cookies_from_browser="chrome",
        )
        assert target.is_file()
        assert captured_opts["cookiefile"] == str(cookies_file)
        assert captured_opts["cookiesfrombrowser"] == ("chrome",)
        assert captured_opts["extractor_args"] == {
            "youtube": {"player_client": ["android"]}
        }
        assert captured_opts["js_runtimes"] == {"node": {}}
        assert (
            captured_opts["format"]
            == "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best"
        )


def test_downloader_player_client_without_cookies(tmp_path: Path) -> None:
    """Test that _download_worker uses player_client: ['default', '-tv_samsung'] and js_runtimes."""
    from metrolite.library.downloader import _download_worker

    captured_opts: dict = {}

    def fake_ydl(opts):
        captured_opts.update(opts)
        mock = MagicMock()
        fake_file = tmp_path / "dl_nocookies.opus"
        fake_file.write_bytes(b"audio data")
        mock.extract_info.return_value = {
            "title": "Song",
            "artist": "Artist",
            "duration": 150,
        }
        mock.__enter__.return_value = mock
        return mock

    with patch("yt_dlp.YoutubeDL", side_effect=fake_ydl):
        target, _meta = _download_worker(
            video_id="dl_nocookies",
            downloads_dir=tmp_path,
            preferred_ext="opus",
            format_selector="bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best",
            embed_metadata=False,
            embed_thumbnail=False,
            meta={},
            progress_cb=lambda pct, st: None,
        )
        assert target.is_file()
        assert captured_opts["extractor_args"] == {
            "youtube": {"player_client": ["android"]}
        }
        assert captured_opts["js_runtimes"] == {"node": {}}
        assert (
            captured_opts["format"]
            == "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best"
        )


def test_downloader_retry_on_format_not_available(tmp_path: Path) -> None:
    """Test that _download_worker retries with bestaudio*/best if format is not available."""
    from yt_dlp.utils import DownloadError as YtDlpDownloadError

    from metrolite.library.downloader import _download_worker

    opts_history: list[dict] = []
    call_count = 0

    def fake_ydl(opts):
        nonlocal call_count
        call_count += 1
        opts_history.append(dict(opts))
        mock = MagicMock()
        if call_count == 1:
            mock.extract_info.side_effect = YtDlpDownloadError(
                "ERROR: [youtube] dl_retry_vid: Requested format is not available. Use --list-formats"
            )
        else:
            fake_file = tmp_path / "dl_retry_vid.opus"
            fake_file.write_bytes(b"fallback audio data")
            mock.extract_info.return_value = {
                "title": "Fallback Song",
                "artist": "Artist",
                "duration": 180,
            }
        mock.__enter__.return_value = mock
        return mock

    with patch("yt_dlp.YoutubeDL", side_effect=fake_ydl):
        target, _meta = _download_worker(
            video_id="dl_retry_vid",
            downloads_dir=tmp_path,
            preferred_ext="opus",
            format_selector="custom_format",
            embed_metadata=False,
            embed_thumbnail=False,
            meta={},
            progress_cb=lambda pct, st: None,
        )
        assert target.is_file()
        assert len(opts_history) == 2
        assert opts_history[0]["format"] == "custom_format"
        assert opts_history[0]["extractor_args"] == {
            "youtube": {"player_client": ["android"]}
        }
        assert opts_history[0]["js_runtimes"] == {"node": {}}
        assert opts_history[1]["format"] == "bestaudio*/best"
        assert opts_history[1]["extractor_args"] == {
            "youtube": {"player_client": ["android"]}
        }
        assert opts_history[1]["js_runtimes"] == {"node": {}}


def test_player_direct_play_sets_headers_and_calls_mpv_play() -> None:
    """Test that player.play(url, headers=...) sets self.mpv.user_agent and self.mpv.http_header_fields then calls self.mpv.play(url)."""
    mock_mpv = MagicMock()
    player = AudioPlayer(resolver=MagicMock(), mpv_instance=mock_mpv)

    headers = {
        "User-Agent": "Custom-Test-UA/1.0",
        "Referer": "https://www.youtube.com/",
        "Accept": "*/*",
    }
    player.play("https://stream.googlevideo.com/videoplayback?id=123", headers=headers)

    assert mock_mpv.user_agent == "Custom-Test-UA/1.0"
    assert "User-Agent: Custom-Test-UA/1.0" in mock_mpv.http_header_fields
    assert "Referer: https://www.youtube.com/" in mock_mpv.http_header_fields
    assert "Accept: */*" in mock_mpv.http_header_fields
    mock_mpv.play.assert_called_once_with("https://stream.googlevideo.com/videoplayback?id=123")


@pytest.mark.asyncio
async def test_resolver_prefetch_dedup_and_skips_cached(tmp_path: Path) -> None:
    """Verify StreamResolver.prefetch warms uncached streams cleanly while skipping duplicates/cached."""
    resolver = StreamResolver(db_path=tmp_path / "empty.db")
    extracted: list[str] = []

    def fake_ydl_init(opts):
        mock = MagicMock()

        def _extract(url, download=False):
            vid = url.split("v=")[-1]
            extracted.append(vid)
            if vid == "fail_prefetch":
                raise RuntimeError("Network timeout")
            return {"url": f"https://stream.googlevideo.com/{vid}"}

        mock.extract_info.side_effect = _extract
        mock.__enter__.return_value = mock
        return mock

    with patch("yt_dlp.YoutubeDL", side_effect=fake_ydl_init):
        # Warm first track manually
        await resolver.resolve("vid_cached")
        assert len(extracted) == 1

        # Prefetch overlapping, cached, duplicate, and invalid IDs
        warmed = await resolver.prefetch(
            ["vid_cached", "vid_next_1", "vid_next_1", "vid_next_2", "fail_prefetch", "../bad"]
        )
        assert warmed == 2
        assert resolver.is_cached("vid_next_1")
        assert resolver.is_cached("vid_next_2")
        assert extracted.count("vid_cached") == 1
        assert extracted.count("vid_next_1") == 1
        assert extracted.count("vid_next_2") == 1

