"""Local bridge server (FastAPI & WebSocket) for MetroLite."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import secrets
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from metrolite.audio.media_keys import MediaKeysListener
from metrolite.audio.player import AudioPlayer, trim_memory
from metrolite.audio.queue import QueueManager
from metrolite.audio.resolver import StreamResolutionError
from metrolite.config import get_config
from metrolite.library.downloader import DownloadManager, DownloadProgress, validate_video_id
from metrolite.library.manager import LibraryManager
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

_MOOD_CONFIG: dict[str, tuple[str, str, list[str]]] = {
    "trending": (
        "Trending Right Now",
        "Hottest tracks climbing the charts",
        ["top trending songs 2026", "viral hits india 2026", "global top songs"],
    ),
    "bollywood": (
        "Bollywood & Desi Hits",
        "Latest Hindi, Punjabi & indie favorites",
        ["latest hindi bollywood hits", "trending punjabi hindi songs", "arijit singh top songs"],
    ),
    "chill": (
        "Chill & Lo-Fi Vibes",
        "Laid-back beats to unwind",
        ["chill lofi beats relax", "hindi lofi chill mix", "aesthetic chill indie"],
    ),
    "focus": (
        "Deep Focus & Study",
        "Distraction-free instrumental flow",
        ["deep focus study instrumental", "lofi study beats concentration", "ambient focus music"],
    ),
    "workout": (
        "Workout Energy",
        "High-tempo tracks to keep you moving",
        ["high energy workout gym music", "punjabi workout bass hits", "electronic workout pump"],
    ),
    "latenight": (
        "Late Night Drives",
        "Moody acoustics and midnight melodies",
        ["late night drive hindi indie", "midnight acoustic chill songs", "slowed reverb night vibes"],
    ),
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
        self.queue = queue or QueueManager(
            db_path=self.db_path, ytm_client=self.ytm, auto_persist=True
        )
        self.library = library or LibraryManager(db_path=self.db_path)
        self.downloader = downloader or DownloadManager(db_path=self.db_path)
        self.updater = updater or UpdateManager()
        self.media_keys = media_keys
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._radio_task: asyncio.Task[None] | None = None
        self._preload_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._play_seq: int = 0
        self._shutdown: bool = False
        self._home_cache: dict[str, tuple[float, dict[str, Any]]] = {}

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
        self.run_coro_threadsafe(self.handle_track_ended(reason=reason))

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

    async def build_personalized_home(
        self, mood: str = "for_you", refresh: bool = False
    ) -> dict[str, Any]:
        """Build a fast, personalized home feed adapting to user history, top artists & trends."""
        cache_key = mood.lower().strip() or "for_you"
        now = time.monotonic()
        if not refresh and cache_key in self._home_cache:
            expires_at, cached_payload = self._home_cache[cache_key]
            if now < expires_at:
                return cached_payload

        greeting = _time_greeting()

        # Mood-specific view
        if cache_key in _MOOD_CONFIG:
            title, subtitle, queries = _MOOD_CONFIG[cache_key]
            chosen_query = random.choice(queries) if refresh else queries[0]
            try:
                tracks = await self.ytm.search(chosen_query, filter_type="songs", limit=18)
                dumped = [t.model_dump() for t in tracks]
            except Exception as e:  # noqa: BLE001
                logger.warning("Mood search failed for %s: %s", cache_key, e)
                dumped = []

            payload = {
                "greeting": greeting,
                "speed_dial": [],
                "sections": [
                    {
                        "id": f"mood_{cache_key}",
                        "title": title,
                        "subtitle": subtitle,
                        "layout": "cards",
                        "contents": dumped,
                    }
                ],
                "personalized": False,
            }
            if dumped:
                if len(self._home_cache) >= 4 and cache_key not in self._home_cache:
                    self._home_cache.pop(next(iter(self._home_cache)), None)
                self._home_cache[cache_key] = (now + 300.0, payload)
            self.spawn_task(asyncio.to_thread(trim_memory))
            return payload

        # Personalized "For You" view
        most_played: list[dict[str, Any]] = []
        recent_history: list[dict[str, Any]] = []
        liked_tracks: list[dict[str, Any]] = []

        with suppress(Exception):
            most_played, recent_history, liked_tracks = await asyncio.gather(
                self.library.get_most_played(limit=12),
                self.library.get_history(limit=12),
                self.library.list_liked(),
            )

        # Build Speed Dial (Jump Back In) from most played + recent + liked
        speed_dial: list[dict[str, Any]] = []
        seen_dial: set[str] = set()
        for pool in (most_played[:4], recent_history[:4], liked_tracks[:4]):
            for item in pool:
                vid = item.get("video_id")
                if vid and vid not in seen_dial:
                    seen_dial.add(vid)
                    speed_dial.append(item)
                if len(speed_dial) >= 6:
                    break
            if len(speed_dial) >= 6:
                break

        # Choose personalization seeds from user's listening profile
        seed_pool = most_played[:8] + liked_tracks[:6] + recent_history[:6]
        unique_seeds: list[dict[str, Any]] = []
        seen_seed_ids: set[str] = set()
        for s in seed_pool:
            vid = s.get("video_id")
            if vid and vid not in seen_seed_ids:
                seen_seed_ids.add(vid)
                unique_seeds.append(s)

        seed_track: dict[str, Any] | None = None
        if unique_seeds:
            seed_track = random.choice(unique_seeds[:6]) if refresh else unique_seeds[0]

        # Extract top artists from user's play counts & likes
        artist_scores: dict[str, int] = {}
        for item in most_played:
            raw_artist = (item.get("artist") or "").split(",")[0].strip()
            if raw_artist and raw_artist.lower() != "unknown artist":
                artist_scores[raw_artist] = artist_scores.get(raw_artist, 0) + max(
                    1, int(item.get("play_count") or 1)
                )
        for item in liked_tracks[:15]:
            raw_artist = (item.get("artist") or "").split(",")[0].strip()
            if raw_artist and raw_artist.lower() != "unknown artist":
                artist_scores[raw_artist] = artist_scores.get(raw_artist, 0) + 2

        top_artists = [
            a for a, _ in sorted(artist_scores.items(), key=lambda kv: kv[1], reverse=True)
        ]
        chosen_artist: str | None = None
        if top_artists:
            chosen_artist = random.choice(top_artists[:4]) if refresh else top_artists[0]

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
                return [t.model_dump() for t in res if t.video_id != vid]
            except Exception:  # noqa: BLE001
                return []

        async def _safe_search(query: str | None, limit: int = 12) -> list[dict[str, Any]]:
            if not query:
                return []
            try:
                res = await self.ytm.search(query, filter_type="songs", limit=limit)
                return [t.model_dump() for t in res]
            except Exception:  # noqa: BLE001
                return []

        home_raw, radio_tracks, artist_tracks, trending_tracks = await asyncio.gather(
            _safe_home(),
            _safe_radio(seed_track.get("video_id") if seed_track else None),
            _safe_search(f"{chosen_artist} songs" if chosen_artist else None, limit=12),
            _safe_search(trending_q, limit=12),
        )

        sections: list[dict[str, Any]] = []
        is_personalized = bool(seed_track or most_played or liked_tracks)

        # 1. Quick Picks (Metrolist-style compact grid combining YTM Quick Picks + radio)
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
                    "contents": radio_tracks[:12],
                }
            )

        # 3. Your Most Played (from user's local play_count stats)
        if most_played:
            sections.append(
                {
                    "id": "most_played",
                    "title": "Your Most Played",
                    "subtitle": "Songs you keep coming back to",
                    "layout": "cards",
                    "contents": most_played[:12],
                }
            )

        # 4. More from Top Artist
        if chosen_artist and artist_tracks:
            sections.append(
                {
                    "id": "top_artist",
                    "title": f"More from {chosen_artist}",
                    "subtitle": "Based on your most listened artists",
                    "layout": "cards",
                    "contents": artist_tracks[:12],
                }
            )

        # 5. Live Trending Now
        if trending_tracks:
            sections.append(
                {
                    "id": "trending_now",
                    "title": "Trending Now",
                    "subtitle": "Fresh chart-toppers on YouTube Music",
                    "layout": "cards",
                    "contents": trending_tracks[:12],
                }
            )

        # 6. Additional YTM Home sections if available
        for idx, sec in enumerate(home_raw[1:3]):
            valid = [
                c.model_dump() if isinstance(c, Track) else c
                for c in sec.get("contents", [])
                if isinstance(c, Track) or (isinstance(c, dict) and c.get("video_id"))
            ]
            if valid:
                sections.append(
                    {
                        "id": f"ytm_home_{idx}",
                        "title": sec.get("title") or "Recommended Mixes",
                        "layout": "cards",
                        "contents": valid[:12],
                    }
                )

        payload = {
            "greeting": greeting,
            "speed_dial": speed_dial,
            "sections": sections,
            "personalized": is_personalized,
        }
        has_content = bool(speed_dial) or any(
            bool(sec.get("contents")) for sec in sections
        )
        if has_content:
            if len(self._home_cache) >= 4 and cache_key not in self._home_cache:
                self._home_cache.pop(next(iter(self._home_cache)), None)
            self._home_cache[cache_key] = (now + 300.0, payload)
        self.spawn_task(asyncio.to_thread(trim_memory))
        return payload

    def _cancel_pending_prefetch(self) -> None:
        """Cancel any in-flight radio refill or gapless preload tasks before switching tracks."""
        if self._radio_task and not self._radio_task.done():
            self._radio_task.cancel()
        if self._preload_task and not self._preload_task.done():
            self._preload_task.cancel()

    async def _after_play_started(self, target: Track | None) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            preview = self.queue.next_track_preview
            if preview:
                with suppress(Exception):
                    await self.player.preload_next(preview)
            return

        self._cancel_pending_prefetch()

        if target is not None:
            async def _record() -> None:
                with suppress(Exception):
                    await self.library.record_play(target.video_id, target)

            self.spawn_task(_record())

        remaining = len(self.queue.items) - 1 - self.queue.current_index
        if self.queue.autoplay_radio and remaining < 3 and self.queue.current_track is not None:
            async def _refill() -> None:
                with suppress(asyncio.CancelledError, Exception):
                    added = await self.queue.refill_radio(limit=20)
                    if added > 0:
                        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
                    nxt_preview = self.queue.next_track_preview
                    if nxt_preview:
                        curr_preloaded = getattr(self.player, "next_track", None)
                        if curr_preloaded is None or curr_preloaded.video_id != nxt_preview.video_id:
                            with suppress(Exception):
                                await self.player.preload_next(nxt_preview)
                    await asyncio.to_thread(trim_memory)

            self._radio_task = self.spawn_task(_refill())
        else:
            preview = self.queue.next_track_preview
            if preview:
                async def _preload() -> None:
                    with suppress(asyncio.CancelledError, Exception):
                        await self.player.preload_next(preview)
                        await asyncio.to_thread(trim_memory)

                self._preload_task = self.spawn_task(_preload())
            else:
                self.spawn_task(asyncio.to_thread(trim_memory))

    def sync_preloaded_next(self) -> None:
        """Invalidate stale mpv preloaded tracks and preload the updated next_track_preview."""
        preview = self.queue.next_track_preview
        curr_preloaded = getattr(self.player, "next_track", None)
        preview_id = preview.video_id if preview is not None else None
        curr_id = curr_preloaded.video_id if curr_preloaded is not None else None

        if preview_id is None:
            if self._preload_task and not self._preload_task.done():
                self._preload_task.cancel()
            if curr_id is not None:
                if hasattr(self.player, "clear_preloaded_next"):
                    self.player.clear_preloaded_next()
                else:
                    self.player.next_track = None
            return

        if preview_id == curr_id:
            return

        if self._preload_task and not self._preload_task.done():
            self._preload_task.cancel()
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
                    await asyncio.to_thread(trim_memory)

            with suppress(RuntimeError):
                self._preload_task = self.spawn_task(_repreload(preview))

    def get_playback_tick_data(self) -> dict[str, Any]:
        return {
            "time_pos": round(self.player.time_pos, 2),
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

    async def handle_track_ended(self, reason: str = "eof") -> None:
        if reason in _NON_COMPLETION_REASONS:
            return

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

        next_track = await self.queue.next_track(is_error=is_error)
        if req_seq != self._play_seq:
            return

        if next_track:
            try:
                if not is_error and self.player.consume_gapless_promotion(next_track.video_id):
                    await self._after_play_started(next_track)
                else:
                    await self.player.load_and_play(next_track)
                    if req_seq != self._play_seq:
                        return
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
            self.player.pause()
            await self.ws_manager.broadcast(
                "track_changed",
                self._track_changed_payload(None, state="stopped"),
            )
        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())

    async def toggle_play_pause(self) -> dict[str, Any]:
        if self.player.state == "playing":
            self.player.pause()
        elif self.player.state == "paused":
            self.player.resume()
        else:
            self.player.reset_errors()
            self.queue.reset_errors()
            self._cancel_pending_prefetch()
            self._play_seq += 1
            req_seq = self._play_seq
            curr = self.queue.current_track
            if curr:
                try:
                    await self.player.load_and_play(curr)
                    if req_seq != self._play_seq:
                        return self.get_playback_tick_data()
                    await self._after_play_started(curr)
                    await self.ws_manager.broadcast(
                        "track_changed",
                        self._track_changed_payload(curr),
                    )
                except Exception as e:  # noqa: BLE001
                    if req_seq != self._play_seq:
                        return self.get_playback_tick_data()
                    logger.error("Failed to start track on toggle_play_pause: %s", e)
                    self.player.record_error()
                    self.queue.consecutive_errors = self.player.consecutive_errors
                    await self.ws_manager.broadcast(
                        "error",
                        {
                            "message": f"Failed to play track: {e}",
                            "video_id": curr.video_id,
                            "consecutive_errors": self.player.consecutive_errors,
                        },
                    )
                    return {
                        "error": str(e),
                        "status": "error",
                        "video_id": curr.video_id,
                        **self.get_playback_tick_data(),
                    }
        return self.get_playback_tick_data()

    async def play(
        self, video_id: str | None = None, track: Track | None = None
    ) -> dict[str, Any]:
        self.player.reset_errors()
        self.queue.reset_errors()
        self._cancel_pending_prefetch()
        self._play_seq += 1
        req_seq = self._play_seq

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
                self.queue.current_index = len(self.queue.items) - 1
                self.queue._notify()
        elif target is not None:
            curr_q = self.queue.current_track
            if curr_q is None or curr_q.video_id != target.video_id:
                idx = next(
                    (i for i, t in enumerate(self.queue.items) if t.video_id == target.video_id),
                    None,
                )
                if idx is not None:
                    self.queue.items[idx] = target
                    self.queue.current_index = idx
                    self.queue._notify()
                else:
                    self.queue.append(target)
                    self.queue.current_index = len(self.queue.items) - 1
                    self.queue._notify()

        curr_target = target
        try:
            if target is not None:
                await self.ws_manager.broadcast(
                    "track_changed",
                    self._track_changed_payload(target, state="buffering"),
                )
                await self.player.load_and_play(target)
                if req_seq != self._play_seq:
                    return self.get_playback_tick_data()
                if (
                    self.player.state in ("paused", "stopped")
                    and getattr(self.player, "loaded_video_id", target.video_id) != target.video_id
                ):
                    await self.ws_manager.broadcast("playback_tick", self.get_playback_tick_data())
                    return self.get_playback_tick_data()
                await self._after_play_started(target)
                await self.ws_manager.broadcast(
                    "track_changed",
                    self._track_changed_payload(target),
                )
                await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
            else:
                curr = self.queue.current_track
                curr_target = curr
                same_track_paused = (
                    self.player.state == "paused"
                    and curr is not None
                    and self.player.current_track is not None
                    and self.player.current_track.video_id == curr.video_id
                    and getattr(self.player, "loaded_video_id", curr.video_id) == curr.video_id
                )
                if same_track_paused or (
                    self.player.state == "paused"
                    and curr is None
                    and getattr(self.player, "loaded_video_id", None) is not None
                ):
                    self.player.resume()
                elif curr is not None:
                    await self.ws_manager.broadcast(
                        "track_changed",
                        self._track_changed_payload(curr, state="buffering"),
                    )
                    await self.player.load_and_play(curr)
                    if req_seq != self._play_seq:
                        return self.get_playback_tick_data()
                    if (
                        self.player.state in ("paused", "stopped")
                        and getattr(self.player, "loaded_video_id", curr.video_id) != curr.video_id
                    ):
                        await self.ws_manager.broadcast(
                            "playback_tick", self.get_playback_tick_data()
                        )
                        return self.get_playback_tick_data()
                    await self._after_play_started(curr)
                    await self.ws_manager.broadcast(
                        "track_changed",
                        self._track_changed_payload(curr),
                    )
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

    async def next_track(self) -> dict[str, Any]:
        self.player.reset_errors()
        self.queue.reset_errors()
        self._cancel_pending_prefetch()
        self._play_seq += 1
        req_seq = self._play_seq
        nxt = await self.queue.next_track()
        if req_seq != self._play_seq:
            return self.get_playback_tick_data()
        if nxt:
            try:
                if self.player.switch_to_preloaded_next(nxt):
                    await self._after_play_started(nxt)
                else:
                    await self.ws_manager.broadcast(
                        "track_changed",
                        self._track_changed_payload(nxt, state="buffering"),
                    )
                    await self.player.load_and_play(nxt)
                    if req_seq != self._play_seq:
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

    async def prev_track(self) -> dict[str, Any]:
        self.player.reset_errors()
        self.queue.reset_errors()
        self._cancel_pending_prefetch()
        self._play_seq += 1
        req_seq = self._play_seq
        prv = self.queue.prev_track()
        if prv:
            try:
                await self.ws_manager.broadcast(
                    "track_changed",
                    self._track_changed_payload(prv, state="buffering"),
                )
                await self.player.load_and_play(prv)
                if req_seq != self._play_seq:
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
                if curr_state == "playing" or last_state == "playing":
                    if self.ws_manager.active_connections:
                        data = self.get_playback_tick_data()
                        await self.ws_manager.broadcast("playback_tick", data)
                    if last_state == "playing" and curr_state != "playing":
                        last_trim_ts = time.monotonic()
                        await asyncio.to_thread(trim_memory)
                    last_state = curr_state
                now = time.monotonic()
                if now - last_trim_ts >= 120.0:
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
        self.stop_tick_loop()
        self.stop_media_keys()
        if hasattr(self.queue, "flush_sync"):
            self.queue.flush_sync()
        if self._radio_task and not self._radio_task.done():
            self._radio_task.cancel()
        if self._preload_task and not self._preload_task.done():
            self._preload_task.cancel()
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        self._background_tasks.clear()
        if hasattr(self.downloader, "stop"):
            self.downloader.stop()
        if hasattr(self.updater, "stop"):
            self.updater.stop()
        if hasattr(self.player, "terminate"):
            self.player.terminate()
        trim_memory()


class PlayRequest(BaseModel):
    video_id: str | None = None
    track: Track | None = None
    track_meta: Track | None = None


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


class DownloadTrackRequest(BaseModel):
    track_meta: Track | dict[str, Any] | None = None


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

                upd_cfg = getattr(get_config(), "updates", None)
                if upd_cfg and upd_cfg.enabled and upd_cfg.check_on_startup:
                    async def _startup_update_check() -> None:
                        await asyncio.sleep(4.0)
                        with suppress(Exception):
                            await ctrl.updater.check_for_updates(force=False)

                    ctrl.spawn_task(_startup_update_check())
            yield
        finally:
            ctrl.shutdown()

    app = FastAPI(
        title="MetroLite Bridge API",
        version="0.1.0",
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
            "version": "0.1.0",
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
    ) -> dict[str, Any]:
        """Fetch time-synced lyrics via LRCLIB + YouTube Music native timestamps."""
        from metrolite.ytm.lyrics import resolve_lyrics

        valid_vid = _require_valid_video_id(video_id)
        return await resolve_lyrics(
            ctrl.ytm,
            video_id=valid_vid,
            title=title,
            artist=artist,
            duration=duration,
            custom_query=q,
            db_path=ctrl.db_path,
        )

    @app.get("/search", dependencies=[Depends(verify_token)])
    async def get_search(
        q: str = Query(..., min_length=1),
        filter: str = Query(default="songs"),
        limit: int = Query(default=25, ge=1, le=50),
    ) -> list[dict[str, Any]]:
        try:
            eff_filter: str | None = None if filter.lower() == "all" else filter
            results = await ctrl.ytm.search(query=q, filter_type=eff_filter, limit=limit)
            return [t.model_dump() for t in results]
        except Exception as e:
            logger.error("Search failed for query %r: %s", q, e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Search failed: {e}",
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
        ctrl.player.pause()
        tick_data = ctrl.get_playback_tick_data()
        await ctrl.ws_manager.broadcast("playback_tick", tick_data)
        return tick_data

    @app.post("/player/toggle", dependencies=[Depends(verify_token)])
    async def player_toggle() -> dict[str, Any]:
        """Toggle playback between playing and paused."""
        if ctrl.player.state == "playing":
            ctrl.player.pause()
            tick_data = ctrl.get_playback_tick_data()
            await ctrl.ws_manager.broadcast("playback_tick", tick_data)
            return tick_data
        return await ctrl.play()

    @app.post("/player/next", dependencies=[Depends(verify_token)])
    async def player_next() -> dict[str, Any]:
        return await ctrl.next_track()

    @app.post("/player/prev", dependencies=[Depends(verify_token)])
    async def player_prev() -> dict[str, Any]:
        return await ctrl.prev_track()

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
        """Silently warm stream URL cache on track hover for instant playback."""
        vid = body.get("video_id") if isinstance(body, dict) else None
        if isinstance(vid, str) and vid:
            valid_vid = _require_valid_video_id(vid)
            if not ctrl.player.resolver.is_cached(valid_vid):
                async def _warm() -> None:
                    with suppress(Exception):
                        await ctrl.player.resolver.resolve(valid_vid)

                ctrl.spawn_task(_warm())
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

        if action == "set":
            tracks = body.items or ([body.item] if body.item else [])
            ctrl.queue.set_queue(tracks, start_index=body.start_index)
            if body.auto_play and ctrl.queue.current_track:
                try:
                    await ctrl.play(track=ctrl.queue.current_track)
                except StreamResolutionError as e:
                    logger.error("Stream resolution failed during queue autoplay: %s", e)

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
                ctrl.queue.move(body.from_idx, body.to_idx)

        elif action == "clear":
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
                await ctrl.play(track=ctrl.queue.current_track)

        elif action == "autoplay_radio":
            new_radio = (
                body.autoplay_radio
                if body.autoplay_radio is not None
                else not ctrl.queue.autoplay_radio
            )
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
        if ctrl.player.next_track is not None and ctrl.player.next_track.video_id == valid_vid:
            ctrl.player.clear_preloaded_next()
        active_is_target = (
            (ctrl.player.current_track is not None and ctrl.player.current_track.video_id == valid_vid)
            or getattr(ctrl.player, "loaded_video_id", None) == valid_vid
        )
        if active_is_target:
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

        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "settings_updated", "settings": settings_dict},
        )
        return settings_dict

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
        return ctrl.updater.launch_installer()

    @app.post("/shutdown", dependencies=[Depends(verify_token)])
    async def shutdown_server() -> dict[str, str]:
        """Cleanly terminate audio playback, remove bridge file, and stop FastAPI server."""
        logger.info("Shutdown requested via bridge API")
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
