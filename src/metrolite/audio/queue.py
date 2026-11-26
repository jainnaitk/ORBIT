"""Queue manager and persistence for MetroLite."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import re
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Literal

from metrolite.config import get_config
from metrolite.store.db import (
    get_connection,
    get_default_db_path,
    get_sync_connection,
)
from metrolite.ytm.client import Track, YTMusicClient

logger = logging.getLogger(__name__)

RepeatMode = Literal["off", "all", "one"]
VALID_REPEAT_MODES: set[str] = {"off", "all", "one"}

_NOISE_KEYWORDS: tuple[str, ...] = (
    "afrobeat",
    "slowed",
    "reverb",
    "8d audio",
    "8d ",
    "ringtone",
    "whatsapp status",
    "status video",
    "mashup",
    "dj remix",
    "dj mix",
    "sped up",
    "nightcore",
    "bass boosted",
    "karaoke",
    "instrumental",
    "reaction",
    "podcast",
    "full album",
    "jukebox",
    "nonstop",
    "1 hour",
    "10 hours",
    "remix",
    "lofi flip",
    "lo-fi flip",
    "bootleg",
)

_PAREN_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT_RE = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\b.*$", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\u0900-\u097f]+")


def _canonical_title(title: str) -> str:
    """Normalize a song title to detect duplicate uploads, remixes, and lyric-video variants."""
    cleaned = _PAREN_RE.sub(" ", title.lower())
    cleaned = _FEAT_RE.sub(" ", cleaned)
    # Strip trailing dash/pipe descriptors like "- Official Audio" or "| Lyrical"
    cleaned = re.split(r"\s+[-|–—]\s+", cleaned, maxsplit=1)[0]
    cleaned = _NON_ALNUM_RE.sub(" ", cleaned).strip()
    return cleaned or title.lower().strip()


def _primary_artist(artist: str) -> str:
    """Extract the normalized lead artist name."""
    first = re.split(r"[,&/]|\b(?:feat\.?|ft\.?|x)\b", artist, maxsplit=1, flags=re.IGNORECASE)[0]
    return first.strip().lower()


def _has_unwanted_noise(title: str, seed_title_lc: str) -> bool:
    """Return True if candidate looks like an unwanted remix/spam upload unless seed shares that tag."""
    tlc = title.lower()
    for kw in _NOISE_KEYWORDS:
        if kw in tlc and kw not in seed_title_lc:
            return True
    return False


class QueueManager:
    """Manages the playback queue, shuffle, repeat modes, radio refill, and SQLite state."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        ytm_client: YTMusicClient | None = None,
        autoplay_radio: bool | None = None,
        auto_persist: bool = False,
        on_queue_changed: Callable[[], None] | None = None,
    ) -> None:
        cfg = get_config()
        self._db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self._ytm = ytm_client
        self.autoplay_radio = (
            autoplay_radio if autoplay_radio is not None else cfg.queue.autoplay_radio
        )
        self.auto_persist = auto_persist
        self._on_queue_changed = on_queue_changed

        self.items: list[Track] = []
        self._original_items: list[Track] = []
        self.current_index: int = 0
        self.shuffle: bool = False
        self.repeat_mode: RepeatMode = "off"
        self.consecutive_errors: int = 0
        self.max_consecutive_errors: int = 2
        self._persist_task: asyncio.Task[None] | None = None
        self._persist_dirty: bool = False
        self._generation: int = 0
        self._refill_seq: int = 0

    def record_error(self) -> int:
        """Record a playback or resolution error and increment consecutive error count."""
        self.consecutive_errors += 1
        return self.consecutive_errors

    def reset_errors(self) -> None:
        """Reset consecutive error count to 0."""
        self.consecutive_errors = 0

    def can_skip_on_error(self) -> bool:
        """Return True if consecutive errors are within the allowed limit (<= 2)."""
        return self.consecutive_errors <= self.max_consecutive_errors

    @property
    def current_track(self) -> Track | None:
        """Return currently playing track, or None if queue is empty."""
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index]
        return None

    @property
    def next_track_preview(self) -> Track | None:
        """Preview next track without advancing the queue index."""
        if not self.items:
            return None
        if self.repeat_mode == "one":
            return self.current_track
        if self.current_index + 1 < len(self.items):
            return self.items[self.current_index + 1]
        if self.repeat_mode == "all":
            return self.items[0]
        return None

    def _schedule_async_persist(self) -> None:
        """Coalesce and offload queue persistence off the asyncio event loop."""
        self._persist_dirty = True
        if self._persist_task is not None and not self._persist_task.done():
            return

        async def _flush() -> None:
            while self._persist_dirty:
                self._persist_dirty = False
                try:
                    await self.save_to_db()
                except (sqlite3.Error, OSError) as e:
                    logger.warning("Failed to auto-persist queue state: %s", e)
                except asyncio.CancelledError:
                    break

        with suppress(RuntimeError):
            self._persist_task = asyncio.get_running_loop().create_task(_flush())

    def _notify(self) -> None:
        """Trigger queue changed callback and auto-persistence."""
        self._generation += 1
        if self.auto_persist and self._db_path and self._db_path.is_file():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                try:
                    self.save_to_db_sync()
                except (sqlite3.Error, OSError) as e:
                    logger.warning("Failed to auto-persist queue state: %s", e)
            else:
                self._schedule_async_persist()
        if self._on_queue_changed:
            try:
                self._on_queue_changed()
            except Exception as e:  # noqa: BLE001
                logger.error("Error in on_queue_changed callback: %s", e)

    def set_queue(self, tracks: list[Track], start_index: int = 0) -> None:
        """Replace the entire queue with new tracks."""
        self.consecutive_errors = 0
        self.items = list(tracks)
        self._original_items = list(tracks)
        self.current_index = max(0, min(start_index, len(self.items) - 1)) if self.items else 0
        if self.shuffle and len(self.items) > 1:
            self._apply_shuffle()
        self._notify()

    def append(self, tracks: Track | list[Track]) -> None:
        """Append one or more tracks to the end of the queue."""
        new_tracks = [tracks] if isinstance(tracks, Track) else list(tracks)
        if not new_tracks:
            return
        self.items.extend(new_tracks)
        self._original_items.extend(new_tracks)
        self._notify()

    def add_next(self, tracks: Track | list[Track]) -> None:
        """Insert track(s) immediately after the current playing track."""
        new_tracks = [tracks] if isinstance(tracks, Track) else list(tracks)
        if not new_tracks:
            return
        if not self.items:
            self.items.extend(new_tracks)
            self._original_items.extend(new_tracks)
            self.current_index = 0
        else:
            insert_idx = self.current_index + 1
            for offset, t in enumerate(new_tracks):
                self.items.insert(insert_idx + offset, t)
            self._original_items.extend(new_tracks)
        self._notify()

    def remove(self, idx: int) -> Track | None:
        """Remove a track by index, adjusting current_index safely."""
        if not (0 <= idx < len(self.items)):
            return None
        removed = self.items.pop(idx)
        if removed in self._original_items:
            self._original_items.remove(removed)

        if not self.items:
            self.current_index = 0
        elif idx < self.current_index:
            self.current_index -= 1
        elif self.current_index >= len(self.items):
            self.current_index = len(self.items) - 1

        self._notify()
        return removed

    def move(self, from_idx: int, to_idx: int) -> None:
        """Move a track from one index to another, preserving current track pointer."""
        n = len(self.items)
        if not (0 <= from_idx < n and 0 <= to_idx < n) or from_idx == to_idx:
            return

        track = self.items.pop(from_idx)
        self.items.insert(to_idx, track)

        if self.current_index == from_idx:
            self.current_index = to_idx
        elif from_idx < self.current_index <= to_idx:
            self.current_index -= 1
        elif to_idx <= self.current_index < from_idx:
            self.current_index += 1

        self._notify()

    def clear(self) -> None:
        """Clear all tracks from the queue."""
        self.consecutive_errors = 0
        self.items.clear()
        self._original_items.clear()
        self.current_index = 0
        self._notify()

    def _apply_shuffle(self) -> None:
        """Apply Fisher-Yates shuffle preserving current track at index 0."""
        if len(self.items) <= 1:
            return
        curr = self.current_track
        remaining = [t for i, t in enumerate(self.items) if i != self.current_index]

        # Fisher-Yates shuffle on remaining tracks
        for i in range(len(remaining) - 1, 0, -1):
            j = random.randint(0, i)
            remaining[i], remaining[j] = remaining[j], remaining[i]

        if curr is not None:
            self.items = [curr, *remaining]
            self.current_index = 0
        else:
            self.items = remaining
            self.current_index = 0

    def set_shuffle(self, enabled: bool) -> None:
        """Toggle or set shuffle mode."""
        if self.shuffle == enabled:
            return
        self.shuffle = enabled

        if self.shuffle:
            self._original_items = list(self.items)
            self._apply_shuffle()
        else:
            # Restore original order preserving current track position
            curr = self.current_track
            if self._original_items:
                self.items = list(self._original_items)
                if curr and curr in self.items:
                    self.current_index = self.items.index(curr)
                else:
                    self.current_index = max(0, min(self.current_index, len(self.items) - 1))

        self._notify()

    def set_repeat_mode(self, mode: str) -> None:
        """Set repeat mode: 'off', 'all', or 'one'."""
        if mode not in VALID_REPEAT_MODES:
            raise ValueError(f"Invalid repeat mode: {mode}. Must be one of {VALID_REPEAT_MODES}")
        if self.repeat_mode != mode:
            self.repeat_mode = mode  # type: ignore[assignment]
            self._notify()

    def toggle_repeat_mode(self) -> str:
        """Cycle repeat mode: off -> all -> one -> off."""
        cycle: dict[RepeatMode, RepeatMode] = {"off": "all", "all": "one", "one": "off"}
        self.set_repeat_mode(cycle[self.repeat_mode])
        return self.repeat_mode

    async def refill_radio(self, limit: int = 15) -> int:
        """Refill queue with contextually relevant, deduplicated, popular radio tracks."""
        if not self.autoplay_radio:
            return 0
        curr = self.current_track
        if not curr:
            return 0

        # Never balloon the queue when plenty of tracks are already queued ahead
        upcoming_count = max(0, len(self.items) - 1 - self.current_index)
        effective_limit = min(limit, max(0, 18 - upcoming_count))
        if effective_limit <= 0:
            return 0

        self._refill_seq += 1
        my_refill_seq = self._refill_seq
        start_gen = self._generation
        seed_id = curr.video_id

        ytm = self._ytm or YTMusicClient()
        try:
            recommended = await ytm.get_watch_radio(curr.video_id, limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch radio recommendations: %s", e)
            recommended = []

        if (
            not self.autoplay_radio
            or self._generation != start_gen
            or self._refill_seq != my_refill_seq
            or self.current_track is None
            or self.current_track.video_id != seed_id
        ):
            return 0

        existing_ids = {t.video_id for t in self.items}
        existing_titles = {_canonical_title(t.title) for t in self.items}
        seed_title_lc = curr.title.lower()
        seed_artist = _primary_artist(curr.artist)
        seed_album = (curr.album or "").strip().lower()

        def _score_and_filter(candidates: list[Track], base_priority: float) -> list[tuple[float, Track]]:
            scored: list[tuple[float, Track]] = []
            for rank, track in enumerate(candidates):
                if track.video_id in existing_ids:
                    continue
                canon = _canonical_title(track.title)
                if canon in existing_titles:
                    continue
                # Skip ultra-short clips/ringtones (<45s) or full-album/podcast uploads (>12m)
                if (0 < track.duration_s < 45) or track.duration_s > 720:
                    continue
                if _has_unwanted_noise(track.title, seed_title_lc):
                    continue

                score = base_priority - (rank * 0.45)
                cand_artist = _primary_artist(track.artist)
                if seed_artist and seed_artist != "unknown artist" and cand_artist == seed_artist:
                    score += 3.5
                if seed_album and track.album and track.album.strip().lower() == seed_album:
                    score += 4.0
                if 110 <= track.duration_s <= 420:
                    score += 1.5
                scored.append((score, track))
            return scored

        scored_pool = _score_and_filter(list(recommended), base_priority=20.0)

        # If watch radio returned too few clean tracks (or drifted into remixes/duplicates),
        # blend in popular contextual songs from the seed artist / soundtrack.
        if (
            len(scored_pool) < 6
            and seed_artist
            and seed_artist not in ("unknown artist", "artist")
            and hasattr(ytm, "search")
            and inspect.iscoroutinefunction(ytm.search)
        ):
            queries = [f"{curr.artist.split(',')[0].strip()} popular songs"]
            if curr.album and curr.album.strip().lower() != curr.title.strip().lower():
                queries.append(f"{curr.album} songs")
            for q in queries:
                if (
                    not self.autoplay_radio
                    or self._generation != start_gen
                    or self._refill_seq != my_refill_seq
                    or self.current_track is None
                    or self.current_track.video_id != seed_id
                ):
                    return 0
                try:
                    extra = await ytm.search(q, filter_type="songs", limit=12)
                    scored_pool.extend(_score_and_filter(list(extra), base_priority=15.0))
                except Exception as e:  # noqa: BLE001
                    logger.debug("Contextual radio fallback search failed for %r: %s", q, e)

        if (
            not self.autoplay_radio
            or self._generation != start_gen
            or self._refill_seq != my_refill_seq
            or self.current_track is None
            or self.current_track.video_id != seed_id
        ):
            return 0

        scored_pool.sort(key=lambda pair: pair[0], reverse=True)

        selected: list[Track] = []
        seen_ids = set(existing_ids)
        seen_titles = set(existing_titles)
        artist_counts: dict[str, int] = {}

        for _, track in scored_pool:
            if track.video_id in seen_ids:
                continue
            canon = _canonical_title(track.title)
            if canon in seen_titles:
                continue
            cand_artist = _primary_artist(track.artist)
            # Cap per-artist concentration so one artist/upload spam cannot dominate Up Next
            max_for_artist = 4 if (seed_artist and cand_artist == seed_artist) else 2
            if cand_artist and cand_artist not in ("unknown artist", "artist"):
                if artist_counts.get(cand_artist, 0) >= max_for_artist:
                    continue
                artist_counts[cand_artist] = artist_counts.get(cand_artist, 0) + 1

            seen_ids.add(track.video_id)
            seen_titles.add(canon)
            selected.append(track)
            if len(selected) >= effective_limit:
                break

        # Graceful fallback if strict filtering removed everything from a valid watch list
        if not selected and recommended:
            for track in recommended:
                if track.video_id not in existing_ids:
                    selected.append(track)
                if len(selected) >= min(effective_limit, 8):
                    break

        if selected:
            self.items.extend(selected)
            self._original_items.extend(selected)
            self._notify()
            return len(selected)
        return 0

    async def next_track(self, is_error: bool = False) -> Track | None:
        """Advance queue index and return next Track, or None if queue ends.

        If is_error is True, verifies consecutive errors do not exceed
        max_consecutive_errors (2) before allowing advance.
        If is_error is False, resets consecutive errors.
        """
        if is_error:
            if not self.can_skip_on_error():
                logger.warning(
                    "QueueManager: consecutive error limit (%d) reached. Halting auto-advance.",
                    self.max_consecutive_errors,
                )
                return None
        else:
            self.consecutive_errors = 0

        if not self.items:
            return None

        if self.repeat_mode == "one":
            return self.current_track

        if self.current_index + 1 < len(self.items):
            self.current_index += 1
            self._notify()
            return self.current_track

        # Reached the end of queue
        if self.repeat_mode == "all":
            self.current_index = 0
            self._notify()
            return self.current_track

        # Off mode: check autoplay radio refill
        if self.autoplay_radio:
            gen_before = self._generation
            seed_idx = self.current_index
            seed_id = self.current_track.video_id if self.current_track else None
            added = await self.refill_radio()
            if (
                added > 0
                and self.current_index == seed_idx
                and self.current_track is not None
                and self.current_track.video_id == seed_id
                and self._generation == gen_before + 1
                and self.current_index + 1 < len(self.items)
            ):
                self.current_index += 1
                self._notify()
                return self.current_track

        return None

    def prev_track(self) -> Track | None:
        """Go to previous track in queue, or wrap around if repeat all."""
        if not self.items:
            return None

        if self.repeat_mode == "one":
            return self.current_track

        if self.current_index > 0:
            self.current_index -= 1
        elif self.repeat_mode == "all":
            self.current_index = len(self.items) - 1
        else:
            self.current_index = 0

        self._notify()
        return self.current_track

    def neighbor_tracks(self, offsets: tuple[int, ...] = (1, -1, 2, -2, 3)) -> list[Track]:
        """Return unique queue tracks around current_index in priority offset order."""
        n = len(self.items)
        if n <= 1:
            return []
        curr_id = self.current_track.video_id if self.current_track else None
        seen: set[str] = {curr_id} if curr_id else set()
        out: list[Track] = []
        for off in offsets:
            if off == 0:
                continue
            idx = self.current_index + off
            if idx < 0 or idx >= n:
                if self.repeat_mode == "all":
                    idx = idx % n
                else:
                    continue
            cand = self.items[idx]
            if cand.video_id in seen:
                continue
            seen.add(cand.video_id)
            out.append(cand)
        return out

    async def step_tracks(self, steps: int = 1, direction: int = 1) -> Track | None:
        """Jump forward or backward by `steps` tracks in a single step without intermediate loads."""
        steps = max(1, int(steps))
        if direction >= 0 and steps == 1:
            return await self.next_track()
        if direction < 0 and steps == 1:
            return self.prev_track()

        if not self.items:
            return None
        self.consecutive_errors = 0
        if self.repeat_mode == "one":
            return self.current_track

        n = len(self.items)
        if direction < 0:
            target = self.current_index - steps
            if target < 0:
                target = (target % n) if self.repeat_mode == "all" else 0
            self.current_index = target
            self._notify()
            return self.current_track

        target = self.current_index + steps
        if target < n:
            self.current_index = target
            self._notify()
            return self.current_track

        if self.repeat_mode == "all":
            self.current_index = target % n
            self._notify()
            return self.current_track

        if self.autoplay_radio:
            gen_before = self._generation
            seed_idx = self.current_index
            seed_id = self.current_track.video_id if self.current_track else None
            added = await self.refill_radio(limit=max(15, target - n + 5))
            if (
                added > 0
                and self.current_index == seed_idx
                and self.current_track is not None
                and self.current_track.video_id == seed_id
                and self._generation == gen_before + 1
                and self.current_index + 1 < len(self.items)
            ):
                self.current_index = min(target, len(self.items) - 1)
                self._notify()
                return self.current_track

        return None

    def save_to_db_sync(self, db_path: Path | str | None = None) -> None:
        """Synchronously persist queue state to SQLite queue_state table."""
        path = Path(db_path) if db_path is not None else self._db_path
        if not path.is_file():
            return
        items_json = json.dumps([t.model_dump() for t in self.items])
        with get_sync_connection(path) as conn:
            conn.execute(
                """
                INSERT INTO queue_state (id, current_index, shuffle, repeat_mode, items_json)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    current_index = excluded.current_index,
                    shuffle = excluded.shuffle,
                    repeat_mode = excluded.repeat_mode,
                    items_json = excluded.items_json;
                """,
                (self.current_index, 1 if self.shuffle else 0, self.repeat_mode, items_json),
            )
            conn.commit()

    async def save_to_db(self, db_path: Path | str | None = None) -> None:
        """Asynchronously persist queue state to SQLite queue_state table."""
        path = Path(db_path) if db_path is not None else self._db_path
        if not path.is_file():
            return
        items_json = json.dumps([t.model_dump() for t in self.items])
        async with get_connection(path) as conn:
            await conn.execute(
                """
                INSERT INTO queue_state (id, current_index, shuffle, repeat_mode, items_json)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    current_index = excluded.current_index,
                    shuffle = excluded.shuffle,
                    repeat_mode = excluded.repeat_mode,
                    items_json = excluded.items_json;
                """,
                (self.current_index, 1 if self.shuffle else 0, self.repeat_mode, items_json),
            )
            await conn.commit()

    def flush_sync(self) -> None:
        """Cancel pending async persistence task and flush dirty queue state synchronously."""
        if self._persist_task is not None and not self._persist_task.done():
            self._persist_task.cancel()
        self._persist_task = None
        if self.auto_persist and self._persist_dirty:
            self._persist_dirty = False
            try:
                self.save_to_db_sync()
            except (sqlite3.Error, OSError) as e:
                logger.warning("Failed to flush queue state on shutdown: %s", e)

    def _parse_persisted_items(self, raw_json: str | None) -> list[Track]:
        """Parse persisted items_json defensively, skipping malformed entries."""
        raw_items = json.loads(raw_json or "[]")
        if not isinstance(raw_items, list):
            return []
        parsed: list[Track] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                logger.warning("Skipping non-dict persisted queue entry at index %d", idx)
                continue
            try:
                parsed.append(Track(**item))
            except (ValueError, TypeError, KeyError) as e:
                logger.warning("Skipping malformed persisted queue entry at index %d: %s", idx, e)
        return parsed

    def load_from_db_sync(self, db_path: Path | str | None = None) -> bool:
        """Synchronously load queue state from SQLite queue_state table."""
        path = Path(db_path) if db_path is not None else self._db_path
        if not path.is_file():
            return False
        try:
            with get_sync_connection(path) as conn:
                cursor = conn.execute("SELECT * FROM queue_state WHERE id = 1;")
                row = cursor.fetchone()
                if not row:
                    return False
                self.items = self._parse_persisted_items(row["items_json"])
                self._original_items = list(self.items)
                raw_idx = int(row["current_index"] or 0)
                self.current_index = max(0, min(raw_idx, len(self.items) - 1)) if self.items else 0
                self.shuffle = bool(row["shuffle"])
                raw_repeat = row["repeat_mode"] or "off"
                self.repeat_mode = raw_repeat if raw_repeat in VALID_REPEAT_MODES else "off"  # type: ignore[assignment]
                self._generation += 1
                return True
        except (sqlite3.Error, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to load queue state from database: %s", e)
            return False

    async def load_from_db(self, db_path: Path | str | None = None) -> bool:
        """Asynchronously load queue state from SQLite queue_state table."""
        path = Path(db_path) if db_path is not None else self._db_path
        if not path.is_file():
            return False
        try:
            async with get_connection(path) as conn:
                cursor = await conn.execute("SELECT * FROM queue_state WHERE id = 1;")
                row = await cursor.fetchone()
                if not row:
                    return False
                self.items = self._parse_persisted_items(row["items_json"])
                self._original_items = list(self.items)
                raw_idx = int(row["current_index"] or 0)
                self.current_index = max(0, min(raw_idx, len(self.items) - 1)) if self.items else 0
                self.shuffle = bool(row["shuffle"])
                raw_repeat = row["repeat_mode"] or "off"
                self.repeat_mode = raw_repeat if raw_repeat in VALID_REPEAT_MODES else "off"  # type: ignore[assignment]
                self._generation += 1
                return True
        except (sqlite3.Error, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to load queue state from database: %s", e)
            return False
