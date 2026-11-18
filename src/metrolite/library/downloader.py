"""Background audio downloader for MetroLite with concurrency, tagging, and DB updates."""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yt_dlp
from pydantic import BaseModel, Field

from metrolite.config import get_config
from metrolite.store.db import get_connection, get_default_db_path, init_db
from metrolite.ytm.client import Track

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


class DownloadError(Exception):
    """Exception raised when a track download fails."""


class DownloadProgress(BaseModel):
    """Progress event for an audio download task."""

    video_id: str
    status: str = Field(
        default="queued",
        description="Current status: queued | downloading | tagging | completed | error | deleted",
    )
    percent: float = Field(default=0.0, description="Download progress percent (0.0 to 100.0)")
    error: str | None = Field(default=None, description="Error message if status is error")
    local_path: str | None = Field(default=None, description="Local file path when completed")


def _embed_metadata(
    file_path: Path,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    thumbnail_url: str | None = None,
    embed_thumbnail: bool = True,
) -> None:
    """Embed title, artist, album, and thumbnail cover into audio file using mutagen."""
    if not file_path.is_file():
        return

    ext = file_path.suffix.lower()

    # Fetch thumbnail bytes if requested
    cover_bytes: bytes | None = None
    cover_mime = "image/jpeg"
    if embed_thumbnail and thumbnail_url:
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(thumbnail_url)
                if resp.status_code == 200 and resp.content:
                    cover_bytes = resp.content
                    ctype = resp.headers.get("content-type", "").lower()
                    if "png" in ctype or thumbnail_url.lower().endswith(".png"):
                        cover_mime = "image/png"
                    else:
                        cover_mime = "image/jpeg"
        except (httpx.HTTPError, OSError) as e:
            logger.debug("Failed to fetch thumbnail for embedding from %s: %s", thumbnail_url, e)

    try:
        from mutagen import MutagenError

        if ext in (".opus", ".ogg"):
            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus

            audio = OggOpus(file_path)
            if title:
                audio["title"] = [title]
            if artist:
                audio["artist"] = [artist]
            if album:
                audio["album"] = [album]
            if cover_bytes:
                pic = Picture()
                pic.data = cover_bytes
                pic.type = 3  # front cover
                pic.mime = cover_mime
                encoded = base64.b64encode(pic.write()).decode("ascii")
                audio["metadata_block_picture"] = [encoded]
            audio.save()
            logger.debug("Tagged OggOpus file %s", file_path)

        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(file_path)
            if title:
                audio["\xa9nam"] = [title]
            if artist:
                audio["\xa9ART"] = [artist]
            if album:
                audio["\xa9alb"] = [album]
            if cover_bytes:
                fmt = MP4Cover.FORMAT_PNG if cover_mime == "image/png" else MP4Cover.FORMAT_JPEG
                audio["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
            audio.save()
            logger.debug("Tagged MP4/M4A file %s", file_path)

    except MutagenError as e:
        logger.warning("Mutagen error tagging %s: %s", file_path, e)
    except (OSError, ValueError, TypeError) as e:
        logger.warning("Unexpected error tagging %s: %s", file_path, e)


def _download_worker(
    video_id: str,
    downloads_dir: Path,
    preferred_ext: str,
    format_selector: str,
    embed_metadata: bool,
    embed_thumbnail: bool,
    meta: dict[str, Any],
    progress_cb: Callable[[float, str], None],
) -> tuple[Path, dict[str, Any]]:
    """Synchronous worker that downloads audio with yt-dlp and embeds metadata."""
    downloads_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(downloads_dir / f"{video_id}.%(ext)s")

    def ydl_hook(d: dict[str, Any]) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                pct = (downloaded / total) * 90.0
                progress_cb(min(90.0, pct), "downloading")
            else:
                progress_cb(45.0, "downloading")
        elif status == "finished":
            progress_cb(90.0, "tagging")

    ydl_opts: dict[str, Any] = {
        "format": format_selector,
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "progress_hooks": [ydl_hook],
    }

    if shutil.which("ffmpeg"):
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": preferred_ext,
                "preferredquality": "192",
            }
        ]

    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise DownloadError(f"yt-dlp returned no info for {video_id}")

    # Determine downloaded file location
    preferred_target = downloads_dir / f"{video_id}.{preferred_ext}"
    target_file: Path | None = None
    if preferred_target.is_file():
        target_file = preferred_target
    else:
        # Search for any non-temporary file matching video_id.*
        candidates = [
            f
            for f in downloads_dir.glob(f"{video_id}.*")
            if f.is_file() and not f.name.endswith((".part", ".ytdl", ".temp"))
        ]
        if candidates:
            # Sort by newest or matching preferred
            candidates.sort(
                key=lambda p: (p.suffix.lower() == f".{preferred_ext}", p.stat().st_mtime)
            )
            target_file = candidates[-1]

    if target_file is None or not target_file.is_file():
        raise DownloadError(f"Downloaded audio file for {video_id} not found in {downloads_dir}")

    # Extract metadata from yt-dlp info if not provided
    title = meta.get("title")
    if not title or title == "Unknown Title":
        title = info.get("title") or info.get("track") or meta.get("title") or "Unknown Title"

    artist = meta.get("artist")
    if not artist or artist == "Unknown Artist":
        artist = (
            info.get("artist") or info.get("uploader") or meta.get("artist") or "Unknown Artist"
        )

    album = meta.get("album") or info.get("album")
    thumbnail_url = meta.get("thumbnail_url") or info.get("thumbnail")
    duration_s = int(meta.get("duration_s") or info.get("duration") or 0)

    extracted_meta = {
        "video_id": video_id,
        "title": title,
        "artist": artist,
        "album": album,
        "duration_s": duration_s,
        "thumbnail_url": thumbnail_url,
    }

    if embed_metadata:
        progress_cb(95.0, "tagging")
        _embed_metadata(
            file_path=target_file,
            title=title,
            artist=artist,
            album=album,
            thumbnail_url=thumbnail_url,
            embed_thumbnail=embed_thumbnail,
        )

    return target_file, extracted_meta


class DownloadManager:
    """Manages asynchronous background audio downloads with concurrency limits and SQLite sync."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        downloads_dir: Path | str | None = None,
        max_concurrent: int | None = None,
    ) -> None:
        cfg = get_config()
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self.downloads_dir = (
            Path(downloads_dir) if downloads_dir is not None else cfg.app.downloads_dir
        )
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        self.max_concurrent = (
            max_concurrent if max_concurrent is not None else cfg.downloads.max_concurrent
        )
        self.preferred_ext = cfg.downloads.preferred_ext
        self.embed_metadata = cfg.downloads.embed_metadata
        self.embed_thumbnail = cfg.downloads.embed_thumbnail
        self.format_selector = cfg.audio.format_selector

        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._active_tasks: dict[str, asyncio.Task[str]] = {}
        self._statuses: dict[str, DownloadProgress] = {}
        self._listeners: list[Callable[..., Any]] = []

    async def ensure_db(self) -> None:
        """Ensure database tables exist."""
        await init_db(self.db_path)

    def add_progress_listener(self, listener: Callable[..., Any]) -> None:
        """Register a progress listener callback."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_progress_listener(self, listener: Callable[..., Any]) -> None:
        """Unregister a progress listener callback."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit_progress(
        self,
        video_id: str,
        percent: float,
        status: str,
        error: str | None = None,
        local_path: str | None = None,
    ) -> None:
        """Emit progress event to all registered listeners."""
        progress = DownloadProgress(
            video_id=video_id,
            percent=round(percent, 1),
            status=status,
            error=error,
            local_path=local_path,
        )
        self._statuses[video_id] = progress

        for listener in list(self._listeners):
            try:
                sig = inspect.signature(listener)
                param_count = len(sig.parameters)
                if param_count == 1:
                    listener(progress)
                elif param_count >= 3:
                    listener(video_id, progress.percent, status)
                else:
                    listener(progress)
            except Exception as e:  # noqa: BLE001
                logger.warning("Error in download progress listener: %s", e)

    def get_status(self, video_id: str) -> DownloadProgress | None:
        """Get the current download status of a track."""
        return self._statuses.get(video_id)

    async def is_downloaded(self, video_id: str) -> bool:
        """Check if a track is downloaded and exists locally on disk."""
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT local_path FROM tracks WHERE video_id = ?;", (video_id,)
            )
            row = await cursor.fetchone()
            if row and row["local_path"]:
                return Path(row["local_path"]).is_file()
        return False

    async def download_track(
        self,
        video_id: str,
        track_meta: Track | dict[str, Any] | None = None,
    ) -> str:
        """Download a track in the background and return its resolved local path."""
        # 1. Check if already downloaded
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT local_path, file_size_bytes FROM tracks WHERE video_id = ?;",
                (video_id,),
            )
            row = await cursor.fetchone()
            if row and row["local_path"]:
                local_path = Path(row["local_path"])
                if local_path.is_file():
                    self._emit_progress(
                        video_id, 100.0, "completed", local_path=str(local_path.resolve())
                    )
                    return str(local_path.resolve())

        # 2. Check if a download task is already in-flight
        if video_id in self._active_tasks and not self._active_tasks[video_id].done():
            return await self._active_tasks[video_id]

        # 3. Create and track download task
        task = asyncio.create_task(self._process_download(video_id, track_meta))
        self._active_tasks[video_id] = task
        try:
            return await task
        finally:
            self._active_tasks.pop(video_id, None)

    async def _process_download(
        self,
        video_id: str,
        track_meta: Track | dict[str, Any] | None = None,
    ) -> str:
        """Execute queued download governed by concurrency semaphore."""
        meta_dict: dict[str, Any] = {}
        if isinstance(track_meta, Track):
            meta_dict = track_meta.model_dump()
        elif isinstance(track_meta, dict):
            meta_dict = dict(track_meta)

        self._emit_progress(video_id, 0.0, "queued")

        loop = asyncio.get_running_loop()

        def progress_bridge(pct: float, st: str) -> None:
            loop.call_soon_threadsafe(self._emit_progress, video_id, pct, st, None, None)

        async with self._semaphore:
            self._emit_progress(video_id, 0.0, "downloading")
            try:
                target_file, extracted_meta = await asyncio.to_thread(
                    _download_worker,
                    video_id=video_id,
                    downloads_dir=self.downloads_dir,
                    preferred_ext=self.preferred_ext,
                    format_selector=self.format_selector,
                    embed_metadata=self.embed_metadata,
                    embed_thumbnail=self.embed_thumbnail,
                    meta=meta_dict,
                    progress_cb=progress_bridge,
                )

                file_size = target_file.stat().st_size
                now_iso = _utc_now_iso()
                abs_path = str(target_file.resolve())

                # Update SQLite record
                async with get_connection(self.db_path) as conn:
                    await conn.execute(
                        """
                        INSERT INTO tracks (
                            video_id, title, artist, album, duration_s,
                            thumbnail_url, local_path, file_size_bytes, downloaded_at
                        )
                        VALUES (:video_id, :title, :artist, :album, :duration_s,
                                :thumbnail_url, :local_path, :file_size_bytes, :downloaded_at)
                        ON CONFLICT(video_id) DO UPDATE SET
                            title = CASE
                                WHEN excluded.title != 'Unknown Title' THEN excluded.title
                                ELSE tracks.title
                            END,
                            artist = CASE
                                WHEN excluded.artist != 'Unknown Artist' THEN excluded.artist
                                ELSE tracks.artist
                            END,
                            album = COALESCE(excluded.album, tracks.album),
                            duration_s = CASE
                                WHEN excluded.duration_s > 0 THEN excluded.duration_s
                                ELSE tracks.duration_s
                            END,
                            thumbnail_url = COALESCE(excluded.thumbnail_url, tracks.thumbnail_url),
                            local_path = excluded.local_path,
                            file_size_bytes = excluded.file_size_bytes,
                            downloaded_at = excluded.downloaded_at;
                        """,
                        {
                            "video_id": video_id,
                            "title": extracted_meta.get("title") or "Unknown Title",
                            "artist": extracted_meta.get("artist") or "Unknown Artist",
                            "album": extracted_meta.get("album"),
                            "duration_s": extracted_meta.get("duration_s") or 0,
                            "thumbnail_url": extracted_meta.get("thumbnail_url"),
                            "local_path": abs_path,
                            "file_size_bytes": file_size,
                            "downloaded_at": now_iso,
                        },
                    )
                    await conn.commit()

                self._emit_progress(video_id, 100.0, "completed", local_path=abs_path)
                logger.info("Successfully downloaded track %s (%d bytes)", video_id, file_size)
                return abs_path

            except Exception as e:
                err_msg = str(e)
                self._emit_progress(video_id, 0.0, "error", error=err_msg)
                logger.error("Download failed for %s: %s", video_id, e)
                raise DownloadError(err_msg) from e

    async def delete_download(self, video_id: str) -> bool:
        """Delete local audio file and clear download fields in SQLite."""
        # Cancel active task if running
        if video_id in self._active_tasks:
            task = self._active_tasks[video_id]
            if not task.done():
                task.cancel()

        file_deleted = False
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT local_path FROM tracks WHERE video_id = ?;", (video_id,)
            )
            row = await cursor.fetchone()
            if row and row["local_path"]:
                local_path = Path(row["local_path"])
                if local_path.is_file():
                    try:
                        local_path.unlink()
                        file_deleted = True
                    except OSError as e:
                        logger.warning("Failed to delete local file %s: %s", local_path, e)

            # Check downloads_dir for any residual files
            for residual in self.downloads_dir.glob(f"{video_id}.*"):
                try:
                    residual.unlink(missing_ok=True)
                    file_deleted = True
                except OSError:
                    pass

            # Update SQLite tracks record
            upd_cursor = await conn.execute(
                """
                UPDATE tracks
                SET local_path = NULL,
                    file_size_bytes = 0,
                    downloaded_at = NULL
                WHERE video_id = ?;
                """,
                (video_id,),
            )
            await conn.commit()
            db_updated = upd_cursor.rowcount > 0

        self._emit_progress(video_id, 0.0, "deleted")
        logger.info("Deleted download for track %s", video_id)
        return file_deleted or db_updated

    async def list_downloads(self) -> list[dict[str, Any]]:
        """List all downloaded tracks whose files actually exist on disk."""
        query = """
            SELECT video_id, title, artist, album, duration_s, thumbnail_url,
                   is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                   play_count, last_played_at
            FROM tracks
            WHERE local_path IS NOT NULL AND local_path != ''
            ORDER BY downloaded_at DESC;
        """
        verified_tracks: list[dict[str, Any]] = []
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query)
            rows = await cursor.fetchall()
            stale_ids: list[str] = []

            for row in rows:
                item = dict(row)
                p = Path(item["local_path"]) if item.get("local_path") else None
                if p and p.is_file():
                    verified_tracks.append(item)
                else:
                    stale_ids.append(item["video_id"])

            # Clean up stale records where the file was deleted outside the app
            if stale_ids:
                for vid in stale_ids:
                    await conn.execute(
                        """
                        UPDATE tracks
                        SET local_path = NULL, file_size_bytes = 0, downloaded_at = NULL
                        WHERE video_id = ?;
                        """,
                        (vid,),
                    )
                await conn.commit()

        return verified_tracks
