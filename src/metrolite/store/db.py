"""SQLite database initialization and connection management for MetroLite."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path

import aiosqlite

from metrolite.config import get_config

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tracks (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    duration_s INTEGER NOT NULL DEFAULT 0,
    thumbnail_url TEXT,
    is_liked INTEGER NOT NULL DEFAULT 0,
    liked_at TEXT,
    local_path TEXT,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    downloaded_at TEXT,
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tracks_is_liked ON tracks(is_liked);
CREATE INDEX IF NOT EXISTS idx_tracks_local_path ON tracks(local_path);
CREATE INDEX IF NOT EXISTS idx_tracks_play_history ON tracks(play_count DESC, last_played_at DESC);
CREATE INDEX IF NOT EXISTS idx_tracks_last_played ON tracks(last_played_at DESC);

CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (playlist_id, video_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_playlist_tracks_pos ON playlist_tracks(playlist_id, position);

CREATE TABLE IF NOT EXISTS queue_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_index INTEGER NOT NULL DEFAULT 0,
    shuffle INTEGER NOT NULL DEFAULT 0,
    repeat_mode TEXT NOT NULL DEFAULT 'off',
    items_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recent_search_terms (
    id INTEGER PRIMARY KEY,
    term_key TEXT NOT NULL UNIQUE,
    term TEXT NOT NULL,
    edit_id TEXT,
    hidden_fragment INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recent_search_tracks (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL UNIQUE,
    track_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS play_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    played_at TEXT NOT NULL,
    listened_s INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_play_events_played_at ON play_events(played_at DESC);
CREATE INDEX IF NOT EXISTS idx_play_events_video_id ON play_events(video_id);

CREATE TABLE IF NOT EXISTS pinned_tracks (
    video_id TEXT PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0,
    pinned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocked_artists (
    artist_norm TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    blocked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followed_artists (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    artist_norm TEXT NOT NULL,
    thumbnail_url TEXT,
    followed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_followed_artists_norm ON followed_artists(artist_norm);

CREATE TABLE IF NOT EXISTS pinned_artists (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    thumbnail_url TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    pinned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artist_releases_cache (
    channel_id TEXT PRIMARY KEY,
    artist_name TEXT NOT NULL,
    releases_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

INSERT OR IGNORE INTO queue_state (id, current_index, shuffle, repeat_mode, items_json)
VALUES (1, 0, 0, 'off', '[]');
"""


def get_default_db_path() -> Path:
    """Return the default SQLite database path from configuration."""
    cfg = get_config()
    db_path = cfg.app.data_dir / "metrolite.db"
    return db_path


def _upgrade_search_terms(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(recent_search_terms)")}
    if "edit_id" in columns:
        return
    conn.execute("ALTER TABLE recent_search_terms ADD COLUMN edit_id TEXT")
    conn.execute("ALTER TABLE recent_search_terms ADD COLUMN hidden_fragment INTEGER NOT NULL DEFAULT 0")

    # Old live lookups had no edit ID. Hide only consecutive one-letter growth runs
    # starting at a single character (s, sh, shi, ...); keep their final name visible.
    run: list[int] = []
    previous = ""
    for row_id, key in conn.execute("SELECT id, term_key FROM recent_search_terms ORDER BY id").fetchall():
        if run and key.startswith(previous) and len(key) == len(previous) + 1:
            run.append(row_id)
            if len(run) == 3:
                conn.executemany("UPDATE recent_search_terms SET hidden_fragment = 1 WHERE id = ?", [(i,) for i in run[:2]])
            elif len(run) > 3:
                conn.execute("UPDATE recent_search_terms SET hidden_fragment = 1 WHERE id = ?", (run[-2],))
        else:
            run = [row_id] if len(key) == 1 else []
        previous = key


def init_db_sync(db_path: Path | str | None = None) -> Path:
    """Initialize database tables synchronously with crash-safe WAL recovery."""
    path = Path(db_path) if db_path is not None else get_default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        with conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute("BEGIN IMMEDIATE")
            _upgrade_search_terms(conn)
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_history'").fetchone():
                # Replay oldest first so the last spelling/selection wins independently in each list.
                for term, video_id, track_json in conn.execute(
                    "SELECT term, video_id, track_json FROM search_history ORDER BY id"
                ):
                    term = term.strip()
                    if term:
                        conn.execute("INSERT OR REPLACE INTO recent_search_terms(term_key, term) VALUES (?, ?)", (term.casefold(), term))
                    if video_id and track_json:
                        conn.execute("INSERT OR REPLACE INTO recent_search_tracks(video_id, track_json) VALUES (?, ?)", (video_id, track_json))
                conn.execute("DROP TABLE search_history")
        with suppress(Exception):
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()
    return path


async def init_db(db_path: Path | str | None = None) -> Path:
    """Initialize database tables asynchronously using aiosqlite with WAL recovery."""
    return await asyncio.to_thread(init_db_sync, db_path)


@asynccontextmanager
async def get_connection(db_path: Path | str | None = None) -> AsyncIterator[aiosqlite.Connection]:
    """Yield an async SQLite connection configured with busy_timeout, WAL synchronous and foreign keys."""
    path = Path(db_path) if db_path is not None else get_default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path), timeout=5.0) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 5000;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        await conn.execute("PRAGMA foreign_keys = ON;")
        yield conn


@contextmanager
def get_sync_connection(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a synchronous SQLite connection configured with busy_timeout, WAL synchronous and foreign keys."""
    path = Path(db_path) if db_path is not None else get_default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
    finally:
        conn.close()
