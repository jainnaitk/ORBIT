"""Comprehensive backend & bridge API tests for ORBIT v1.3.0 (Phase C + Artist Library)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from metrolite.audio.player import AudioPlayer
from metrolite.audio.queue import QueueManager
from metrolite.bridge.server import BridgeController, create_app
from metrolite.library.manager import (
    LibraryManager,
    artist_matches_blocked,
    normalize_artist_name,
    split_artist_names,
)
from metrolite.store.db import get_connection, init_db
from metrolite.ytm.client import Track


class _FakeMpvForDetails:
    """Fake libmpv instance exposing runtime properties without spawning DLLs."""

    audio_codec_name = "opus"
    audio_bitrate = 128_000
    file_size = 4_200_000
    path = "https://rr1---sn.googlevideo.com/videoplayback?mime=audio/webm"
    pause = False
    time_pos = 12.0
    duration = 210.0

    def __init__(self) -> None:
        self.audio_params = {"samplerate": 48000, "channel-count": 2, "channels": "stereo"}
        self._props = {
            "audio-codec-name": "opus",
            "audio-bitrate": 128_000,
            "audio-params/samplerate": 48000,
            "audio-params/channel-count": 2,
            "file-size": 4_200_000,
            "path": self.path,
        }

    def _get_property(self, name: str):
        return self._props.get(name)

    def terminate(self) -> None:
        pass


class _DummyResolver:
    def is_cached(self, video_id: str) -> bool:
        return False

    async def resolve(self, track_or_id) -> dict[str, str]:
        vid = track_or_id if isinstance(track_or_id, str) else track_or_id.video_id
        return {"url": f"https://stream.example/{vid}"}


class _DummyDownloader:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.deleted_ids: list[str] = []

    def add_progress_listener(self, cb) -> None:
        pass

    def queue_download(self, video_id: str, track_meta=None) -> None:
        pass

    async def delete_download(self, video_id: str) -> bool:
        self.deleted_ids.append(video_id)
        return True

    async def list_downloads(self) -> list[dict]:
        return []

    def stop(self) -> None:
        pass


class _FakePlayer:
    def __init__(self) -> None:
        self.resolver = _DummyResolver()
        self.state = "idle"
        self.time_pos = 0.0
        self.duration = 200.0
        self.volume = 80
        self.current_track: Track | None = None
        self.next_track: Track | None = None
        self.loaded_video_id: str | None = None
        self.consecutive_errors = 0

    def set_callbacks(self, **kwargs) -> None:
        pass

    def reset_errors(self) -> None:
        self.consecutive_errors = 0

    def record_error(self) -> None:
        self.consecutive_errors += 1

    def clear_preloaded_next(self) -> None:
        self.next_track = None

    def get_live_time_pos(self) -> float:
        return self.time_pos

    async def load_and_play(self, track: Track, *, is_current=None) -> None:
        self.current_track = track
        self.loaded_video_id = track.video_id
        self.duration = float(track.duration_s or 200)
        self.state = "playing"

    async def preload_next(self, track: Track) -> None:
        self.next_track = track

    def switch_to_preloaded_next(self, track: Track) -> bool:
        self.current_track = track
        self.loaded_video_id = track.video_id
        self.state = "playing"
        return True

    def pause(self) -> None:
        self.state = "paused"

    def resume(self) -> None:
        self.state = "playing"

    def seek(self, seconds: float) -> None:
        self.time_pos = seconds

    def set_volume(self, vol: int) -> None:
        self.volume = vol

    def stop(self) -> None:
        self.state = "stopped"

    def terminate(self) -> None:
        pass

    def get_audio_details(self) -> dict:
        curr = self.current_track
        return {
            "video_id": curr.video_id if curr else None,
            "title": curr.title if curr else None,
            "artist": curr.artist if curr else None,
            "album": curr.album if curr else None,
            "duration_s": curr.duration_s if curr else 0,
            "codec": "opus",
            "bitrate_kbps": 128,
            "sample_rate_hz": 48000,
            "channel_count": 2,
            "channel_layout": "stereo",
            "file_size_bytes": 3_600_000,
            "path": "https://stream.example/audio",
            "state": self.state,
        }


def _make_ytm_mock() -> AsyncMock:
    ytm = AsyncMock()
    ytm.get_home_sections = AsyncMock(
        return_value=[
            {
                "title": "Quick Picks",
                "contents": [
                    Track(video_id="blocked1_vid", title="Blocked Song", artist="Bad Artist"),
                    Track(video_id="good1_vid_01", title="Good Song", artist="Arijit Singh"),
                ],
            }
        ]
    )
    ytm.search = AsyncMock(
        return_value=[
            Track(video_id="blocked2_vid", title="Noisy Track", artist="Bad Artist feat. Other"),
            Track(video_id="good2_vid_02", title="Clean Track", artist="Pritam"),
        ]
    )
    ytm.get_watch_radio = AsyncMock(
        return_value=[
            Track(video_id="radio_blk_01", title="Radio Blocked", artist="Bad Artist"),
            Track(video_id="radio_ok_001", title="Radio Good 1", artist="Pritam"),
            Track(video_id="radio_ok_002", title="Radio Good 2", artist="Arijit Singh"),
        ]
    )
    ytm.get_artist_profile = AsyncMock(
        return_value={
            "channel_id": "UC_ARTIST_01",
            "name": "Arijit Singh",
            "artist": "Arijit Singh",
            "subscribers": "50M",
            "views": None,
            "description": "Indian playback singer.",
            "thumbnail_url": "https://img.example/arijit.jpg",
            "top_songs": [
                {
                    "video_id": "arijit_top1",
                    "title": "Tum Hi Ho",
                    "artist": "Arijit Singh",
                    "artist_id": "UC_ARTIST_01",
                    "duration_s": 262,
                }
            ],
            "popular_tracks": [
                {
                    "video_id": "arijit_top1",
                    "title": "Tum Hi Ho",
                    "artist": "Arijit Singh",
                    "artist_id": "UC_ARTIST_01",
                    "duration_s": 262,
                }
            ],
            "all_tracks": [
                {
                    "video_id": "arijit_top1",
                    "title": "Tum Hi Ho",
                    "artist": "Arijit Singh",
                    "artist_id": "UC_ARTIST_01",
                    "duration_s": 262,
                }
            ],
            "albums": [
                {
                    "browse_id": "MPRE_ALB_1",
                    "browseId": "MPRE_ALB_1",
                    "title": "Aashiqui 2",
                    "year": "2013",
                    "type": "Album",
                    "thumbnail_url": "https://img.example/alb.jpg",
                }
            ],
            "singles": [
                {
                    "browse_id": "MPRE_SNG_1",
                    "browseId": "MPRE_SNG_1",
                    "title": "Satranga",
                    "year": "2024",
                    "type": "Single",
                    "thumbnail_url": "https://img.example/sng.jpg",
                }
            ],
            "related_artists": [
                {
                    "name": "Shreya Ghoshal",
                    "browse_id": "UC_SHREYA_01",
                    "channel_id": "UC_SHREYA_01",
                    "subscribers": "20M",
                    "thumbnail_url": "https://img.example/shreya.jpg",
                }
            ],
        }
    )
    return ytm


def test_artist_helpers() -> None:
    assert normalize_artist_name("  Arijit   Singh  ") == "arijit singh"
    assert split_artist_names("Arijit Singh, Pritam feat. Amitabh • Shreya Ghoshal") == [
        "Arijit Singh",
        "Pritam",
        "Amitabh",
        "Shreya Ghoshal",
    ]
    blocked = {normalize_artist_name("Pritam")}
    assert artist_matches_blocked("Arijit Singh, Pritam", blocked)
    assert not artist_matches_blocked("Shreya Ghoshal", blocked)


def test_audio_player_get_audio_details() -> None:
    fake_mpv = _FakeMpvForDetails()
    player = AudioPlayer(mpv_instance=fake_mpv)
    player.current_track = Track(
        video_id="dQw4w9WgXcQ",
        title="Track One",
        artist="Artist One",
        album="Album One",
        duration_s=210,
    )
    details = player.get_audio_details()
    assert details["codec"] == "opus"
    assert details["bitrate_kbps"] == 128
    assert details["sample_rate_hz"] == 48000
    assert details["channel_count"] == 2
    assert details["channel_layout"] == "stereo"
    assert details["file_size_bytes"] == 4_200_000


def test_schema_migrations_and_library_phase_c(tmp_path: Path) -> None:
    db_path = tmp_path / "phase_c.db"

    async def _run() -> None:
        await init_db(db_path)
        async with get_connection(db_path) as conn:
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            )
            tables = {r["name"] for r in await cur.fetchall()}
            assert {
                "play_events",
                "pinned_tracks",
                "blocked_artists",
                "followed_artists",
                "pinned_artists",
                "artist_releases_cache",
            }.issubset(tables)

        lib = LibraryManager(db_path=db_path)
        t1 = Track(video_id="trk_aaa_111", title="Alpha Song", artist="Arijit Singh, Pritam", duration_s=180)
        t2 = Track(video_id="trk_bbb_222", title="Beta Song", artist="Shreya Ghoshal", duration_s=200)

        await lib.toggle_like(t1.video_id, t1)
        await lib.record_play(t2.video_id, t2)

        # Pins
        pins = await lib.pin_track(t1.video_id, t1)
        assert len(pins["tracks"]) == 1
        pins = await lib.pin_artist("UC_ARTIST_01", "Arijit Singh", "https://img.example/a.jpg")
        assert len(pins["artists"]) == 1
        assert await lib.unpin_track(t1.video_id)
        assert await lib.unpin_artist("UC_ARTIST_01")

        # Update metadata
        updated = await lib.update_track_metadata(t1.video_id, title="Alpha Edited", artist="Arijit Singh", album="New Album")
        assert updated is not None
        assert updated["title"] == "Alpha Edited"
        assert updated["album"] == "New Album"

        # History remove
        assert await lib.remove_from_history(t2.video_id)
        hist = await lib.get_history(limit=20)
        assert all(h["video_id"] != t2.video_id for h in hist)

        # Play events & stats timeframes
        now = datetime.now(UTC)
        eid = await lib.record_play_event(
            t1.video_id,
            listened_s=45,
            played_at=(now - timedelta(days=2)).isoformat(),
            track_meta=t1,
        )
        await lib.update_play_event_duration(eid, 90)
        await lib.record_play_event(
            t2.video_id,
            listened_s=60,
            played_at=(now - timedelta(days=40)).isoformat(),
            track_meta=t2,
        )
        stats_1w = await lib.get_stats("1w")
        stats_all = await lib.get_stats("all")
        assert stats_1w["total_plays"] == 1
        assert stats_1w["total_seconds"] == 90
        assert stats_all["total_plays"] == 2
        assert stats_all["total_seconds"] == 150
        assert len(stats_all["top_tracks"]) == 2
        assert stats_all["top_tracks"][0]["listened_s"] == stats_all["top_tracks"][0]["seconds"]
        assert len(stats_all["top_artists"]) >= 2
        assert stats_all["top_artists"][0]["name"] == stats_all["top_artists"][0]["artist"]
        assert stats_all["top_artists"][0]["listened_s"] > 0
        assert stats_all["top_artists"][0]["artist_norm"]

        # Artist library & local tracks
        await lib.follow_artist("UC_ARTIST_01", "Arijit Singh", "https://img.example/a.jpg")
        art_lib = await lib.list_artists_library()
        assert any(a["name"] == "Arijit Singh" for a in art_lib["followed"])
        # Shreya Ghoshal wasn't liked/offline (only had play recorded then removed? play_count=1 still > 0, liked=0):
        await lib.toggle_like(t2.video_id, t2)
        art_lib2 = await lib.list_artists_library()
        assert any("shreya" in a["artist_norm"] for a in art_lib2["in_library"])

        local_arijit = await lib.get_local_artist_tracks(name="Arijit Singh")
        assert len(local_arijit) == 1
        assert local_arijit[0]["video_id"] == t1.video_id

    asyncio.run(_run())


def test_queue_split_tagging_promote_and_blocked_filter() -> None:
    async def _run() -> None:
        ytm = _make_ytm_mock()
        blocked = {normalize_artist_name("Bad Artist")}
        q = QueueManager(
            ytm_client=ytm,
            auto_persist=False,
            blocked_artists_fn=lambda: set(blocked),
        )
        seed = Track(video_id="seed_vid_001", title="Seed", artist="Arijit Singh")
        q.set_queue([seed], start_index=0)
        assert q.items[0].source == "user"

        added = await q.refill_radio(limit=10)
        assert added == 3  # 2 watch radio + 1 clean contextual search; Bad Artist filtered out
        assert all("bad artist" not in item.artist.lower() for item in q.items)
        assert all(item.source == "radio" for item in q.items[1:])

        # Adding next or appending inserts user items BEFORE radio suggestions
        manual = Track(video_id="manual_vid_9", title="Manual Pick", artist="Artist M")
        q.append(manual)
        assert q.items[1].video_id == "manual_vid_9"
        assert q.items[1].source == "user"
        assert q.items[2].source == "radio"

        # Promoting a radio item makes it 'user' and places it at the end of user block
        radio_idx = next(i for i, t in enumerate(q.items) if t.source == "radio")
        promoted_vid = q.items[radio_idx].video_id
        assert q.promote(radio_idx)
        assert q.items[2].video_id == promoted_vid
        assert q.items[2].source == "user"

    asyncio.run(_run())


def test_bridge_endpoints_phase_c(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge_phase_c.db"
    player = _FakePlayer()
    ytm = _make_ytm_mock()
    ctrl = BridgeController(
        player=player,  # type: ignore[arg-type]
        downloader=_DummyDownloader(db_path),  # type: ignore[arg-type]
        ytm=ytm,
        db_path=db_path,
    )
    app = create_app(controller=ctrl, token="phase-c-token")
    headers = {"X-Bridge-Token": "phase-c-token"}

    with TestClient(app) as client:
        # 1. Play track & verify /player/details
        r = client.post(
            "/player/play",
            headers=headers,
            json={
                "video_id": "trk_live_001",
                "track": {"video_id": "trk_live_001", "title": "Live Song", "artist": "Arijit Singh", "duration_s": 210},
            },
        )
        assert r.status_code == 200
        det = client.get("/player/details", headers=headers)
        assert det.status_code == 200
        assert det.json()["codec"] == "opus"
        assert det.json()["bitrate_kbps"] == 128

        # 2. Block artist filters /home/personalized
        blk = client.post("/privacy/blocked-artists", headers=headers, json={"name": "Bad Artist"})
        assert blk.status_code == 200
        home = client.get("/home/personalized?refresh=true", headers=headers)
        assert home.status_code == 200
        home_data = home.json()
        for sec in home_data["sections"]:
            for item in sec["contents"]:
                assert "bad artist" not in (item.get("artist") or "").lower()

        blist = client.get("/privacy/blocked-artists", headers=headers)
        assert blist.status_code == 200
        assert len(blist.json()) == 1
        assert client.delete("/privacy/blocked-artists/Bad%20Artist", headers=headers).status_code == 200

        # 3. Pins & PATCH track metadata
        pin_res = client.post(
            "/pins/trk_live_001",
            headers=headers,
            json={"track_meta": {"video_id": "trk_live_001", "title": "Live Song", "artist": "Arijit Singh", "duration_s": 210}},
        )
        assert pin_res.status_code == 200
        assert len(pin_res.json()["tracks"]) == 1

        patch_res = client.patch(
            "/tracks/trk_live_001",
            headers=headers,
            json={"title": "Renamed Song", "artist": "Arijit Singh", "album": "Studio Edition"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["title"] == "Renamed Song"

        # 4. Stats endpoint validation + payload
        assert client.get("/stats?range=1m", headers=headers).status_code == 200
        assert client.get("/stats?range=bad_range", headers=headers).status_code == 400

        # 5. Follow artist + related artists + local songs + artist radio + new releases
        f_res = client.post(
            "/artists/follow",
            headers=headers,
            json={"channel_id": "UC_ARTIST_01", "name": "Arijit Singh", "thumbnail_url": "https://img.example/a.jpg"},
        )
        assert f_res.status_code == 200

        art_profile = client.get("/artist?name=Arijit%20Singh", headers=headers)
        assert art_profile.status_code == 200
        ap = art_profile.json()
        assert ap["is_followed"] is True
        assert len(ap["related_artists"]) == 1
        assert ap["related_artists"][0]["name"] == "Shreya Ghoshal"

        art_hub = client.get("/artists/library", headers=headers)
        assert art_hub.status_code == 200
        assert any(a["name"] == "Arijit Singh" for a in art_hub.json()["followed"])

        rel_res = client.get("/artists/new-releases?refresh=true", headers=headers)
        assert rel_res.status_code == 200
        assert len(rel_res.json()) >= 1
        assert rel_res.json()[0]["title"] == "Satranga"

        radio_res = client.post(
            "/artists/radio",
            headers=headers,
            json={"channel_id": "UC_ARTIST_01", "name": "Arijit Singh"},
        )
        assert radio_res.status_code == 200
        assert len(radio_res.json()["queue"]["items"]) >= 1

        # 6. Surprise radio + Queue promote + local artist tracks
        surp = client.post("/radio/surprise", headers=headers, json={"seed_video_id": "trk_live_001"})
        assert surp.status_code == 200
        assert len(surp.json()["queue"]["items"]) >= 1

        loc = client.get("/artists/local?name=Arijit%20Singh", headers=headers)
        assert loc.status_code == 200

        # Add a simulated radio track and promote it via POST /queue
        ctrl.queue.items.append(
            Track(video_id="sim_radio_1", title="Suggested", artist="Pritam", source="radio")
        )
        radio_i = len(ctrl.queue.items) - 1
        prom = client.post(
            "/queue",
            headers=headers,
            json={"action": "promote", "index": radio_i},
        )
        assert prom.status_code == 200
        promoted_item = next(
            it for it in prom.json()["items"] if it["video_id"] == "sim_radio_1"
        )
        assert promoted_item["source"] == "user"

        # Verify global drag-and-drop move with explicit target_source
        mv = client.post(
            "/queue",
            headers=headers,
            json={
                "action": "move",
                "from_idx": len(ctrl.queue.items) - 1,
                "to_idx": 1,
                "target_source": "user",
            },
        )
        assert mv.status_code == 200


def test_queue_22_active_before_suggestions_and_surprise_excludes_home(tmp_path: Path) -> None:
    """Verify queue builds 20-25 active songs first before radio suggestions & Surprise excludes home tracks."""
    async def _run() -> None:
        many_radio = [
            Track(video_id=f"r_trk_{i:02d}", title=f"Radio Song {i}", artist=f"Artist {i % 7}", duration_s=195)
            for i in range(28)
        ]
        ytm = _make_ytm_mock()
        ytm.get_watch_radio = AsyncMock(return_value=many_radio)

        q = QueueManager(ytm_client=ytm, auto_persist=False)
        q.set_queue([Track(video_id="seed_00", title="Start Song", artist="Artist 0", duration_s=190)])

        added = await q.refill_radio(limit=28, auto_promote_until=22)
        assert added >= 22
        upcoming = q.items[1:]
        assert all(t.source == "user" for t in upcoming[:22])
        if len(upcoming) > 22:
            assert all(t.source == "radio" for t in upcoming[22:])

        # Dragging a radio suggestion up into user range promotes it automatically
        radio_idx = next(i for i, t in enumerate(q.items) if t.source == "radio")
        moved_vid = q.items[radio_idx].video_id
        q.move(radio_idx, 1, target_source="user")
        assert q.items[1].video_id == moved_vid
        assert q.items[1].source == "user"

        # Surprise Me endpoint excludes cached Home + previously played tracks
        db_path = tmp_path / "surprise_test.db"
        ctrl = BridgeController(
            player=_FakePlayer(),  # type: ignore[arg-type]
            downloader=_DummyDownloader(db_path),  # type: ignore[arg-type]
            ytm=ytm,
            db_path=db_path,
        )
        ytm.search = AsyncMock(
            return_value=[
                Track(video_id="home_dup_1", title="Home Track", artist="Artist H", duration_s=200),
                Track(video_id="fresh_s_01", title="Fresh Discovery 1", artist="Artist D1", duration_s=210),
                Track(video_id="fresh_s_02", title="Fresh Discovery 2", artist="Artist D2", duration_s=215),
            ]
        )
        app = create_app(controller=ctrl, token="surp-tok")
        with TestClient(app) as client:
            # Home is loaded after startup refreshes the blocked-artist cache.
            ctrl._home_cache["for_you"] = (
                9e12,
                {
                    "greeting": "Hi",
                    "speed_dial": [{"video_id": "home_dup_1", "title": "Home Track"}],
                    "sections": [],
                },
            )
            res = client.post("/radio/surprise", headers={"X-Bridge-Token": "surp-tok"})
            assert res.status_code == 200
            q_vids = [it["video_id"] for it in res.json()["queue"]["items"]]
            assert "fresh_s_01" in q_vids
            assert "fresh_s_02" in q_vids
            assert q_vids[0] != "home_dup_1"

    asyncio.run(_run())
