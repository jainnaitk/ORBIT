"""SQLite database initialization and connection management for MetroLite."""

from __future__ import annotations

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
        with suppress(Exception):
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()
    return path


async def init_db(db_path: Path | str | None = None) -> Path:
    """Initialize database tables asynchronously using aiosqlite with WAL recovery."""
    path = Path(db_path) if db_path is not None else get_default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path), timeout=5.0) as conn:
        await conn.execute("PRAGMA busy_timeout = 5000;")
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()
        with suppress(Exception):
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    return path


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
