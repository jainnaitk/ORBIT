"""Offline tests for playlist URL importer, smart matcher, idempotent likes, and routes."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from metrolite.audio.media_keys import MediaKeysListener
from metrolite.audio.player import AudioPlayer
from metrolite.audio.queue import QueueManager
from metrolite.bridge.server import BridgeController, WebSocketManager, create_app
from metrolite.library.downloader import DownloadManager
from metrolite.library.importer import (
    FetchedImportSource,
    ImportSourceError,
    ImportTrackSeed,
    PlaylistImportManager,
    detect_import_target,
    parse_apple_music_html,
    parse_spotify_embed_html,
    score_match_candidate,
)
from metrolite.library.manager import LibraryManager
from metrolite.store.db import SCHEMA_SQL, get_connection
from metrolite.ytm.client import Track, YTMusicClient


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    db_file = tmp_path / "importer_test.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.executescript(SCHEMA_SQL)
    conn.close()
    return db_file


def test_detect_import_target_all_four_platforms() -> None:
    yt_pl = detect_import_target("https://www.youtube.com/playlist?list=PL1234567890")
    assert yt_pl.platform == "youtube"
    assert yt_pl.kind == "playlist"
    assert yt_pl.item_id == "PL1234567890"

    yt_watch = detect_import_target("https://youtube.com/watch?v=dQw4w9WgXcQ&list=PLwatch123")
    assert yt_watch.platform == "youtube"
    assert yt_watch.kind == "playlist"
    assert yt_watch.item_id == "PLwatch123"

    yt_short = detect_import_target("https://youtu.be/dQw4w9WgXcQ")
    assert yt_short.platform == "youtube"
    assert yt_short.kind == "video"
    assert yt_short.item_id == "dQw4w9WgXcQ"

    ytm_pl = detect_import_target("https://music.youtube.com/playlist?list=OLAK5uy_abc123")
    assert ytm_pl.platform == "youtube_music"
    assert ytm_pl.kind == "playlist"
    assert ytm_pl.item_id == "OLAK5uy_abc123"

    ytm_alb = detect_import_target("https://music.youtube.com/browse/MPREb_xyz999")
    assert ytm_alb.platform == "youtube_music"
    assert ytm_alb.kind == "album"
    assert ytm_alb.item_id == "MPREb_xyz999"

    ytm_vl = detect_import_target("https://music.youtube.com/browse/VLPLvl999")
    assert ytm_vl.platform == "youtube_music"
    assert ytm_vl.kind == "playlist"
    assert ytm_vl.item_id == "PLvl999"

    sp_pl = detect_import_target("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc")
    assert sp_pl.platform == "spotify"
    assert sp_pl.kind == "playlist"
    assert sp_pl.item_id == "37i9dQZF1DXcBWIGoYBM5M"
    assert sp_pl.canonical_url == "https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M"

    sp_intl_alb = detect_import_target("https://open.spotify.com/intl-en/album/4aawyAB9vmqN3uQ7FjRGTy")
    assert sp_intl_alb.platform == "spotify"
    assert sp_intl_alb.kind == "album"
    assert sp_intl_alb.canonical_url == "https://open.spotify.com/embed/album/4aawyAB9vmqN3uQ7FjRGTy"

    am_pl = detect_import_target("https://music.apple.com/us/playlist/today-hits/pl.f4d106fed2bd41148aaacabb133eb569")
    assert am_pl.platform == "apple_music"
    assert am_pl.kind == "playlist"

    am_alb = detect_import_target("https://music.apple.com/in/album/after-hours/1499378108")
    assert am_alb.platform == "apple_music"
    assert am_alb.kind == "album"

    with pytest.raises(ImportSourceError):
        detect_import_target("https://soundcloud.com/artist/sets/chill")

    with pytest.raises(ImportSourceError):
        detect_import_target("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_parse_spotify_embed_next_data_fixture() -> None:
    next_data = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "type": "playlist",
                            "name": "Late Night Orbit",
                            "trackList": [
                                {
                                    "title": "Blinding Lights",
                                    "subtitle": "The Weeknd",
                                    "duration": 200000,
                                },
                                {
                                    "title": "Midnight City",
                                    "artists": [{"name": "M83"}],
                                    "duration": 244000,
                                },
                            ],
                        }
                    }
                }
            }
        }
    }
    html_doc = (
        f"<html><head><script id=\"__NEXT_DATA__\" type=\"application/json\">"
        f"{json.dumps(next_data)}</script></head></html>"
    )
    parsed = parse_spotify_embed_html(html_doc)
    assert parsed.title == "Late Night Orbit"
    assert len(parsed.seeds) == 2
    assert parsed.seeds[0] == ImportTrackSeed(
        title="Blinding Lights", artist="The Weeknd", duration_s=200
    )
    assert parsed.seeds[1] == ImportTrackSeed(
        title="Midnight City", artist="M83", duration_s=244
    )

    with pytest.raises(ImportSourceError):
        parse_spotify_embed_html("<html><body>Private playlist</body></html>")


def test_parse_apple_music_json_ld_fixture() -> None:
    ld_json = {
        "@context": "https://schema.org",
        "@type": "MusicPlaylist",
        "name": "Synthwave Drive",
        "track": [
            {
                "@type": "MusicRecording",
                "name": "Nightcall",
                "byArtist": [{"@type": "MusicGroup", "name": "Kavinsky"}],
                "inAlbum": {"@type": "MusicAlbum", "name": "OutRun"},
                "duration": "PT4M18S",
            },
            {
                "@type": "MusicRecording",
                "name": "Resonance",
                "byArtist": {"name": "HOME"},
                "duration": "PT3M32S",
            },
        ],
    }
    html_doc = (
        f"<html><head><script type=\"application/ld+json\">"
        f"{json.dumps(ld_json)}</script></head></html>"
    )
    parsed = parse_apple_music_html(html_doc)
    assert parsed.title == "Synthwave Drive"
    assert len(parsed.seeds) == 2
    assert parsed.seeds[0].title == "Nightcall"
    assert parsed.seeds[0].artist == "Kavinsky"
    assert parsed.seeds[0].album == "OutRun"
    assert parsed.seeds[0].duration_s == 258
    assert parsed.seeds[1].duration_s == 212

    with pytest.raises(ImportSourceError):
        parse_apple_music_html("<html><body>Unavailable</body></html>")


def test_matcher_prefers_studio_over_penalized_variants() -> None:
    seed = ImportTrackSeed(title="Blinding Lights", artist="The Weeknd", duration_s=200)
    studio = Track(
        video_id="vid_studio1",
        title="Blinding Lights",
        artist="The Weeknd",
        duration_s=201,
    )
    karaoke = Track(
        video_id="vid_karaoke1",
        title="Blinding Lights (Karaoke Version)",
        artist="The Weeknd",
        duration_s=200,
    )
    sped_up = Track(
        video_id="vid_spedup1",
        title="Blinding Lights (Sped Up)",
        artist="The Weeknd",
        duration_s=172,
    )
    cover = Track(
        video_id="vid_cover1",
        title="Blinding Lights - Acoustic Cover",
        artist="Cover Band",
        duration_s=205,
    )

    assert score_match_candidate(seed, studio) > score_match_candidate(seed, karaoke)
    assert score_match_candidate(seed, studio) > score_match_candidate(seed, sped_up)
    assert score_match_candidate(seed, studio) > score_match_candidate(seed, cover)

    # Explicitly requested remix should not receive remix penalty
    remix_seed = ImportTrackSeed(title="Blinding Lights (Remix)", artist="The Weeknd", duration_s=210)
    remix_cand = Track(
        video_id="vid_remix1",
        title="Blinding Lights (Remix)",
        artist="The Weeknd",
        duration_s=210,
    )
    assert score_match_candidate(remix_seed, remix_cand) > 70.0


async def test_add_liked_tracks_idempotent(db_path: Path) -> None:
    lib = LibraryManager(db_path=db_path)
    t1 = Track(video_id="like_v1", title="Song One", artist="Artist A", duration_s=180)
    t2 = Track(video_id="like_v2", title="Song Two", artist="Artist B", duration_s=210)

    first_added = await lib.add_liked_tracks([t1, t2])
    assert first_added == 2
    assert await lib.is_liked("like_v1") is True
    assert await lib.is_liked("like_v2") is True

    async with get_connection(db_path) as conn:
        cur = await conn.execute(
            "SELECT liked_at FROM tracks WHERE video_id = ?;", ("like_v1",)
        )
        row = await cur.fetchone()
        original_liked_at = row["liked_at"]
        assert original_liked_at is not None

    await asyncio.sleep(0.01)
    t3 = Track(video_id="like_v3", title="Song Three", artist="Artist C", duration_s=195)
    second_added = await lib.add_liked_tracks([t1, t2, t3])
    assert second_added == 1
    assert await lib.is_liked("like_v1") is True
    assert await lib.is_liked("like_v2") is True
    assert await lib.is_liked("like_v3") is True

    async with get_connection(db_path) as conn:
        cur = await conn.execute(
            "SELECT liked_at FROM tracks WHERE video_id = ?;", ("like_v1",)
        )
        row = await cur.fetchone()
        assert row["liked_at"] == original_liked_at


async def test_import_conflicting_artist_and_cancel_during_commit(db_path: Path) -> None:
    library = LibraryManager(db_path=db_path)
    ytm = MagicMock(spec=YTMusicClient)
    manager = PlaylistImportManager(ytm=ytm, library=library)
    ytm.search = AsyncMock(return_value=[Track(video_id="other", title="Stay", artist="The Other Band", duration_s=200)])
    assert await manager._match_seed(ImportTrackSeed(title="Stay", artist="The Original Band", duration_s=200)) is None
    manager._fetch_source = AsyncMock(return_value=FetchedImportSource("Mix", [ImportTrackSeed(title="Stay", video_id="right")]))
    saving, release = asyncio.Event(), asyncio.Event()
    persist = library.add_liked_tracks

    async def delayed(tracks):
        saving.set()
        await release.wait()
        return await persist(tracks)

    library.add_liked_tracks = delayed
    job = await manager.start_import("https://www.youtube.com/playlist?list=PLtest", "liked")
    await saving.wait()
    assert manager.cancel_job(job.job_id).status == "saving"
    release.set()
    await manager._tasks[job.job_id]
    assert manager.get_job(job.job_id).status == "completed"
    assert await library.is_liked("right")


async def test_import_manager_dedupes_spotify_and_retains_max_five_jobs(db_path: Path) -> None:
    lib = LibraryManager(db_path=db_path)
    next_data = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "type": "playlist",
                            "name": "Dedupe Test Mix",
                            "trackList": [
                                {"title": "Track A", "subtitle": "Artist A", "duration": 180000},
                                {"title": "Track A (Live)", "subtitle": "Artist A", "duration": 182000},
                                {"title": "Track Missing", "subtitle": "Ghost", "duration": 150000},
                            ],
                        }
                    }
                }
            }
        }
    }
    embed_html = (
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=embed_html))

    mock_ytm = MagicMock(spec=YTMusicClient)

    async def _search(query: str, filter_type: str | None = "songs", limit: int = 5) -> list[Track]:
        if "Track Missing" in query:
            return []
        # Both Track A queries resolve to the same video_id to test deduplication
        return [
            Track(
                video_id="ytm_vid_a",
                title="Track A (Sped Up)",
                artist="Artist A",
                duration_s=150,
            ),
            Track(
                video_id="ytm_vid_a",
                title="Track A",
                artist="Artist A",
                duration_s=180,
            ),
        ]

    mock_ytm.search = AsyncMock(side_effect=_search)

    completed_events: list[str] = []
    manager = PlaylistImportManager(
        ytm=mock_ytm,
        library=lib,
        on_completed=lambda s: completed_events.append(s.job_id),
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
    )

    job = await manager.start_import(
        url="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        destination="new_playlist",
    )
    for _ in range(50):
        curr = manager.get_job(job.job_id)
        if curr and curr.status in ("completed", "error", "cancelled"):
            break
        await asyncio.sleep(0.02)

    final_job = manager.get_job(job.job_id)
    assert final_job is not None
    assert final_job.status == "completed"
    assert final_job.playlist_name == "Dedupe Test Mix"
    assert final_job.total == 3
    assert final_job.matched == 1
    assert final_job.failed == 1
    assert final_job.unmatched_items == ["Track Missing — Ghost"]
    assert final_job.target_playlist_id is not None
    assert completed_events == [job.job_id]

    saved_tracks = await lib.get_playlist_tracks(final_job.target_playlist_id)
    assert [t["video_id"] for t in saved_tracks] == ["ytm_vid_a"]

    # Retention check: enqueue 6 more fast YT imports and ensure <= 5 jobs kept
    manager._ytdlp_fetcher = lambda _url: FetchedImportSource(
        title="YT Fallback",
        seeds=[ImportTrackSeed(title="Direct YT", artist="Creator", video_id="yt_vid_1")],
    )
    mock_ytm._yt = MagicMock()
    mock_ytm._run_sync = MagicMock(side_effect=RuntimeError("use fallback"))
    for idx in range(6):
        j = await manager.start_import(
            url=f"https://www.youtube.com/playlist?list=PLkeep{idx}",
            destination="liked",
        )
        for _ in range(40):
            s = manager.get_job(j.job_id)
            if s and s.status in ("completed", "error", "cancelled"):
                break
            await asyncio.sleep(0.01)

    assert len(manager.list_jobs()) <= 5


def _build_test_client(db_path: Path, token: str = "import-test-token") -> tuple[TestClient, BridgeController]:
    mock_mpv = MagicMock()
    mock_resolver = MagicMock()
    mock_player = AudioPlayer(resolver=mock_resolver, mpv_instance=mock_mpv)

    mock_ytm = MagicMock(spec=YTMusicClient)
    mock_ytm._yt = MagicMock()
    mock_ytm._yt.get_playlist = MagicMock(
        return_value={
            "title": "YTM Favorites",
            "tracks": [
                {
                    "videoId": "ytm_direct_1",
                    "title": "Direct Hit",
                    "artists": [{"name": "Artist One", "id": "UC1"}],
                    "duration_seconds": 195,
                },
                {
                    "videoId": "ytm_direct_2",
                    "title": "Second Song",
                    "artists": [{"name": "Artist Two", "id": "UC2"}],
                    "duration_seconds": 205,
                },
            ],
        }
    )
    mock_ytm._run_sync = MagicMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    mock_ytm.search = AsyncMock(
        return_value=[Track(video_id="search_match_1", title="Matched", artist="Artist", duration_s=180)]
    )

    mock_downloader = MagicMock(spec=DownloadManager)
    mock_downloader.db_path = db_path
    mock_downloader.stop = MagicMock()
    mock_downloader.list_downloads = AsyncMock(return_value=[])

    ctrl = BridgeController(
        player=mock_player,
        queue=QueueManager(db_path=db_path, ytm_client=mock_ytm, auto_persist=False),
        library=LibraryManager(db_path=db_path),
        downloader=mock_downloader,
        ytm=mock_ytm,
        media_keys=MediaKeysListener(enabled=False),
        db_path=db_path,
        ws_manager=WebSocketManager(),
    )
    app = create_app(controller=ctrl, token=token)
    return TestClient(app), ctrl


def test_import_rest_endpoints_create_poll_cancel_and_auth(db_path: Path) -> None:
    token = "import-test-token"
    client, ctrl = _build_test_client(db_path, token=token)
    headers = {"X-Bridge-Token": token}

    with client:
        # Unauthenticated requests rejected
        unauth = client.post(
            "/import/url",
            json={"url": "https://music.youtube.com/playlist?list=PLabc123", "destination": "new_playlist"},
        )
        assert unauth.status_code == 401

        # Unsupported domain returns 400
        bad = client.post(
            "/import/url",
            headers=headers,
            json={"url": "https://deezer.com/playlist/123", "destination": "new_playlist"},
        )
        assert bad.status_code == 400

        # Unknown existing playlist returns 404
        missing_pl = client.post(
            "/import/url",
            headers=headers,
            json={
                "url": "https://music.youtube.com/playlist?list=PLabc123",
                "destination": "existing_playlist",
                "target_playlist_id": "non-existent-id",
            },
        )
        assert missing_pl.status_code == 404

        # Valid YouTube Music playlist import into new playlist
        resp = client.post(
            "/import/url",
            headers=headers,
            json={
                "url": "https://music.youtube.com/playlist?list=PLabc123",
                "destination": "new_playlist",
                "playlist_name": "My YTM Import",
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        final_state = None
        for _ in range(40):
            polled = client.get(f"/import/jobs/{job_id}", headers=headers)
            assert polled.status_code == 200
            final_state = polled.json()
            if final_state["status"] in ("completed", "error", "cancelled"):
                break
            time.sleep(0.02)

        assert final_state is not None
        assert final_state["status"] == "completed"
        assert final_state["playlist_name"] == "My YTM Import"
        assert final_state["matched"] == 2
        created_pid = final_state["target_playlist_id"]
        assert created_pid

        tracks_resp = client.get(f"/playlists/{created_pid}/tracks", headers=headers)
        assert tracks_resp.status_code == 200
        assert [t["video_id"] for t in tracks_resp.json()] == ["ytm_direct_1", "ytm_direct_2"]

        # Slow Spotify search job cancellation via POST /import/jobs/{id}/cancel
        embed_html = (
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(
                {
                    "props": {
                        "pageProps": {
                            "state": {
                                "data": {
                                    "entity": {
                                        "type": "playlist",
                                        "name": "Slow Cancel Mix",
                                        "trackList": [
                                            {"title": f"Song {i}", "subtitle": "Slow Artist", "duration": 180000}
                                            for i in range(6)
                                        ],
                                    }
                                }
                            }
                        }
                    }
                }
            )
            + "</script>"
        )

        async def _slow_search(*_args: object, **_kwargs: object) -> list[Track]:
            await asyncio.sleep(0.25)
            return []

        ctrl.ytm.search = AsyncMock(side_effect=_slow_search)
        slow_transport = httpx.MockTransport(lambda req: httpx.Response(200, text=embed_html))
        ctrl.importer._http_client_factory = lambda: httpx.AsyncClient(transport=slow_transport)

        slow_start = client.post(
            "/import/url",
            headers=headers,
            json={"url": "https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd", "destination": "liked"},
        )
        assert slow_start.status_code == 200
        slow_job_id = slow_start.json()["job_id"]

        cancel_resp = client.post(f"/import/jobs/{slow_job_id}/cancel", headers=headers)
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        # Missing job IDs return 404
        assert client.get("/import/jobs/does_not_exist", headers=headers).status_code == 404
        assert client.post("/import/jobs/does_not_exist/cancel", headers=headers).status_code == 404

        # Settings round-trip persists one-time v1.4 promo flag
        put_res = client.put("/settings", headers=headers, json={"import_promo_v1_4_seen": True})
        assert put_res.status_code == 200
        get_res = client.get("/settings", headers=headers)
        assert get_res.status_code == 200
        assert get_res.json().get("import_promo_v1_4_seen") is True
