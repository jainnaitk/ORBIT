"""Unit tests for QueueManager: mutations, shuffle, repeat, refill, and SQLite persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from metrolite.audio.queue import QueueManager
from metrolite.store.db import SCHEMA_SQL
from metrolite.ytm.client import Track


@pytest.fixture
def sample_tracks() -> list[Track]:
    """Provide a list of 5 test tracks."""
    return [
        Track(video_id=f"vid_{i}", title=f"Track {i}", artist="Artist", duration_s=180)
        for i in range(1, 6)
    ]


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary initialized SQLite database."""
    db_file = tmp_path / "queue_test.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.executescript(SCHEMA_SQL)
    conn.close()
    return db_file


def test_queue_basic_mutations(sample_tracks: list[Track]) -> None:
    """Test append, add_next, remove, move, and clear."""
    qm = QueueManager(autoplay_radio=False)
    assert qm.current_track is None

    # Set queue
    qm.set_queue(sample_tracks[:3], start_index=1)
    assert len(qm.items) == 3
    assert qm.current_index == 1
    assert qm.current_track.video_id == "vid_2"  # type: ignore[union-attr]

    # Append
    qm.append(sample_tracks[3])
    assert len(qm.items) == 4
    assert qm.items[-1].video_id == "vid_4"

    # Add next (should insert right after current_index=1, so at index 2)
    qm.add_next(sample_tracks[4])
    assert len(qm.items) == 5
    assert qm.items[2].video_id == "vid_5"
    assert qm.current_track.video_id == "vid_2"  # type: ignore[union-attr]

    # Move item: move index 2 ("vid_5") to index 0
    qm.move(from_idx=2, to_idx=0)
    assert qm.items[0].video_id == "vid_5"
    # Current track was at index 1, moving from 2 to 0 shifted index 1 to 2
    assert qm.current_track.video_id == "vid_2"  # type: ignore[union-attr]
    assert qm.current_index == 2

    # Remove item at index 0
    removed = qm.remove(0)
    assert removed is not None
    assert removed.video_id == "vid_5"
    assert qm.current_track.video_id == "vid_2"  # type: ignore[union-attr]
    assert qm.current_index == 1

    # Clear
    qm.clear()
    assert len(qm.items) == 0
    assert qm.current_track is None
    assert qm.current_index == 0


def test_queue_shuffle_preserves_current(sample_tracks: list[Track]) -> None:
    """Test that shuffle keeps current track at index 0 and unshuffle restores order."""
    qm = QueueManager(autoplay_radio=False)
    qm.set_queue(sample_tracks, start_index=2)
    current_track = qm.current_track
    assert current_track is not None
    assert current_track.video_id == "vid_3"

    # Turn shuffle on
    qm.set_shuffle(True)
    assert qm.shuffle is True
    assert qm.current_index == 0
    assert qm.current_track == current_track
    assert len(qm.items) == len(sample_tracks)
    # Check that all tracks are still present
    assert {t.video_id for t in qm.items} == {t.video_id for t in sample_tracks}

    # Turn shuffle off -> restores original ordering and points to current track
    qm.set_shuffle(False)
    assert qm.shuffle is False
    assert [t.video_id for t in qm.items] == [t.video_id for t in sample_tracks]
    assert qm.current_track == current_track
    assert qm.current_index == 2


@pytest.mark.asyncio
async def test_queue_repeat_modes(sample_tracks: list[Track]) -> None:
    """Test repeat modes: 'off', 'all', and 'one'."""
    qm = QueueManager(autoplay_radio=False)
    qm.set_queue(sample_tracks[:3], start_index=0)

    # 1. Repeat "off": moves forward, stops at end
    qm.set_repeat_mode("off")
    assert (await qm.next_track()).video_id == "vid_2"  # type: ignore[union-attr]
    assert (await qm.next_track()).video_id == "vid_3"  # type: ignore[union-attr]
    assert await qm.next_track() is None
    assert qm.current_index == 2

    # Prev track in "off"
    assert qm.prev_track().video_id == "vid_2"  # type: ignore[union-attr]
    assert qm.prev_track().video_id == "vid_1"  # type: ignore[union-attr]
    assert qm.prev_track().video_id == "vid_1"  # stops at 0

    # 2. Repeat "all": wraps around on next and prev
    qm.set_repeat_mode("all")
    qm.current_index = 2
    assert (await qm.next_track()).video_id == "vid_1"  # type: ignore[union-attr]
    assert qm.current_index == 0
    assert qm.prev_track().video_id == "vid_3"  # wraps to end
    assert qm.current_index == 2

    # 3. Repeat "one": next and prev stay on same track
    qm.set_repeat_mode("one")
    assert (await qm.next_track()).video_id == "vid_3"  # type: ignore[union-attr]
    assert qm.current_index == 2
    assert qm.prev_track().video_id == "vid_3"  # type: ignore[union-attr]
    assert qm.current_index == 2

    # Cycle repeat modes
    qm.set_repeat_mode("off")
    assert qm.toggle_repeat_mode() == "all"
    assert qm.toggle_repeat_mode() == "one"
    assert qm.toggle_repeat_mode() == "off"


@pytest.mark.asyncio
async def test_queue_radio_refill(sample_tracks: list[Track]) -> None:
    """Test automatic radio refill when queue reaches the end and autoplay_radio is True."""
    mock_ytm = MagicMock()
    mock_ytm.get_watch_radio = AsyncMock(
        return_value=[
            Track(video_id="vid_radio_1", title="Radio 1", artist="Artist"),
            Track(video_id="vid_radio_2", title="Radio 2", artist="Artist"),
        ]
    )

    qm = QueueManager(ytm_client=mock_ytm, autoplay_radio=True)
    qm.set_queue([sample_tracks[0]], start_index=0)
    assert len(qm.items) == 1

    # Advancing queue should trigger radio refill automatically
    next_track = await qm.next_track()
    assert next_track is not None
    assert next_track.video_id == "vid_radio_1"
    assert len(qm.items) == 3
    mock_ytm.get_watch_radio.assert_called_once_with("vid_1", limit=25)


@pytest.mark.asyncio
async def test_queue_sqlite_persistence(temp_db: Path, sample_tracks: list[Track]) -> None:
    """Test saving queue state to SQLite and loading it back."""
    qm1 = QueueManager(db_path=temp_db, autoplay_radio=False)
    qm1.set_queue(sample_tracks, start_index=3)
    qm1.set_repeat_mode("all")
    qm1.shuffle = True

    # Save asynchronously
    await qm1.save_to_db()

    # Load in a fresh QueueManager
    qm2 = QueueManager(db_path=temp_db, autoplay_radio=False)
    loaded = await qm2.load_from_db()
    assert loaded is True
    assert len(qm2.items) == 5
    assert qm2.current_index == 3
    assert qm2.shuffle is True
    assert qm2.repeat_mode == "all"
    assert [t.video_id for t in qm2.items] == [t.video_id for t in sample_tracks]

    # Test sync save and load
    qm2.current_index = 1
    qm2.save_to_db_sync()

    qm3 = QueueManager(db_path=temp_db)
    loaded_sync = qm3.load_from_db_sync()
    assert loaded_sync is True
    assert qm3.current_index == 1


@pytest.mark.asyncio
async def test_queue_consecutive_errors_limit(sample_tracks: list[Track]) -> None:
    """Test consecutive error tracking and max 2 consecutive error skips limit."""
    qm = QueueManager(autoplay_radio=False)
    qm.set_queue(sample_tracks, start_index=0)
    assert qm.consecutive_errors == 0
    assert qm.can_skip_on_error() is True

    # Error skip 1: allowed
    qm.record_error()
    t1 = await qm.next_track(is_error=True)
    assert t1 is not None
    assert t1.video_id == "vid_2"
    assert qm.consecutive_errors == 1
    assert qm.can_skip_on_error() is True

    # Error skip 2: allowed
    qm.record_error()
    t2 = await qm.next_track(is_error=True)
    assert t2 is not None
    assert t2.video_id == "vid_3"
    assert qm.consecutive_errors == 2
    assert qm.can_skip_on_error() is True

    # Error skip 3: exceeds limit of 2, auto-advance halted
    qm.record_error()
    t3 = await qm.next_track(is_error=True)
    assert t3 is None
    assert qm.consecutive_errors == 3
    assert qm.can_skip_on_error() is False
    assert qm.current_index == 2  # did not advance

    # Normal playback advancement resets error count
    t_normal = await qm.next_track(is_error=False)
    assert t_normal is not None
    assert t_normal.video_id == "vid_4"
    assert qm.consecutive_errors == 0
    assert qm.can_skip_on_error() is True

    # Manual reset
    qm.record_error()
    assert qm.consecutive_errors == 1
    qm.reset_errors()
    assert qm.consecutive_errors == 0

    # set_queue resets error count
    qm.record_error()
    qm.record_error()
    assert qm.consecutive_errors == 2
    qm.set_queue(sample_tracks[:2])
    assert qm.consecutive_errors == 0
