"""Local bridge server (FastAPI & WebSocket) for MetroLite."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import random
import secrets
import socket
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from metrolite.audio.media_keys import MediaKeysListener
from metrolite.audio.player import AudioPlayer, trim_memory
from metrolite.audio.queue import QueueManager
from metrolite.audio.resolver import StreamResolutionError
from metrolite.config import get_config
from metrolite.library.downloader import DownloadManager, DownloadProgress, validate_video_id
from metrolite.library.importer import (
    ImportDestination,
    ImportJobState,
    ImportSourceError,
    PlaylistImportManager,
    detect_import_target,
)
from metrolite.library.manager import (
    LibraryManager,
    artist_matches_blocked,
    normalize_artist_name,
    split_artist_names,
)
from metrolite.store.db import get_connection, get_default_db_path, init_db
from metrolite.updates import UpdateManager
from metrolite.ytm.client import Track, YTMusicClient

logger = logging.getLogger(__name__)

_ALLOWED_ORIGIN_REGEX = r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|https?://tauri\.localhost|tauri://localhost)$"
_ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "tauri.localhost"}


def _is_allowed_local_origin(origin: str | None) -> bool:
    if not origin:
        return False
    parsed = urlparse(origin)
    return parsed.scheme in ("http", "https", "tauri") and parsed.hostname in _ALLOWED_LOCAL_HOSTS


def _require_valid_video_id(video_id: str) -> str:
    try:
        return validate_video_id(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

_NON_COMPLETION_REASONS = {"aborted", "stop", "restarted", "quit", "redirect"}

_TRENDING_QUERIES = [
    "top trending songs 2026",
    "latest viral music hits india",
    "trending global music charts",
    "new hit songs bollywood pop",
]

_SURPRISE_DISCOVERY_QUERIES = [
    "underrated indie pop songs",
    "modern soulful r&b hits",
    "melodic electronic vocal tracks",
    "fresh indie hindi gems",
    "synthwave midnight drive tracks",
    "alternative chill rock hits",
    "global acoustic session hits",
    "fresh punjabi chill vibes",
    "late night groove jazz pop",
    "feel good summer indie hits",
]

_MOOD_CONFIG: dict[str, list[tuple[str, str, str, str, list[str]]]] = {
    "trending": [
        (
            "picks",
            "compact_grid",
            "Trending Right Now",
            "Hottest tracks climbing the charts",
            ["top trending songs 2026", "viral hits india 2026"],
        ),
        (
            "global",
            "cards",
            "Global Pop & Hits",
            "Worldwide chart-toppers and breaking singles",
            ["global top songs", "billboard hot hits"],
        ),
        (
            "viral",
            "cards",
            "Fresh Viral & Rising",
            "Tracks surging across playlists this week",
            ["new viral trending songs", "new music charts"],
        ),
    ],
    "bollywood": [
        (
            "picks",
            "compact_grid",
            "Bollywood & Desi Hits",
            "Latest Hindi, Punjabi & indie favorites",
            ["latest hindi bollywood hits", "trending punjabi hindi songs"],
        ),
        (
            "romantic",
            "cards",
            "Romantic & Soulful Melodies",
            "Timeless Hindi love songs and acoustic vocals",
            ["arijit singh top songs", "bollywood romantic unplugged hits"],
        ),
        (
            "urban",
            "cards",
            "Punjabi & Desi Urban",
            "High-rotation Punjabi drops and desi pop anthems",
            ["trending punjabi pop songs", "indian indie pop hits"],
        ),
    ],
    "chill": [
        (
            "picks",
            "compact_grid",
            "Chill & Lo-Fi Vibes",
            "Laid-back beats to unwind",
            ["chill lofi beats relax", "aesthetic chill indie"],
        ),
        (
            "desilofi",
            "cards",
            "Hindi Lo-Fi & Unplugged",
            "Warm evening acoustics and mellow lo-fi",
            ["hindi lofi chill mix", "bollywood acoustic chill"],
        ),
        (
            "dream",
            "cards",
            "Dreamy Indie & Coffeehouse",
            "Smooth downtempo grooves and soft guitars",
            ["chill indie pop songs", "coffeehouse acoustic chill"],
        ),
    ],
    "focus": [
        (
            "picks",
            "compact_grid",
            "Deep Focus & Study",
            "Distraction-free instrumental flow",
            ["deep focus study instrumental", "lofi study beats concentration"],
        ),
        (
            "ambient",
            "cards",
            "Ambient & Peaceful Piano",
            "Calm soundscapes for coding, reading & deep work",
            ["ambient focus music", "peaceful piano instrumental study"],
        ),
        (
            "synth",
            "cards",
            "Minimal Synth & Neoclassical",
            "Steady instrumental textures for sustained concentration",
            ["neoclassical focus instrumental", "chill synthwave study"],
        ),
    ],
    "workout": [
        (
            "picks",
            "compact_grid",
            "Workout Energy",
            "High-tempo tracks to keep you moving",
            ["high energy workout gym music", "punjabi workout bass hits"],
        ),
        (
            "pump",
            "cards",
            "Gym & Power Anthems",
            "Heavy drops, hip-hop & adrenaline bangers",
            ["gym motivation hip hop workout", "desi hip hop workout hits"],
        ),
        (
            "cardio",
            "cards",
            "Cardio & Running Tempo",
            "Upbeat electronic & pop to lock in your pace",
            ["electronic workout pump", "cardio running hits"],
        ),
    ],
    "latenight": [
        (
            "picks",
            "compact_grid",
            "Late Night Drives",
            "Moody acoustics and midnight melodies",
            ["late night drive hindi indie", "midnight acoustic chill songs"],
        ),
        (
            "afterhours",
            "cards",
            "After Hours & Midnight R&B",
            "Velvet synths and dark-lit city vibes",
            ["late night r&b chill vibes", "midnight drive pop indie"],
        ),
        (
            "unplugged",
            "cards",
            "2 AM Acoustic & Soul",
            "Stripped-back ballads for quiet hours",
            ["slowed reverb night vibes", "midnight acoustic ballads"],
        ),
    ],
}


def _time_greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def find_available_port(
    host: str = "127.0.0.1", start_port: int = 8766, max_attempts: int = 100
) -> int:
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"No available port found on {host} between {start_port} and {start_port + max_attempts}"
    )


def write_bridge_file(bridge_path: Path, host: str, port: int, token: str) -> None:
    bridge_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": host,
        "port": port,
        "token": token,
    }
    tmp_path = bridge_path.with_name(f"{bridge_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if os.name != "nt":
        with suppress(OSError):
            os.chmod(tmp_path, 0o600)
    tmp_path.replace(bridge_path)
    logger.info("Bridge configuration written to %s", bridge_path)


def remove_bridge_file(bridge_path: Path) -> None:
    try:
        if bridge_path.is_file():
            bridge_path.unlink(missing_ok=True)
            logger.info("Bridge configuration removed from %s", bridge_path)
    except OSError as e:
        logger.debug("Failed to remove bridge file %s: %s", bridge_path, e)


class WebSocketManager:
    """Manages active WebSocket connections and broadcasting."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.active_connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.active_connections.discard(ws)

    async def broadcast(self, event: str, data: Any) -> None:
        async with self._lock:
            if not self.active_connections:
                return
            snapshot = list(self.active_connections)

        payload = json.dumps({"event": event, "data": data})

        stale: list[WebSocket] = []
        for ws in snapshot:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                stale.append(ws)

        if stale:
            async with self._lock:
                for ws in stale:
                    self.active_connections.discard(ws)

    def broadcast_sync(
        self, event: str, data: Any, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(event, data), loop)


class BridgeController:
    """Orchestrates audio player, queue, library, downloads, and websockets."""

    def __init__(
        self,
        player: AudioPlayer | None = None,
        queue: QueueManager | None = None,
        library: LibraryManager | None = None,
        downloader: DownloadManager | None = None,
        ytm: YTMusicClient | None = None,
        media_keys: MediaKeysListener | None = None,
        db_path: Path | None = None,
        ws_manager: WebSocketManager | None = None,
        updater: UpdateManager | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self.ws_manager = ws_manager or WebSocketManager()
        self.player = player or AudioPlayer()
        self.ytm = ytm or YTMusicClient()
        self._blocked_norms: set[str] = set()
        self.queue = queue or QueueManager(
            db_path=self.db_path,
            ytm_client=self.ytm,
            auto_persist=True,
            blocked_artists_fn=lambda: set(self._blocked_norms),
        )
        if getattr(self.queue, "blocked_artists_fn", None) is None:
            self.queue.blocked_artists_fn = lambda: set(self._blocked_norms)
        self.library = library or LibraryManager(db_path=self.db_path)
        self.downloader = downloader or DownloadManager(db_path=self.db_path)
        self.updater = updater or UpdateManager()
        self.importer = PlaylistImportManager(
            ytm=self.ytm,
            library=self.library,
            on_progress=self._on_import_progress,
            on_completed=self._on_import_completed,
        )
        self.media_keys = media_keys
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._radio_task: asyncio.Task[None] | None = None
        self._preload_task: asyncio.Task[None] | None = None
        self._adjacent_prefetch_task: asyncio.Task[None] | None = None
        self._lyrics_prefetch_task: asyncio.Task[None] | None = None
        self._releases_refresh_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._play_seq: int = 0
        self._audio_command_seq = 0
        self._user_intent = 0
        self._shutdown: bool = False
        self._home_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._history_revision = 0
        self._artist_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._album_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._history_threshold_s: float = 30.0
        self._pause_listening_history: bool = False
        self._play_occurrence = 0
        self._session_occurrence = 0
        self._play_session_vid: str | None = None
        self._play_session_track: Track | None = None
        self._play_session_listened_s: float = 0.0
        self._play_session_last_ts: float | None = None
        self._play_session_event_id: int | None = None
        self._play_session_last_flushed_s: int = 0
        self._history_lock = asyncio.Lock()
        self._surprise_lock: asyncio.Lock = asyncio.Lock()
        self._surprise_last_ts: float = 0.0

        self._bind_callbacks()

    def spawn_task(self, coro: Any) -> asyncio.Task[Any]:
        """Spawn an asyncio background task with strong-reference retention until completion."""
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    logger.debug("Background task failed: %s", exc)

        task.add_done_callback(_done)
        return task

    def _bind_callbacks(self) -> None:
        self.player.set_callbacks(
            on_track_ended=self._on_player_track_ended,
            on_state_changed=self._on_player_state_changed,
        )
        self.queue._on_queue_changed = self._on_queue_changed
        if hasattr(self.downloader, "add_progress_listener"):
            self.downloader.add_progress_listener(self._on_download_progress)
        if hasattr(self.updater, "add_listener"):
            self.updater.add_listener(self._on_update_status)

        if self.media_keys is not None:
            self.media_keys.on_play_pause = self._on_media_key_play_pause
            self.media_keys.on_next = self._on_media_key_next
            self.media_keys.on_prev = self._on_media_key_prev

    def _on_update_status(self, state: dict[str, Any]) -> None:
        self.ws_manager.broadcast_sync("update_status", state, self.loop)

    def _on_import_progress(self, state: ImportJobState) -> None:
        self.ws_manager.broadcast_sync("import_progress", state.model_dump(), self.loop)

    def _on_import_completed(self, state: ImportJobState) -> None:
        if state.status != "completed":
            return
        self.ws_manager.broadcast_sync(
            "library_changed",
            {
                "type": "playlist_imported",
                "destination": state.destination,
                "playlist_id": state.target_playlist_id,
                "matched": state.matched,
            },
            self.loop,
        )

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def run_coro_threadsafe(self, coro: Any) -> None:
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        else:
            try:
                asyncio.get_running_loop()
                self.spawn_task(coro)
            except RuntimeError:
                pass

    def _on_player_state_changed(self, _new_state: str) -> None:
        tick_data = self.get_playback_tick_data()
        self.ws_manager.broadcast_sync("playback_tick", tick_data, self.loop)

    def _on_player_track_ended(self, reason: str = "eof") -> None:
        # EOF belongs to the audio command, not a newer selection still awaiting its broadcast.
        expected_play_seq = self._audio_command_seq
        expected_load_seq = getattr(self.player, "_load_seq", None)
        self.run_coro_threadsafe(
            self.handle_track_ended(
                reason=reason,
                expected_play_seq=expected_play_seq,
                expected_load_seq=expected_load_seq,
            )
        )

    def _on_queue_changed(self) -> None:
        q_data = self.get_queue_data()
        self.ws_manager.broadcast_sync("queue_changed", q_data, self.loop)

    def _on_download_progress(self, progress: DownloadProgress) -> None:
        p_data = progress.model_dump()
        self.ws_manager.broadcast_sync("download_progress", p_data, self.loop)
        if progress.status in ("completed", "deleted"):
            self.ws_manager.broadcast_sync(
                "library_changed",
                {"type": "download_updated", "video_id": progress.video_id, "status": progress.status},
                self.loop,
            )
        if progress.status == "completed" and not os.environ.get("PYTEST_CURRENT_TEST"):
            from metrolite.ytm.lyrics import cache_lyrics_for_download

            self.run_coro_threadsafe(
                cache_lyrics_for_download(self.ytm, self.db_path, progress.video_id)
            )

    def _on_media_key_play_pause(self) -> None:
        self.run_coro_threadsafe(self.toggle_play_pause())

    def _on_media_key_next(self) -> None:
        self.run_coro_threadsafe(self.next_track())

    def _on_media_key_prev(self) -> None:
        self.run_coro_threadsafe(self.prev_track())

    def _track_changed_payload(self, track: Track | None, state: str | None = None) -> dict[str, Any]:
        dumped = track.model_dump() if track else None
        return {
            "track": dumped,
            "current_track": dumped,
            "index": self.queue.current_index,
            "queue_index": self.queue.current_index,
            "time_pos": 0.0,
            "duration": float(track.duration_s) if (track and track.duration_s) else 0.0,
            "state": state or self.player.state,
        }

    async def get_setting(self, key: str, default: Any = None) -> Any:
        """Read a single persisted setting from SQLite."""
        try:
            async with get_connection(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT value_json FROM settings WHERE key = ?;", (key,)
                )
                row = await cursor.fetchone()
                if row:
                    return json.loads(row["value_json"])
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed reading setting %s: %s", key, e)
        return default

    async def refresh_blocked_cache(self) -> set[str]:
        """Synchronize the in-memory set of blocked normalized artist names from SQLite."""
        with suppress(Exception):
            self._blocked_norms = await self.library.get_blocked_artist_norms()
        self._home_cache.clear()
        return self._blocked_norms

    def _filter_blocked_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter out dict items matching any blocked artist."""
        if not self._blocked_norms:
            return items
        return [
            item
            for item in items
            if not artist_matches_blocked(str(item.get("artist") or ""), self._blocked_norms)
        ]

    def _accumulate_play_session(self) -> None:
        """Add elapsed wall-clock seconds to the active track play session."""
        if self._pause_listening_history or self._play_session_vid is None or self._play_session_last_ts is None:
            return
        now = time.monotonic()
        delta = max(0.0, min(5.0, now - self._play_session_last_ts))
        self._play_session_listened_s += delta
        self._play_session_last_ts = now

    def _pause_play_session(self) -> None:
        self._accumulate_play_session()
        self._play_session_last_ts = None

    def _reset_play_session(self, track: Track | None, playing: bool = False) -> None:
        self._session_occurrence = self._play_occurrence
        self._play_session_vid = track.video_id if track else None
        self._play_session_track = track
        self._play_session_listened_s = 0.0
        self._play_session_last_ts = time.monotonic() if playing and track and not self._pause_listening_history else None
        self._play_session_event_id = None
        self._play_session_last_flushed_s = 0

    async def _flush_play_session(self, force: bool = False) -> None:
        async with self._history_lock:
            await self._write_play_session(force)

    async def _write_play_session(self, force: bool) -> None:
        """Persist qualified play event (>= history_threshold_s) and update duration."""
        self._accumulate_play_session()
        if self._pause_listening_history or not self._play_session_vid:
            return
        sec = int(self._play_session_listened_s)
        threshold = max(1, int(self._history_threshold_s or 30))
        if sec < threshold:
            return
        try:
            if self._play_session_event_id is None:
                eid = await self.library.record_play_event(
                    self._play_session_vid,
                    listened_s=sec,
                    track_meta=self._play_session_track,
                )
                self._play_session_event_id = eid
                self._play_session_last_flushed_s = sec
            elif (force and sec > self._play_session_last_flushed_s) or (
                sec >= self._play_session_last_flushed_s + 5
            ):
                await self.library.update_play_event_duration(self._play_session_event_id, sec)
                self._play_session_last_flushed_s = sec
        except Exception as e:  # noqa: BLE001
            logger.debug("Play event flush skipped: %s", e)

    async def _rotate_play_session(
        self, next_track: Track | None, playing: bool = True, *, expected_seq: int | None = None,
    ) -> None:
        """Flush any qualified previous play session before starting the next track."""
        async with self._history_lock:
            if expected_seq is not None and expected_seq != self._play_seq:
                return
            if (
                next_track is None or self._play_session_vid != next_track.video_id
                or self._session_occurrence != self._play_occurrence
            ):
                await self._write_play_session(force=True)
                if expected_seq is not None and expected_seq != self._play_seq:
                    return
                self._reset_play_session(next_track, playing=playing)
            elif playing and not self._pause_listening_history and self._play_session_last_ts is None:
                self._play_session_last_ts = time.monotonic()

    async def _refresh_followed_releases_worker(self, limit_artists: int = 6) -> None:
        """Fetch latest releases for stale followed artists with bounded concurrency (<= 2)."""
        try:
            followed = await self.library.list_followed_artists()
            if not followed:
                return
            cached_rows = await self.library.get_cached_artist_releases()
            cached_map = {r["channel_id"]: r for r in cached_rows}
            cutoff = datetime.now(UTC).timestamp() - 86400.0
            stale: list[dict[str, Any]] = []
            for art in followed:
                cid = art["channel_id"]
                c = cached_map.get(cid)
                is_stale = True
                if c and c.get("fetched_at"):
                    with suppress(Exception):
                        ts = datetime.fromisoformat(str(c["fetched_at"]))
                        if ts.timestamp() > cutoff:
                            is_stale = False
                if is_stale:
                    stale.append(art)
            if not stale:
                return

            sem = asyncio.Semaphore(2)
            updated_any = False

            async def _fetch_one(art: dict[str, Any]) -> None:
                nonlocal updated_any
                async with sem:
                    cid = art["channel_id"]
                    name = art["name"]
                    lookup_cid = None if str(cid).startswith(("local:", "name:")) else cid
                    with suppress(Exception):
                        profile = await self.ytm.get_artist_profile(
                            name=name, channel_id=lookup_cid
                        )
                        combined = [
                            *[dict(item, type=item.get("type") or "Album") for item in (profile.get("albums") or [])[:4]],
                            *[dict(item, type=item.get("type") or "Single") for item in (profile.get("singles") or [])[:4]],
                        ]
                        def _yr(item: dict[str, Any]) -> int:
                            try:
                                return int(str(item.get("year") or "0").strip()[:4])
                            except (TypeError, ValueError):
                                return 0
                        combined.sort(key=_yr, reverse=True)
                        await self.library.upsert_cached_artist_releases(
                            channel_id=cid,
                            artist_name=name,
                            releases=combined[:6],
                        )
                        updated_any = True

            await asyncio.gather(*[_fetch_one(a) for a in stale[:limit_artists]])
            if updated_any:
                self._home_cache.pop("for_you", None)
                await self.ws_manager.broadcast(
                    "library_changed", {"type": "artist_releases_updated"}
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("Followed artist releases refresh skipped: %s", e)

    async def get_followed_new_releases(
        self, refresh: bool = False, trigger_refresh: bool = True
    ) -> list[dict[str, Any]]:
        """Return cached releases from followed artists, optionally refreshing stale entries."""
        if refresh:
            await self._refresh_followed_releases_worker(limit_artists=8)
        elif (
            trigger_refresh
            and not os.environ.get("PYTEST_CURRENT_TEST")
            and (self._releases_refresh_task is None or self._releases_refresh_task.done())
        ):
            self._releases_refresh_task = self.spawn_task(
                self._refresh_followed_releases_worker(limit_artists=6)
            )

        rows = await self.library.get_cached_artist_releases()
        flattened: list[dict[str, Any]] = []
        for entry in rows:
            cid = entry["channel_id"]
            aname = entry["artist_name"]
            if self._blocked_norms and artist_matches_blocked(aname, self._blocked_norms):
                continue
            for rel in (entry.get("releases") or [])[:3]:
                if not isinstance(rel, dict) or not rel.get("title"):
                    continue
                flattened.append(
                    {
                        "channel_id": cid,
                        "artist_name": aname,
                        "title": rel.get("title"),
                        "browseId": rel.get("browse_id") or rel.get("browseId"),
                        "year": rel.get("year"),
                        "type": rel.get("type") or "Release",
                        "thumbnail_url": rel.get("thumbnail_url"),
                        "fetched_at": entry.get("fetched_at"),
                    }
                )

        def _sort_year(item: dict[str, Any]) -> int:
            try:
                return int(str(item.get("year") or "0").strip()[:4])
            except (TypeError, ValueError):
                return 0

        flattened.sort(key=_sort_year, reverse=True)
        return flattened[:18]

    async def build_personalized_home(
        self, mood: str = "for_you", refresh: bool = False
    ) -> dict[str, Any]:
        """Build a rich, personalized multi-shelf home feed adapting to taste, pins & moods."""
        cache_key = mood.lower().strip() or "for_you"
        history_revision = self._history_revision
        now = time.monotonic()
        if not refresh and cache_key in self._home_cache:
            expires_at, cached_payload = self._home_cache[cache_key]
            if now < expires_at:
                return cached_payload

        if not self._blocked_norms:
            with suppress(Exception):
                self._blocked_norms = await self.library.get_blocked_artist_norms()

        greeting = _time_greeting()

        async def _safe_search(query: str | None, limit: int = 14) -> list[dict[str, Any]]:
            if not query:
                return []
            try:
                res = await self.ytm.search(query, filter_type="songs", limit=limit)
                return self._filter_blocked_items([t.model_dump() for t in res])
            except Exception:  # noqa: BLE001
                return []

        # Multi-shelf mood view
        if cache_key in _MOOD_CONFIG:
            shelf_defs = _MOOD_CONFIG[cache_key]

            async def _fetch_mood_shelf(
                idx: int, spec: tuple[str, str, str, str, list[str]]
            ) -> dict[str, Any] | None:
                shelf_id, layout, title, subtitle, queries = spec
                chosen_query = random.choice(queries) if refresh else queries[0]
                candidates = [chosen_query, *[q for q in queries if q != chosen_query][:1]]
                limit = 16 if layout == "compact_grid" else 14
                dumped: list[dict[str, Any]] = []
                for q in candidates:
                    dumped = await _safe_search(q, limit=limit)
                    if dumped:
                        break
                if not dumped:
                    return None
                if refresh:
                    random.shuffle(dumped)
                return {
                    "id": f"mood_{cache_key}_{shelf_id}_{idx}",
                    "title": title,
                    "subtitle": subtitle,
                    "layout": layout,
                    "contents": dumped[:limit],
                }

            raw_mood_sections = await asyncio.gather(
                *[_fetch_mood_shelf(i, spec) for i, spec in enumerate(shelf_defs)]
            )
            mood_sections = [s for s in raw_mood_sections if s is not None]
            speed_dial = (mood_sections[0]["contents"][:6] if mood_sections else [])
            payload = {
                "greeting": greeting,
                "speed_dial": speed_dial,
                "pinned_tracks": [],
                "pinned_artists": [],
                "new_releases": [],
                "sections": mood_sections,
                "personalized": False,
            }
            if mood_sections:
                if len(self._home_cache) >= 8 and cache_key not in self._home_cache:
                    self._home_cache.pop(next(iter(self._home_cache)), None)
                self._home_cache[cache_key] = (now + 300.0, payload)
            return payload

        # Full Personalized "For You" view
        most_played: list[dict[str, Any]] = []
        recent_history: list[dict[str, Any]] = []
        liked_tracks: list[dict[str, Any]] = []
        forgotten_favs: list[dict[str, Any]] = []
        offline_tracks: list[dict[str, Any]] = []
        pins_data: dict[str, list[dict[str, Any]]] = {"tracks": [], "artists": []}
        new_releases: list[dict[str, Any]] = []

        with suppress(Exception):
            (
                most_played,
                recent_history,
                liked_tracks,
                forgotten_favs,
                offline_tracks,
                pins_data,
                new_releases,
            ) = await asyncio.gather(
                self.library.get_most_played(limit=16),
                self.library.get_history(limit=16),
                self.library.list_liked(),
                self.library.get_forgotten_favorites(limit=12),
                self.library.list_offline_tracks(limit=14),
                self.library.get_pins(),
                self.get_followed_new_releases(trigger_refresh=True),
            )

        pinned_tracks = pins_data.get("tracks", [])
        pinned_artists = [
            a
            for a in pins_data.get("artists", [])
            if not (self._blocked_norms and artist_matches_blocked(a.get("name", ""), self._blocked_norms))
        ]

        # Build Speed Dial (Jump Back In) from most played + recent + liked (excluding blocked)
        speed_dial: list[dict[str, Any]] = []
        seen_dial: set[str] = set()
        for pool in (most_played[:5], recent_history[:5], liked_tracks[:5]):
            for item in self._filter_blocked_items(pool):
                vid = item.get("video_id")
                if vid and vid not in seen_dial:
                    seen_dial.add(vid)
                    speed_dial.append(item)
                if len(speed_dial) >= 6:
                    break
            if len(speed_dial) >= 6:
                break

        # Choose multiple personalization seeds from user's listening profile
        seed_pool = self._filter_blocked_items(most_played[:10] + liked_tracks[:8] + recent_history[:8])
        unique_seeds: list[dict[str, Any]] = []
        seen_seed_ids: set[str] = set()
        for s in seed_pool:
            vid = s.get("video_id")
            if vid and vid not in seen_seed_ids:
                seen_seed_ids.add(vid)
                unique_seeds.append(s)

        seed_track: dict[str, Any] | None = None
        second_seed_track: dict[str, Any] | None = None
        if unique_seeds:
            if refresh and len(unique_seeds) > 1:
                sampled = random.sample(unique_seeds[:8], k=min(2, len(unique_seeds[:8])))
                seed_track = sampled[0]
                second_seed_track = sampled[1] if len(sampled) > 1 else None
            else:
                seed_track = unique_seeds[0]
                second_seed_track = unique_seeds[1] if len(unique_seeds) > 1 else None

        # Extract top artists from user's play counts & likes
        artist_scores: dict[str, int] = {}
        for item in most_played:
            raw_artist = (split_artist_names(item.get("artist")) or [""])[0].strip()
            if (
                raw_artist
                and raw_artist.lower() != "unknown artist"
                and not artist_matches_blocked(raw_artist, self._blocked_norms)
            ):
                artist_scores[raw_artist] = artist_scores.get(raw_artist, 0) + max(
                    1, int(item.get("play_count") or 1)
                )
        for item in liked_tracks[:18]:
            raw_artist = (split_artist_names(item.get("artist")) or [""])[0].strip()
            if (
                raw_artist
                and raw_artist.lower() != "unknown artist"
                and not artist_matches_blocked(raw_artist, self._blocked_norms)
            ):
                artist_scores[raw_artist] = artist_scores.get(raw_artist, 0) + 2

        top_artists = [
            a for a, _ in sorted(artist_scores.items(), key=lambda kv: kv[1], reverse=True)
        ]
        chosen_artist: str | None = None
        second_artist: str | None = None
        if top_artists:
            if refresh and len(top_artists) > 1:
                picked = random.sample(top_artists[:5], k=min(2, len(top_artists[:5])))
                chosen_artist = picked[0]
                second_artist = picked[1] if len(picked) > 1 else None
            else:
                chosen_artist = top_artists[0]
                second_artist = top_artists[1] if len(top_artists) > 1 else None

        trending_q = random.choice(_TRENDING_QUERIES) if refresh else _TRENDING_QUERIES[0]

        # Launch all network calls concurrently for fast startup
        async def _safe_home() -> list[dict[str, Any]]:
            try:
                raw = await self.ytm.get_home_sections()
                return [s.model_dump() if hasattr(s, "model_dump") else s for s in raw]
            except Exception:  # noqa: BLE001
                return []

        async def _safe_radio(vid: str | None) -> list[dict[str, Any]]:
            if not vid:
                return []
            try:
                res = await self.ytm.get_watch_radio(vid, limit=14)
                dumped = [t.model_dump() for t in res if t.video_id != vid]
                return self._filter_blocked_items(dumped)
            except Exception:  # noqa: BLE001
                return []

        (
            home_raw,
            radio_tracks,
            second_radio_tracks,
            artist_tracks,
            second_artist_tracks,
            trending_tracks,
            chill_discover,
        ) = await asyncio.gather(
            _safe_home(),
            _safe_radio(seed_track.get("video_id") if seed_track else None),
            _safe_radio(second_seed_track.get("video_id") if second_seed_track else None),
            _safe_search(f"{chosen_artist} songs" if chosen_artist else None, limit=14),
            _safe_search(f"{second_artist} songs" if second_artist else None, limit=14),
            _safe_search(trending_q, limit=14),
            _safe_search("chill acoustic indie hit songs", limit=14),
        )

        if not trending_tracks:
            fallback_trending_q = next((q for q in _TRENDING_QUERIES if q != trending_q), None)
            if fallback_trending_q:
                trending_tracks = await _safe_search(fallback_trending_q, limit=14)

        sections: list[dict[str, Any]] = []
        is_personalized = bool(seed_track or most_played or liked_tracks or pinned_tracks)

        # 1. Quick Picks (Metrolist-style compact grid combining YTM Quick Picks + radio/trending)
        quick_pick_items: list[dict[str, Any]] = []
        seen_qp: set[str] = set()
        for sec in home_raw:
            for item in sec.get("contents", []):
                if isinstance(item, Track):
                    item_dict = item.model_dump()
                elif isinstance(item, dict) and item.get("video_id"):
                    item_dict = item
                else:
                    continue
                if artist_matches_blocked(str(item_dict.get("artist") or ""), self._blocked_norms):
                    continue
                vid = item_dict.get("video_id")
                if vid and vid not in seen_qp:
                    seen_qp.add(vid)
                    quick_pick_items.append(item_dict)
                if len(quick_pick_items) >= 12:
                    break
            if len(quick_pick_items) >= 12:
                break

        if len(quick_pick_items) < 6 and trending_tracks:
            for item_dict in trending_tracks:
                vid = item_dict.get("video_id")
                if vid and vid not in seen_qp:
                    seen_qp.add(vid)
                    quick_pick_items.append(item_dict)

        if quick_pick_items:
            if refresh:
                random.shuffle(quick_pick_items)
            sections.append(
                {
                    "id": "quick_picks",
                    "title": "Quick Picks",
                    "subtitle": "Pick a song to launch an instant radio queue",
                    "layout": "compact_grid",
                    "contents": quick_pick_items[:12],
                }
            )

        # 2. Personalized Taste Mix (Because you played <Song>)
        if seed_track and radio_tracks:
            sections.append(
                {
                    "id": "taste_radio",
                    "title": f"Because you played {seed_track.get('title', 'your favorites')}",
                    "subtitle": "Radio mix curated from your personal listening taste",
                    "layout": "cards",
                    "contents": radio_tracks[:14],
                }
            )

        # 3. Your Most Played
        filtered_most = self._filter_blocked_items(most_played)
        if filtered_most:
            sections.append(
                {
                    "id": "most_played",
                    "title": "Your Most Played",
                    "subtitle": "Songs you keep coming back to",
                    "layout": "cards",
                    "contents": filtered_most[:14],
                }
            )

        # 4. Forgotten Favorites
        filtered_forgotten = self._filter_blocked_items(forgotten_favs)
        if filtered_forgotten:
            sections.append(
                {
                    "id": "forgotten_favorites",
                    "title": "Forgotten Favorites",
                    "subtitle": "Past favorites you haven't spun recently",
                    "layout": "cards",
                    "contents": filtered_forgotten[:12],
                }
            )

        # 5. More from Top Artist
        if chosen_artist and artist_tracks:
            sections.append(
                {
                    "id": "top_artist",
                    "title": f"More from {chosen_artist}",
                    "subtitle": "Based on your most listened artists",
                    "layout": "cards",
                    "contents": artist_tracks[:14],
                }
            )

        # 6. Second Artist Mix (richer multi-artist personal feed)
        if second_artist and second_artist_tracks:
            sections.append(
                {
                    "id": "second_artist",
                    "title": f"Deep Cut Mix: {second_artist}",
                    "subtitle": "Because they appear across your library",
                    "layout": "cards",
                    "contents": second_artist_tracks[:14],
                }
            )

        # 7. Secondary Taste Radio Mix
        if second_seed_track and second_radio_tracks:
            primary_ids = {t.get("video_id") for t in radio_tracks}
            distinct_radio = [t for t in second_radio_tracks if t.get("video_id") not in primary_ids]
            if len(distinct_radio) >= 4:
                sections.append(
                    {
                        "id": "taste_radio_2",
                        "title": f"Similar to {second_seed_track.get('title', 'your picks')}",
                        "subtitle": "Explore closely matched tracks from your history",
                        "layout": "cards",
                        "contents": distinct_radio[:14],
                    }
                )

        # 8. Ready Offline
        filtered_offline = self._filter_blocked_items(offline_tracks)
        if filtered_offline:
            sections.append(
                {
                    "id": "offline_ready",
                    "title": "Ready Offline",
                    "subtitle": "Cached on your PC for instant zero-buffering playback",
                    "layout": "cards",
                    "contents": filtered_offline[:14],
                }
            )

        # 9. Live Trending Now
        if trending_tracks:
            sections.append(
                {
                    "id": "trending_now",
                    "title": "Trending Now",
                    "subtitle": "Fresh chart-toppers on YouTube Music",
                    "layout": "cards",
                    "contents": trending_tracks[:14],
                }
            )

        # 10. Additional YTM Home sections (up to 4 shelves) + fallback discovery mix
        added_ytm_count = 0
        for idx, sec in enumerate(home_raw[1:5]):
            raw_items = [
                c.model_dump() if isinstance(c, Track) else c
                for c in sec.get("contents", [])
                if isinstance(c, Track) or (isinstance(c, dict) and c.get("video_id"))
            ]
            valid = self._filter_blocked_items(raw_items)
            if valid:
                added_ytm_count += 1
                sections.append(
                    {
                        "id": f"ytm_home_{idx}",
                        "title": sec.get("title") or "Recommended Mixes",
                        "layout": "cards",
                        "contents": valid[:14],
                    }
                )

        if added_ytm_count == 0 and chill_discover:
            seen_t = {t.get("video_id") for t in trending_tracks}
            fresh_chill = [t for t in chill_discover if t.get("video_id") not in seen_t]
            if fresh_chill:
                sections.append(
                    {
                        "id": "discover_vibes",
                        "title": "Recommended Acoustic & Chill",
                        "subtitle": "Hand-picked mellow vibes to discover",
                        "layout": "cards",
                        "contents": fresh_chill[:14],
                    }
                )

        payload = {
            "greeting": greeting,
            "speed_dial": speed_dial,
            "pinned_tracks": pinned_tracks,
            "pinned_artists": pinned_artists,
            "new_releases": new_releases,
            "sections": sections,
            "personalized": is_personalized,
        }
        has_content = bool(speed_dial or pinned_tracks or pinned_artists or new_releases) or any(
            bool(sec.get("contents")) for sec in sections
        )
        if has_content and history_revision == self._history_revision:
            if len(self._home_cache) >= 8 and cache_key not in self._home_cache:
                self._home_cache.pop(next(iter(self._home_cache)), None)
            self._home_cache[cache_key] = (now + 300.0, payload)
        return payload

    def _cancel_pending_prefetch(self) -> None:
        """Cancel any in-flight radio refill, gapless preload, or neighbor/lyrics prefetch tasks."""
        if self._radio_task and not self._radio_task.done():
            self._radio_task.cancel()
        if self._preload_task and not self._preload_task.done():
            self._preload_task.cancel()
        if self._adjacent_prefetch_task and not self._adjacent_prefetch_task.done():
            self._adjacent_prefetch_task.cancel()
        if self._lyrics_prefetch_task and not self._lyrics_prefetch_task.done():
            self._lyrics_prefetch_task.cancel()

    def _schedule_lyrics_prefetch(self, tracks: list[Track]) -> None:
        """Warm lyrics cache in background for active/adjacent tracks without blocking audio."""
        if not tracks or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        from metrolite.ytm.lyrics import prefetch_lyrics

        if self._lyrics_prefetch_task and not self._lyrics_prefetch_task.done():
            self._lyrics_prefetch_task.cancel()

        async def _warm() -> None:
            with suppress(asyncio.CancelledError, Exception):
                await prefetch_lyrics(self.ytm, tracks, db_path=self.db_path)

        with suppress(RuntimeError):
            self._lyrics_prefetch_task = self.spawn_task(_warm())

    def _schedule_adjacent_prefetch(self, current_track: Track | None = None) -> None:
        """Warm stream URLs for neighboring queue tracks (+2, -1, -2, +3) and lyrics."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        neighbors = (
            self.queue.neighbor_tracks((1, -1, 2, -2, 3))
            if hasattr(self.queue, "neighbor_tracks")
            else []
        )
        preview = self.queue.next_track_preview
        preview_id = preview.video_id if preview is not None else None
        stream_ids = [t.video_id for t in neighbors if t.video_id and t.video_id != preview_id]
        resolver = getattr(self.player, "resolver", None)
        if (
            stream_ids
            and resolver is not None
            and hasattr(resolver, "prefetch")
            and inspect.iscoroutinefunction(resolver.prefetch)
        ):
            if self._adjacent_prefetch_task and not self._adjacent_prefetch_task.done():
                self._adjacent_prefetch_task.cancel()

            async def _warm_streams() -> None:
                with suppress(asyncio.CancelledError, Exception):
                    await resolver.prefetch(stream_ids)

            with suppress(RuntimeError):
                self._adjacent_prefetch_task = self.spawn_task(_warm_streams())

        lyrics_targets: list[Track] = []
        if current_track is not None:
            lyrics_targets.append(current_track)
        lyrics_targets.extend(neighbors[:3])
        if lyrics_targets:
            self._schedule_lyrics_prefetch(lyrics_targets)

    async def _load_for_command(self, track: Track, seq: int, *, announce: bool = True) -> bool:
        def is_current() -> bool:
            return seq == self._play_seq

        if announce:
            await self.ws_manager.broadcast(
                "track_changed", self._track_changed_payload(track, state="buffering")
            )
        if not is_current():
            return False
        if announce:
            self._schedule_lyrics_prefetch([track])
        self._audio_command_seq = seq
        await self.player.load_and_play(track, is_current=is_current)
        return is_current()

    async def _after_play_started(self, target: Track | None) -> None:
        play_seq = self._play_seq
        # Pause/resume changes the transport command, but not this native play occurrence.
        self._play_occurrence = play_seq
        await self._rotate_play_session(target, expected_seq=play_seq)
        if play_seq != self._play_seq:
            return
        if os.environ.get("PYTEST_CURRENT_TEST"):
            preview = self.queue.next_track_preview
            if preview:
                with suppress(Exception):
                    await self.player.preload_next(preview)
            return

        self._cancel_pending_prefetch()

        preview = self.queue.next_track_preview
        if preview:
            self._preload_task = self.spawn_task(self.player.preload_next(preview))

        remaining = len(self.queue.items) - 1 - self.queue.current_index
        if self.queue.autoplay_radio and remaining < 22 and self.queue.current_track is not None:
            my_seq = self._play_seq

            async def _refill() -> None:
                with suppress(asyncio.CancelledError, Exception):
                    added = await self.queue.refill_radio(limit=28, auto_promote_until=22)
                    if my_seq != self._play_seq:
                        return
                    if added > 0:
                        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
                    nxt_preview = self.queue.next_track_preview
                    if nxt_preview and (not preview or preview.video_id != nxt_preview.video_id):
                        curr_preloaded = getattr(self.player, "next_track", None)
                        if curr_preloaded is None or curr_preloaded.video_id != nxt_preview.video_id:
                            with suppress(Exception):
                                await self.player.preload_next(nxt_preview)
                    self._schedule_adjacent_prefetch(target or self.queue.current_track)

            self._radio_task = self.spawn_task(_refill())
        else:
            self._schedule_adjacent_prefetch(target or self.queue.current_track)

        async with self._history_lock:
            if target is not None and not self._pause_listening_history and play_seq == self._play_seq:
                try:
                    await self.library.record_play(target.video_id, target)
                except (sqlite3.Error, OSError) as exc:
                    logger.warning("Could not record listening history: %s", exc)

    def sync_preloaded_next(self) -> None:
        """Invalidate stale mpv preloaded tracks and preload the updated next_track_preview."""
        preview = self.queue.next_track_preview
        curr_preloaded = getattr(self.player, "next_track", None)
        preview_id = preview.video_id if preview is not None else None
        curr_id = curr_preloaded.video_id if curr_preloaded is not None else None

        if preview_id is None:
            self._cancel_pending_prefetch()
            if curr_id is not None:
                if hasattr(self.player, "clear_preloaded_next"):
                    self.player.clear_preloaded_next()
                else:
                    self.player.next_track = None
            return

        if preview_id == curr_id:
            return

        self._cancel_pending_prefetch()
        if hasattr(self.player, "clear_preloaded_next"):
            self.player.clear_preloaded_next()
        else:
            self.player.next_track = None

        if (
            not os.environ.get("PYTEST_CURRENT_TEST")
            and getattr(self.player, "state", "stopped") in ("playing", "paused", "buffering")
        ):
            async def _repreload(target: Track) -> None:
                with suppress(asyncio.CancelledError, Exception):
                    await self.player.preload_next(target)

            with suppress(RuntimeError):
                self._preload_task = self.spawn_task(_repreload(preview))
        self._schedule_adjacent_prefetch(self.queue.current_track)

    def get_playback_tick_data(self) -> dict[str, Any]:
        live_pos = (
            self.player.get_live_time_pos()
            if hasattr(self.player, "get_live_time_pos")
            else self.player.time_pos
        )
        return {
            "time_pos": round(live_pos, 3),
            "duration": round(self.player.duration, 2),
            "state": self.player.state,
            "volume": self.player.volume,
            "current_track": self.player.current_track.model_dump()
            if self.player.current_track
            else None,
        }

    def get_queue_data(self) -> dict[str, Any]:
        return {
            "current_index": self.queue.current_index,
            "items": [t.model_dump() for t in self.queue.items],
            "shuffle": self.queue.shuffle,
            "repeat_mode": self.queue.repeat_mode,
            "autoplay_radio": self.queue.autoplay_radio,
            "current_track": self.queue.current_track.model_dump()
            if self.queue.current_track
            else None,
        }

    async def handle_track_ended(
        self,
        reason: str = "eof",
        expected_play_seq: int | None = None,
        expected_load_seq: int | None = None,
    ) -> None:
        if reason in _NON_COMPLETION_REASONS:
            return
        if expected_play_seq is not None and expected_play_seq != self._play_seq:
            return
        if (
            expected_load_seq is not None
            and expected_load_seq != getattr(self.player, "_load_seq", expected_load_seq)
        ):
            return

        self._cancel_pending_prefetch()
        self._play_seq += 1
        req_seq = self._play_seq

        is_error = reason == "error"
        if not is_error:
            self.player.reset_errors()
            self.queue.reset_errors()
        else:
            failed_track = self.player.current_track or self.queue.current_track
            if failed_track is not None and hasattr(self.player.resolver, "invalidate"):
                self.player.resolver.invalidate(failed_track.video_id)
            self.queue.consecutive_errors = max(
                self.queue.consecutive_errors, self.player.consecutive_errors
            )
            self.player.consecutive_errors = self.queue.consecutive_errors
            if self.player.consecutive_errors > 2:
                logger.error(
                    "Exceeded maximum consecutive playback errors (%d). Halting auto-advance.",
                    self.player.consecutive_errors,
                )
                self.player.pause()
                await self.ws_manager.broadcast(
                    "error",
                    {
                        "message": "Playback paused: too many consecutive playback errors",
                        "consecutive_errors": self.player.consecutive_errors,
                    },
                )
                return

        next_track = await self.queue.next_track(
            is_error=is_error, is_current=lambda: req_seq == self._play_seq
        )
        if req_seq != self._play_seq:
            return

        if next_track:
            try:
                promoted = not is_error and self.player.consume_gapless_promotion(next_track.video_id)
                if not promoted and not await self._load_for_command(next_track, req_seq, announce=False):
                    return
                self._audio_command_seq = req_seq
                await self._after_play_started(next_track)
            except Exception as e:  # noqa: BLE001
                if req_seq != self._play_seq:
                    return
                logger.error("Failed to play next track after end: %s", e)
                if hasattr(self.player.resolver, "invalidate"):
                    self.player.resolver.invalidate(next_track.video_id)
                self.player.record_error()
                self.queue.consecutive_errors = self.player.consecutive_errors
                if self.player.consecutive_errors > 2:
                    logger.error(
                        "Exceeded maximum consecutive playback errors (%d). Halting.",
                        self.player.consecutive_errors,
                    )
                    self.player.pause()
                    await self.ws_manager.broadcast(
                        "error",
                        {
                            "message": f"Playback paused after consecutive errors: {e}",
                            "consecutive_errors": self.player.consecutive_errors,
                        },
                    )
                    return
                await self.handle_track_ended(reason="error")
                return

            if req_seq != self._play_seq:
                return
            await self.ws_manager.broadcast(
                "track_changed",
                self._track_changed_payload(next_track),
            )
        else:
            await self._rotate_play_session(None, playing=False, expected_seq=req_seq)
            if req_seq != self._play_seq:
                return
            self.player.pause()
            await self.ws_manager.broadcast(
                "track_changed",
                self._track_changed_payload(None, state="stopped"),
            )
        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())

    async def pause(self) -> dict[str, Any]:
        self._user_intent += 1
        self._play_seq += 1
        req_seq = self._play_seq
        self.player.pause()
        self._pause_play_session()
        await self._flush_play_session(force=True)
        tick_data = self.get_playback_tick_data()
        if req_seq == self._play_seq:
            await self.ws_manager.broadcast("playback_tick", tick_data)
        return tick_data

    async def toggle_play_pause(self) -> dict[str, Any]:
        if self.player.state in ("playing", "buffering"):
            return await self.pause()
        return await self.play()

    async def play(
        self, video_id: str | None = None, track: Track | None = None
    ) -> dict[str, Any]:
        self._user_intent += 1
        self._play_seq += 1
        req_seq = self._play_seq
        self.player.reset_errors()
        self.queue.reset_errors()
        curr = self.queue.current_track
        if (
            track is None and video_id is None and self.player.state == "paused"
            and curr is not None and self.player.current_track is not None
            and self.player.current_track.video_id == curr.video_id
            and getattr(self.player, "loaded_video_id", None) == curr.video_id
        ):
            self._audio_command_seq = req_seq
            self.player.resume()
            await self._rotate_play_session(curr, playing=True, expected_seq=req_seq)
            return self.get_playback_tick_data()
        self._cancel_pending_prefetch()

        target: Track | None = track
        if target is None and video_id:
            idx = next(
                (i for i, t in enumerate(self.queue.items) if t.video_id == video_id),
                None,
            )
            if idx is not None:
                self.queue.current_index = idx
                target = self.queue.items[idx]
                self.queue._notify()
            else:
                target = Track(video_id=video_id, title=video_id)
                self.queue.append(target)
                inserted_idx = next(
                    (i for i, t in enumerate(self.queue.items) if t.video_id == target.video_id),
                    len(self.queue.items) - 1,
                )
                self.queue.current_index = inserted_idx
                self.queue._notify()
        elif target is not None:
            curr_q = self.queue.current_track
            if curr_q is None or curr_q.video_id != target.video_id:
                idx = next(
                    (i for i, t in enumerate(self.queue.items) if t.video_id == target.video_id),
                    None,
                )
                if idx is not None:
                    self.queue.replace_track(idx, target)
                    self.queue.current_index = idx
                    self.queue._notify()
                else:
                    self.queue.append(target)
                    inserted_idx = next(
                        (i for i, t in enumerate(self.queue.items) if t.video_id == target.video_id),
                        len(self.queue.items) - 1,
                    )
                    self.queue.current_index = inserted_idx
                    self.queue._notify()

        curr_target = target or self.queue.current_track
        try:
            if curr_target is not None:
                if not await self._load_for_command(curr_target, req_seq):
                    return self.get_playback_tick_data()
                if (
                    self.player.state in ("paused", "stopped")
                    and getattr(self.player, "loaded_video_id", curr_target.video_id) != curr_target.video_id
                ):
                    await self.ws_manager.broadcast("playback_tick", self.get_playback_tick_data())
                    return self.get_playback_tick_data()
                await self._after_play_started(curr_target)
                if req_seq != self._play_seq:
                    return self.get_playback_tick_data()
                await self.ws_manager.broadcast(
                    "track_changed",
                    self._track_changed_payload(curr_target),
                )
                if target is not None:
                    await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
            elif self.player.state == "paused" and getattr(self.player, "loaded_video_id", None) is not None:
                self._audio_command_seq = req_seq
                self.player.resume()
                if self._play_session_vid is not None and not self._pause_listening_history:
                    self._play_session_last_ts = time.monotonic()
        except Exception as e:  # noqa: BLE001
            if req_seq != self._play_seq:
                return self.get_playback_tick_data()
            logger.error("Failed to play track %s: %s", curr_target.video_id if curr_target else None, e)
            self.player.record_error()
            self.queue.consecutive_errors = self.player.consecutive_errors
            failed_vid = curr_target.video_id if curr_target else (video_id or "")
            await self.ws_manager.broadcast(
                "error",
                {
                    "message": str(e),
                    "video_id": failed_vid,
                    "consecutive_errors": self.player.consecutive_errors,
                },
            )
            return {
                "error": str(e),
                "status": "error",
                "video_id": failed_vid,
                **self.get_playback_tick_data(),
            }
        return self.get_playback_tick_data()

    async def next_track(self, steps: int = 1) -> dict[str, Any]:
        self._user_intent += 1
        steps = max(1, int(steps))
        self.player.reset_errors()
        self.queue.reset_errors()
        self._cancel_pending_prefetch()
        self._play_seq += 1
        req_seq = self._play_seq
        nxt = await self.queue.step_tracks(
            steps=steps, direction=1, is_current=lambda: req_seq == self._play_seq
        )
        if req_seq != self._play_seq:
            return self.get_playback_tick_data()
        if nxt:
            try:
                if steps == 1 and self.player.switch_to_preloaded_next(nxt):
                    self._audio_command_seq = req_seq
                    await self._after_play_started(nxt)
                else:
                    if not await self._load_for_command(nxt, req_seq):
                        return self.get_playback_tick_data()
                    if (
                        self.player.state in ("paused", "stopped")
                        and getattr(self.player, "loaded_video_id", nxt.video_id) != nxt.video_id
                    ):
                        await self.ws_manager.broadcast(
                            "playback_tick", self.get_playback_tick_data()
                        )
                        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
                        return self.get_playback_tick_data()
                    await self._after_play_started(nxt)
            except Exception as e:  # noqa: BLE001
                if req_seq != self._play_seq:
                    return self.get_playback_tick_data()
                logger.error("Failed to play track on skip: %s", e)
                self.player.record_error()
                self.queue.consecutive_errors = self.player.consecutive_errors
                await self.ws_manager.broadcast(
                    "error",
                    {
                        "message": f"Failed to play track: {e}",
                        "video_id": nxt.video_id,
                        "consecutive_errors": self.player.consecutive_errors,
                    },
                )
                await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
                return {
                    "error": str(e),
                    "status": "error",
                    "video_id": nxt.video_id,
                    **self.get_playback_tick_data(),
                }
            if req_seq != self._play_seq:
                return self.get_playback_tick_data()
            await self.ws_manager.broadcast(
                "track_changed",
                self._track_changed_payload(nxt),
            )
        else:
            self.player.pause()
            await self.ws_manager.broadcast(
                "track_changed",
                self._track_changed_payload(None, state="stopped"),
            )
        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
        return self.get_playback_tick_data()

    async def prev_track(self, steps: int = 1) -> dict[str, Any]:
        self._user_intent += 1
        steps = max(1, int(steps))
        self.player.reset_errors()
        self.queue.reset_errors()
        self._cancel_pending_prefetch()
        self._play_seq += 1
        req_seq = self._play_seq
        prv = (
            self.queue.prev_track()
            if steps == 1
            else await self.queue.step_tracks(steps=steps, direction=-1)
        )
        if prv:
            try:
                if not await self._load_for_command(prv, req_seq):
                    return self.get_playback_tick_data()
                if (
                    self.player.state in ("paused", "stopped")
                    and getattr(self.player, "loaded_video_id", prv.video_id) != prv.video_id
                ):
                    await self.ws_manager.broadcast(
                        "playback_tick", self.get_playback_tick_data()
                    )
                    await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
                    return self.get_playback_tick_data()
                await self._after_play_started(prv)
            except (StreamResolutionError, Exception) as e:  # noqa: BLE001
                if req_seq != self._play_seq:
                    return self.get_playback_tick_data()
                logger.error("Failed to play previous track: %s", e)
                self.player.record_error()
                self.queue.consecutive_errors = self.player.consecutive_errors
                await self.ws_manager.broadcast(
                    "error",
                    {
                        "message": f"Failed to play track: {e}",
                        "video_id": prv.video_id,
                        "consecutive_errors": self.player.consecutive_errors,
                    },
                )
                return {
                    "error": str(e),
                    "status": "error",
                    "video_id": prv.video_id,
                    **self.get_playback_tick_data(),
                }
            if req_seq != self._play_seq:
                return self.get_playback_tick_data()
            await self.ws_manager.broadcast(
                "track_changed",
                self._track_changed_payload(prv),
            )
        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
        return self.get_playback_tick_data()

    def start_tick_loop(self) -> None:
        if self._tick_task is None or self._tick_task.done():
            self._shutdown = False
            self._tick_task = asyncio.create_task(self._playback_tick_loop())

    def stop_tick_loop(self) -> None:
        self._shutdown = True
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()
            self._tick_task = None

    async def _playback_tick_loop(self) -> None:
        last_state = ""
        last_trim_ts = time.monotonic()
        while not self._shutdown:
            curr_state = self.player.state
            try:
                if curr_state == "playing":
                    async with self._history_lock:
                        curr_trk = self.player.current_track
                        if (
                            self.player.state == "playing" and curr_trk is not None
                            and curr_trk.video_id == self._play_session_vid and not self._pause_listening_history
                            and self._play_occurrence == self._session_occurrence
                        ):
                            if self._play_session_last_ts is None:
                                self._play_session_last_ts = time.monotonic()
                            await self._write_play_session(force=False)
                elif last_state == "playing":
                    self._pause_play_session()
                    await self._flush_play_session(force=True)

                if curr_state == "playing" or last_state == "playing":
                    if self.ws_manager.active_connections:
                        data = self.get_playback_tick_data()
                        await self.ws_manager.broadcast("playback_tick", data)
                    if last_state == "playing" and curr_state in ("stopped", "paused"):
                        last_trim_ts = time.monotonic()
                        await asyncio.to_thread(trim_memory)
                    last_state = curr_state
                now = time.monotonic()
                if curr_state in ("stopped", "paused") and now - last_trim_ts >= 120.0:
                    last_trim_ts = now
                    await asyncio.to_thread(trim_memory)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.debug("Playback tick broadcast error: %s", e)
            await asyncio.sleep(0.25 if curr_state == "playing" else 1.0)

    def start_media_keys(self) -> None:
        if self.media_keys is not None:
            self.media_keys.start()

    def stop_media_keys(self) -> None:
        if self.media_keys is not None:
            self.media_keys.stop()

    def queue_download(
        self, video_id: str, track_meta: Track | dict[str, Any] | None = None
    ) -> Any:
        if hasattr(self.downloader, "queue_download"):
            return self.downloader.queue_download(video_id, track_meta)
        if hasattr(self.downloader, "download_track"):
            return self.spawn_task(self.downloader.download_track(video_id, track_meta))
        return None

    def shutdown(self) -> None:
        self._pause_play_session()
        self.stop_tick_loop()
        self.stop_media_keys()
        self._cancel_pending_prefetch()
        if self._releases_refresh_task and not self._releases_refresh_task.done():
            self._releases_refresh_task.cancel()
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        self._background_tasks.clear()
        if hasattr(self.downloader, "stop"):
            self.downloader.stop()
        if hasattr(self.importer, "cancel_all"):
            self.importer.cancel_all()
        if hasattr(self.updater, "stop"):
            self.updater.stop()
        if hasattr(self.player, "terminate"):
            self.player.terminate()
        trim_memory()


class PlayRequest(BaseModel):
    video_id: str | None = None
    track: Track | None = None
    track_meta: Track | None = None


class SearchHistoryRequest(BaseModel):
    term: str | None = Field(default=None, min_length=1)
    track: Track | None = None
    edit_id: UUID | None = None


class SeekRequest(BaseModel):
    seconds: float = Field(ge=0.0)


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)


class QueueActionRequest(BaseModel):
    action: str = "set"
    items: list[Track] | None = None
    item: Track | None = None
    index: int | None = None
    from_idx: int | None = None
    to_idx: int | None = None
    target_source: str | None = None
    shuffle: bool | None = None
    repeat_mode: str | None = None
    autoplay_radio: bool | None = None
    start_index: int = 0
    auto_play: bool = False


class LikeTrackRequest(BaseModel):
    track_meta: Track | dict[str, Any] | None = None


class PlaylistCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class PlaylistAddTracksRequest(BaseModel):
    tracks: list[Track | dict[str, Any] | str] = []
    track: Track | dict[str, Any] | str | None = None


class PlaylistDeleteTrackRequest(BaseModel):
    video_id: str | None = None


class ImportUrlRequest(BaseModel):
    url: str = Field(min_length=1)
    destination: ImportDestination = "new_playlist"
    playlist_name: str | None = None
    target_playlist_id: str | None = None


class DownloadTrackRequest(BaseModel):
    track_meta: Track | dict[str, Any] | None = None


class PinArtistRequest(BaseModel):
    channel_id: str | None = None
    name: str = Field(min_length=1)
    thumbnail_url: str | None = None


class BlockArtistRequest(BaseModel):
    name: str = Field(min_length=1)


class TrackUpdateRequest(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None


class FollowArtistRequest(BaseModel):
    channel_id: str | None = None
    name: str = Field(min_length=1)
    thumbnail_url: str | None = None


class ArtistRadioRequest(BaseModel):
    channel_id: str | None = None
    name: str = Field(min_length=1)


class SurpriseRadioRequest(BaseModel):
    seed_video_id: str | None = None


def create_app(
    controller: BridgeController | None = None,
    token: str | None = None,
) -> FastAPI:
    expected_token = token or secrets.token_urlsafe(32)
    ctrl = controller or BridgeController()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        ctrl.set_loop(loop)
        await init_db(ctrl.db_path)
        await ctrl.refresh_blocked_cache()
        threshold = await ctrl.get_setting("history_threshold_s", 30)
        if isinstance(threshold, (int, float)) and threshold > 0:
            ctrl._history_threshold_s = float(threshold)
        ctrl._pause_listening_history = bool(
            await ctrl.get_setting("pause_listening_history", False)
        )
        if not ctrl.queue.items:
            await ctrl.queue.load_from_db()
        ctrl.start_tick_loop()
        ctrl.start_media_keys()
        trim_memory()
        app.state.controller = ctrl
        app.state.token = expected_token
        try:
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                from metrolite.ytm.lyrics import backfill_downloaded_lyrics

                ctrl.spawn_task(backfill_downloaded_lyrics(ctrl.ytm, ctrl.db_path))

                async def _startup_releases_refresh() -> None:
                    await asyncio.sleep(3.0)
                    await ctrl._refresh_followed_releases_worker(limit_artists=6)

                ctrl.spawn_task(_startup_releases_refresh())

                upd_cfg = getattr(get_config(), "updates", None)
                if upd_cfg and upd_cfg.enabled and upd_cfg.check_on_startup:
                    async def _startup_update_check() -> None:
                        await asyncio.sleep(4.0)
                        with suppress(Exception):
                            await ctrl.updater.check_for_updates(force=False)

                    ctrl.spawn_task(_startup_update_check())
            yield
        finally:
            ctrl._pause_play_session()
            await ctrl._flush_play_session(force=True)
            await ctrl.queue.flush()
            ctrl.shutdown()
            with suppress(Exception):
                from metrolite.ytm.lyrics import close_lyrics_clients

                await close_lyrics_clients()

    app = FastAPI(
        title="MetroLite Bridge API",
        version=get_config().updates.current_version,
        description="Localhost REST & WebSocket audio bridge for MetroLite",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_ALLOWED_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_pna_header(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network") and _is_allowed_local_origin(
            request.headers.get("origin")
        ):
            response.headers["access-control-allow-private-network"] = "true"
        return response

    @app.exception_handler(StreamResolutionError)
    async def stream_resolution_exception_handler(
        _request: Request, exc: StreamResolutionError
    ) -> JSONResponse:
        logger.error("Unhandled StreamResolutionError caught by exception handler: %s", exc)
        await ctrl.ws_manager.broadcast(
            "error",
            {
                "message": str(exc),
                "consecutive_errors": ctrl.player.consecutive_errors,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": str(exc), "status": "error"},
        )

    app.state.controller = ctrl
    app.state.token = expected_token

    def verify_token(
        request: Request,
        x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token"),
        authorization: str | None = Header(default=None),
        token: str | None = Query(default=None),
    ) -> str:
        provided: str | None = None
        if x_bridge_token:
            provided = x_bridge_token
        elif authorization and authorization.startswith("Bearer "):
            provided = authorization[7:].strip()
        elif token:
            provided = token

        valid_token: str = app.state.token
        if not provided or not secrets.compare_digest(provided, valid_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: invalid or missing bridge token",
            )
        return provided

    @app.get("/health")
    async def get_health() -> dict[str, Any]:
        curr = ctrl.queue.current_track
        return {
            "status": "ok",
            "app": "MetroLite",
            "version": app.version,
            "state": ctrl.player.state,
            "track": f"{curr.title} • {curr.artist}" if curr else None,
        }

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        token: str | None = Query(default=None),
    ) -> None:
        origin = websocket.headers.get("origin")
        if origin and not _is_allowed_local_origin(origin):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        valid_token: str = app.state.token
        if not token or not secrets.compare_digest(token, valid_token):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()
        await ctrl.ws_manager.connect(websocket)

        try:
            await websocket.send_text(
                json.dumps({"event": "playback_tick", "data": ctrl.get_playback_tick_data()})
            )
            await websocket.send_text(
                json.dumps({"event": "queue_changed", "data": ctrl.get_queue_data()})
            )

            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
        except (WebSocketDisconnect, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug("WebSocket client disconnected: %s", e)
        finally:
            await ctrl.ws_manager.disconnect(websocket)

    @app.get("/home", dependencies=[Depends(verify_token)])
    async def get_home() -> list[dict[str, Any]]:
        try:
            sections = await ctrl.ytm.get_home_sections()
            return [s.model_dump() if hasattr(s, "model_dump") else s for s in sections]
        except Exception as e:
            logger.error("Failed to load home sections: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch home sections: {e}",
            ) from e

    @app.get("/home/personalized", dependencies=[Depends(verify_token)])
    async def get_personalized_home(
        mood: str = Query(default="for_you"),
        refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        return await ctrl.build_personalized_home(mood=mood, refresh=refresh)

    @app.get("/lyrics/{video_id}", dependencies=[Depends(verify_token)])
    async def get_lyrics(
        video_id: str,
        title: str | None = Query(default=None),
        artist: str | None = Query(default=None),
        duration: int | None = Query(default=None),
        q: str | None = Query(default=None),
        query: str | None = Query(default=None),
        force: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Fetch time-synced lyrics via LRCLIB + YouTube Music native timestamps."""
        from metrolite.ytm.lyrics import resolve_lyrics

        valid_vid = _require_valid_video_id(video_id)
        eff_duration = duration
        if (
            ctrl.player.current_track is not None
            and ctrl.player.current_track.video_id == valid_vid
            and ctrl.player.duration
            and ctrl.player.duration > 10
        ):
            eff_duration = round(ctrl.player.duration)
        return await resolve_lyrics(
            ctrl.ytm,
            video_id=valid_vid,
            title=title,
            artist=artist,
            duration=eff_duration,
            query=query or q,
            force=force,
            db_path=ctrl.db_path,
        )

    @app.get("/search/history", dependencies=[Depends(verify_token)])
    async def search_history() -> dict[str, Any]:
        return await ctrl.library.search_history()

    @app.post("/search/history", dependencies=[Depends(verify_token)])
    async def remember_search(body: SearchHistoryRequest) -> dict[str, str]:
        if not (body.term or "").strip() and body.track is None:
            raise HTTPException(status_code=422, detail="A search term or track is required")
        if not ctrl._pause_listening_history:
            if body.track is not None:
                await ctrl.library.remember_search_track(body.track)
            elif body.term:
                await ctrl.library.remember_search_term(body.term, str(body.edit_id) if body.edit_id else None)
            await ctrl.ws_manager.broadcast("search_history_changed", {})
        return {"status": "ok"}

    @app.get("/search", dependencies=[Depends(verify_token)])
    async def get_search(
        q: str = Query(..., min_length=1),
        filter: str = Query(default="songs"),
        limit: int = Query(default=25, ge=1, le=50),
        remember: bool = Query(default=True),
    ) -> list[dict[str, Any]]:
        if remember and q.strip() and not ctrl._pause_listening_history:
            try:
                await ctrl.library.remember_search_term(q)
                ctrl.ws_manager.broadcast_sync("search_history_changed", {}, ctrl.loop)
            except (sqlite3.Error, OSError) as exc:
                logger.warning("Could not save recent search: %s", exc)
        try:
            eff_filter: str | None = None if filter.lower() == "all" else filter
            results = await ctrl.ytm.search(query=q, filter_type=eff_filter, limit=limit)
            if not results and eff_filter == "songs":
                results = await ctrl.ytm.search(query=q, filter_type=None, limit=limit)
            if results and not os.environ.get("PYTEST_CURRENT_TEST"):
                top_ids = [t.video_id for t in results[:2] if t.video_id]
                resolver = getattr(ctrl.player, "resolver", None)
                if (
                    top_ids
                    and resolver is not None
                    and hasattr(resolver, "prefetch")
                    and inspect.iscoroutinefunction(resolver.prefetch)
                ):
                    ctrl.spawn_task(resolver.prefetch(top_ids))
            return [t.model_dump() for t in results]
        except Exception as e:
            logger.error("Search failed for query %r: %s", q, e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Search failed: {e}",
            ) from e

    @app.get("/artist", dependencies=[Depends(verify_token)])
    async def get_artist_profile(
        name: str | None = Query(default=None),
        channel_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if not (name and name.strip()) and not (channel_id and channel_id.strip()):
            raise HTTPException(status_code=400, detail="Either name or channel_id is required")
        cache_key = f"{(channel_id or '').strip()}::{(name or '').strip().lower()}"
        now = time.monotonic()
        cached_payload: dict[str, Any] | None = None
        if cache_key in ctrl._artist_cache:
            exp, payload = ctrl._artist_cache[cache_key]
            if now < exp:
                cached_payload = payload
        if cached_payload is None:
            try:
                cached_payload = await ctrl.ytm.get_artist_profile(name=name, channel_id=channel_id)
                has_profile_content = bool(
                    cached_payload.get("all_tracks")
                    or cached_payload.get("popular_tracks")
                    or cached_payload.get("albums")
                    or cached_payload.get("singles")
                    or cached_payload.get("description")
                    or cached_payload.get("thumbnail_url")
                )
                if has_profile_content:
                    if len(ctrl._artist_cache) >= 8 and cache_key not in ctrl._artist_cache:
                        ctrl._artist_cache.pop(next(iter(ctrl._artist_cache)), None)
                    ctrl._artist_cache[cache_key] = (now + 300.0, cached_payload)
            except Exception as e:
                logger.error("Artist profile fetch failed (%r / %r): %s", name, channel_id, e)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to fetch artist profile: {e}",
                ) from e

        artist_name = str(
            cached_payload.get("name") or cached_payload.get("artist") or name or ""
        ).strip()
        artist_cid = str(cached_payload.get("channel_id") or channel_id or "").strip() or None
        is_followed = await ctrl.library.is_artist_followed(channel_id=artist_cid, name=artist_name)
        pins = await ctrl.library.get_pins()
        artist_norm = normalize_artist_name(artist_name)
        is_pinned = any(
            (artist_cid and p.get("channel_id") == artist_cid)
            or (artist_norm and normalize_artist_name(p.get("name")) == artist_norm)
            for p in pins.get("artists", [])
        )
        return {**cached_payload, "is_followed": is_followed, "is_pinned": is_pinned}

    @app.get("/album", dependencies=[Depends(verify_token)])
    async def get_album_details(
        browse_id: str | None = Query(default=None),
        title: str | None = Query(default=None),
        artist: str | None = Query(default=None),
        video_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if video_id:
            video_id = _require_valid_video_id(video_id)
        if not any([(browse_id or "").strip(), (title or "").strip(), (video_id or "").strip()]):
            raise HTTPException(
                status_code=400, detail="At least one of browse_id, title, or video_id is required"
            )
        cache_key = (
            f"{(browse_id or '').strip()}::{(title or '').strip().lower()}::"
            f"{(artist or '').strip().lower()}::{(video_id or '').strip()}"
        )
        now = time.monotonic()
        if cache_key in ctrl._album_cache:
            exp, payload = ctrl._album_cache[cache_key]
            if now < exp:
                return payload
        try:
            payload = await ctrl.ytm.get_album_details(
                browse_id=browse_id, title=title, artist=artist, video_id=video_id
            )
            if len(ctrl._album_cache) >= 8 and cache_key not in ctrl._album_cache:
                ctrl._album_cache.pop(next(iter(ctrl._album_cache)), None)
            ctrl._album_cache[cache_key] = (now + 300.0, payload)
            return payload
        except Exception as e:
            logger.error("Album fetch failed (%r / %r): %s", browse_id, title, e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch album: {e}",
            ) from e

    @app.api_route("/player/play", methods=["GET", "POST"], dependencies=[Depends(verify_token)])
    async def player_play(
        video_id: str | None = Query(default=None),
        body: PlayRequest | None = None,
    ) -> dict[str, Any]:
        target_video_id = body.video_id if body and body.video_id else video_id
        target_track = (body.track or body.track_meta) if body else None
        if target_video_id is not None:
            target_video_id = _require_valid_video_id(target_video_id)
        if target_track is not None:
            _require_valid_video_id(target_track.video_id)
        return await ctrl.play(video_id=target_video_id, track=target_track)

    @app.post("/player/pause", dependencies=[Depends(verify_token)])
    async def player_pause() -> dict[str, Any]:
        return await ctrl.pause()

    @app.get("/player/state", dependencies=[Depends(verify_token)])
    async def player_state() -> dict[str, Any]:
        return ctrl.get_playback_tick_data()

    @app.post("/player/toggle", dependencies=[Depends(verify_token)])
    async def player_toggle() -> dict[str, Any]:
        """Toggle playback between playing and paused."""
        return await ctrl.toggle_play_pause()

    @app.post("/player/next", dependencies=[Depends(verify_token)])
    async def player_next(steps: int = Query(default=1, ge=1, le=20)) -> dict[str, Any]:
        return await ctrl.next_track(steps=steps)

    @app.post("/player/prev", dependencies=[Depends(verify_token)])
    async def player_prev(steps: int = Query(default=1, ge=1, le=20)) -> dict[str, Any]:
        return await ctrl.prev_track(steps=steps)

    @app.post("/player/seek", dependencies=[Depends(verify_token)])
    async def player_seek(
        seconds: float | None = Query(default=None),
        body: SeekRequest | None = None,
    ) -> dict[str, Any]:
        target_sec = body.seconds if body is not None else seconds
        if target_sec is None or target_sec < 0:
            raise HTTPException(status_code=400, detail="Missing or invalid seconds parameter")
        with suppress(Exception):
            ctrl.player.seek(target_sec)
        tick_data = ctrl.get_playback_tick_data()
        await ctrl.ws_manager.broadcast("playback_tick", tick_data)
        return {"time_pos": ctrl.player.time_pos}

    @app.post("/player/preload", dependencies=[Depends(verify_token)])
    async def player_preload(body: dict[str, Any]) -> dict[str, Any]:
        """Silently warm stream URL & optional lyrics cache on track hover for instant playback."""
        if not isinstance(body, dict):
            return {"status": "ok"}

        raw_ids: list[str] = []
        single_vid = body.get("video_id")
        if isinstance(single_vid, str) and single_vid:
            raw_ids.append(single_vid)
        extra_ids = body.get("video_ids")
        if isinstance(extra_ids, list):
            for item in extra_ids[:3]:
                if isinstance(item, str) and item:
                    raw_ids.append(item)

        valid_ids = [_require_valid_video_id(vid) for vid in raw_ids]
        resolver = getattr(ctrl.player, "resolver", None)
        if valid_ids and resolver is not None:
            to_warm = [vid for vid in valid_ids if not resolver.is_cached(vid)]
            if to_warm:
                if hasattr(resolver, "prefetch") and inspect.iscoroutinefunction(resolver.prefetch):
                    ctrl.spawn_task(resolver.prefetch(to_warm))
                else:
                    async def _warm_one(vid: str) -> None:
                        with suppress(Exception):
                            await resolver.resolve(vid)

                    for vid in to_warm[:2]:
                        ctrl.spawn_task(_warm_one(vid))

        wants_lyrics = bool(
            body.get("preload_lyrics") or body.get("title") or body.get("artist")
        )
        if valid_ids and wants_lyrics and not os.environ.get("PYTEST_CURRENT_TEST"):
            from metrolite.ytm.lyrics import prefetch_lyrics

            raw_dur = body.get("duration_s")
            dur_val = int(raw_dur) if isinstance(raw_dur, (int, float)) and raw_dur > 0 else 0
            meta_track = Track(
                video_id=valid_ids[0],
                title=str(body.get("title") or valid_ids[0]),
                artist=str(body.get("artist") or "Unknown Artist"),
                duration_s=dur_val,
            )
            ctrl.spawn_task(prefetch_lyrics(ctrl.ytm, [meta_track], db_path=ctrl.db_path))

        return {"status": "ok"}

    @app.post("/player/eq", dependencies=[Depends(verify_token)])
    async def player_eq(body: dict[str, Any]) -> dict[str, Any]:
        """Apply native libmpv audio equalizer preset."""
        preset = str(body.get("preset", "flat")) if isinstance(body, dict) else "flat"
        applied = ctrl.player.set_eq_preset(preset)
        return {"preset": applied}

    @app.post("/player/volume", dependencies=[Depends(verify_token)])
    async def player_volume(
        volume: int | None = Query(default=None),
        body: VolumeRequest | None = None,
    ) -> dict[str, Any]:
        target_vol = body.volume if body is not None else volume
        if target_vol is None or target_vol < 0 or target_vol > 100:
            raise HTTPException(status_code=400, detail="Volume must be between 0 and 100")
        ctrl.player.set_volume(target_vol)
        tick_data = ctrl.get_playback_tick_data()
        await ctrl.ws_manager.broadcast("playback_tick", tick_data)
        return {"volume": ctrl.player.volume}

    @app.get("/queue", dependencies=[Depends(verify_token)])
    async def get_queue() -> dict[str, Any]:
        return ctrl.get_queue_data()

    @app.post("/queue", dependencies=[Depends(verify_token)])
    async def update_queue(body: QueueActionRequest) -> dict[str, Any]:
        action = body.action.lower()
        ctrl._user_intent += 1

        if action == "set":
            ctrl._cancel_pending_prefetch()
            tracks = body.items or ([body.item] if body.item else [])
            if body.shuffle is not None:
                ctrl.queue.shuffle = body.shuffle
            ctrl.queue.set_queue(tracks, start_index=body.start_index)
            if body.auto_play and ctrl.queue.current_track:
                result = await ctrl.play(track=ctrl.queue.current_track)
                if result.get("error"):
                    return {**ctrl.get_queue_data(), "playback_error": "Unable to play the selected track"}

        elif action == "append":
            if body.items:
                ctrl.queue.append(body.items)
            elif body.item:
                ctrl.queue.append(body.item)

        elif action == "add_next":
            if body.item:
                ctrl.queue.add_next(body.item)
            elif body.items:
                for t in reversed(body.items):
                    ctrl.queue.add_next(t)

        elif action == "remove":
            if body.index is not None:
                ctrl.queue.remove(body.index)

        elif action == "move":
            if body.from_idx is not None and body.to_idx is not None:
                try:
                    ctrl.queue.move(
                        body.from_idx, body.to_idx, target_source=body.target_source
                    )
                except TypeError:
                    ctrl.queue.move(body.from_idx, body.to_idx)

        elif action == "promote":
            if body.index is None or not ctrl.queue.promote(body.index):
                raise HTTPException(status_code=400, detail="Invalid radio item index to promote")

        elif action == "clear":
            ctrl._cancel_pending_prefetch()
            current = ctrl.player.current_track
            if current is not None and ctrl.player.state in ("playing", "paused", "buffering"):
                ctrl.queue.set_queue([current])
            else:
                ctrl.queue.clear()

        elif action == "shuffle":
            new_shuf = body.shuffle if body.shuffle is not None else not ctrl.queue.shuffle
            ctrl.queue.set_shuffle(new_shuf)

        elif action == "repeat":
            if body.repeat_mode:
                ctrl.queue.set_repeat_mode(body.repeat_mode)
            else:
                ctrl.queue.toggle_repeat_mode()

        elif action == "jump":
            if body.index is None or not (0 <= body.index < len(ctrl.queue.items)):
                raise HTTPException(status_code=400, detail="Invalid queue jump index")
            ctrl.queue.current_index = body.index
            ctrl.queue._notify()
            if body.auto_play and ctrl.queue.current_track:
                result = await ctrl.play(track=ctrl.queue.current_track)
                if result.get("error"):
                    return {**ctrl.get_queue_data(), "playback_error": "Unable to play the selected track"}

        elif action == "autoplay_radio":
            new_radio = (
                body.autoplay_radio
                if body.autoplay_radio is not None
                else not ctrl.queue.autoplay_radio
            )
            if not new_radio and ctrl._radio_task and not ctrl._radio_task.done():
                ctrl._radio_task.cancel()
            ctrl.queue.autoplay_radio = new_radio
            ctrl.queue._notify()

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported queue action: {body.action}")

        if not (action in ("set", "jump") and body.auto_play):
            ctrl.sync_preloaded_next()

        return ctrl.get_queue_data()

    @app.post("/liked/{video_id}", dependencies=[Depends(verify_token)])
    async def toggle_liked(
        video_id: str,
        body: LikeTrackRequest | None = None,
    ) -> dict[str, Any]:
        valid_vid = _require_valid_video_id(video_id)
        track_meta = body.track_meta if body else None
        is_liked = await ctrl.library.toggle_like(valid_vid, track_meta)

        # Auto-download newly liked songs if enabled in Settings
        if is_liked and not os.environ.get("PYTEST_CURRENT_TEST"):
            auto_dl = await ctrl.get_setting("auto_download_liked", False)
            if auto_dl:
                ctrl.queue_download(valid_vid, track_meta)

        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "liked", "video_id": valid_vid, "is_liked": is_liked},
        )
        return {"video_id": valid_vid, "is_liked": is_liked}

    @app.get("/liked", dependencies=[Depends(verify_token)])
    async def list_liked(q: str | None = Query(default=None)) -> list[dict[str, Any]]:
        return await ctrl.library.list_liked(q)

    @app.get("/history", dependencies=[Depends(verify_token)])
    async def list_history(
        limit: int = Query(default=100, ge=1, le=500),
        sort: str = Query(default="recent"),
    ) -> list[dict[str, Any]]:
        if sort.lower() == "most_played":
            return await ctrl.library.get_most_played(limit=limit)
        return await ctrl.library.get_history(limit=limit)

    @app.get("/history/most-played", dependencies=[Depends(verify_token)])
    async def list_most_played(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return await ctrl.library.get_most_played(limit=limit)

    @app.get("/playlists", dependencies=[Depends(verify_token)])
    async def list_playlists() -> list[dict[str, Any]]:
        return await ctrl.library.list_playlists()

    @app.post("/playlists", dependencies=[Depends(verify_token)])
    async def create_playlist(body: PlaylistCreateRequest) -> dict[str, Any]:
        record = await ctrl.library.create_playlist(body.name, body.description)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "playlist_created", "playlist": record},
        )
        return record

    @app.delete("/playlists/{playlist_id}", dependencies=[Depends(verify_token)])
    async def delete_playlist(playlist_id: str) -> dict[str, Any]:
        deleted = await ctrl.library.delete_playlist(playlist_id)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "playlist_deleted", "id": playlist_id},
        )
        return {"status": "deleted" if deleted else "not_found", "id": playlist_id}

    @app.get("/playlists/{playlist_id}/tracks", dependencies=[Depends(verify_token)])
    async def get_playlist_tracks(playlist_id: str) -> list[dict[str, Any]]:
        return await ctrl.library.get_playlist_tracks(playlist_id)

    @app.post("/playlists/{playlist_id}/tracks", dependencies=[Depends(verify_token)])
    async def add_playlist_tracks(
        playlist_id: str,
        body: PlaylistAddTracksRequest,
    ) -> dict[str, Any]:
        items: list[Any] = []
        if body.tracks:
            items.extend(body.tracks)
        if body.track:
            items.append(body.track)
        added = await ctrl.library.add_to_playlist(playlist_id, items)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "playlist_tracks_added", "playlist_id": playlist_id, "added": added},
        )
        return {"status": "ok", "added": added}

    @app.delete("/playlists/{playlist_id}/tracks", dependencies=[Depends(verify_token)])
    async def delete_playlist_track(
        playlist_id: str,
        video_id: str | None = Query(default=None),
        body: PlaylistDeleteTrackRequest | None = None,
    ) -> dict[str, Any]:
        target_video_id = body.video_id if body and body.video_id else video_id
        if not target_video_id:
            raise HTTPException(status_code=400, detail="Missing video_id parameter")
        removed = await ctrl.library.remove_from_playlist(playlist_id, target_video_id)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {
                "type": "playlist_tracks_removed",
                "playlist_id": playlist_id,
                "video_id": target_video_id,
            },
        )
        return {"status": "ok", "removed": removed}

    @app.post("/import/url", dependencies=[Depends(verify_token)])
    async def start_url_import(body: ImportUrlRequest) -> dict[str, Any]:
        try:
            detect_import_target(body.url)
        except ImportSourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if body.destination == "existing_playlist":
            pid = (body.target_playlist_id or "").strip()
            if not pid:
                raise HTTPException(status_code=400, detail="Missing target_playlist_id")
            existing = await ctrl.library.list_playlists()
            if not any(p.get("id") == pid for p in existing):
                raise HTTPException(status_code=404, detail="Target playlist not found")

        try:
            job = await ctrl.importer.start_import(
                url=body.url,
                destination=body.destination,
                playlist_name=body.playlist_name,
                target_playlist_id=body.target_playlist_id,
                spawn_fn=ctrl.spawn_task,
            )
        except ImportSourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return job.model_dump()

    @app.get("/import/jobs/{job_id}", dependencies=[Depends(verify_token)])
    async def get_import_job(job_id: str) -> dict[str, Any]:
        job = ctrl.importer.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Import job not found")
        return job.model_dump()

    @app.post("/import/jobs/{job_id}/cancel", dependencies=[Depends(verify_token)])
    async def cancel_import_job(job_id: str) -> dict[str, Any]:
        job = ctrl.importer.cancel_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Import job not found")
        return job.model_dump()

    @app.post("/downloads/{video_id}", dependencies=[Depends(verify_token)])
    async def queue_download(
        video_id: str,
        body: DownloadTrackRequest | None = None,
    ) -> dict[str, Any]:
        valid_vid = _require_valid_video_id(video_id)
        track_meta = body.track_meta if body else None
        ctrl.queue_download(valid_vid, track_meta)
        return {"status": "queued", "video_id": valid_vid}

    @app.delete("/downloads/{video_id}", dependencies=[Depends(verify_token)])
    async def delete_download(video_id: str) -> dict[str, Any]:
        valid_vid = _require_valid_video_id(video_id)
        row = await ctrl.library.get_track(valid_vid)
        local_path = row.get("local_path") if row else None
        if local_path and getattr(ctrl.player, "next_source", None) == local_path:
            ctrl.player.clear_preloaded_next()
        if local_path and getattr(ctrl.player, "current_source", None) == local_path:
            ctrl.player.stop()
            if hasattr(ctrl.player.resolver, "invalidate"):
                ctrl.player.resolver.invalidate(valid_vid)
            await ctrl.ws_manager.broadcast("playback_tick", ctrl.get_playback_tick_data())
        deleted = await ctrl.downloader.delete_download(valid_vid)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "download_deleted", "video_id": valid_vid},
        )
        return {"status": "deleted" if deleted else "not_found", "video_id": valid_vid}

    @app.get("/downloads", dependencies=[Depends(verify_token)])
    async def list_downloads() -> list[dict[str, Any]]:
        return await ctrl.downloader.list_downloads()

    @app.get("/downloads/status", dependencies=[Depends(verify_token)])
    async def download_statuses() -> dict[str, DownloadProgress]:
        return ctrl.downloader.list_statuses()

    @app.get("/downloads/{video_id}/artwork", dependencies=[Depends(verify_token)])
    async def get_download_artwork(video_id: str) -> Response:
        valid_vid = _require_valid_video_id(video_id)
        art = await ctrl.downloader.get_local_artwork(valid_vid)
        if art is None:
            raise HTTPException(status_code=404, detail="Local artwork not available")
        art_bytes, mime_type = art
        return Response(
            content=art_bytes,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/settings", dependencies=[Depends(verify_token)])
    async def get_settings() -> dict[str, Any]:
        async with get_connection(ctrl.db_path) as conn:
            cursor = await conn.execute("SELECT key, value_json FROM settings;")
            rows = await cursor.fetchall()
            result: dict[str, Any] = {}
            for row in rows:
                with suppress(json.JSONDecodeError):
                    result[row["key"]] = json.loads(row["value_json"])
            return result

    @app.put("/settings", dependencies=[Depends(verify_token)])
    async def update_settings(settings_dict: dict[str, Any]) -> dict[str, Any]:
        if len(settings_dict) > 50:
            raise HTTPException(status_code=400, detail="Too many settings keys (max 50)")
        serialized: list[tuple[str, str]] = []
        for k, v in settings_dict.items():
            if not isinstance(k, str) or not k.strip() or len(k) > 64:
                raise HTTPException(
                    status_code=400, detail="Setting key must be a non-empty string (<= 64 chars)"
                )
            try:
                val_json = json.dumps(v)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail=f"Setting value for {k!r} is not JSON-serializable"
                ) from exc
            if len(val_json.encode("utf-8")) > 8192:
                raise HTTPException(
                    status_code=400, detail=f"Setting value for {k!r} exceeds 8192 bytes"
                )
            serialized.append((k, val_json))

        async with get_connection(ctrl.db_path) as conn:
            for k, val_json in serialized:
                await conn.execute(
                    """
                    INSERT INTO settings (key, value_json)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json;
                    """,
                    (k, val_json),
                )
            await conn.commit()

        if "history_threshold_s" in settings_dict:
            val = settings_dict["history_threshold_s"]
            if isinstance(val, (int, float)) and val > 0:
                ctrl._history_threshold_s = float(val)
        if "pause_listening_history" in settings_dict:
            paused = bool(settings_dict["pause_listening_history"])
            async with ctrl._history_lock:
                if paused != ctrl._pause_listening_history:
                    ctrl._pause_play_session()
                    await ctrl._write_play_session(force=True)
                    ctrl._pause_listening_history = paused
                    if not paused and ctrl.player.state == "playing":
                        ctrl._play_session_last_ts = time.monotonic()

        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "settings_updated", "settings": settings_dict},
        )
        return settings_dict

    @app.get("/player/details", dependencies=[Depends(verify_token)])
    async def get_player_details(video_id: str | None = Query(default=None)) -> dict[str, Any]:
        live = ctrl.player.get_audio_details()
        live_vid = live.get("video_id")
        target_vid = _require_valid_video_id(video_id) if video_id else live_vid
        db_row = await ctrl.library.get_track(target_vid) if target_vid else None
        is_current = bool(target_vid and live_vid and target_vid == live_vid)

        if is_current:
            result = dict(live)
        elif db_row is not None:
            result = {
                "video_id": db_row.get("video_id"),
                "title": db_row.get("title"),
                "artist": db_row.get("artist"),
                "album": db_row.get("album"),
                "duration_s": db_row.get("duration_s") or 0,
                "codec": None,
                "bitrate_kbps": None,
                "sample_rate_hz": None,
                "channel_count": None,
                "channel_layout": None,
                "file_size_bytes": db_row.get("file_size_bytes"),
                "path": db_row.get("local_path"),
                "state": "idle",
            }
        else:
            result = dict(live)
            if target_vid and not result.get("video_id"):
                result["video_id"] = target_vid

        if db_row is not None:
            result["title"] = db_row.get("title") or result.get("title")
            result["artist"] = db_row.get("artist") or result.get("artist")
            result["album"] = db_row.get("album") or result.get("album")
            result["is_liked"] = bool(db_row.get("is_liked"))
            result["play_count"] = int(db_row.get("play_count") or 0)
            result["downloaded_at"] = db_row.get("downloaded_at")
            local_path = db_row.get("local_path")
            result["local_path"] = local_path
            result["is_offline"] = bool(local_path)
            if result.get("path") is None and local_path:
                result["path"] = local_path
            if result.get("file_size_bytes") is None:
                if db_row.get("file_size_bytes") is not None:
                    result["file_size_bytes"] = int(db_row["file_size_bytes"])
                elif local_path:
                    with suppress(OSError):
                        p = Path(str(local_path))
                        if p.is_file():
                            result["file_size_bytes"] = p.stat().st_size
        else:
            result["is_offline"] = False
            result["local_path"] = None
            result["play_count"] = 0
            result["is_liked"] = False
        return result

    @app.get("/pins", dependencies=[Depends(verify_token)])
    async def list_pins() -> dict[str, list[dict[str, Any]]]:
        return await ctrl.library.get_pins()

    @app.post("/pins/artists", dependencies=[Depends(verify_token)])
    async def pin_artist(body: PinArtistRequest) -> dict[str, Any]:
        try:
            pins = await ctrl.library.pin_artist(
                channel_id=body.channel_id,
                name=body.name,
                thumbnail_url=body.thumbnail_url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ctrl._home_cache.clear()
        await ctrl.ws_manager.broadcast("library_changed", {"type": "pins_updated"})
        return pins

    @app.delete("/pins/artists/{channel_id:path}", dependencies=[Depends(verify_token)])
    async def unpin_artist(channel_id: str) -> dict[str, Any]:
        removed = await ctrl.library.unpin_artist(channel_id)
        ctrl._home_cache.clear()
        await ctrl.ws_manager.broadcast("library_changed", {"type": "pins_updated"})
        return {"status": "deleted" if removed else "not_found", "channel_id": channel_id}

    @app.post("/pins/{video_id}", dependencies=[Depends(verify_token)])
    async def pin_track(
        video_id: str,
        body: LikeTrackRequest | None = None,
    ) -> dict[str, Any]:
        valid_vid = _require_valid_video_id(video_id)
        pins = await ctrl.library.pin_track(valid_vid, body.track_meta if body else None)
        ctrl._home_cache.clear()
        await ctrl.ws_manager.broadcast(
            "library_changed", {"type": "pins_updated", "video_id": valid_vid}
        )
        return pins

    @app.delete("/pins/{video_id}", dependencies=[Depends(verify_token)])
    async def unpin_track(video_id: str) -> dict[str, Any]:
        valid_vid = _require_valid_video_id(video_id)
        removed = await ctrl.library.unpin_track(valid_vid)
        ctrl._home_cache.clear()
        await ctrl.ws_manager.broadcast(
            "library_changed", {"type": "pins_updated", "video_id": valid_vid}
        )
        return {"status": "deleted" if removed else "not_found", "video_id": valid_vid}

    @app.get("/privacy/blocked-artists", dependencies=[Depends(verify_token)])
    async def get_blocked_artists() -> list[dict[str, Any]]:
        return await ctrl.library.list_blocked_artists()

    @app.post("/privacy/blocked-artists", dependencies=[Depends(verify_token)])
    async def add_blocked_artist(body: BlockArtistRequest) -> dict[str, Any]:
        try:
            entry = await ctrl.library.block_artist(body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await ctrl.refresh_blocked_cache()
        await ctrl.ws_manager.broadcast(
            "library_changed", {"type": "blocked_artists_updated", "entry": entry}
        )
        return entry

    @app.delete("/privacy/blocked-artists/{name:path}", dependencies=[Depends(verify_token)])
    async def remove_blocked_artist(name: str) -> dict[str, Any]:
        removed = await ctrl.library.unblock_artist(name)
        await ctrl.refresh_blocked_cache()
        await ctrl.ws_manager.broadcast(
            "library_changed", {"type": "blocked_artists_updated", "name": name}
        )
        return {"status": "deleted" if removed else "not_found", "name": name}

    @app.delete("/history", dependencies=[Depends(verify_token)])
    async def clear_history() -> dict[str, str]:
        async with ctrl._history_lock:
            await ctrl.library.clear_history()
            ctrl._history_revision += 1
            ctrl._reset_play_session(ctrl.player.current_track, playing=ctrl.player.state == "playing")
        ctrl._home_cache.clear()
        await ctrl.ws_manager.broadcast("library_changed", {"type": "history_cleared"})
        return {"status": "cleared"}

    @app.delete("/history/{video_id}", dependencies=[Depends(verify_token)])
    async def delete_history_entry(video_id: str) -> dict[str, Any]:
        valid_vid = _require_valid_video_id(video_id)
        removed = await ctrl.library.remove_from_history(valid_vid)
        ctrl._home_cache.clear()
        await ctrl.ws_manager.broadcast(
            "library_changed", {"type": "history_updated", "video_id": valid_vid}
        )
        return {"status": "deleted" if removed else "not_found", "video_id": valid_vid}

    @app.patch("/tracks/{video_id}", dependencies=[Depends(verify_token)])
    async def patch_track_metadata(
        video_id: str,
        body: TrackUpdateRequest,
    ) -> dict[str, Any]:
        valid_vid = _require_valid_video_id(video_id)
        updated = await ctrl.library.update_track_metadata(
            valid_vid,
            title=body.title,
            artist=body.artist,
            album=body.album,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Track not found")
        queue_changed = False
        for item in ctrl.queue.items:
            if item.video_id == valid_vid:
                item.title = updated["title"]
                item.artist = updated["artist"]
                item.album = updated.get("album")
                queue_changed = True
        if queue_changed:
            ctrl.queue._notify()
        if ctrl.player.current_track and ctrl.player.current_track.video_id == valid_vid:
            ctrl.player.current_track.title = updated["title"]
            ctrl.player.current_track.artist = updated["artist"]
            ctrl.player.current_track.album = updated.get("album")
            await ctrl.ws_manager.broadcast(
                "track_changed",
                ctrl._track_changed_payload(ctrl.player.current_track),
            )
        ctrl._home_cache.clear()
        await ctrl.ws_manager.broadcast(
            "library_changed", {"type": "track_updated", "video_id": valid_vid, "track": updated}
        )
        return updated

    @app.get("/stats", dependencies=[Depends(verify_token)])
    async def get_listening_stats(range: str = Query(default="1m")) -> dict[str, Any]:
        try:
            return await ctrl.library.get_stats(range)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/radio/surprise", dependencies=[Depends(verify_token)])
    async def start_surprise_radio(
        body: SurpriseRadioRequest | None = None,
    ) -> dict[str, Any]:
        seed_vid = (body.seed_video_id if body else None) or None
        is_discovery_surprise = seed_vid is None

        if is_discovery_surprise and ctrl._surprise_lock.locked():
            return {"status": "busy", "seed": None, "queue": ctrl.get_queue_data()}

        ctrl._user_intent += 1
        intent = ctrl._user_intent
        async with ctrl._surprise_lock:
            now_mono = time.monotonic()
            if is_discovery_surprise and (now_mono - ctrl._surprise_last_ts) < 0.85:
                return {"status": "debounced", "seed": None, "queue": ctrl.get_queue_data()}

            exclude_ids: set[str] = set()
            seed_meta: dict[str, Any] | None = None

            if seed_vid:
                seed_vid = _require_valid_video_id(seed_vid)
            else:
                liked, played, hist, pins_data = await asyncio.gather(
                    ctrl.library.list_liked(),
                    ctrl.library.get_most_played(limit=50),
                    ctrl.library.get_history(limit=80),
                    ctrl.library.get_pins(),
                )
                for group in (liked, played, hist, pins_data.get("tracks", [])):
                    for item in group:
                        vid = item.get("video_id")
                        if vid:
                            exclude_ids.add(str(vid))

                for _, cached_home in list(ctrl._home_cache.values()):
                    for item in cached_home.get("speed_dial", []) or []:
                        if isinstance(item, dict) and item.get("video_id"):
                            exclude_ids.add(str(item["video_id"]))
                    for sec in cached_home.get("sections", []) or []:
                        for item in sec.get("contents", []) or []:
                            if isinstance(item, dict) and item.get("video_id"):
                                exclude_ids.add(str(item["video_id"]))

                curr_t = ctrl.queue.current_track
                if curr_t and curr_t.video_id:
                    exclude_ids.add(curr_t.video_id)

                taste_pool = ctrl._filter_blocked_items(liked[:12] + played[:12] + hist[:12])
                if taste_pool:
                    seed_meta = random.choice(taste_pool)
                    seed_vid = str(seed_meta["video_id"])

            async def _safe_radio_pick(vid: str | None) -> list[Track]:
                if not vid:
                    return []
                try:
                    return list(await ctrl.ytm.get_watch_radio(vid, limit=25))
                except Exception:  # noqa: BLE001
                    return []

            async def _safe_discovery(q: str) -> list[Track]:
                try:
                    return list(await ctrl.ytm.search(q, filter_type="songs", limit=24))
                except Exception:  # noqa: BLE001
                    return []

            discovery_queries = random.sample(
                _SURPRISE_DISCOVERY_QUERIES, k=min(2, len(_SURPRISE_DISCOVERY_QUERIES))
            )
            radio_res, disc_a, disc_b = await asyncio.gather(
                _safe_radio_pick(seed_vid),
                _safe_discovery(discovery_queries[0]),
                _safe_discovery(discovery_queries[1]) if len(discovery_queries) > 1 else _safe_discovery(discovery_queries[0]),
            )

            def _is_valid(t: Track) -> bool:
                if not t.video_id:
                    return False
                return not (ctrl._blocked_norms and artist_matches_blocked(t.artist, ctrl._blocked_norms))

            fresh_tracks: list[Track] = []
            seen_vids: set[str] = set()
            fallback_tracks: list[Track] = []

            raw_candidates = (
                [*disc_a, *disc_b, *radio_res]
                if is_discovery_surprise
                else [*radio_res, *disc_a]
            )
            for t in raw_candidates:
                if not _is_valid(t) or t.video_id in seen_vids:
                    continue
                seen_vids.add(t.video_id)
                if is_discovery_surprise and t.video_id in exclude_ids:
                    fallback_tracks.append(t)
                else:
                    fresh_tracks.append(t)

            if is_discovery_surprise:
                random.shuffle(fresh_tracks)
                random.shuffle(fallback_tracks)
                if len(fresh_tracks) < 12:
                    fresh_tracks.extend(fallback_tracks)

            if not fresh_tracks:
                raise HTTPException(status_code=502, detail="No playable tracks available for radio")

            if not is_discovery_surprise and len(fresh_tracks) > 2:
                head, tail = fresh_tracks[0], fresh_tracks[1:]
                random.shuffle(tail)
                fresh_tracks = [head, *tail]

            active_slice = [t.model_copy(update={"source": "user"}) for t in fresh_tracks[:24]]
            if intent != ctrl._user_intent:
                return {"status": "superseded", "seed": seed_meta, "queue": ctrl.get_queue_data()}
            radio_tail = [t.model_copy(update={"source": "radio"}) for t in fresh_tracks[24:30]]

            ctrl._cancel_pending_prefetch()
            ctrl.queue.set_queue(active_slice, start_index=0)
            if radio_tail:
                ctrl.queue.items.extend(radio_tail)
                ctrl.queue._original_items.extend(radio_tail)
                ctrl.queue._notify()

            if ctrl.queue.current_track:
                with suppress(StreamResolutionError):
                    await ctrl.play(track=ctrl.queue.current_track)

            if is_discovery_surprise:
                ctrl._surprise_last_ts = time.monotonic()

            return {"status": "ok", "seed": seed_meta, "queue": ctrl.get_queue_data()}

    @app.get("/artists/library", dependencies=[Depends(verify_token)])
    async def get_artists_library() -> dict[str, Any]:
        return await ctrl.library.list_artists_library()

    @app.post("/artists/follow", dependencies=[Depends(verify_token)])
    async def follow_artist_endpoint(body: FollowArtistRequest) -> dict[str, Any]:
        try:
            entry = await ctrl.library.follow_artist(
                channel_id=body.channel_id,
                name=body.name,
                thumbnail_url=body.thumbnail_url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ctrl._home_cache.pop("for_you", None)
        await ctrl.ws_manager.broadcast(
            "library_changed", {"type": "artist_followed", "artist": entry}
        )
        return entry

    @app.delete("/artists/follow/{channel_id:path}", dependencies=[Depends(verify_token)])
    async def unfollow_artist_endpoint(channel_id: str) -> dict[str, Any]:
        removed = await ctrl.library.unfollow_artist(channel_id)
        ctrl._home_cache.pop("for_you", None)
        await ctrl.ws_manager.broadcast(
            "library_changed", {"type": "artist_unfollowed", "channel_id": channel_id}
        )
        return {"status": "deleted" if removed else "not_found", "channel_id": channel_id}

    @app.get("/artists/local", dependencies=[Depends(verify_token)])
    async def get_local_artist_songs(
        name: str | None = Query(default=None),
        channel_id: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        if not (name and name.strip()) and not (channel_id and channel_id.strip()):
            raise HTTPException(status_code=400, detail="Either name or channel_id is required")
        return await ctrl.library.get_local_artist_tracks(name=name, channel_id=channel_id)

    @app.post("/artists/radio", dependencies=[Depends(verify_token)])
    async def start_artist_radio(body: ArtistRadioRequest) -> dict[str, Any]:
        ctrl._user_intent += 1
        intent = ctrl._user_intent
        lookup_cid = (
            None
            if not body.channel_id or str(body.channel_id).startswith(("local:", "name:"))
            else body.channel_id
        )
        profile: dict[str, Any] = {}
        with suppress(Exception):
            profile = await ctrl.ytm.get_artist_profile(name=body.name, channel_id=lookup_cid)

        top_raw = (
            profile.get("popular_tracks")
            or profile.get("top_songs")
            or profile.get("all_tracks")
            or []
        )
        seed_tracks: list[Track] = []
        for item in top_raw:
            if isinstance(item, dict) and item.get("video_id"):
                with suppress(Exception):
                    seed_tracks.append(Track(**item))

        related_tracks: list[Track] = []
        if seed_tracks:
            with suppress(Exception):
                related_tracks = await ctrl.ytm.get_watch_radio(seed_tracks[0].video_id, limit=24)
        if len(seed_tracks) + len(related_tracks) < 22:
            with suppress(Exception):
                extra = await ctrl.ytm.search(
                    f"{body.name} songs", filter_type="songs", limit=24
                )
                related_tracks.extend(extra)

        merged: list[Track] = []
        seen_ids: set[str] = set()
        for t in [*seed_tracks[:12], *related_tracks]:
            if t.video_id in seen_ids:
                continue
            if ctrl._blocked_norms and artist_matches_blocked(t.artist, ctrl._blocked_norms):
                continue
            seen_ids.add(t.video_id)
            merged.append(t)

        if not merged:
            raise HTTPException(status_code=502, detail="Unable to build artist radio queue")

        if len(merged) > 3:
            first = merged[0]
            rest = merged[1:]
            random.shuffle(rest)
            merged = [first, *rest]

        active_slice = [t.model_copy(update={"source": "user"}) for t in merged[:24]]
        if intent != ctrl._user_intent:
            return {"status": "superseded", "artist": body.name, "queue": ctrl.get_queue_data()}
        radio_tail = [t.model_copy(update={"source": "radio"}) for t in merged[24:30]]

        ctrl._cancel_pending_prefetch()
        ctrl.queue.set_queue(active_slice, start_index=0)
        if radio_tail:
            ctrl.queue.items.extend(radio_tail)
            ctrl.queue._original_items.extend(radio_tail)
            ctrl.queue._notify()
        if ctrl.queue.current_track:
            with suppress(StreamResolutionError):
                await ctrl.play(track=ctrl.queue.current_track)
        return {
            "status": "ok",
            "artist": profile.get("name") or profile.get("artist") or body.name,
            "queue": ctrl.get_queue_data(),
        }

    @app.get("/artists/new-releases", dependencies=[Depends(verify_token)])
    async def get_artists_new_releases(refresh: bool = Query(default=False)) -> list[dict[str, Any]]:
        return await ctrl.get_followed_new_releases(refresh=refresh, trigger_refresh=True)

    @app.get("/updates/status", dependencies=[Depends(verify_token)])
    async def get_update_status() -> dict[str, Any]:
        return ctrl.updater.get_state()

    @app.post("/updates/check", dependencies=[Depends(verify_token)])
    async def check_updates(force: bool = Query(default=True)) -> dict[str, Any]:
        return await ctrl.updater.check_for_updates(force=force)

    @app.post("/updates/download", dependencies=[Depends(verify_token)])
    async def download_update(auto_install: bool = Query(default=True)) -> dict[str, Any]:
        ctrl.updater.start_download(auto_install=auto_install)
        return ctrl.updater.get_state()

    @app.post("/updates/install", dependencies=[Depends(verify_token)])
    async def install_update() -> dict[str, Any]:
        return await asyncio.to_thread(ctrl.updater.launch_installer)

    @app.get("/updates/verified-path", dependencies=[Depends(verify_token)])
    async def verified_update_path() -> str:
        try:
            return str(await asyncio.to_thread(ctrl.updater.verified_installer_path))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/shutdown", dependencies=[Depends(verify_token)])
    async def shutdown_server() -> dict[str, str]:
        """Cleanly terminate audio playback, remove bridge file, and stop FastAPI server."""
        logger.info("Shutdown requested via bridge API")
        await ctrl.queue.flush()
        ctrl._pause_play_session()
        await ctrl._flush_play_session(force=True)
        loop = asyncio.get_running_loop()
        server_obj = getattr(app.state, "server", None)

        def _do_shutdown() -> None:
            ctrl.shutdown()
            if server_obj is not None:
                server_obj.stop()

        loop.call_later(0.1, _do_shutdown)
        return {"status": "shutting_down"}

    @app.post("/player/quit", dependencies=[Depends(verify_token)])
    async def player_quit() -> dict[str, str]:
        """Alias for /shutdown."""
        return await shutdown_server()

    return app


class BridgeServer:
    """Manages the lifecycle of the FastAPI bridge and background workers."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        token: str | None = None,
        controller: BridgeController | None = None,
        bridge_file_path: Path | None = None,
    ) -> None:
        cfg = get_config()
        self.host = host
        self.target_port = port if port is not None else cfg.bridge.port
        self.port: int = find_available_port(self.host, self.target_port)
        self.token: str = token or secrets.token_urlsafe(32)
        self.bridge_file: Path = (
            Path(bridge_file_path)
            if bridge_file_path is not None
            else cfg.app.data_dir / "bridge.json"
        )
        self.controller = controller or BridgeController()
        self.app = create_app(controller=self.controller, token=self.token)
        self.app.state.server = self
        self._uvicorn_server: uvicorn.Server | None = None

    def start_sync(self) -> None:
        write_bridge_file(self.bridge_file, self.host, self.port, self.token)
        uv_config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            workers=1,
            loop="asyncio",
        )
        self._uvicorn_server = uvicorn.Server(uv_config)
        try:
            self._uvicorn_server.run()
        finally:
            remove_bridge_file(self.bridge_file)

    async def start(self) -> None:
        write_bridge_file(self.bridge_file, self.host, self.port, self.token)
        uv_config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            workers=1,
            loop="asyncio",
        )
        self._uvicorn_server = uvicorn.Server(uv_config)
        try:
            await self._uvicorn_server.serve()
        finally:
            remove_bridge_file(self.bridge_file)

    def stop(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        remove_bridge_file(self.bridge_file)
        self.controller.shutdown()
