"""YouTube Music client and Track normalization for MetroLite."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import requests
from pydantic import BaseModel, Field
from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicError, YTMusicServerError

from metrolite.config import get_config

logger = logging.getLogger(__name__)

T = TypeVar("T")


class YTMError(Exception):
    """Base exception for YouTube Music API errors."""


class YTMNetworkError(YTMError):
    """Network connection or timeout error contacting YouTube Music."""


class Track(BaseModel):
    """Normalized metadata for a YouTube Music track."""

    video_id: str = Field(description="Unique YouTube video identifier")
    title: str = Field(default="Unknown Title", description="Track title")
    artist: str = Field(default="Unknown Artist", description="Artist or comma-separated artists")
    artist_id: str | None = Field(default=None, description="Primary YouTube Music artist channel ID")
    album: str | None = Field(default=None, description="Album name")
    album_id: str | None = Field(default=None, description="YouTube Music album browse ID")
    duration_s: int = Field(default=0, description="Duration in seconds")
    thumbnail_url: str | None = Field(default=None, description="Preferred artwork URL")


def parse_duration(val: Any) -> int:
    """Parse duration into integer seconds from int, float, or mm:ss/hh:mm:ss string."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return max(0, int(val))
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return 0
        if val.isdigit():
            return int(val)
        parts = val.split(":")
        try:
            if len(parts) == 1:
                return int(parts[0])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except (ValueError, TypeError):
            return 0
    return 0


def extract_thumbnail_url(raw: Any) -> str | None:
    """Extract the best thumbnail URL, preferring 120-240px per MetroLite UI spec."""
    if not raw:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        url = raw.get("url")
        return str(url).strip() if url else None
    if isinstance(raw, list):
        valid = [item for item in raw if isinstance(item, dict) and item.get("url")]
        if not valid:
            for item in raw:
                if isinstance(item, str) and item.strip():
                    return item.strip()
            return None

        # Sort by width if available
        with_width = [t for t in valid if isinstance(t.get("width"), (int, float))]
        if with_width:
            with_width.sort(key=lambda t: t.get("width", 0))
            # Prefer 120-300px range for list items and cards
            in_range = [t for t in with_width if 120 <= t.get("width", 0) <= 300]
            if in_range:
                return str(in_range[-1]["url"]).strip()
            # Otherwise find the closest width to 120
            return str(min(with_width, key=lambda t: abs(t.get("width", 0) - 120))["url"]).strip()

        return str(valid[-1]["url"]).strip()
    return None


def extract_artist(raw: Any) -> str:
    """Extract comma-separated artist name(s) from varied ytmusicapi structures."""
    if not raw:
        return "Unknown Artist"
    if isinstance(raw, str):
        return raw.strip() or "Unknown Artist"
    if isinstance(raw, list):
        names: list[str] = []
        for item in raw:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    names.append(cleaned)
            elif isinstance(item, dict) and "name" in item:
                cleaned = str(item["name"]).strip()
                if cleaned:
                    names.append(cleaned)
        if names:
            return ", ".join(names)
    elif isinstance(raw, dict) and "name" in raw:
        return str(raw["name"]).strip() or "Unknown Artist"
    return "Unknown Artist"


def extract_album(raw: Any) -> str | None:
    """Extract album name from string or dictionary."""
    if not raw:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        name = raw.get("name") or raw.get("title")
        if name:
            return str(name).strip() or None
    return None


def extract_title(raw: Any) -> str:
    """Extract title from string, dict, or runs list."""
    if not raw:
        return "Unknown Title"
    if isinstance(raw, str):
        return raw.strip() or "Unknown Title"
    if isinstance(raw, dict):
        if "text" in raw:
            return str(raw["text"]).strip() or "Unknown Title"
        if "title" in raw:
            return extract_title(raw["title"])
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, str):
            return first.strip() or "Unknown Title"
        if isinstance(first, dict) and "text" in first:
            return str(first["text"]).strip() or "Unknown Title"
    return "Unknown Title"


def extract_artist_id(raw: Any) -> str | None:
    """Extract the primary artist browse/channel ID from ytmusicapi artists field."""
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                val = str(item["id"]).strip()
                if val:
                    return val
    elif isinstance(raw, dict) and raw.get("id"):
        val = str(raw["id"]).strip()
        if val:
            return val
    return None


def extract_album_id(raw: Any) -> str | None:
    """Extract album browse ID from ytmusicapi album field."""
    if isinstance(raw, dict) and raw.get("id"):
        val = str(raw["id"]).strip()
        if val:
            return val
    return None


def normalize_track(raw: dict[str, Any], default_album: str | None = None) -> Track:
    """Normalize a raw dictionary from ytmusicapi into a clean Track model.

    Raises:
        ValueError: If videoId is missing or empty.
    """
    video_id = raw.get("videoId") or raw.get("id")
    if not video_id:
        raise ValueError(f"Track missing videoId: {raw}")
    video_id_str = str(video_id).strip()
    if not video_id_str:
        raise ValueError(f"Empty videoId in track: {raw}")

    title = extract_title(raw.get("title"))
    raw_artists = raw.get("artists") or raw.get("artist")
    artist = extract_artist(raw_artists)
    artist_id = raw.get("artist_id") or extract_artist_id(raw_artists)
    raw_album = raw.get("album")
    album = extract_album(raw_album) or default_album
    album_id = raw.get("album_id") or extract_album_id(raw_album)
    duration_s = parse_duration(
        raw.get("duration_seconds") or raw.get("duration") or raw.get("length")
    )
    thumbnail_url = extract_thumbnail_url(raw.get("thumbnails") or raw.get("thumbnail"))

    return Track(
        video_id=video_id_str,
        title=title,
        artist=artist,
        artist_id=str(artist_id).strip() if artist_id else None,
        album=album,
        album_id=str(album_id).strip() if album_id else None,
        duration_s=duration_s,
        thumbnail_url=thumbnail_url,
    )


def try_normalize_track(raw: Any, default_album: str | None = None) -> Track | None:
    """Attempt track normalization, returning None on failure."""
    if not isinstance(raw, dict):
        return None
    try:
        return normalize_track(raw, default_album=default_album)
    except (KeyError, ValueError, TypeError):
        return None


class YTMusicClient:
    """Async-friendly YouTube Music client wrapping ytmusicapi."""

    def __init__(
        self,
        cookies_file: str | Path | None = None,
        language: str = "en",
        yt_instance: YTMusic | None = None,
    ) -> None:
        """Initialize YTMusicClient in unauthenticated mode or with optional cookies."""
        if yt_instance is not None:
            self._yt = yt_instance
            return

        if cookies_file is None:
            cfg = get_config()
            cookies_file = getattr(cfg.app, "cookies_file", None) or getattr(
                cfg.ytm, "cookies_file", None
            )

        auth_val: str | None = None
        if cookies_file:
            c_path = Path(cookies_file)
            if c_path.is_file():
                auth_val = str(c_path)

        try:
            self._yt = YTMusic(auth=auth_val, language=language)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to initialize YTMusic with auth: %s. Falling back to unauthenticated.", e
            )
            self._yt = YTMusic(auth=None, language=language)
        finally:
            import locale

            try:
                locale.setlocale(locale.LC_NUMERIC, "C")
            except locale.Error:
                pass

    def _run_sync(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute a synchronous ytmusicapi function with standardized error handling."""
        try:
            return func(*args, **kwargs)
        except (
            requests.exceptions.RequestException,
            ConnectionError,
            TimeoutError,
            socket.gaierror,
        ) as e:
            logger.error("YTM network error during %s: %s", func.__name__, e)
            raise YTMNetworkError(f"Network error during {func.__name__}: {e}") from e
        except (YTMusicError, YTMusicServerError) as e:
            logger.error("YTM API error during %s: %s", func.__name__, e)
            raise YTMError(f"YouTube Music API error during {func.__name__}: {e}") from e
        except Exception as e:
            logger.error("Unexpected error during %s: %s", func.__name__, e)
            raise YTMError(f"Unexpected error during {func.__name__}: {e}") from e

    def _search_sync(self, query: str, filter_type: str | None, limit: int) -> list[Track]:
        results = self._yt.search(query=query, filter=filter_type, limit=limit)
        tracks: list[Track] = []
        for item in results:
            track = try_normalize_track(item)
            if track is not None:
                tracks.append(track)
        return tracks

    async def search(
        self,
        query: str,
        filter_type: str | None = "songs",
        limit: int = 25,
    ) -> list[Track]:
        """Search YouTube Music and return normalized tracks."""
        return await asyncio.to_thread(self._run_sync, self._search_sync, query, filter_type, limit)

    def _get_home_sections_sync(self) -> list[dict[str, Any]]:
        sections = self._yt.get_home(limit=3)
        normalized_sections: list[dict[str, Any]] = []
        for sec in sections:
            sec_dict = dict(sec)
            contents = sec.get("contents", [])
            normalized_contents: list[Any] = []
            for item in contents:
                track = try_normalize_track(item)
                normalized_contents.append(track if track is not None else item)
            sec_dict["contents"] = normalized_contents
            normalized_sections.append(sec_dict)
        return normalized_sections

    async def get_home_sections(self) -> list[dict[str, Any]]:
        """Fetch home sections (Quick Picks, etc.) with normalized tracks."""
        return await asyncio.to_thread(self._run_sync, self._get_home_sections_sync)

    def _get_album_sync(self, browse_id: str) -> dict[str, Any]:
        data = self._yt.get_album(browse_id)
        album_dict = dict(data)
        album_title = album_dict.get("title")
        raw_tracks = album_dict.get("tracks", [])
        album_dict["tracks"] = [
            normalize_track(t, default_album=album_title)
            for t in raw_tracks
            if isinstance(t, dict) and (t.get("videoId") or t.get("id"))
        ]
        album_dict["thumbnail_url"] = extract_thumbnail_url(album_dict.get("thumbnails"))
        album_dict["artist"] = extract_artist(album_dict.get("artists"))
        return album_dict

    async def get_album(self, browse_id: str) -> dict[str, Any]:
        """Fetch album details and normalized track list."""
        return await asyncio.to_thread(self._run_sync, self._get_album_sync, browse_id)

    def _get_artist_sync(self, channel_id: str) -> dict[str, Any]:
        data = self._yt.get_artist(channel_id)
        artist_dict = dict(data)
        if "songs" in artist_dict and isinstance(artist_dict["songs"], dict):
            songs_data = dict(artist_dict["songs"])
            if "results" in songs_data and isinstance(songs_data["results"], list):
                songs_data["results"] = [try_normalize_track(t) or t for t in songs_data["results"]]
            artist_dict["songs"] = songs_data
        artist_dict["thumbnail_url"] = extract_thumbnail_url(artist_dict.get("thumbnails"))
        return artist_dict

    async def get_artist(self, channel_id: str) -> dict[str, Any]:
        """Fetch artist information and discography."""
        return await asyncio.to_thread(self._run_sync, self._get_artist_sync, channel_id)

    def _get_artist_profile_sync(
        self,
        name: str | None = None,
        channel_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_id = (channel_id or "").strip() or None
        search_name = (name or "").split(",")[0].strip()

        if not resolved_id and search_name:
            try:
                artist_hits = self._yt.search(query=search_name, filter="artists", limit=6) or []
            except Exception:  # noqa: BLE001
                artist_hits = []
            lower_target = search_name.lower()
            best_hit = None
            for hit in artist_hits:
                if not isinstance(hit, dict) or not hit.get("browseId"):
                    continue
                hit_name = str(hit.get("artist") or hit.get("title") or "").strip().lower()
                if hit_name == lower_target:
                    best_hit = hit
                    break
                if best_hit is None:
                    best_hit = hit
            if best_hit and best_hit.get("browseId"):
                resolved_id = str(best_hit["browseId"]).strip()

        artist_data: dict[str, Any] = {}
        if resolved_id:
            try:
                artist_data = dict(self._yt.get_artist(resolved_id) or {})
            except Exception as e:  # noqa: BLE001
                logger.warning("get_artist failed for %s: %s", resolved_id, e)

        display_name = (
            str(artist_data.get("name") or "").strip()
            or search_name
            or "Unknown Artist"
        )
        description = str(artist_data.get("description") or "").strip() or None
        subscribers = str(artist_data.get("subscribers") or "").strip() or None
        views = str(artist_data.get("views") or "").strip() or None
        thumbnail_url = extract_thumbnail_url(artist_data.get("thumbnails"))

        popular_tracks: list[Track] = []
        seen_popular: set[str] = set()
        songs_section = artist_data.get("songs")
        songs_browse_id: str | None = None
        if isinstance(songs_section, dict):
            if songs_section.get("browseId"):
                songs_browse_id = str(songs_section["browseId"]).strip()
            for raw_song in songs_section.get("results") or []:
                trk = try_normalize_track(raw_song)
                if trk and trk.video_id not in seen_popular:
                    if not trk.artist_id and resolved_id:
                        trk.artist_id = resolved_id
                    if trk.artist == "Unknown Artist":
                        trk.artist = display_name
                    seen_popular.add(trk.video_id)
                    popular_tracks.append(trk)

        all_tracks: list[Track] = list(popular_tracks)
        seen_all: set[str] = set(seen_popular)
        duration_by_id: dict[str, int] = {
            t.video_id: t.duration_s for t in popular_tracks if t.duration_s > 0
        }

        if songs_browse_id:
            try:
                pl = self._yt.get_playlist(songs_browse_id, limit=60)
                raw_pl_tracks = ((pl.get("tracks") or []) if isinstance(pl, dict) else [])[:60]
                for raw_song in raw_pl_tracks:
                    trk = try_normalize_track(raw_song)
                    if not trk:
                        continue
                    if trk.duration_s > 0 and trk.video_id not in duration_by_id:
                        duration_by_id[trk.video_id] = trk.duration_s
                    if trk.video_id not in seen_all:
                        if not trk.artist_id and resolved_id:
                            trk.artist_id = resolved_id
                        if trk.artist == "Unknown Artist":
                            trk.artist = display_name
                        seen_all.add(trk.video_id)
                        all_tracks.append(trk)
            except Exception as e:  # noqa: BLE001
                logger.debug("Artist songs playlist fetch skipped for %s: %s", songs_browse_id, e)

        # Supplement catalog via songs search so artists with partial browse pages still show catalog
        if display_name and display_name != "Unknown Artist" and len(all_tracks) < 35:
            try:
                search_hits = self._yt.search(query=display_name, filter="songs", limit=35) or []
            except Exception:  # noqa: BLE001
                search_hits = []
            artist_lc = display_name.lower()
            for raw_song in search_hits:
                trk = try_normalize_track(raw_song)
                if not trk:
                    continue
                if trk.duration_s > 0 and trk.video_id not in duration_by_id:
                    duration_by_id[trk.video_id] = trk.duration_s
                if trk.video_id in seen_all:
                    continue
                if artist_lc in trk.artist.lower() or (resolved_id and trk.artist_id == resolved_id):
                    seen_all.add(trk.video_id)
                    all_tracks.append(trk)

        # Backfill missing durations on popular tracks (get_artist songs.results often omits duration)
        for trk in popular_tracks:
            if trk.duration_s <= 0 and trk.video_id in duration_by_id:
                trk.duration_s = duration_by_id[trk.video_id]
        for trk in all_tracks:
            if trk.duration_s <= 0 and trk.video_id in duration_by_id:
                trk.duration_s = duration_by_id[trk.video_id]

        # Cap artist catalog to 60 cleanest tracks so UI & Play/Shuffle stay snappy
        all_tracks = all_tracks[:60]

        if not popular_tracks and all_tracks:
            popular_tracks = all_tracks[:10]

        if not thumbnail_url and all_tracks:
            thumbnail_url = all_tracks[0].thumbnail_url

        def _parse_releases(section_key: str, default_type: str) -> list[dict[str, Any]]:
            sec = artist_data.get(section_key)
            if not isinstance(sec, dict):
                return []
            items: list[dict[str, Any]] = []
            seen_rel: set[str] = set()
            for raw_rel in sec.get("results") or []:
                if not isinstance(raw_rel, dict):
                    continue
                bid = str(raw_rel.get("browseId") or "").strip()
                title = str(raw_rel.get("title") or "").strip()
                if not bid or not title or bid in seen_rel:
                    continue
                seen_rel.add(bid)
                items.append(
                    {
                        "browse_id": bid,
                        "title": title,
                        "year": str(raw_rel.get("year") or "").strip() or None,
                        "type": str(raw_rel.get("type") or default_type).strip(),
                        "thumbnail_url": extract_thumbnail_url(raw_rel.get("thumbnails")),
                        "artist": display_name,
                        "artist_id": resolved_id,
                    }
                )
            return items

        albums = _parse_releases("albums", "Album")
        singles = _parse_releases("singles", "Single")

        return {
            "channel_id": resolved_id,
            "name": display_name,
            "description": description,
            "subscribers": subscribers,
            "views": views,
            "thumbnail_url": thumbnail_url,
            "popular_tracks": [t.model_dump() for t in popular_tracks[:12]],
            "all_tracks": [t.model_dump() for t in all_tracks],
            "albums": albums,
            "singles": singles,
        }

    async def get_artist_profile(
        self,
        name: str | None = None,
        channel_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a complete Spotify-style artist profile with popular tracks, discography, and catalog."""
        return await asyncio.to_thread(
            self._run_sync, self._get_artist_profile_sync, name, channel_id
        )

    def _get_album_details_sync(
        self,
        browse_id: str | None = None,
        title: str | None = None,
        artist: str | None = None,
        video_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_bid = (browse_id or "").strip() or None
        search_title = (title or "").strip()
        search_artist = (artist or "").split(",")[0].strip()

        # If we have a video_id without album browseId, inspect watch playlist metadata first
        if not resolved_bid and video_id:
            try:
                watch = self._yt.get_watch_playlist(videoId=video_id, limit=2)
                w_tracks = watch.get("tracks", []) if isinstance(watch, dict) else []
                for wt in w_tracks:
                    if isinstance(wt, dict) and wt.get("videoId") == video_id:
                        alb = wt.get("album")
                        if isinstance(alb, dict) and alb.get("id"):
                            resolved_bid = str(alb["id"]).strip()
                        if not search_title and isinstance(alb, dict) and alb.get("name"):
                            search_title = str(alb["name"]).strip()
                        break
            except Exception as e:  # noqa: BLE001
                logger.debug("Watch playlist album lookup skipped for %s: %s", video_id, e)

        if not resolved_bid and search_title:
            query = f"{search_title} {search_artist}".strip()
            try:
                hits = self._yt.search(query=query, filter="albums", limit=6) or []
            except Exception:  # noqa: BLE001
                hits = []
            title_lc = search_title.lower()
            best_hit = None
            for hit in hits:
                if not isinstance(hit, dict) or not hit.get("browseId"):
                    continue
                ht = str(hit.get("title") or "").strip().lower()
                if ht == title_lc or title_lc in ht:
                    best_hit = hit
                    break
                if best_hit is None:
                    best_hit = hit
            if best_hit and best_hit.get("browseId"):
                resolved_bid = str(best_hit["browseId"]).strip()

        if resolved_bid:
            try:
                album_data = self._get_album_sync(resolved_bid)
                tracks = album_data.get("tracks") or []
                album_artist_id = extract_artist_id(album_data.get("artists"))
                dumped_tracks: list[dict[str, Any]] = []
                for t in tracks:
                    if isinstance(t, Track):
                        if not t.album_id:
                            t.album_id = resolved_bid
                        if not t.artist_id and album_artist_id:
                            t.artist_id = album_artist_id
                        if not t.thumbnail_url and album_data.get("thumbnail_url"):
                            t.thumbnail_url = album_data["thumbnail_url"]
                        dumped_tracks.append(t.model_dump())
                return {
                    "browse_id": resolved_bid,
                    "title": str(album_data.get("title") or search_title or "Album"),
                    "artist": str(album_data.get("artist") or search_artist or "Unknown Artist"),
                    "artist_id": album_artist_id,
                    "year": str(album_data.get("year") or "").strip() or None,
                    "type": str(album_data.get("type") or "Album"),
                    "description": str(album_data.get("description") or "").strip() or None,
                    "thumbnail_url": album_data.get("thumbnail_url"),
                    "tracks": dumped_tracks,
                }
            except Exception as e:  # noqa: BLE001
                logger.warning("get_album failed for %s: %s", resolved_bid, e)

        # Fallback for singles or tracks without a YTM album browseId
        fallback_tracks: list[Track] = []
        q = f"{search_title} {search_artist}".strip()
        if q:
            try:
                fallback_tracks = self._search_sync(q, "songs", 12)
            except Exception:  # noqa: BLE001
                fallback_tracks = []
        return {
            "browse_id": resolved_bid,
            "title": search_title or "Single",
            "artist": search_artist or (fallback_tracks[0].artist if fallback_tracks else "Unknown Artist"),
            "artist_id": fallback_tracks[0].artist_id if fallback_tracks else None,
            "year": None,
            "type": "Single",
            "description": None,
            "thumbnail_url": fallback_tracks[0].thumbnail_url if fallback_tracks else None,
            "tracks": [t.model_dump() for t in fallback_tracks],
        }

    async def get_album_details(
        self,
        browse_id: str | None = None,
        title: str | None = None,
        artist: str | None = None,
        video_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch normalized album details and full tracklist, resolving by title/artist if needed."""
        return await asyncio.to_thread(
            self._run_sync, self._get_album_details_sync, browse_id, title, artist, video_id
        )

    def _get_watch_radio_sync(self, video_id: str, limit: int) -> list[Track]:
        data = self._yt.get_watch_playlist(videoId=video_id, limit=limit, radio=True)
        raw_tracks = data.get("tracks", []) if isinstance(data, dict) else []
        tracks: list[Track] = []
        for t in raw_tracks:
            track = try_normalize_track(t)
            if track is not None:
                tracks.append(track)
            if len(tracks) >= limit:
                break
        return tracks

    async def get_watch_radio(self, video_id: str, limit: int = 25) -> list[Track]:
        """Fetch Up-Next watch radio recommendation tracks."""
        return await asyncio.to_thread(self._run_sync, self._get_watch_radio_sync, video_id, limit)

    def _get_lyrics_sync(self, video_id: str) -> dict[str, str | None]:
        watch = self._yt.get_watch_playlist(videoId=video_id, limit=1)
        lyrics_id = watch.get("lyrics") if isinstance(watch, dict) else None
        if not lyrics_id:
            return {"lyrics": None, "source": None}
        data = self._yt.get_lyrics(lyrics_id)
        if isinstance(data, dict):
            return {
                "lyrics": data.get("lyrics"),
                "source": data.get("source"),
            }
        return {"lyrics": None, "source": None}

    async def get_lyrics(self, video_id: str) -> dict[str, str | None]:
        """Fetch track lyrics from YouTube Music when available."""
        return await asyncio.to_thread(self._run_sync, self._get_lyrics_sync, video_id)
