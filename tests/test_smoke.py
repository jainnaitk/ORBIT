"""Smoke tests for MetroLite configuration, SQLite schema, and CLI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from metrolite.config import get_config, load_config
from metrolite.library.manager import LibraryManager
from metrolite.main import main
from metrolite.store.db import (
    get_connection,
    get_sync_connection,
    init_db,
    init_db_sync,
)

EXPECTED_TABLES = {"tracks", "playlists", "playlist_tracks", "queue_state", "settings"}


def test_config_loading_defaults() -> None:
    """Test loading configuration from default config.yaml."""
    cfg = get_config(reload=True)
    assert cfg.app.name == "MetroLite"
    assert "%LOCALAPPDATA%" not in str(cfg.app.data_dir)
    assert "%LOCALAPPDATA%" not in str(cfg.app.downloads_dir)
    assert cfg.audio.quality == "high"
    assert cfg.audio.gapless is True
    assert cfg.bridge.port == 8766
    assert cfg.ui.close_to_tray is True


def test_config_custom_yaml(tmp_path: Path) -> None:
    """Test loading configuration from a custom YAML file."""
    custom_yaml = tmp_path / "custom_config.yaml"
    custom_yaml.write_text(
        """
app:
  name: CustomMetro
  ram_budget_active_mb: 150
bridge:
  port: 9999
""",
        encoding="utf-8",
    )

    cfg = load_config(custom_yaml)
    assert cfg.app.name == "CustomMetro"
    assert cfg.app.ram_budget_active_mb == 150
    assert cfg.bridge.port == 9999
    # Unspecified fields use model defaults
    assert cfg.audio.quality == "high"


def test_sqlite_sync_initialization(tmp_path: Path) -> None:
    """Test synchronous SQLite schema creation and table verification."""
    db_file = tmp_path / "test_metrolite_sync.db"
    init_db_sync(db_file)

    with get_sync_connection(db_file) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row["name"] for row in cursor.fetchall()}
        assert EXPECTED_TABLES.issubset(tables)

        # Check default queue_state row
        cursor = conn.execute("SELECT * FROM queue_state WHERE id = 1;")
        row = cursor.fetchone()
        assert row is not None
        assert row["id"] == 1
        assert row["repeat_mode"] == "off"
        assert json.loads(row["items_json"]) == []

        # Check settings table write and read
        conn.execute(
            "INSERT INTO settings (key, value_json) VALUES (?, ?);",
            ("test_key", json.dumps({"active": True})),
        )
        conn.commit()
        cursor = conn.execute("SELECT value_json FROM settings WHERE key = ?;", ("test_key",))
        result = cursor.fetchone()
        assert result is not None
        assert json.loads(result["value_json"]) == {"active": True}


@pytest.mark.asyncio
async def test_sqlite_async_initialization(tmp_path: Path) -> None:
    """Test asynchronous SQLite schema creation with aiosqlite."""
    db_file = tmp_path / "test_metrolite_async.db"
    await init_db(db_file)

    async with get_connection(db_file) as conn:
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        rows = await cursor.fetchall()
        tables = {row["name"] for row in rows}
        assert EXPECTED_TABLES.issubset(tables)

        # Check tracks insert and query
        await conn.execute(
            """
            INSERT INTO tracks (
                video_id, title, artist, album, duration_s, thumbnail_url,
                is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                play_count, last_played_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "vid123",
                "Test Song",
                "Test Artist",
                "Test Album",
                210,
                "https://example.com/thumb.jpg",
                1,
                "2026-11-18T12:00:00Z",
                None,
                0,
                None,
                5,
                "2026-11-18T12:30:00Z",
            ),
        )
        await conn.commit()

        cursor = await conn.execute(
            "SELECT title, artist, is_liked FROM tracks WHERE video_id = ?;", ("vid123",)
        )
        track = await cursor.fetchone()
        assert track is not None
        assert track["title"] == "Test Song"
        assert track["artist"] == "Test Artist"
        assert track["is_liked"] == 1


async def test_search_history_upgrade_preserves_both_orders(tmp_path: Path) -> None:
    db_file = tmp_path / "upgrade.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE search_history(id INTEGER PRIMARY KEY, term TEXT, video_id TEXT, track_json TEXT)")
        conn.executemany("INSERT INTO search_history(term, video_id, track_json) VALUES (?, ?, ?)", [
            ("Jazz", "a", '{"video_id":"a"}'), ("ambient", "", None),
            ("JAZZ", "b", '{"video_id":"b"}'), ("other", "a", '{"video_id":"a"}'),
        ])
    conn.close()
    await init_db(db_file)
    init_db_sync(db_file)
    history = await LibraryManager(db_file).search_history()
    assert history == {"terms": ["other", "JAZZ", "ambient"], "tracks": [{"video_id": "a"}, {"video_id": "b"}]}


async def test_upgrade_hides_existing_typing_chain_without_erasing_terms(tmp_path: Path) -> None:
    db = tmp_path / "search.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE recent_search_terms(id INTEGER PRIMARY KEY, term_key TEXT UNIQUE, term TEXT NOT NULL)")
        terms = ["palpal", "s", "sh", "shi", "shiv", "shiva", "shivaa", "a", "ambient"]
        conn.executemany("INSERT INTO recent_search_terms(term_key, term) VALUES (?, ?)", [(t, t) for t in terms])
    conn.close()
    init_db_sync(db)
    lib = LibraryManager(db)
    assert (await lib.search_history())["terms"] == ["ambient", "a", "shivaa", "palpal"]
    with get_sync_connection(db) as conn:
        assert conn.execute("SELECT count(*) FROM recent_search_terms").fetchone()[0] == len(terms)
    await lib.remember_search_term("sh")
    await init_db(db)
    assert (await lib.search_history())["terms"][0] == "sh"


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Test CLI --help exit code and help message output."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "MetroLite: Lightweight Windows desktop YouTube Music client." in captured.out


def test_cli_subcommands(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """Test CLI `run` and `play` subcommands."""
    tmp_dir_str = str(tmp_path).replace("\\", "/")
    custom_yaml = tmp_path / "cli_config.yaml"
    custom_yaml.write_text(
        f"""
app:
  name: CliTest
  data_dir: "{tmp_dir_str}"
bridge:
  host: "127.0.0.1"
  port: 8999
""",
        encoding="utf-8",
    )

    # Test run subcommand
    rc = main(["--config", str(custom_yaml), "run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Starting CliTest backend on 127.0.0.1:8999" in captured.out

    # Test play subcommand
    rc = main(["--config", str(custom_yaml), "play", "Never Gonna Give You Up"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "[CliTest] Playing query: Never Gonna Give You Up" in captured.out
