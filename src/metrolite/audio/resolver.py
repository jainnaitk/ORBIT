"""Audio stream resolver for MetroLite."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import yt_dlp

from metrolite.config import get_config
from metrolite.store.db import get_connection, get_default_db_path, get_sync_connection

logger = logging.getLogger(__name__)


class StreamResolutionError(Exception):
    """Exception raised when an audio stream cannot be resolved."""


class StreamResolver:
    """Resolves YouTube video IDs to playable audio sources (local file or stream URL)."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        format_selector: str | None = None,
        stream_url_ttl_s: int | None = None,
    ) -> None:
        cfg = get_config()
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self.format_selector = format_selector or cfg.audio.format_selector
        self.ttl_s = (
            stream_url_ttl_s if stream_url_ttl_s is not None else cfg.audio.stream_url_ttl_s
        )
        # In-memory stream cache: {video_id: (stream_url, expires_at_monotonic)}
        self._cache: dict[str, tuple[str, float]] = {}

    def is_cached(self, video_id: str) -> bool:
        """Return True if video_id has a valid non-expired cached stream URL."""
        if video_id in self._cache:
            _, expires_at = self._cache[video_id]
            return time.monotonic() < expires_at
        return False

    def clear_cache(self) -> None:
        """Clear all cached stream URLs."""
        self._cache.clear()

    def _check_local_sync(self, video_id: str) -> str | None:
        """Check if track has a local file on disk synchronously."""
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
                        logger.info("Resolved %s from local storage: %s", video_id, local_path)
                        return str(local_path.resolve())
        except (sqlite3.Error, OSError) as e:
            logger.warning("Error checking local database for %s: %s", video_id, e)
        return None

    async def _check_local_async(self, video_id: str) -> str | None:
        """Check if track has a local file on disk asynchronously."""
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
                        logger.info("Resolved %s from local storage: %s", video_id, local_path)
                        return str(local_path.resolve())
        except (sqlite3.Error, OSError) as e:
            logger.warning("Error checking local database for %s: %s", video_id, e)
        return None

    def _extract_url_sync(self, video_id: str) -> str:
        """Extract stream URL using yt-dlp synchronously."""
        ydl_opts: dict[str, Any] = {
            "format": self.format_selector,
            "quiet": True,
            "noplaylist": True,
            "no_warnings": True,
            "skip_download": True,
        }
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise StreamResolutionError(f"No stream information found for {video_id}")
                stream_url = info.get("url")
                if not stream_url and "formats" in info and info["formats"]:
                    stream_url = info["formats"][-1].get("url")
                if not stream_url:
                    raise StreamResolutionError(f"No playable audio URL extracted for {video_id}")
                return str(stream_url)
        except Exception as e:
            if isinstance(e, StreamResolutionError):
                raise
            raise StreamResolutionError(f"yt-dlp extraction failed for {video_id}: {e}") from e

    async def resolve(self, video_id: str) -> str:
        """Resolve video_id to playable audio source (local path or direct stream URL)."""
        # 1. Check local file storage (instant offline playback)
        local_path = await self._check_local_async(video_id)
        if local_path:
            return local_path

        # 2. Check in-memory stream cache
        now = time.monotonic()
        if video_id in self._cache:
            cached_url, expires_at = self._cache[video_id]
            if now < expires_at:
                logger.debug("Cache hit for stream URL %s", video_id)
                return cached_url

        # 3. Extract stream URL via yt-dlp in a background thread
        stream_url = await asyncio.to_thread(self._extract_url_sync, video_id)
        self._cache[video_id] = (stream_url, now + self.ttl_s)
        return stream_url

    def resolve_sync(self, video_id: str) -> str:
        """Resolve video_id synchronously."""
        local_path = self._check_local_sync(video_id)
        if local_path:
            return local_path

        now = time.monotonic()
        if video_id in self._cache:
            cached_url, expires_at = self._cache[video_id]
            if now < expires_at:
                return cached_url

        stream_url = self._extract_url_sync(video_id)
        self._cache[video_id] = (stream_url, now + self.ttl_s)
        return stream_url
