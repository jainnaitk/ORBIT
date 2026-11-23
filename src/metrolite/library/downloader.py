"""Background audio downloader for MetroLite with concurrency, tagging, and DB updates."""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import re
import shutil
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yt_dlp
from pydantic import BaseModel, Field
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from metrolite.config import get_config
from metrolite.store.db import get_connection, get_default_db_path, init_db
from metrolite.ytm.client import Track

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TRUSTED_THUMBNAIL_DOMAINS = (
    "googleusercontent.com",
    "ggpht.com",
    "ytimg.com",
    "youtube.com",
)
_MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024  # 5 MB hard cap
CLIENT_FALLBACK_ORDER = ["android", "mweb", "web_embedded", "ios", "tv_embedded"]
_AUDIO_EXTENSIONS = {".opus", ".m4a", ".mp4", ".ogg", ".webm", ".flac", ".mp3", ".wav", ".aac"}


class _DownloadCancelledError(Exception):
    """Internal signal raised inside _download_worker when a download is cancelled."""


def _is_trusted_thumbnail_url(url: str) -> bool:
    """Allow only HTTPS URLs pointing to trusted YouTube/Google image hosts."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == dom or host.endswith(f".{dom}") for dom in _TRUSTED_THUMBNAIL_DOMAINS)


def validate_video_id(video_id: str) -> str:
    """Ensure video_id is a safe identifier without path traversal or glob metacharacters."""
    if not isinstance(video_id, str) or not _VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError(f"Invalid video_id: {video_id!r}")
    return video_id


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

    # Fetch thumbnail bytes safely (trusted HTTPS domains only, no cross-host redirects, 5 MB cap)
    cover_bytes: bytes | None = None
    cover_mime = "image/jpeg"
    if embed_thumbnail and thumbnail_url and _is_trusted_thumbnail_url(thumbnail_url):
        try:
            with (
                httpx.Client(timeout=10.0, follow_redirects=False) as client,
                client.stream("GET", thumbnail_url) as resp,
            ):
                    if resp.status_code == 200:
                        content_len = resp.headers.get("content-length")
                        if content_len and content_len.isdigit() and int(content_len) > _MAX_THUMBNAIL_BYTES:
                            logger.warning(
                                "Skipping oversized thumbnail (%s bytes) for %s",
                                content_len,
                                file_path,
                            )
                        else:
                            chunks: list[bytes] = []
                            total = 0
                            oversized = False
                            for chunk in resp.iter_bytes(chunk_size=16384):
                                total += len(chunk)
                                if total > _MAX_THUMBNAIL_BYTES:
                                    oversized = True
                                    break
                                chunks.append(chunk)
                            if not oversized and chunks:
                                cover_bytes = b"".join(chunks)
                                ctype = resp.headers.get("content-type", "").lower()
                                if "png" in ctype or thumbnail_url.lower().endswith(".png"):
                                    cover_mime = "image/png"
                                else:
                                    cover_mime = "image/jpeg"
        except (httpx.HTTPError, OSError) as e:
            logger.debug("Failed to fetch thumbnail for embedding from %s: %s", thumbnail_url, e)
    elif embed_thumbnail and thumbnail_url:
        logger.warning("Blocked untrusted thumbnail URL during metadata embed: %s", thumbnail_url)

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
    cookies_file: Path | str | None = None,
    cookies_from_browser: str | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Synchronous worker that downloads audio with yt-dlp and embeds metadata."""
    validate_video_id(video_id)
    if cancel_event is not None and cancel_event.is_set():
        raise _DownloadCancelledError(f"Download cancelled for {video_id}")
    downloads_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(downloads_dir / f"{video_id}.%(ext)s")

    def ydl_hook(d: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _DownloadCancelledError(f"Download cancelled for {video_id}")
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

    has_ffmpeg = bool(shutil.which("ffmpeg"))

    def _build_opts(fmt: str, client_name: str) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "format": fmt,
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "progress_hooks": [ydl_hook],
            "extractor_args": {"youtube": {"player_client": [client_name]}},
            "js_runtimes": {"node": {}},
        }
        if cookies_file and Path(cookies_file).is_file():
            opts["cookiefile"] = str(cookies_file)
        if cookies_from_browser:
            opts["cookiesfrombrowser"] = (
                (cookies_from_browser,)
                if isinstance(cookies_from_browser, str)
                else tuple(cookies_from_browser)
            )
        if has_ffmpeg:
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": preferred_ext,
                    "preferredquality": "192",
                }
            ]
        return opts

    url = f"https://www.youtube.com/watch?v={video_id}"
    attempts: list[tuple[str, str]] = [
        (format_selector, CLIENT_FALLBACK_ORDER[0]),
        ("bestaudio*/best", CLIENT_FALLBACK_ORDER[0]),
    ]
    for fallback_client in CLIENT_FALLBACK_ORDER[1:]:
        attempts.append(("bestaudio*/best", fallback_client))

    info: Any = None
    last_err: Exception | None = None
    for idx, (fmt, client_name) in enumerate(attempts):
        if cancel_event is not None and cancel_event.is_set():
            raise _DownloadCancelledError(f"Download cancelled for {video_id}")
        opts = _build_opts(fmt, client_name)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            last_err = None
            break
        except _DownloadCancelledError:
            raise
        except (YtDlpDownloadError, DownloadError, Exception) as e:  # noqa: BLE001
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
            logger.warning(
                "Download attempt %d (%s/%s) failed for %s, trying fallback",
                idx + 1,
                client_name,
                fmt,
                video_id,
            )

    if last_err is not None:
        raise DownloadError(f"yt-dlp download failed for {video_id}: {last_err}") from last_err

    if cancel_event is not None and cancel_event.is_set():
        raise _DownloadCancelledError(f"Download cancelled for {video_id}")

    if info is None:
        raise DownloadError(f"yt-dlp returned no info for {video_id}")

    # Determine downloaded file location:
    # If ffmpeg is available, extract to preferred_ext (e.g. .opus)
    # If ffmpeg is not on PATH, save cleanly with actual container extension (e.g. .m4a or .mp4)
    target_file: Path | None = None
    preferred_target = downloads_dir / f"{video_id}.{preferred_ext}"
    if has_ffmpeg and preferred_target.is_file():
        target_file = preferred_target

    if target_file is None and isinstance(info, dict):
        req_dls = info.get("requested_downloads")
        if req_dls and isinstance(req_dls, list):
            for rd in reversed(req_dls):
                if isinstance(rd, dict):
                    fp = rd.get("filepath") or rd.get("_filename")
                    if fp and Path(fp).is_file():
                        target_file = Path(fp)
                        break

        if target_file is None:
            fn = info.get("_filename")
            if fn and Path(fn).is_file():
                target_file = Path(fn)

        if target_file is None and info.get("ext"):
            ext_str = str(info["ext"]).lstrip(".")
            candidate = downloads_dir / f"{video_id}.{ext_str}"
            if candidate.is_file():
                target_file = candidate

    if target_file is None:
        candidates = [
            f
            for f in downloads_dir.glob(f"{video_id}.*")
            if f.is_file()
            and not f.name.endswith((".part", ".ytdl", ".temp", ".lrc", ".webp", ".jpg", ".jpeg", ".png", ".json", ".aria2"))
            and f.suffix.lower() in _AUDIO_EXTENSIONS
        ]
        if not candidates:
            candidates = [
                f
                for f in downloads_dir.glob(f"{video_id}.*")
                if f.is_file()
                and not f.name.endswith((".part", ".ytdl", ".temp", ".lrc", ".webp", ".jpg", ".jpeg", ".png", ".json", ".aria2"))
            ]
        if candidates:
            if has_ffmpeg:
                candidates.sort(
                    key=lambda p: (p.suffix.lower() == f".{preferred_ext}", p.stat().st_mtime)
                )
            else:
                candidates.sort(key=lambda p: p.stat().st_mtime)
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

    if cancel_event is not None and cancel_event.is_set():
        raise _DownloadCancelledError(f"Download cancelled for {video_id}")

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
        cookies_file: Path | str | None = None,
        cookies_from_browser: str | None = None,
        format_selector: str | None = None,
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
        self.format_selector = (
            format_selector
            or cfg.audio.format_selector
            or "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best"
        )
        self.cookies_file = (
            Path(cookies_file)
            if cookies_file is not None
            else getattr(cfg.downloads, "cookies_file", None) or cfg.app.cookies_file
        )
        if self.cookies_file is None:
            auto_cookies = cfg.app.data_dir / "cookies.txt"
            if auto_cookies.is_file():
                self.cookies_file = auto_cookies
        self.cookies_from_browser = (
            cookies_from_browser
            if cookies_from_browser is not None
            else getattr(cfg.downloads, "cookies_from_browser", None)
            or getattr(cfg.app, "cookies_from_browser", None)
        )

        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._active_tasks: dict[str, asyncio.Task[str]] = {}
        self._queued_tasks: set[asyncio.Task[str]] = set()
        self._cancel_events: dict[str, threading.Event] = {}
        self._worker_done_events: dict[str, threading.Event] = {}
        self._statuses: dict[str, DownloadProgress] = {}
        self._listeners: list[Callable[..., Any]] = []

    def queue_download(
        self,
        video_id: str,
        track_meta: Track | dict[str, Any] | None = None,
    ) -> asyncio.Task[str]:
        """Schedule a tracked background download whose errors are safely consumed."""
        validate_video_id(video_id)
        existing = self._active_tasks.get(video_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(self._download_track_inner(video_id, track_meta))
        self._active_tasks[video_id] = task
        self._queued_tasks.add(task)

        def _on_done(t: asyncio.Task[str]) -> None:
            self._queued_tasks.discard(t)
            if self._active_tasks.get(video_id) is t:
                self._active_tasks.pop(video_id, None)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    logger.debug("Queued download task for %s finished with error: %s", video_id, exc)

        task.add_done_callback(_on_done)
        return task

    def stop(self) -> None:
        """Cancel all in-flight and queued download tasks cleanly."""
        for ev in list(self._cancel_events.values()):
            ev.set()
        for task in list(self._active_tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._queued_tasks):
            if not task.done():
                task.cancel()
        self._active_tasks.clear()
        self._queued_tasks.clear()

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
        validate_video_id(video_id)
        # Register synchronously before the first await to eliminate duplicate download races
        existing = self._active_tasks.get(video_id)
        if existing is not None and not existing.done():
            return await existing

        task = asyncio.create_task(self._download_track_inner(video_id, track_meta))
        self._active_tasks[video_id] = task
        try:
            return await task
        finally:
            if self._active_tasks.get(video_id) is task:
                self._active_tasks.pop(video_id, None)

    async def _download_track_inner(
        self,
        video_id: str,
        track_meta: Track | dict[str, Any] | None = None,
    ) -> str:
        """Check local cache then execute download under semaphore."""
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

        return await self._process_download(video_id, track_meta)

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

        cancel_event = threading.Event()
        self._cancel_events[video_id] = cancel_event
        self._emit_progress(video_id, 0.0, "queued")

        loop = asyncio.get_running_loop()

        def progress_bridge(pct: float, st: str) -> None:
            if not cancel_event.is_set():
                loop.call_soon_threadsafe(self._emit_progress, video_id, pct, st, None, None)

        worker_done: threading.Event | None = None
        try:
            async with self._semaphore:
                if cancel_event.is_set():
                    raise asyncio.CancelledError()
                self._emit_progress(video_id, 0.0, "downloading")
                worker_done = threading.Event()
                self._worker_done_events[video_id] = worker_done

                def _run_worker() -> tuple[Path, dict[str, Any]]:
                    try:
                        return _download_worker(
                            video_id=video_id,
                            downloads_dir=self.downloads_dir,
                            preferred_ext=self.preferred_ext,
                            format_selector=self.format_selector,
                            embed_metadata=self.embed_metadata,
                            embed_thumbnail=self.embed_thumbnail,
                            meta=meta_dict,
                            progress_cb=progress_bridge,
                            cookies_file=self.cookies_file,
                            cookies_from_browser=self.cookies_from_browser,
                            cancel_event=cancel_event,
                        )
                    finally:
                        worker_done.set()

                try:
                    target_file, extracted_meta = await asyncio.to_thread(_run_worker)

                    if cancel_event.is_set():
                        raise asyncio.CancelledError()

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

                except (_DownloadCancelledError, asyncio.CancelledError) as exc:
                    logger.info("Download cancelled for track %s", video_id)
                    raise asyncio.CancelledError() from exc
                except Exception as e:
                    err_msg = str(e)
                    self._emit_progress(video_id, 0.0, "error", error=err_msg)
                    logger.error("Download failed for %s: %s", video_id, e)
                    raise DownloadError(err_msg) from e
        finally:
            if self._cancel_events.get(video_id) is cancel_event:
                self._cancel_events.pop(video_id, None)
            if worker_done is not None and self._worker_done_events.get(video_id) is worker_done:
                self._worker_done_events.pop(video_id, None)

    async def delete_download(self, video_id: str) -> bool:
        """Delete local audio file and clear download fields in SQLite."""
        validate_video_id(video_id)
        # Signal cancellation to any in-flight worker thread and await clean shutdown
        cancel_ev = self._cancel_events.get(video_id)
        if cancel_ev is not None:
            cancel_ev.set()
        worker_done = self._worker_done_events.get(video_id)

        active_task = self._active_tasks.get(video_id)
        if active_task is not None and not active_task.done():
            active_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await active_task

        if worker_done is not None and not worker_done.is_set():
            await asyncio.to_thread(worker_done.wait, 5.0)

        async def _try_unlink(path: Path) -> bool:
            for attempt in range(4):
                try:
                    path.unlink()
                    return True
                except FileNotFoundError:
                    return False
                except PermissionError:
                    if attempt < 3:
                        await asyncio.sleep(0.05)
                    else:
                        logger.warning("File locked and could not be deleted: %s", path)
                        return False
                except OSError as e:
                    logger.warning("Failed to delete local file %s: %s", path, e)
                    return False
            return False

        file_deleted = False
        file_still_exists = False
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT local_path FROM tracks WHERE video_id = ?;", (video_id,)
            )
            row = await cursor.fetchone()
            local_path: Path | None = Path(row["local_path"]) if (row and row["local_path"]) else None
            if local_path is not None and local_path.is_file():
                if await _try_unlink(local_path):
                    file_deleted = True
                if local_path.is_file():
                    file_still_exists = True

            # Check downloads_dir for any residual files
            for residual in self.downloads_dir.glob(f"{video_id}.*"):
                if not residual.is_file():
                    continue
                if await _try_unlink(residual):
                    file_deleted = True
                if residual.is_file() and (
                    (local_path is not None and residual.resolve() == local_path.resolve())
                    or residual.suffix.lower() in _AUDIO_EXTENSIONS
                ):
                    file_still_exists = True

            if file_still_exists:
                logger.warning(
                    "Keeping SQLite download record for %s because file still exists on disk",
                    video_id,
                )
                return False

            # Update SQLite tracks record only once file is removed from disk
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
