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
from metrolite.library.manager import artist_matches_blocked
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
        blocked_artists_fn: Callable[[], set[str]] | None = None,
    ) -> None:
        cfg = get_config()
        self._db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self._ytm = ytm_client
        self.autoplay_radio = (
            autoplay_radio if autoplay_radio is not None else cfg.queue.autoplay_radio
        )
        self.auto_persist = auto_persist
        self._on_queue_changed = on_queue_changed
        self.blocked_artists_fn = blocked_artists_fn

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
                    self._persist_dirty = True
                    return
                except asyncio.CancelledError:
                    self._persist_dirty = True
                    break

        with suppress(RuntimeError):
            self._persist_task = asyncio.get_running_loop().create_task(_flush())

    def _notify(self) -> None:
        """Trigger queue changed callback and auto-persistence."""
        self._generation += 1
        if not self.shuffle:
            self._original_items = list(self.items)
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

    @staticmethod
    def _tag_track(track: Track, source: Literal["user", "radio"] = "user") -> Track:
        """Ensure queue tracks carry a valid provenance source ('user' or 'radio')."""
        effective: Literal["user", "radio"] = track.source if track.source in ("user", "radio") else source
        return track.model_copy(update={"source": effective})

    def _original_index(self, track: Track) -> int:
        return next(i for i, entry in enumerate(self._original_items) if entry is track)

    def replace_track(self, idx: int, track: Track) -> None:
        replacement = track.model_copy(update={"source": self.items[idx].source})
        self._original_items[self._original_index(self.items[idx])] = replacement
        self.items[idx] = replacement

    def set_queue(self, tracks: list[Track], start_index: int = 0) -> None:
        """Replace the entire queue with new tracks."""
        self.consecutive_errors = 0
        tagged = [self._tag_track(t, "user") for t in tracks]
        self.items = list(tagged)
        self._original_items = list(tagged)
        self.current_index = max(0, min(start_index, len(self.items) - 1)) if self.items else 0
        if self.shuffle and len(self.items) > 1:
            self._apply_shuffle()
        self._notify()

    def append(self, tracks: Track | list[Track]) -> None:
        """Append one or more tracks to the end of the queue (inserting before radio tail if any)."""
        raw_tracks = [tracks] if isinstance(tracks, Track) else list(tracks)
        if not raw_tracks:
            return
        new_tracks = [self._tag_track(t, "user") for t in raw_tracks]
        # Place explicit user appends after existing explicit tracks (ahead of suggested radio)
        insert_idx = self.current_index + 1 if self.items else 0
        while insert_idx < len(self.items) and self.items[insert_idx].source != "radio":
            insert_idx += 1
        for off, t in enumerate(new_tracks):
            self.items.insert(insert_idx + off, t)
        self._original_items.extend(new_tracks)
        self._notify()

    def add_next(self, tracks: Track | list[Track]) -> None:
        """Insert track(s) immediately after the current playing track."""
        raw_tracks = [tracks] if isinstance(tracks, Track) else list(tracks)
        if not raw_tracks:
            return
        new_tracks = [self._tag_track(t, "user").model_copy(update={"source": "user"}) for t in raw_tracks]
        if not self.items:
            self.items.extend(new_tracks)
            self._original_items.extend(new_tracks)
            self.current_index = 0
        else:
            insert_idx = self.current_index + 1
            for offset, t in enumerate(new_tracks):
                self.items.insert(insert_idx + offset, t)
            original_pos = self._original_index(self.items[self.current_index]) + 1
            self._original_items[original_pos:original_pos] = new_tracks
        self._notify()

    def promote(self, idx: int) -> bool:
        """Promote a suggested radio track into the explicit user queue."""
        if not (0 <= idx < len(self.items)):
            return False
        promoted = self.items[idx].model_copy(update={"source": "user"})
        # Update _original_items entry if present
        if self.shuffle:
            self._original_items[self._original_index(self.items[idx])] = promoted
        if idx <= self.current_index:
            self.items[idx] = promoted
            self._notify()
            return True

        self.items.pop(idx)
        target_pos = self.current_index + 1
        while target_pos < len(self.items) and self.items[target_pos].source != "radio":
            target_pos += 1
        self.items.insert(target_pos, promoted)
        self._notify()
        return True

    def remove(self, idx: int) -> Track | None:
        """Remove a track by index, adjusting current_index safely."""
        if not (0 <= idx < len(self.items)):
            return None
        removed = self.items.pop(idx)
        self._original_items.pop(self._original_index(removed))

        if not self.items:
            self.current_index = 0
        elif idx < self.current_index:
            self.current_index -= 1
        elif self.current_index >= len(self.items):
            self.current_index = len(self.items) - 1

        self._notify()
        return removed

    def move(
        self,
        from_idx: int,
        to_idx: int,
        target_source: str | None = None,
    ) -> None:
        """Move a track from one index to another, supporting global cross-section drag-and-drop."""
        n = len(self.items)
        if not (0 <= from_idx < n and 0 <= to_idx < n):
            return
        if from_idx == to_idx and target_source is None:
            return

        destination = self.items[to_idx]
        track = self.items.pop(from_idx)
        self.items.insert(to_idx, track)

        if self.current_index == from_idx:
            self.current_index = to_idx
        elif from_idx < self.current_index <= to_idx:
            self.current_index -= 1
        elif to_idx <= self.current_index < from_idx:
            self.current_index += 1

        next_source = track.source
        if target_source in ("user", "radio"):
            next_source = target_source  # type: ignore[assignment]
        elif to_idx > self.current_index:
            # Infer cleanly when dragged above remaining radio suggestions or below radio items
            has_user_after = any(
                self.items[i].source != "radio" for i in range(to_idx + 1, len(self.items))
            )
            has_radio_before = any(
                self.items[i].source == "radio"
                for i in range(self.current_index + 1, to_idx)
            )
            if has_user_after:
                next_source = "user"
            elif has_radio_before:
                next_source = "radio"

        if next_source != track.source:
            self.items[to_idx] = track.model_copy(update={"source": next_source})

        if self.shuffle:
            original = self._original_index(track)
            if from_idx == to_idx:
                self._original_items[original] = self.items[to_idx]
            else:
                self._original_items.pop(original)
                target = self._original_index(destination) + int(from_idx < to_idx)
                self._original_items.insert(target, self.items[to_idx])

        self._notify()

    def clear(self) -> None:
        """Clear all tracks from the queue."""
        self.consecutive_errors = 0
        self.items.clear()
        self._original_items.clear()
        self.current_index = 0
        self._notify()

    def _apply_shuffle(self) -> None:
        """Apply Fisher-Yates shuffle preserving current track at index 0 and user/radio partition."""
        if len(self.items) <= 1:
            return
        curr = self.current_track
        remaining = [t for i, t in enumerate(self.items) if i != self.current_index]
        user_remaining = [t for t in remaining if t.source != "radio"]
        radio_remaining = [t for t in remaining if t.source == "radio"]

        def _shuffle_in_place(bucket: list[Track]) -> None:
            for i in range(len(bucket) - 1, 0, -1):
                j = random.randint(0, i)
                bucket[i], bucket[j] = bucket[j], bucket[i]

        _shuffle_in_place(user_remaining)
        _shuffle_in_place(radio_remaining)

        shuffled = [*user_remaining, *radio_remaining]
        if curr is not None:
            self.items = [curr, *shuffled]
            self.current_index = 0
        else:
            self.items = shuffled
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
                if curr and any(item is curr for item in self.items):
                    self.current_index = self._original_index(curr)
                elif curr is not None:
                    by_vid = next(
                        (i for i, t in enumerate(self.items) if t.video_id == curr.video_id),
                        None,
                    )
                    self.current_index = (
                        by_vid
                        if by_vid is not None
                        else max(0, min(self.current_index, len(self.items) - 1))
                    )
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

    async def refill_radio(
        self,
        limit: int = 15,
        auto_promote_until: int = 0,
    ) -> int:
        """Refill queue with contextually relevant tracks, building active queue first when configured."""
        if not self.autoplay_radio:
            return 0
        curr = self.current_track
        if not curr:
            return 0

        # Allow larger buffers when building the initial 20-25 active queue + trailing suggestions
        upcoming_count = max(0, len(self.items) - 1 - self.current_index)
        max_upcoming = max(18, auto_promote_until + 8) if auto_promote_until > 0 else 18
        effective_limit = min(limit, max(0, max_upcoming - upcoming_count))
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
        blocked_norms: set[str] = set()
        if callable(self.blocked_artists_fn):
            with suppress(Exception):
                blocked_norms = self.blocked_artists_fn() or set()

        def _score_and_filter(candidates: list[Track], base_priority: float) -> list[tuple[float, Track]]:
            scored: list[tuple[float, Track]] = []
            for rank, track in enumerate(candidates):
                if track.video_id in existing_ids:
                    continue
                if blocked_norms and artist_matches_blocked(track.artist, blocked_norms):
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

        # If watch radio returned fewer clean tracks than needed (e.g. to reach 20-25 songs),
        # blend in popular contextual songs from the seed artist / soundtrack.
        min_pool_target = max(6, effective_limit) if auto_promote_until > 0 else 6
        if (
            len(scored_pool) < min_pool_target
            and seed_artist
            and seed_artist not in ("unknown artist", "artist")
            and hasattr(ytm, "search")
            and inspect.iscoroutinefunction(ytm.search)
        ):
            queries = [f"{curr.artist.split(',')[0].strip()} popular songs"]
            if curr.album and curr.album.strip().lower() != curr.title.strip().lower():
                queries.append(f"{curr.album} songs")
            if auto_promote_until > 0:
                queries.append(f"{curr.artist.split(',')[0].strip()} hits playlist")
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
                    extra = await ytm.search(q, filter_type="songs", limit=18)
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
            # Cap per-artist concentration while allowing larger 22-28 track queues when building initial active queue
            if auto_promote_until > 0:
                max_for_artist = 6 if (seed_artist and cand_artist == seed_artist) else 4
            else:
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
                if track.video_id not in existing_ids and not (
                    blocked_norms and artist_matches_blocked(track.artist, blocked_norms)
                ):
                    selected.append(track)
                if len(selected) >= min(effective_limit, 8):
                    break

        if selected:
            upcoming_user_count = sum(
                1 for t in self.items[self.current_index + 1:] if t.source != "radio"
            )
            promote_slots = max(0, auto_promote_until - upcoming_user_count) if auto_promote_until > 0 else 0
            to_user = [
                t.model_copy(update={"source": "user"}) for t in selected[:promote_slots]
            ]
            to_radio = [
                t.model_copy(update={"source": "radio"}) for t in selected[promote_slots:]
            ]

            if to_user:
                insert_idx = self.current_index + 1
                while insert_idx < len(self.items) and self.items[insert_idx].source != "radio":
                    insert_idx += 1
                for off, ut in enumerate(to_user):
                    self.items.insert(insert_idx + off, ut)
                self._original_items.extend(to_user)

            if to_radio:
                self.items.extend(to_radio)
                self._original_items.extend(to_radio)

            self._notify()
            return len(to_user) + len(to_radio)
        return 0

    async def _advance_after_radio(
        self, steps: int, limit: int, is_current: Callable[[], bool] | None
    ) -> Track | None:
        seed = self.current_track
        generation = self._generation
        added = await self.refill_radio(limit=limit)
        if seed is None or self.current_track is not seed or (is_current is not None and not is_current()):
            return None
        if not added and generation == self._generation:
            return None

        # An edit can invalidate the recommendations but supply its own next item.
        target = self.current_index + steps
        if self.repeat_mode == "one":
            return seed
        if self.repeat_mode == "all":
            target %= len(self.items)
        elif self.current_index + 1 < len(self.items):
            target = min(target, len(self.items) - 1)
        else:
            return None
        self.current_index = target
        self._notify()
        return self.current_track

    async def next_track(
        self, is_error: bool = False, *, is_current: Callable[[], bool] | None = None
    ) -> Track | None:
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
            return await self._advance_after_radio(1, 15, is_current)

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

    async def step_tracks(
        self, steps: int = 1, direction: int = 1, *, is_current: Callable[[], bool] | None = None
    ) -> Track | None:
        """Jump forward or backward by `steps` tracks in a single step without intermediate loads."""
        steps = max(1, int(steps))
        if direction >= 0 and steps == 1:
            return await self.next_track(is_current=is_current)
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
            return await self._advance_after_radio(steps, max(15, target - n + 5), is_current)

        return None

    def _snapshot(self) -> tuple[int, int, str, str]:
        records = [track.model_dump() for track in self.items]
        if self.shuffle:
            positions = {id(track): i for i, track in enumerate(self._original_items)}
            for track, record in zip(self.items, records, strict=True):
                record["queue_order"] = positions[id(track)]
        return self.current_index, int(self.shuffle), self.repeat_mode, json.dumps(records)

    def save_to_db_sync(self, db_path: Path | str | None = None) -> None:
        """Synchronously persist queue state to SQLite queue_state table."""
        path = Path(db_path) if db_path is not None else self._db_path
        if not path.is_file():
            return
        snapshot = self._snapshot()
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
                snapshot,
            )
            conn.commit()

    async def save_to_db(self, db_path: Path | str | None = None) -> None:
        """Asynchronously persist queue state to SQLite queue_state table."""
        path = Path(db_path) if db_path is not None else self._db_path
        if not path.is_file():
            return
        snapshot = self._snapshot()
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
                snapshot,
            )
            await conn.commit()

    async def flush(self) -> None:
        """Drain queued writes before acknowledging shutdown."""
        if self._persist_task is not None:
            await asyncio.shield(self._persist_task)
        if self.auto_persist and self._persist_dirty:
            await self.save_to_db()
            self._persist_dirty = False

    def _parse_persisted_items(self, raw_json: str | None) -> list[Track]:
        """Parse persisted items_json defensively, skipping malformed entries."""
        raw_items = json.loads(raw_json or "[]")
        self._original_items = []
        if not isinstance(raw_items, list):
            return []
        parsed: list[Track] = []
        positions: list[int] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                logger.warning("Skipping non-dict persisted queue entry at index %d", idx)
                continue
            try:
                parsed.append(Track(**item))
                positions.append(item.get("queue_order", idx))
            except (ValueError, TypeError, KeyError) as e:
                logger.warning("Skipping malformed persisted queue entry at index %d: %s", idx, e)
        self._original_items = list(parsed)
        if all(isinstance(p, int) for p in positions) and sorted(positions) == list(range(len(parsed))):
            self._original_items = [track for _, track in sorted(zip(positions, parsed), key=lambda pair: pair[0])]
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
