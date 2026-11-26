"""Audio stream resolver for ORBIT (fast single-client + sticky fallback)."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
import weakref
from contextlib import suppress
from pathlib import Path
from typing import Any, Self

import yt_dlp
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from metrolite.config import get_config
from metrolite.library.downloader import validate_video_id
from metrolite.store.db import get_connection, get_default_db_path, get_sync_connection

logger = logging.getLogger(__name__)

DEFAULT_CLIENT = "android"
CLIENT_FALLBACK_ORDER = ["android", "mweb", "web_embedded", "ios", "tv_embedded"]


class _QuietYtdlLogger:
    """Suppress noisy yt-dlp stderr spam during automatic client fallbacks."""

    def debug(self, _msg: str) -> None:
        pass

    def warning(self, _msg: str) -> None:
        pass

    def error(self, _msg: str) -> None:
        pass


class StreamResolutionError(Exception):
    """Exception raised when an audio stream cannot be resolved."""


class ResolvedSource(str):
    """Audio source string (URL or local path) carrying optional user_agent and headers."""

    user_agent: str | None
    http_headers: dict[str, str]

    def __new__(
        cls,
        value: str,
        user_agent: str | None = None,
        http_headers: dict[str, str] | None = None,
    ) -> Self:
        obj = super().__new__(cls, value)
        obj.user_agent = user_agent
        obj.http_headers = http_headers or {}
        return obj

    def __iter__(self):
        """Allow unpacking: stream_url, headers = resolved_source."""
        yield str(self)
        yield self.http_headers


class StreamResolver:
    """Resolves YouTube video IDs to playable audio sources (local file or stream URL)."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        format_selector: str | None = None,
        stream_url_ttl_s: int | None = None,
        cookies_file: Path | str | None = None,
        cookies_from_browser: str | None = None,
    ) -> None:
        cfg = get_config()
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self.format_selector = (
            format_selector
            or cfg.audio.format_selector
            or "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best"
        )
        self.ttl_s = (
            stream_url_ttl_s if stream_url_ttl_s is not None else cfg.audio.stream_url_ttl_s
        )
        self.cookies_file = (
            Path(cookies_file)
            if cookies_file is not None
            else getattr(cfg.audio, "cookies_file", None) or cfg.app.cookies_file
        )
        if self.cookies_file is None:
            auto_cookies = cfg.app.data_dir / "cookies.txt"
            if auto_cookies.is_file():
                self.cookies_file = auto_cookies

        self.cookies_from_browser = (
            cookies_from_browser
            if cookies_from_browser is not None
            else getattr(cfg.audio, "cookies_from_browser", None)
            or getattr(cfg.app, "cookies_from_browser", None)
        )
        self._working_client: str = DEFAULT_CLIENT
        self._cache: dict[str, tuple[ResolvedSource, float]] = {}
        self._inflight: dict[str, asyncio.Task[ResolvedSource]] = {}
        self._prefetch_sem_map: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Semaphore
        ] = weakref.WeakKeyDictionary()
        self._user_agents: dict[str, str] = {}
        self._http_headers: dict[str, dict[str, str]] = {}
        self._meta_lock = threading.Lock()

    def is_cached(self, video_id: str) -> bool:
        if video_id in self._cache:
            _, expires_at = self._cache[video_id]
            return time.monotonic() < expires_at
        return False

    def get_user_agent(self, video_id: str) -> str | None:
        with self._meta_lock:
            return self._user_agents.get(video_id)

    def get_http_headers(self, video_id: str) -> dict[str, str]:
        with self._meta_lock:
            return dict(self._http_headers.get(video_id, {}))

    def invalidate(self, video_id: str) -> None:
        """Evict a failed or expired stream URL from caches so retry resolves fresh."""
        self._cache.pop(video_id, None)
        with self._meta_lock:
            self._user_agents.pop(video_id, None)
            self._http_headers.pop(video_id, None)

    def clear_cache(self) -> None:
        self._cache.clear()
        with self._meta_lock:
            self._user_agents.clear()
            self._http_headers.clear()

    def _check_local_sync(self, video_id: str) -> ResolvedSource | None:
        if not self.db_path.is_file():
            return None
        try:
            with get_sync_connection(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT local_path FROM tracks WHERE video_id = ?;", (video_id,)
                )
                row = cursor.fetchone()
                if row and row["local_path"]:
                    local_path = Path(row["local_path"])
                    if local_path.is_file():
                        return ResolvedSource(str(local_path.resolve()))
        except (sqlite3.Error, OSError) as e:
            logger.warning("Error checking local database for %s: %s", video_id, e)
        return None

    async def _check_local_async(self, video_id: str) -> ResolvedSource | None:
        if not self.db_path.is_file():
            return None
        try:
            async with get_connection(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT local_path FROM tracks WHERE video_id = ?;", (video_id,)
                )
                row = await cursor.fetchone()
                if row and row["local_path"]:
                    local_path = Path(row["local_path"])
                    if local_path.is_file():
                        return ResolvedSource(str(local_path.resolve()))
        except (sqlite3.Error, OSError) as e:
            logger.warning("Error checking local database for %s: %s", video_id, e)
        return None

    def _extract_url_sync(self, video_id: str) -> ResolvedSource:
        """Extract stream URL querying ONE client at a time and pinning the fastest working client."""
        try:
            validate_video_id(video_id)
        except ValueError as exc:
            raise StreamResolutionError(str(exc)) from exc
        quiet_logger = _QuietYtdlLogger()

        def _build_opts(fmt: str, client_name: str) -> dict[str, Any]:
            opts: dict[str, Any] = {
                "format": fmt,
                "quiet": True,
                "noplaylist": True,
                "no_warnings": True,
                "skip_download": True,
                "logger": quiet_logger,
                "extractor_args": {"youtube": {"player_client": [client_name]}},
                "js_runtimes": {"node": {}},
            }
            if self.cookies_file and Path(self.cookies_file).is_file():
                opts["cookiefile"] = str(self.cookies_file)
            if self.cookies_from_browser:
                cfb = self.cookies_from_browser
                opts["cookiesfrombrowser"] = (cfb,) if isinstance(cfb, str) else tuple(cfb)
            return opts

        url = f"https://www.youtube.com/watch?v={video_id}"

        def _extract(opts: dict[str, Any]) -> ResolvedSource:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise StreamResolutionError(f"No stream information found for {video_id}")
                stream_url = info.get("url")
                selected_fmt: dict[str, Any] | None = None
                if not stream_url and "formats" in info and info["formats"]:
                    selected_fmt = info["formats"][-1]
                    stream_url = selected_fmt.get("url")
                if not stream_url:
                    raise StreamResolutionError(f"No playable audio URL extracted for {video_id}")

                raw_headers: dict[str, Any] = {}
                if isinstance(info.get("http_headers"), dict):
                    raw_headers.update(info["http_headers"])
                if selected_fmt and isinstance(selected_fmt.get("http_headers"), dict):
                    for k, v in selected_fmt["http_headers"].items():
                        raw_headers.setdefault(k, v)
                if not raw_headers and isinstance(getattr(ydl, "params", None), dict):
                    params_headers = ydl.params.get("http_headers")
                    if isinstance(params_headers, dict):
                        for k, v in params_headers.items():
                            raw_headers.setdefault(k, v)

                user_agent = raw_headers.get("User-Agent") or raw_headers.get("user-agent")
                ua_str = str(user_agent) if isinstance(user_agent, str) else None
                final_headers = {str(k): str(v) for k, v in raw_headers.items()}
                with self._meta_lock:
                    if ua_str:
                        if len(self._user_agents) >= 40 and video_id not in self._user_agents:
                            self._user_agents.pop(next(iter(self._user_agents)), None)
                        self._user_agents[video_id] = ua_str
                    if len(self._http_headers) >= 40 and video_id not in self._http_headers:
                        self._http_headers.pop(next(iter(self._http_headers)), None)
                    self._http_headers[video_id] = final_headers

                resolved = ResolvedSource(
                    str(stream_url),
                    user_agent=ua_str,
                    http_headers=final_headers,
                )
                del info
                return resolved

        # Put last known working client first for 1-shot fast resolution
        with self._meta_lock:
            working_client = self._working_client
        ordered_clients = [working_client] + [
            c for c in CLIENT_FALLBACK_ORDER if c != working_client
        ]

        attempts: list[tuple[str, str]] = [
            (self.format_selector, ordered_clients[0]),
            ("bestaudio*/best", ordered_clients[0]),
        ]
        for fallback_c in ordered_clients[1:]:
            attempts.append(("bestaudio*/best", fallback_c))

        last_err: Exception | None = None
        try:
            for idx, (fmt, client_name) in enumerate(attempts):
                try:
                    res = _extract(_build_opts(fmt, client_name))
                    with self._meta_lock:
                        self._working_client = client_name
                    return res
                except (YtDlpDownloadError, Exception) as e:  # noqa: BLE001
                    last_err = e
                    msg = str(e).lower()
                    retryable = (
                        "requested format is not available" in msg
                        or "not a bot" in msg
                        or "sign in" in msg
                        or "403" in msg
                    )
                    if not retryable or idx == len(attempts) - 1:
                        break
        finally:
            with suppress(Exception):
                from metrolite.audio.player import trim_memory

                trim_memory()

        if isinstance(last_err, StreamResolutionError):
            raise last_err
        raise StreamResolutionError(f"yt-dlp extraction failed for {video_id}: {last_err}") from last_err

    def _put_cache(self, video_id: str, stream_url: ResolvedSource, expires_at: float) -> None:
        now = time.monotonic()
        # Purge expired cache items to prevent memory bloat
        expired = [k for k, (_, exp) in self._cache.items() if now >= exp]
        for k in expired:
            self.invalidate(k)
        if len(self._cache) >= 40 and video_id not in self._cache:
            evict_id = next(iter(self._cache))
            self.invalidate(evict_id)
        self._cache[video_id] = (stream_url, expires_at)

    async def _resolve_uncached(self, video_id: str) -> ResolvedSource:
        stream_url = await asyncio.to_thread(self._extract_url_sync, video_id)
        self._put_cache(video_id, stream_url, time.monotonic() + self.ttl_s)
        return stream_url

    async def resolve(self, video_id: str) -> ResolvedSource:
        """Resolve video_id with cache + cancellation-safe in-flight deduplication."""
        local_path = await self._check_local_async(video_id)
        if local_path:
            return local_path

        now = time.monotonic()
        if video_id in self._cache:
            cached_url, expires_at = self._cache[video_id]
            if now < expires_at:
                return cached_url

        task = self._inflight.get(video_id)
        if task is None or task.done():
            task = asyncio.create_task(self._resolve_uncached(video_id))
            self._inflight[video_id] = task

            def _cleanup(t: asyncio.Task[ResolvedSource], vid: str = video_id) -> None:
                if self._inflight.get(vid) is t:
                    self._inflight.pop(vid, None)
                if not t.cancelled():
                    # Mark exception retrieved to avoid "Task exception was never retrieved"
                    t.exception()

            task.add_done_callback(_cleanup)

        return await asyncio.shield(task)

    def resolve_sync(self, video_id: str) -> ResolvedSource:
        local_path = self._check_local_sync(video_id)
        if local_path:
            return local_path

        now = time.monotonic()
        if video_id in self._cache:
            cached_url, expires_at = self._cache[video_id]
            if now < expires_at:
                return cached_url

        stream_url = self._extract_url_sync(video_id)
        self._put_cache(video_id, stream_url, now + self.ttl_s)
        return stream_url

    def _get_prefetch_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        sem = self._prefetch_sem_map.get(loop)
        if sem is None:
            sem = asyncio.Semaphore(2)
            self._prefetch_sem_map[loop] = sem
        return sem

    async def prefetch(self, video_ids: list[str] | tuple[str, ...]) -> int:
        """Silently warm stream URLs in background (deduplicated, Semaphore(2) bounded)."""
        unique_ids: list[str] = []
        seen: set[str] = set()
        for vid in video_ids:
            if not isinstance(vid, str) or not vid or vid in seen:
                continue
            try:
                valid = validate_video_id(vid)
            except ValueError:
                continue
            seen.add(valid)
            if self.is_cached(valid):
                continue
            task = self._inflight.get(valid)
            if task is not None and not task.done():
                continue
            unique_ids.append(valid)

        if not unique_ids:
            return 0

        sem = self._get_prefetch_semaphore()
        warmed = 0

        async def _warm_one(vid: str) -> None:
            nonlocal warmed
            async with sem:
                with suppress(Exception):
                    await self.resolve(vid)
                    warmed += 1

        await asyncio.gather(*(_warm_one(vid) for vid in unique_ids), return_exceptions=True)
        return warmed
