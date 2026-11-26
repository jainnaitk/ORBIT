"""Library manager for MetroLite: liked songs, playlists, and history."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from metrolite.config import get_config
from metrolite.store.db import get_connection, get_default_db_path, init_db
from metrolite.ytm.client import Track

logger = logging.getLogger(__name__)

_COMMA_ARTIST_EXCEPTIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Tyler,\s*The Creator", re.IGNORECASE),
    re.compile(r"Earth,\s*Wind\s*&\s*Fire", re.IGNORECASE),
    re.compile(r"Crosby,\s*Stills\s*(?:,\s*Nash(?:\s*&\s*Young)?)?", re.IGNORECASE),
    re.compile(r"Emerson,\s*Lake\s*&\s*Palmer", re.IGNORECASE),
    re.compile(r"Blood,\s*Sweat\s*&\s*Tears", re.IGNORECASE),
    re.compile(r"Peter,\s*Paul\s*(?:and|&)\s*Mary", re.IGNORECASE),
)

_STATS_RANGE_DAYS: dict[str, int | None] = {
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "all": None,
}


def normalize_artist_name(name: str | None) -> str:
    """Normalize an artist name consistently for blocking, follow matching, and dedupe."""
    if not name:
        return ""
    return " ".join(str(name).strip().lower().split())


def split_artist_names(artist_str: str | None) -> list[str]:
    """Split a combined artist string into individual credited artist names."""
    raw = (artist_str or "").strip()
    if not raw or raw.lower() == "unknown artist":
        return []

    placeholders: list[str] = []
    masked = raw
    for rx in _COMMA_ARTIST_EXCEPTIONS:
        def _repl(m: re.Match[str]) -> str:
            token = f"__ORBIT_ARTIST_{len(placeholders)}__"
            placeholders.append(m.group(0))
            return token
        masked = rx.sub(_repl, masked)

    normalized = re.sub(r"\s+(?:feat\.?|ft\.?|featuring)\s+", ", ", masked, flags=re.IGNORECASE)
    parts = re.split(r"\s*(?:,|•)\s*", normalized)

    out: list[str] = []
    seen: set[str] = set()
    for seg in parts:
        restored = seg.strip()
        for idx, orig in enumerate(placeholders):
            restored = restored.replace(f"__ORBIT_ARTIST_{idx}__", orig)
        norm = normalize_artist_name(restored)
        if not norm or norm == "unknown artist" or norm in seen:
            continue
        seen.add(norm)
        out.append(restored)
    return out


def artist_matches_blocked(artist_str: str | None, blocked_norms: set[str]) -> bool:
    """Return True if the full artist string or any collaborating credit is blocked."""
    if not blocked_norms or not artist_str:
        return False
    full_norm = normalize_artist_name(artist_str)
    if full_norm in blocked_norms:
        return True
    for part in split_artist_names(artist_str):
        if normalize_artist_name(part) in blocked_norms:
            return True
    return False


def make_synthetic_channel_id(name: str) -> str:
    """Generate a deterministic local channel key when an artist lacks a YTM channelId."""
    return f"name:{normalize_artist_name(name)}"


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

    async def list_offline_tracks(self, limit: int = 20) -> list[dict[str, Any]]:
        """List downloaded offline tracks ordered by download recency."""
        query = """
            SELECT video_id, title, artist, album, duration_s, thumbnail_url,
                   is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                   play_count, last_played_at
            FROM tracks
            WHERE local_path IS NOT NULL AND local_path != ''
            ORDER BY downloaded_at DESC, play_count DESC
            LIMIT ?;
        """
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query, (limit,))
            rows = await cursor.fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                if d.get("local_path") and Path(d["local_path"]).is_file():
                    out.append(d)
            return out

    async def get_forgotten_favorites(self, limit: int = 12) -> list[dict[str, Any]]:
        """Return liked or previously played tracks not played most recently."""
        query = """
            SELECT video_id, title, artist, album, duration_s, thumbnail_url,
                   is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                   play_count, last_played_at
            FROM tracks
            WHERE (is_liked = 1 OR play_count >= 2)
            ORDER BY COALESCE(last_played_at, '') ASC, play_count DESC
            LIMIT ?;
        """
        async with get_connection(self.db_path) as conn:
            cursor = await conn.execute(query, (limit,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Speed Dial Pins (Tracks & Artists)
    # -------------------------------------------------------------------------

    async def get_pins(self) -> dict[str, list[dict[str, Any]]]:
        """Return all pinned Speed Dial tracks and pinned artists ordered cleanly."""
        async with get_connection(self.db_path) as conn:
            t_cur = await conn.execute(
                """
                SELECT t.video_id, t.title, t.artist, t.album, t.duration_s, t.thumbnail_url,
                       t.is_liked, t.liked_at, t.local_path, t.file_size_bytes, t.downloaded_at,
                       t.play_count, t.last_played_at, pt.position, pt.pinned_at
                FROM pinned_tracks pt
                JOIN tracks t ON t.video_id = pt.video_id
                ORDER BY pt.position ASC, pt.pinned_at DESC;
                """
            )
            pinned_tracks = [dict(row) for row in await t_cur.fetchall()]

            a_cur = await conn.execute(
                """
                SELECT channel_id, name, thumbnail_url, position, pinned_at
                FROM pinned_artists
                ORDER BY position ASC, pinned_at DESC;
                """
            )
            pinned_artists = [dict(row) for row in await a_cur.fetchall()]

        return {"tracks": pinned_tracks, "artists": pinned_artists}

    async def pin_track(
        self, video_id: str, track_meta: Track | dict[str, Any] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Pin a track to the Home Speed Dial."""
        now = _utc_now_iso()
        async with get_connection(self.db_path) as conn:
            await self._upsert_track(conn, video_id, track_meta)
            pos_cur = await conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM pinned_tracks;")
            row = await pos_cur.fetchone()
            next_pos = int(row["next_pos"]) if row else 0
            await conn.execute(
                """
                INSERT INTO pinned_tracks (video_id, position, pinned_at)
                VALUES (?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET pinned_at = excluded.pinned_at;
                """,
                (video_id, next_pos, now),
            )
            await conn.commit()
        return await self.get_pins()

    async def unpin_track(self, video_id: str) -> bool:
        """Remove a track from Home Speed Dial pins."""
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute("DELETE FROM pinned_tracks WHERE video_id = ?;", (video_id,))
            await conn.commit()
            return cur.rowcount > 0

    async def pin_artist(
        self, channel_id: str | None, name: str, thumbnail_url: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Pin a favorite artist to the Home Speed Dial."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Artist name cannot be empty")
        cid = (channel_id or "").strip() or make_synthetic_channel_id(clean_name)
        now = _utc_now_iso()
        async with get_connection(self.db_path) as conn:
            pos_cur = await conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM pinned_artists;")
            row = await pos_cur.fetchone()
            next_pos = int(row["next_pos"]) if row else 0
            await conn.execute(
                """
                INSERT INTO pinned_artists (channel_id, name, thumbnail_url, position, pinned_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    name = excluded.name,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, pinned_artists.thumbnail_url),
                    pinned_at = excluded.pinned_at;
                """,
                (cid, clean_name, thumbnail_url, next_pos, now),
            )
            await conn.commit()
        return await self.get_pins()

    async def unpin_artist(self, channel_id_or_name: str) -> bool:
        """Unpin an artist by channel_id or synthetic name key."""
        raw = channel_id_or_name.strip()
        synth = make_synthetic_channel_id(raw)
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute(
                "DELETE FROM pinned_artists WHERE channel_id = ? OR channel_id = ? OR LOWER(name) = LOWER(?);",
                (raw, synth, raw),
            )
            await conn.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Blocked Artists
    # -------------------------------------------------------------------------

    async def list_blocked_artists(self) -> list[dict[str, Any]]:
        """List all blocked artists ordered by most recently blocked."""
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute(
                "SELECT artist_norm, display_name, blocked_at FROM blocked_artists ORDER BY blocked_at DESC;"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_blocked_artist_norms(self) -> set[str]:
        """Return normalized blocked artist names for fast radio and Home feed filtering."""
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute("SELECT artist_norm FROM blocked_artists;")
            return {str(r["artist_norm"]) for r in await cur.fetchall() if r["artist_norm"]}

    async def block_artist(self, name: str) -> dict[str, Any]:
        """Add an artist to the normalized blocklist."""
        display_name = name.strip()
        norm = normalize_artist_name(display_name)
        if not norm:
            raise ValueError("Artist name cannot be empty")
        now = _utc_now_iso()
        async with get_connection(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO blocked_artists (artist_norm, display_name, blocked_at)
                VALUES (?, ?, ?)
                ON CONFLICT(artist_norm) DO UPDATE SET
                    display_name = excluded.display_name,
                    blocked_at = excluded.blocked_at;
                """,
                (norm, display_name, now),
            )
            await conn.commit()
        return {"artist_norm": norm, "display_name": display_name, "blocked_at": now}

    async def unblock_artist(self, name_or_norm: str) -> bool:
        """Remove an artist from the blocklist."""
        norm = normalize_artist_name(name_or_norm)
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute(
                "DELETE FROM blocked_artists WHERE artist_norm = ? OR artist_norm = ?;",
                (name_or_norm.strip(), norm),
            )
            await conn.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # History Removal & Track Metadata Editing
    # -------------------------------------------------------------------------

    async def remove_from_history(self, video_id: str) -> bool:
        """Clear a track's play history fields so it no longer appears in History."""
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute(
                """
                UPDATE tracks
                SET play_count = 0,
                    last_played_at = NULL
                WHERE video_id = ?;
                """,
                (video_id,),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def update_track_metadata(
        self,
        video_id: str,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
    ) -> dict[str, Any] | None:
        """Update local title/artist/album metadata for a track in SQLite."""
        clean_title = title.strip() if isinstance(title, str) and title.strip() else None
        clean_artist = artist.strip() if isinstance(artist, str) and artist.strip() else None
        clean_album = album.strip() if isinstance(album, str) else None

        async with get_connection(self.db_path) as conn:
            existing = await conn.execute(
                "SELECT video_id, title, artist, album FROM tracks WHERE video_id = ?;",
                (video_id,),
            )
            row = await existing.fetchone()
            if row is None:
                await conn.execute(
                    """
                    INSERT INTO tracks (video_id, title, artist, album)
                    VALUES (?, ?, ?, ?);
                    """,
                    (
                        video_id,
                        clean_title or "Unknown Title",
                        clean_artist or "Unknown Artist",
                        clean_album if clean_album != "" else None,
                    ),
                )
            else:
                next_title = clean_title if clean_title is not None else row["title"]
                next_artist = clean_artist if clean_artist is not None else row["artist"]
                next_album = (
                    (clean_album or None) if clean_album is not None else row["album"]
                )
                await conn.execute(
                    """
                    UPDATE tracks
                    SET title = ?, artist = ?, album = ?
                    WHERE video_id = ?;
                    """,
                    (next_title, next_artist, next_album, video_id),
                )
            await conn.commit()
        return await self.get_track(video_id)

    # -------------------------------------------------------------------------
    # Qualified Play Events & Listening Statistics
    # -------------------------------------------------------------------------

    async def record_play_event(
        self,
        video_id: str,
        listened_s: int = 30,
        played_at: str | None = None,
        track_meta: Track | dict[str, Any] | None = None,
    ) -> int:
        """Insert a qualified listening play event into play_events."""
        ts = played_at or _utc_now_iso()
        sec = max(0, int(listened_s))
        async with get_connection(self.db_path) as conn:
            if track_meta is not None:
                await self._upsert_track(conn, video_id, track_meta)
            else:
                await conn.execute(
                    "INSERT OR IGNORE INTO tracks (video_id, title, artist) VALUES (?, ?, ?);",
                    (video_id, video_id, "Unknown Artist"),
                )
            cur = await conn.execute(
                "INSERT INTO play_events (video_id, played_at, listened_s) VALUES (?, ?, ?);",
                (video_id, ts, sec),
            )
            await conn.commit()
            return int(cur.lastrowid or 0)

    async def update_play_event_duration(self, event_id: int, listened_s: int) -> None:
        """Update listened_s on an active play event row without duplicating plays."""
        if event_id <= 0:
            return
        sec = max(0, int(listened_s))
        async with get_connection(self.db_path) as conn:
            await conn.execute(
                "UPDATE play_events SET listened_s = MAX(listened_s, ?) WHERE id = ?;",
                (sec, event_id),
            )
            await conn.commit()

    async def get_stats(self, range_key: str = "1m") -> dict[str, Any]:
        """Aggregate total plays, total listening time, top tracks, and top artists for a range."""
        key = (range_key or "1m").lower().strip()
        if key not in _STATS_RANGE_DAYS:
            raise ValueError(f"Invalid stats range: {range_key!r}")

        days = _STATS_RANGE_DAYS[key]
        where_clause = ""
        params: list[Any] = []
        if days is not None:
            cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            where_clause = "WHERE pe.played_at >= ?"
            params.append(cutoff)

        async with get_connection(self.db_path) as conn:
            sum_cur = await conn.execute(
                f"""
                SELECT COUNT(*) AS total_plays,
                       COALESCE(SUM(pe.listened_s), 0) AS total_seconds
                FROM play_events pe
                {where_clause};
                """,
                params,
            )
            sum_row = await sum_cur.fetchone()
            total_plays = int(sum_row["total_plays"]) if sum_row else 0
            total_seconds = int(sum_row["total_seconds"]) if sum_row else 0

            tracks_cur = await conn.execute(
                f"""
                SELECT pe.video_id,
                       COALESCE(t.title, pe.video_id) AS title,
                       COALESCE(t.artist, 'Unknown Artist') AS artist,
                       t.album,
                       COALESCE(t.duration_s, 0) AS duration_s,
                       t.thumbnail_url,
                       COALESCE(t.is_liked, 0) AS is_liked,
                       t.local_path,
                       COUNT(pe.id) AS plays,
                       COALESCE(SUM(pe.listened_s), 0) AS seconds
                FROM play_events pe
                LEFT JOIN tracks t ON t.video_id = pe.video_id
                {where_clause}
                GROUP BY pe.video_id
                ORDER BY plays DESC, seconds DESC;
                """,
                params,
            )
            all_track_rows = [dict(r) for r in await tracks_cur.fetchall()]

            fav_cur = await conn.execute("SELECT name, artist_norm, thumbnail_url FROM followed_artists;")
            followed_thumbs = {
                str(r["artist_norm"]): r["thumbnail_url"]
                for r in await fav_cur.fetchall()
                if r["artist_norm"]
            }

        top_tracks = all_track_rows[:25]

        artist_agg: dict[str, dict[str, Any]] = {}
        for row in all_track_rows:
            credits = split_artist_names(row.get("artist")) or [
                str(row.get("artist") or "Unknown Artist")
            ]
            row_plays = int(row.get("plays") or 0)
            row_secs = int(row.get("seconds") or 0)
            thumb = row.get("thumbnail_url")
            for name in credits:
                norm = normalize_artist_name(name)
                if not norm or norm == "unknown artist":
                    continue
                bucket = artist_agg.get(norm)
                if bucket is None:
                    artist_agg[norm] = {
                        "artist": name,
                        "plays": row_plays,
                        "seconds": row_secs,
                        "thumbnail_url": followed_thumbs.get(norm) or thumb,
                    }
                else:
                    bucket["plays"] += row_plays
                    bucket["seconds"] += row_secs
                    if not bucket["thumbnail_url"] and thumb:
                        bucket["thumbnail_url"] = thumb

        top_artists = sorted(
            artist_agg.values(),
            key=lambda item: (item["plays"], item["seconds"]),
            reverse=True,
        )[:25]

        return {
            "range": key,
            "total_plays": total_plays,
            "total_seconds": total_seconds,
            "top_tracks": top_tracks,
            "top_artists": top_artists,
        }

    # -------------------------------------------------------------------------
    # Full Artist Library (Follow, Local Songs, In-Library Discovery, Releases Cache)
    # -------------------------------------------------------------------------

    async def follow_artist(
        self,
        channel_id: str | None,
        name: str,
        thumbnail_url: str | None = None,
    ) -> dict[str, Any]:
        """Follow / save an artist into the user's Artist Library."""
        clean_name = name.strip()
        norm = normalize_artist_name(clean_name)
        if not norm:
            raise ValueError("Artist name cannot be empty")
        cid = (channel_id or "").strip() or make_synthetic_channel_id(clean_name)
        now = _utc_now_iso()
        async with get_connection(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO followed_artists (channel_id, name, artist_norm, thumbnail_url, followed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    name = excluded.name,
                    artist_norm = excluded.artist_norm,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, followed_artists.thumbnail_url),
                    followed_at = excluded.followed_at;
                """,
                (cid, clean_name, norm, thumbnail_url, now),
            )
            await conn.commit()
        return {
            "channel_id": cid,
            "name": clean_name,
            "artist_norm": norm,
            "thumbnail_url": thumbnail_url,
            "followed_at": now,
        }

    async def unfollow_artist(self, channel_id_or_name: str) -> bool:
        """Unfollow an artist by channel_id or normalized name."""
        raw = channel_id_or_name.strip()
        norm = normalize_artist_name(raw)
        synth = make_synthetic_channel_id(raw)
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute(
                "DELETE FROM followed_artists WHERE channel_id = ? OR channel_id = ? OR artist_norm = ?;",
                (raw, synth, norm),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def is_artist_followed(
        self, channel_id: str | None = None, name: str | None = None
    ) -> bool:
        """Check whether an artist is currently followed."""
        cid = (channel_id or "").strip()
        norm = normalize_artist_name(name)
        async with get_connection(self.db_path) as conn:
            if cid and norm:
                cur = await conn.execute(
                    "SELECT 1 FROM followed_artists WHERE channel_id = ? OR artist_norm = ? LIMIT 1;",
                    (cid, norm),
                )
            elif cid:
                cur = await conn.execute(
                    "SELECT 1 FROM followed_artists WHERE channel_id = ? LIMIT 1;", (cid,)
                )
            elif norm:
                cur = await conn.execute(
                    "SELECT 1 FROM followed_artists WHERE artist_norm = ? LIMIT 1;", (norm,)
                )
            else:
                return False
            return (await cur.fetchone()) is not None

    async def list_followed_artists(self) -> list[dict[str, Any]]:
        """Return all explicitly followed artists."""
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute(
                """
                SELECT channel_id, name, artist_norm, thumbnail_url, followed_at
                FROM followed_artists
                ORDER BY followed_at DESC;
                """
            )
            return [dict(r) for r in await cur.fetchall()]

    async def list_artists_library(self) -> dict[str, Any]:
        """Return followed artists + auto-derived in-library artists from Liked & Offline tracks."""
        async with get_connection(self.db_path) as conn:
            f_cur = await conn.execute(
                """
                SELECT channel_id, name, artist_norm, thumbnail_url, followed_at
                FROM followed_artists
                ORDER BY followed_at DESC;
                """
            )
            followed = [dict(r) for r in await f_cur.fetchall()]
            followed_by_norm = {item["artist_norm"]: item for item in followed}

            t_cur = await conn.execute(
                """
                SELECT video_id, artist, thumbnail_url, is_liked, local_path, play_count
                FROM tracks
                WHERE is_liked = 1 OR (local_path IS NOT NULL AND local_path != '');
                """
            )
            track_rows = await t_cur.fetchall()

        library_map: dict[str, dict[str, Any]] = {}
        for row in track_rows:
            credits = split_artist_names(row["artist"])
            if not credits:
                continue
            is_lk = bool(row["is_liked"])
            has_dl = bool(row["local_path"])
            plays = int(row["play_count"] or 0)
            thumb = row["thumbnail_url"]
            for credit in credits:
                norm = normalize_artist_name(credit)
                if not norm:
                    continue
                entry = library_map.get(norm)
                followed_hit = followed_by_norm.get(norm)
                if entry is None:
                    library_map[norm] = {
                        "name": followed_hit["name"] if followed_hit else credit,
                        "artist_norm": norm,
                        "channel_id": followed_hit["channel_id"] if followed_hit else None,
                        "thumbnail_url": (followed_hit["thumbnail_url"] if followed_hit else None) or thumb,
                        "track_count": 1,
                        "liked_count": 1 if is_lk else 0,
                        "offline_count": 1 if has_dl else 0,
                        "play_count": plays,
                        "is_followed": followed_hit is not None,
                    }
                else:
                    entry["track_count"] += 1
                    if is_lk:
                        entry["liked_count"] += 1
                    if has_dl:
                        entry["offline_count"] += 1
                    entry["play_count"] += plays
                    if not entry["thumbnail_url"] and thumb:
                        entry["thumbnail_url"] = thumb

        in_library = sorted(
            library_map.values(),
            key=lambda a: (a["liked_count"] + a["offline_count"], a["play_count"], a["name"].lower()),
            reverse=True,
        )
        # Keep display names consistent for tie-breaking if needed
        return {"followed": followed, "in_library": in_library}

    async def get_local_artist_tracks(
        self, name: str | None = None, channel_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return Liked, Offline, and Most Played local tracks matching an artist (< 5ms)."""
        target_name = (name or "").strip()
        async with get_connection(self.db_path) as conn:
            if not target_name and channel_id:
                for tbl in ("followed_artists", "pinned_artists"):
                    c = await conn.execute(
                        f"SELECT name FROM {tbl} WHERE channel_id = ? LIMIT 1;", (channel_id,)
                    )
                    r = await c.fetchone()
                    if r and r["name"]:
                        target_name = str(r["name"])
                        break
            target_norm = normalize_artist_name(target_name)
            if not target_norm:
                return []

            escaped = (
                target_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            cur = await conn.execute(
                """
                SELECT video_id, title, artist, album, duration_s, thumbnail_url,
                       is_liked, liked_at, local_path, file_size_bytes, downloaded_at,
                       play_count, last_played_at
                FROM tracks
                WHERE artist LIKE ? ESCAPE '\\'
                  AND (is_liked = 1 OR (local_path IS NOT NULL AND local_path != '') OR play_count > 0)
                ORDER BY (local_path IS NOT NULL AND local_path != '') DESC,
                         is_liked DESC,
                         play_count DESC,
                         last_played_at DESC
                LIMIT 120;
                """,
                (f"%{escaped}%",),
            )
            rows = await cur.fetchall()

        matched: list[dict[str, Any]] = []
        for row in rows:
            credits = [normalize_artist_name(c) for c in split_artist_names(row["artist"])]
            if target_norm in credits or normalize_artist_name(row["artist"]) == target_norm:
                matched.append(dict(row))
        return matched

    async def get_cached_artist_releases(self) -> list[dict[str, Any]]:
        """Read cached latest releases for all currently followed artists from SQLite."""
        async with get_connection(self.db_path) as conn:
            cur = await conn.execute(
                """
                SELECT c.channel_id, c.artist_name, c.releases_json, c.fetched_at,
                       f.thumbnail_url AS artist_thumbnail
                FROM artist_releases_cache c
                JOIN followed_artists f ON f.channel_id = c.channel_id
                ORDER BY f.followed_at DESC;
                """
            )
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                releases = json.loads(r["releases_json"] or "[]")
            except json.JSONDecodeError:
                releases = []
            if not isinstance(releases, list):
                releases = []
            out.append(
                {
                    "channel_id": r["channel_id"],
                    "artist_name": r["artist_name"],
                    "fetched_at": r["fetched_at"],
                    "releases": releases,
                }
            )
        return out

    async def upsert_cached_artist_releases(
        self,
        channel_id: str,
        artist_name: str,
        releases: list[dict[str, Any]],
        fetched_at: str | None = None,
    ) -> None:
        """Persist cached releases for a followed artist."""
        ts = fetched_at or _utc_now_iso()
        payload_json = json.dumps(releases)
        async with get_connection(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO artist_releases_cache (channel_id, artist_name, releases_json, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    artist_name = excluded.artist_name,
                    releases_json = excluded.releases_json,
                    fetched_at = excluded.fetched_at;
                """,
                (channel_id, artist_name, payload_json, ts),
            )
            await conn.commit()
