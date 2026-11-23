"""Queue manager and persistence for MetroLite."""

from __future__ import annotations

import asyncio
import json
import logging
import random
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

    async def refill_radio(self, limit: int = 25) -> int:
        """Refill queue using YouTube Music watch radio recommendations."""
        if not self.autoplay_radio:
            return 0
        curr = self.current_track
        if not curr:
            return 0

        ytm = self._ytm or YTMusicClient()
        try:
            recommended = await ytm.get_watch_radio(curr.video_id, limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch radio recommendations: %s", e)
            return 0

        existing_ids = {t.video_id for t in self.items}
        new_tracks = [t for t in recommended if t.video_id not in existing_ids]

        if new_tracks:
            self.items.extend(new_tracks)
            self._original_items.extend(new_tracks)
            self._notify()
            return len(new_tracks)
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
            added = await self.refill_radio()
            if added > 0 and self.current_index + 1 < len(self.items):
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
                return True
        except (sqlite3.Error, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to load queue state from database: %s", e)
            return False
