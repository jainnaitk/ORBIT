"""Library manager for MetroLite: liked songs, playlists, and history."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from metrolite.config import get_config
from metrolite.store.db import get_connection, get_default_db_path, init_db
from metrolite.ytm.client import Track

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def _normalize_track_dict(
    video_id: str, track_meta: Track | dict[str, Any] | None = None
) -> dict[str, Any]:
    """Extract standard metadata fields from Track model or dictionary."""
    if isinstance(track_meta, Track):
        return {
            "video_id": track_meta.video_id,
            "title": track_meta.title or "Unknown Title",
            "artist": track_meta.artist or "Unknown Artist",
            "album": track_meta.album,
            "duration_s": track_meta.duration_s or 0,
            "thumbnail_url": track_meta.thumbnail_url,
        }
    if isinstance(track_meta, dict):
        return {
            "video_id": track_meta.get("video_id", video_id),
            "title": track_meta.get("title") or "Unknown Title",
            "artist": track_meta.get("artist") or "Unknown Artist",
            "album": track_meta.get("album"),
            "duration_s": int(track_meta.get("duration_s") or 0),
            "thumbnail_url": track_meta.get("thumbnail_url"),
        }
    return {
        "video_id": video_id,
        "title": "Unknown Title",
        "artist": "Unknown Artist",
        "album": None,
        "duration_s": 0,
        "thumbnail_url": None,
    }


class LibraryManager:
    """Manages liked tracks, custom playlists, and playback history in SQLite."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        cfg = get_config()
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_limit = cfg.queue.history_limit

    async def ensure_db(self) -> None:
        await init_db(self.db_path)

    async def _upsert_track(
        self,
        conn: Any,
        video_id: str,
        track_meta: Track | dict[str, Any] | None = None,
    ) -> None:
        meta = _normalize_track_dict(video_id, track_meta)
        await conn.execute(
            """
            INSERT INTO tracks (video_id, title, artist, album, duration_s, thumbnail_url)
            VALUES (:video_id, :title, :artist, :album, :duration_s, :thumbnail_url)
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
                thumbnail_url = COALESCE(excluded.thumbnail_url, tracks.thumbnail_url);
            """,
            meta,
        )

    async def toggle_like(
        self, video_id: str, track_meta: Track | dict[str, Any] | None = None
    ) -> bool:
        async with get_connection(self.db_path) as conn:
            await self._upsert_track(conn, video_id, track_meta)

            cursor = await conn.execute(
                "SELECT is_liked FROM tracks WHERE video_id = ?;", (video_id,)
            )
            row = await cursor.fetchone()
            is_liked = bool(row["is_liked"]) if row else False

            new_liked_state = not is_liked
            now_iso = _utc_now_iso() if new_liked_state else None

            await conn.execute(
                """
                UPDATE tracks
                SET is_liked = ?, liked_at = ?
                WHERE video_id = ?;
                """,
                (1 if new_liked_state else 0, now_iso, video_id),
            )
            await conn.commit()
            logger.info("Track %s like state changed to %s", video_id, new_liked_state)
            return new_liked_state

    async def is_liked(self, video_id: str) -> bool:
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT is_liked FROM tracks WHERE video_id = ?;", (video_id,)
            )
            row = await cursor.fetchone()
            return bool(row["is_liked"]) if row else False

    async def list_liked(self, search_query: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT video_id, title, artist, album, duration_s, thumbnail_url,
                   is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                   play_count, last_played_at
            FROM tracks
            WHERE is_liked = 1
        """
        params: list[Any] = []

        if search_query and search_query.strip():
            escaped = (
                search_query.strip()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            q_clean = f"%{escaped}%"
            query += " AND (title LIKE ? ESCAPE '\\' OR artist LIKE ? ESCAPE '\\' OR album LIKE ? ESCAPE '\\')"
            params.extend([q_clean, q_clean, q_clean])

        query += " ORDER BY liked_at DESC;"

        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def create_playlist(self, name: str, description: str = "") -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Playlist name cannot be empty")

        playlist_id = f"pl_{uuid.uuid4().hex[:12]}"
        now = _utc_now_iso()

        async with get_connection(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO playlists (id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (playlist_id, clean_name, description.strip(), now, now),
            )
            await conn.commit()

        logger.info("Created playlist '%s' (%s)", clean_name, playlist_id)
        return {
            "id": playlist_id,
            "name": clean_name,
            "description": description.strip(),
            "track_count": 0,
            "created_at": now,
            "updated_at": now,
        }

    async def rename_playlist(
        self, playlist_id: str, name: str, description: str | None = None
    ) -> bool:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Playlist name cannot be empty")

        now = _utc_now_iso()
        async with get_connection(self.db_path) as conn:
            if description is not None:
                cursor = await conn.execute(
                    """
                    UPDATE playlists
                    SET name = ?, description = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (clean_name, description.strip(), now, playlist_id),
                )
            else:
                cursor = await conn.execute(
                    """
                    UPDATE playlists
                    SET name = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (clean_name, now, playlist_id),
                )
            await conn.commit()
            return cursor.rowcount > 0

    async def delete_playlist(self, playlist_id: str) -> bool:
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute("DELETE FROM playlists WHERE id = ?;", (playlist_id,))
            await conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info("Deleted playlist %s", playlist_id)
            return deleted

    async def list_playlists(self) -> list[dict[str, Any]]:
        query = """
            SELECT p.id, p.name, p.description, p.created_at, p.updated_at,
                   COUNT(pt.video_id) AS track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            GROUP BY p.id
            ORDER BY p.updated_at DESC;
        """
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_playlist(self, playlist_id: str) -> dict[str, Any] | None:
        query = """
            SELECT p.id, p.name, p.description, p.created_at, p.updated_at,
                   COUNT(pt.video_id) AS track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            WHERE p.id = ?
            GROUP BY p.id;
        """
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query, (playlist_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def add_to_playlist(
        self,
        playlist_id: str,
        tracks: list[Track | dict[str, Any] | str] | Track | dict[str, Any] | str,
    ) -> int:
        track_list = tracks if isinstance(tracks, list) else [tracks]
        if not track_list:
            return 0

        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute("SELECT id FROM playlists WHERE id = ?;", (playlist_id,))
            if not await cursor.fetchone():
                raise ValueError(f"Playlist {playlist_id} does not exist")

            pos_cursor = await conn.execute(
                "SELECT COALESCE(MAX(position), -1) AS max_pos FROM playlist_tracks WHERE playlist_id = ?;",
                (playlist_id,),
            )
            row = await pos_cursor.fetchone()
            next_pos = (row["max_pos"] if row else -1) + 1

            now = _utc_now_iso()
            added_count = 0

            for item in track_list:
                if isinstance(item, str):
                    v_id = item
                    t_meta: Track | dict[str, Any] | None = None
                elif isinstance(item, Track):
                    v_id = item.video_id
                    t_meta = item
                elif isinstance(item, dict):
                    v_id = item.get("video_id", "")
                    t_meta = item
                else:
                    continue

                if not v_id:
                    continue

                await self._upsert_track(conn, v_id, t_meta)

                ins_cursor = await conn.execute(
                    """
                    INSERT INTO playlist_tracks (playlist_id, video_id, position, added_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(playlist_id, video_id) DO NOTHING;
                    """,
                    (playlist_id, v_id, next_pos, now),
                )
                if ins_cursor.rowcount > 0:
                    added_count += 1
                    next_pos += 1

            if added_count > 0:
                await conn.execute(
                    "UPDATE playlists SET updated_at = ? WHERE id = ?;", (now, playlist_id)
                )

            await conn.commit()
            logger.info("Added %d tracks to playlist %s", added_count, playlist_id)
            return added_count

    async def remove_from_playlist(self, playlist_id: str, video_id: str) -> bool:
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND video_id = ?;",
                (playlist_id, video_id),
            )
            removed = cursor.rowcount > 0
            if removed:
                pos_cursor = await conn.execute(
                    "SELECT video_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position ASC;",
                    (playlist_id,),
                )
                rows = await pos_cursor.fetchall()
                now = _utc_now_iso()
                for idx, r in enumerate(rows):
                    await conn.execute(
                        "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND video_id = ?;",
                        (idx, playlist_id, r["video_id"]),
                    )
                await conn.execute(
                    "UPDATE playlists SET updated_at = ? WHERE id = ?;", (now, playlist_id)
                )
                await conn.commit()
                logger.info("Removed track %s from playlist %s", video_id, playlist_id)
            return removed

    async def reorder_playlist(
        self,
        playlist_id: str,
        video_id: str | None = None,
        new_position: int | None = None,
        ordered_video_ids: list[str] | None = None,
    ) -> bool:
        async with get_connection(self.db_path) as conn:
            pos_cursor = await conn.execute(
                "SELECT video_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position ASC;",
                (playlist_id,),
            )
            rows = await pos_cursor.fetchall()
            current_ids = [r["video_id"] for r in rows]

            if not current_ids:
                return False

            if ordered_video_ids is not None:
                new_order = [v for v in ordered_video_ids if v in current_ids]
                for v in current_ids:
                    if v not in new_order:
                        new_order.append(v)
            elif video_id is not None and new_position is not None:
                if video_id not in current_ids:
                    return False
                new_order = [v for v in current_ids if v != video_id]
                target_pos = max(0, min(new_position, len(new_order)))
                new_order.insert(target_pos, video_id)
            else:
                return False

            now = _utc_now_iso()
            for idx, v_id in enumerate(new_order):
                await conn.execute(
                    "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND video_id = ?;",
                    (idx, playlist_id, v_id),
                )
            await conn.execute(
                "UPDATE playlists SET updated_at = ? WHERE id = ?;", (now, playlist_id)
            )
            await conn.commit()
            return True

    async def get_playlist_tracks(self, playlist_id: str) -> list[dict[str, Any]]:
        query = """
            SELECT t.video_id, t.title, t.artist, t.album, t.duration_s, t.thumbnail_url,
                   t.is_liked, t.liked_at, t.local_path, t.file_size_bytes, t.downloaded_at,
                   t.play_count, t.last_played_at, pt.position, pt.added_at
            FROM playlist_tracks pt
            JOIN tracks t ON pt.video_id = t.video_id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position ASC;
        """
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query, (playlist_id,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def record_play(
        self, video_id: str, track_meta: Track | dict[str, Any] | None = None
    ) -> None:
        now = _utc_now_iso()
        async with get_connection(self.db_path) as conn:
            await self._upsert_track(conn, video_id, track_meta)
            await conn.execute(
                """
                UPDATE tracks
                SET play_count = play_count + 1,
                    last_played_at = ?
                WHERE video_id = ?;
                """,
                (now, video_id),
            )
            await conn.commit()
            logger.debug("Recorded play for track %s", video_id)

    async def get_history(self, limit: int | None = None) -> list[dict[str, Any]]:
        lim = limit or self.history_limit
        query = """
            SELECT video_id, title, artist, album, duration_s, thumbnail_url,
                   is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                   play_count, last_played_at
            FROM tracks
            WHERE play_count > 0 AND last_played_at IS NOT NULL
            ORDER BY last_played_at DESC
            LIMIT ?;
        """
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query, (lim,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_most_played(self, limit: int | None = None) -> list[dict[str, Any]]:
        """List user's most played tracks ranked by play_count descending."""
        lim = limit or self.history_limit
        query = """
            SELECT video_id, title, artist, album, duration_s, thumbnail_url,
                   is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                   play_count, last_played_at
            FROM tracks
            WHERE play_count > 0
            ORDER BY play_count DESC, last_played_at DESC
            LIMIT ?;
        """
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query, (lim,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_track(self, video_id: str) -> dict[str, Any] | None:
        query = """
            SELECT video_id, title, artist, album, duration_s, thumbnail_url,
                   is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                   play_count, last_played_at
            FROM tracks
            WHERE video_id = ?;
        """
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query, (video_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None
