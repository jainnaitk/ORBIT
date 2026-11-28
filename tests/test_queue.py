"""Unit tests for QueueManager: mutations, shuffle, repeat, refill, and SQLite persistence."""

from __future__ import annotations

import asyncio
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
    mock_ytm.get_watch_radio.assert_called_once_with("vid_1", limit=15)


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


@pytest.mark.asyncio
async def test_queue_radio_filters_remixes_and_duplicates() -> None:
    """Test smart radio engine removes remix spam, duplicate canonical titles, and blends popular songs."""
    mock_ytm = MagicMock()
    mock_ytm.get_watch_radio = AsyncMock(
        return_value=[
            Track(video_id="dup_1", title="Kiliye Kiliye (Afrobeat Mix)", artist="Spam DJ", duration_s=210),
            Track(video_id="dup_2", title="Kiliye Kiliye (Slowed + Reverb)", artist="Spam DJ", duration_s=220),
            Track(video_id="ring_1", title="Short Ringtone", artist="Random", duration_s=20),
            Track(video_id="clean_1", title="Tum Hi Ho", artist="Arijit Singh", duration_s=260),
            Track(video_id="clean_dup", title="Tum Hi Ho (Official Video)", artist="Arijit Singh", duration_s=262),
        ]
    )
    mock_ytm.search = AsyncMock(
        return_value=[
            Track(video_id="pop_1", title="Kesariya", artist="Arijit Singh", duration_s=268),
            Track(video_id="pop_2", title="Channa Mereya", artist="Arijit Singh", duration_s=289),
        ]
    )

    qm = QueueManager(ytm_client=mock_ytm, autoplay_radio=True)
    qm.set_queue(
        [Track(video_id="seed_1", title="Saiyaara", artist="Arijit Singh", duration_s=250)],
        start_index=0,
    )

    added = await qm.refill_radio(limit=10)
    assert added == 3
    ids = [t.video_id for t in qm.items[1:]]
    assert "dup_1" not in ids
    assert "dup_2" not in ids
    assert "ring_1" not in ids
    assert "clean_dup" not in ids
    assert set(ids) == {"clean_1", "pop_1", "pop_2"}


def test_queue_neighbor_tracks_and_repeat_wrap() -> None:
    """Verify neighbor_tracks returns unique adjacent tracks with priority offsets and wraparound."""
    tracks = [
        Track(video_id=f"n{i}", title=f"Track {i}", artist="Artist") for i in range(6)
    ]
    qm = QueueManager(autoplay_radio=False)
    qm.set_queue(tracks, start_index=1)

    neighbors = qm.neighbor_tracks((1, -1, 2, -2, 3))
    assert [t.video_id for t in neighbors] == ["n2", "n0", "n3", "n4"]

    qm.set_queue(tracks[:4], start_index=0)
    qm.set_repeat_mode("all")
    wrapped = qm.neighbor_tracks((1, -1, 2, -2))
    assert [t.video_id for t in wrapped] == ["n1", "n3", "n2"]


async def test_shutdown_drains_latest_shuffled_edit(temp_db, sample_tracks, monkeypatch):
    queue = QueueManager(db_path=temp_db, auto_persist=True)
    started, release = asyncio.Event(), asyncio.Event()
    save = queue.save_to_db

    async def wait_then_save():
        started.set()
        await release.wait()
        await save()

    monkeypatch.setattr(queue, "save_to_db", wait_then_save)
    queue.set_queue(sample_tracks, start_index=1)
    queue.set_shuffle(True)
    await started.wait()
    removed = queue.items[2]
    queue.move(2, 1, target_source="radio")
    queue.remove(1)
    drain = asyncio.create_task(queue.flush())
    await asyncio.sleep(0)
    assert not drain.done()
    release.set()
    await drain
    restored = QueueManager(db_path=temp_db)
    assert await restored.load_from_db()
    restored.set_shuffle(False)
    assert [t.video_id for t in restored.items] == [t.video_id for t in sample_tracks if t.video_id != removed.video_id]
    assert restored.current_track.video_id == sample_tracks[1].video_id


@pytest.mark.asyncio
async def test_queue_step_tracks_single_step_multi_skip() -> None:
    """Verify step_tracks jumps across multiple tracks with a single index update & notification."""
    tracks = [
        Track(video_id=f"s{i}", title=f"Song {i}", artist="Artist") for i in range(6)
    ]
    notify_count = 0

    def _on_change() -> None:
        nonlocal notify_count
        notify_count += 1

    qm = QueueManager(autoplay_radio=False, on_queue_changed=_on_change)
    qm.set_queue(tracks, start_index=0)
    notify_count = 0

    # Skip forward 3 tracks in a single step
    target = await qm.step_tracks(steps=3, direction=1)
    assert target is not None and target.video_id == "s3"
    assert qm.current_index == 3
    assert notify_count == 1

    # Skip backward 2 tracks in a single step
    back = await qm.step_tracks(steps=2, direction=-1)
    assert back is not None and back.video_id == "s1"
    assert qm.current_index == 1
    assert notify_count == 2
