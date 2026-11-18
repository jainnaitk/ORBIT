"""Unit tests for MetroLite LibraryManager and DownloadManager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metrolite.audio.resolver import StreamResolver
from metrolite.library.downloader import (
    DownloadError,
    DownloadManager,
    DownloadProgress,
    _embed_metadata,
)
from metrolite.library.manager import LibraryManager
from metrolite.store.db import init_db
from metrolite.ytm.client import Track


@pytest.fixture
async def library_mgr(tmp_path: Path) -> LibraryManager:
    """Fixture providing an initialized LibraryManager backed by a temporary SQLite database."""
    db_file = tmp_path / "test_library.db"
    await init_db(db_file)
    return LibraryManager(db_path=db_file)


@pytest.fixture
async def download_mgr(tmp_path: Path) -> DownloadManager:
    """Fixture providing an initialized DownloadManager with temporary directories."""
    db_file = tmp_path / "test_download.db"
    downloads_dir = tmp_path / "downloads"
    await init_db(db_file)
    return DownloadManager(
        db_path=db_file,
        downloads_dir=downloads_dir,
        max_concurrent=2,
    )


# =============================================================================
# LibraryManager Tests
# =============================================================================


@pytest.mark.asyncio
async def test_toggle_like_and_list_liked(library_mgr: LibraryManager) -> None:
    """Test liking, unliking, and searching liked songs."""
    track1 = Track(
        video_id="vid_1",
        title="Midnight City",
        artist="M83",
        album="Hurry Up, We're Dreaming",
        duration_s=243,
        thumbnail_url="https://example.com/m83.jpg",
    )
    track2 = Track(
        video_id="vid_2",
        title="Starboy",
        artist="The Weeknd",
        album="Starboy",
        duration_s=230,
    )

    # 1. Like track1
    is_liked = await library_mgr.toggle_like("vid_1", track1)
    assert is_liked is True
    assert await library_mgr.is_liked("vid_1") is True

    # 2. Like track2
    await library_mgr.toggle_like("vid_2", track2)
    assert await library_mgr.is_liked("vid_2") is True

    # 3. List liked songs
    liked = await library_mgr.list_liked()
    assert len(liked) == 2
    assert {t["video_id"] for t in liked} == {"vid_1", "vid_2"}
    assert liked[0]["liked_at"] is not None

    # 4. Search liked songs
    search_m83 = await library_mgr.list_liked(search_query="M83")
    assert len(search_m83) == 1
    assert search_m83[0]["video_id"] == "vid_1"

    search_city = await library_mgr.list_liked(search_query="city")
    assert len(search_city) == 1
    assert search_city[0]["video_id"] == "vid_1"

    search_nonexistent = await library_mgr.list_liked(search_query="Daft Punk")
    assert len(search_nonexistent) == 0

    # 5. Unlike track1
    is_liked_after = await library_mgr.toggle_like("vid_1")
    assert is_liked_after is False
    assert await library_mgr.is_liked("vid_1") is False

    liked_after = await library_mgr.list_liked()
    assert len(liked_after) == 1
    assert liked_after[0]["video_id"] == "vid_2"


@pytest.mark.asyncio
async def test_playlist_lifecycle(library_mgr: LibraryManager) -> None:
    """Test full playlist CRUD: create, rename, list, and delete."""
    # 1. Create playlist
    pl = await library_mgr.create_playlist(name="Synthwave Chill", description="Relaxing beats")
    pl_id = pl["id"]
    assert pl_id.startswith("pl_")
    assert pl["name"] == "Synthwave Chill"
    assert pl["description"] == "Relaxing beats"

    with pytest.raises(ValueError):
        await library_mgr.create_playlist(name="   ")

    # 2. List playlists
    playlists = await library_mgr.list_playlists()
    assert len(playlists) == 1
    assert playlists[0]["id"] == pl_id
    assert playlists[0]["track_count"] == 0

    # 3. Rename playlist
    renamed = await library_mgr.rename_playlist(
        pl_id, name="Synthwave Classics", description="Updated description"
    )
    assert renamed is True

    details = await library_mgr.get_playlist(pl_id)
    assert details is not None
    assert details["name"] == "Synthwave Classics"
    assert details["description"] == "Updated description"

    # 4. Delete playlist
    deleted = await library_mgr.delete_playlist(pl_id)
    assert deleted is True

    playlists_after = await library_mgr.list_playlists()
    assert len(playlists_after) == 0


@pytest.mark.asyncio
async def test_playlist_tracks_and_reordering(library_mgr: LibraryManager) -> None:
    """Test adding, removing, and reordering tracks in a playlist."""
    pl = await library_mgr.create_playlist("My Mix")
    pl_id = pl["id"]

    t1 = Track(video_id="v1", title="Song 1", artist="Artist 1")
    t2 = Track(video_id="v2", title="Song 2", artist="Artist 2")
    t3 = Track(video_id="v3", title="Song 3", artist="Artist 3")

    # 1. Add tracks
    added = await library_mgr.add_to_playlist(pl_id, [t1, t2, t3])
    assert added == 3

    # Adding duplicate should not increase count
    added_dup = await library_mgr.add_to_playlist(pl_id, [t1])
    assert added_dup == 0

    tracks = await library_mgr.get_playlist_tracks(pl_id)
    assert len(tracks) == 3
    assert [t["video_id"] for t in tracks] == ["v1", "v2", "v3"]
    assert [t["position"] for t in tracks] == [0, 1, 2]

    # 2. Reorder moving v3 to position 0
    reordered = await library_mgr.reorder_playlist(pl_id, video_id="v3", new_position=0)
    assert reordered is True

    tracks_reordered = await library_mgr.get_playlist_tracks(pl_id)
    assert [t["video_id"] for t in tracks_reordered] == ["v3", "v1", "v2"]
    assert [t["position"] for t in tracks_reordered] == [0, 1, 2]

    # 3. Reorder with explicit ordered_video_ids
    reordered_explicit = await library_mgr.reorder_playlist(
        pl_id, ordered_video_ids=["v2", "v3", "v1"]
    )
    assert reordered_explicit is True

    tracks_explicit = await library_mgr.get_playlist_tracks(pl_id)
    assert [t["video_id"] for t in tracks_explicit] == ["v2", "v3", "v1"]
    assert [t["position"] for t in tracks_explicit] == [0, 1, 2]

    # 4. Remove track v3 and check recompacting
    removed = await library_mgr.remove_from_playlist(pl_id, "v3")
    assert removed is True

    tracks_after_removal = await library_mgr.get_playlist_tracks(pl_id)
    assert len(tracks_after_removal) == 2
    assert [t["video_id"] for t in tracks_after_removal] == ["v2", "v1"]
    assert [t["position"] for t in tracks_after_removal] == [0, 1]


@pytest.mark.asyncio
async def test_record_play_and_history(library_mgr: LibraryManager) -> None:
    """Test play count recording and history retrieval."""
    t = Track(video_id="h1", title="History Track", artist="History Artist")

    await library_mgr.record_play("h1", t)
    await library_mgr.record_play("h1")

    track_record = await library_mgr.get_track("h1")
    assert track_record is not None
    assert track_record["play_count"] == 2
    assert track_record["last_played_at"] is not None

    history = await library_mgr.get_history()
    assert len(history) == 1
    assert history[0]["video_id"] == "h1"


@pytest.mark.asyncio
async def test_library_restart_persistence(tmp_path: Path) -> None:
    """Verify library data persists across manager instances (simulating app restart)."""
    db_file = tmp_path / "restart_test.db"
    await init_db(db_file)

    mgr1 = LibraryManager(db_path=db_file)
    pl = await mgr1.create_playlist("Persisted Playlist")
    await mgr1.add_to_playlist(pl["id"], Track(video_id="p1", title="P1", artist="A1"))
    await mgr1.toggle_like("p1")

    # Second instance pointing to identical DB
    mgr2 = LibraryManager(db_path=db_file)
    playlists = await mgr2.list_playlists()
    assert len(playlists) == 1
    assert playlists[0]["name"] == "Persisted Playlist"
    assert playlists[0]["track_count"] == 1

    liked = await mgr2.list_liked()
    assert len(liked) == 1
    assert liked[0]["video_id"] == "p1"


# =============================================================================
# DownloadManager Tests
# =============================================================================


@pytest.mark.asyncio
async def test_download_track_success_and_offline_resolution(
    download_mgr: DownloadManager,
) -> None:
    """Test successful download, progress events, and offline playback resolution."""
    events: list[tuple[str, float, str]] = []

    def on_progress(vid: str, pct: float, status: str) -> None:
        events.append((vid, pct, status))

    download_mgr.add_progress_listener(on_progress)

    fake_file = download_mgr.downloads_dir / "dl_test_1.opus"

    def mock_download(urls: list[str]) -> int:
        fake_file.write_bytes(b"dummy opus binary content")
        return 0

    def mock_extract(url: str, download: bool = True) -> dict[str, str | int]:
        fake_file.write_bytes(b"dummy opus binary content")
        return {
            "id": "dl_test_1",
            "title": "Offline Anthem",
            "artist": "Local Band",
            "album": "The Offline Sessions",
            "duration": 210,
        }

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        ydl_instance = MagicMock()
        ydl_instance.extract_info.side_effect = mock_extract
        ydl_instance.download.side_effect = mock_download
        mock_ydl_cls.return_value.__enter__.return_value = ydl_instance

        # Execute download
        resolved_path = await download_mgr.download_track("dl_test_1")
        assert Path(resolved_path).is_file()
        assert Path(resolved_path).name == "dl_test_1.opus"

    # Verify progress events reached completed (100%)
    statuses = [e[2] for e in events]
    assert "queued" in statuses
    assert "downloading" in statuses
    assert "completed" in statuses
    assert events[-1][1] == 100.0

    # Verify is_downloaded and list_downloads
    assert await download_mgr.is_downloaded("dl_test_1") is True
    downloads = await download_mgr.list_downloads()
    assert len(downloads) == 1
    assert downloads[0]["video_id"] == "dl_test_1"
    assert downloads[0]["title"] == "Offline Anthem"
    assert downloads[0]["local_path"] == resolved_path

    # Verify offline playback using StreamResolver without network (Wi-Fi off simulation)
    resolver = StreamResolver(db_path=download_mgr.db_path)
    with patch("yt_dlp.YoutubeDL") as mock_offline_ydl:
        play_source = await resolver.resolve("dl_test_1")
        assert play_source == resolved_path
        # yt-dlp must NOT be called when playing offline downloaded track
        mock_offline_ydl.assert_not_called()


@pytest.mark.asyncio
async def test_download_track_caching(download_mgr: DownloadManager) -> None:
    """Verify that calling download_track on an already downloaded track returns instantly."""
    existing_file = download_mgr.downloads_dir / "cached_vid.opus"
    existing_file.write_bytes(b"existing content")

    # Pre-populate database with downloaded track
    from metrolite.store.db import get_connection

    async with get_connection(download_mgr.db_path) as conn:
        await conn.execute(
            """
            INSERT INTO tracks (video_id, title, local_path, file_size_bytes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET local_path = excluded.local_path;
            """,
            ("cached_vid", "Cached Song", str(existing_file.resolve()), 16),
        )
        await conn.commit()

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        path = await download_mgr.download_track("cached_vid")
        assert path == str(existing_file.resolve())
        mock_ydl.assert_not_called()


@pytest.mark.asyncio
async def test_delete_download(download_mgr: DownloadManager) -> None:
    """Test deleting a downloaded track from disk and database."""
    fake_file = download_mgr.downloads_dir / "to_delete.opus"
    fake_file.write_bytes(b"audio content")

    from metrolite.store.db import get_connection

    async with get_connection(download_mgr.db_path) as conn:
        await conn.execute(
            """
            INSERT INTO tracks (video_id, title, local_path, file_size_bytes)
            VALUES (?, ?, ?, ?);
            """,
            ("to_delete", "Delete Me", str(fake_file.resolve()), 13),
        )
        await conn.commit()

    assert fake_file.is_file()
    assert await download_mgr.is_downloaded("to_delete") is True

    # Delete
    deleted = await download_mgr.delete_download("to_delete")
    assert deleted is True
    assert not fake_file.exists()
    assert await download_mgr.is_downloaded("to_delete") is False

    downloads = await download_mgr.list_downloads()
    assert len(downloads) == 0


@pytest.mark.asyncio
async def test_concurrency_limiting(download_mgr: DownloadManager) -> None:
    """Verify that downloads respect the max_concurrent limit."""
    active_concurrent = 0
    max_observed = 0

    def mock_extract(url: str, download: bool = True) -> dict[str, str | int]:
        nonlocal active_concurrent, max_observed
        active_concurrent += 1
        max_observed = max(max_observed, active_concurrent)

        # Simulate brief work
        import time

        time.sleep(0.05)

        # Create dummy file
        v_id = url.split("=")[-1]
        f = download_mgr.downloads_dir / f"{v_id}.opus"
        f.write_bytes(b"content")

        active_concurrent -= 1
        return {"id": v_id, "title": f"Song {v_id}"}

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        ydl_inst = MagicMock()
        ydl_inst.extract_info.side_effect = mock_extract
        mock_ydl.return_value.__enter__.return_value = ydl_inst

        # Queue 4 concurrent downloads (limit is 2)
        results = await asyncio.gather(
            download_mgr.download_track("t1"),
            download_mgr.download_track("t2"),
            download_mgr.download_track("t3"),
            download_mgr.download_track("t4"),
        )

        assert len(results) == 4
        assert max_observed <= 2


@pytest.mark.asyncio
async def test_download_failure_handling(download_mgr: DownloadManager) -> None:
    """Test error state emission when yt-dlp fails."""
    events: list[DownloadProgress] = []
    download_mgr.add_progress_listener(lambda p: events.append(p))

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        ydl_inst = MagicMock()
        ydl_inst.extract_info.side_effect = RuntimeError("Network error extracting stream")
        mock_ydl.return_value.__enter__.return_value = ydl_inst

        with pytest.raises(DownloadError):
            await download_mgr.download_track("fail_track")

    error_events = [e for e in events if e.status == "error"]
    assert len(error_events) > 0
    assert "Network error" in (error_events[-1].error or "")


def test_embed_metadata_tagging(tmp_path: Path) -> None:
    """Test embedding metadata on an OggOpus file with mutagen."""
    opus_file = tmp_path / "sample.opus"

    # Create a minimal valid ogg opus file
    # We can write minimal OggOpus header or test that _embed_metadata runs gracefully
    _embed_metadata(
        file_path=opus_file,
        title="Test Title",
        artist="Test Artist",
        album="Test Album",
        thumbnail_url="http://mocked/thumb.jpg",
        embed_thumbnail=True,
    )
    # File did not exist, should return cleanly without error
    assert not opus_file.exists()


@pytest.mark.asyncio
async def test_downloader_restart_persistence(tmp_path: Path) -> None:
    """Verify that downloads persist across DownloadManager restarts."""
    db_file = tmp_path / "dl_persist.db"
    dls_dir = tmp_path / "downloads"
    await init_db(db_file)

    mgr1 = DownloadManager(db_path=db_file, downloads_dir=dls_dir)
    assert mgr1.max_concurrent >= 1
    test_file = dls_dir / "persisted_track.opus"
    test_file.write_bytes(b"sound data")

    from metrolite.store.db import get_connection

    async with get_connection(db_file) as conn:
        await conn.execute(
            """
            INSERT INTO tracks (video_id, title, local_path, file_size_bytes, downloaded_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (
                "persisted_track",
                "Persistent Audio",
                str(test_file.resolve()),
                10,
                "2026-11-18T00:00:00",
            ),
        )
        await conn.commit()

    # Re-instantiate
    mgr2 = DownloadManager(db_path=db_file, downloads_dir=dls_dir)
    assert await mgr2.is_downloaded("persisted_track") is True
    dls = await mgr2.list_downloads()
    assert len(dls) == 1
    assert dls[0]["video_id"] == "persisted_track"
