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


class ArtistCredit(BaseModel):
    """Individual artist credit with optional YouTube Music browse/channel ID."""

    name: str
    id: str | None = None


class Track(BaseModel):
    """Normalized metadata for a YouTube Music track."""

    video_id: str = Field(description="Unique YouTube video identifier")
    title: str = Field(default="Unknown Title", description="Track title")
    artist: str = Field(default="Unknown Artist", description="Artist or comma-separated artists")
    artist_id: str | None = Field(default=None, description="Primary YouTube Music artist channel ID")
    artists: list[ArtistCredit] = Field(
        default_factory=list, description="Individual collaborating artist credits"
    )
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


def extract_artist_credits(raw: Any) -> list[ArtistCredit]:
    """Extract ordered individual artist credits from ytmusicapi artists structures."""
    credits: list[ArtistCredit] = []
    seen: set[str] = set()

    def _push(name_val: Any, id_val: Any = None) -> None:
        cleaned = str(name_val or "").strip()
        if not cleaned:
            return
        key = cleaned.lower()
        cid = str(id_val).strip() if id_val else None
        if key in seen:
            return
        seen.add(key)
        credits.append(ArtistCredit(name=cleaned, id=cid or None))

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                _push(item)
            elif isinstance(item, dict) and item.get("name"):
                _push(item.get("name"), item.get("id"))
    elif isinstance(raw, dict) and raw.get("name"):
        _push(raw.get("name"), raw.get("id"))
    elif isinstance(raw, str):
        _push(raw)
    return credits


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
    raw_credits = raw.get("artists") if isinstance(raw.get("artists"), list) else raw_artists
    credits = extract_artist_credits(raw_credits)
    if artist == "Unknown Artist":
        credits = []
    elif not credits:
        credits = [ArtistCredit(name=artist, id=str(artist_id).strip() if artist_id else None)]
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
        artists=credits,
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

    def _parse_artist_browse_fallback(self, channel_id: str) -> dict[str, Any]:
        """Parse raw YouTube Music browse JSON when ytmusicapi.get_artist fails (e.g. KeyError 'header').

        Handles YouTube Music's twoColumnBrowseResultsRenderer (where header lives inside the
        left column and shelves live in secondaryContents), musicVisualHeaderRenderer,
        musicResponsiveHeaderRenderer, and header-less promo carousels.
        """
        send_req = getattr(self._yt, "_send_request", None)
        if not callable(send_req):
            return {}

        cid = channel_id.removeprefix("MPLA").strip()
        if not cid:
            return {}

        response = send_req("browse", {"browseId": cid})
        if not isinstance(response, dict):
            return {}

        def _runs_text(obj: Any) -> str:
            if isinstance(obj, str):
                return obj.strip()
            if isinstance(obj, dict):
                if isinstance(obj.get("simpleText"), str):
                    return obj["simpleText"].strip()
                runs = obj.get("runs")
                if isinstance(runs, list):
                    return "".join(
                        str(r.get("text") or "") for r in runs if isinstance(r, dict)
                    ).strip()
            return ""

        def _extract_thumbs(obj: Any) -> list[dict[str, Any]]:
            if not isinstance(obj, dict):
                return []
            for path in (
                ("thumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"),
                ("foregroundThumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"),
                ("thumbnail", "thumbnails"),
                ("thumbnails",),
            ):
                cur: Any = obj
                for key in path:
                    if isinstance(cur, dict) and key in cur:
                        cur = cur[key]
                    else:
                        cur = None
                        break
                if isinstance(cur, list) and cur:
                    return [t for t in cur if isinstance(t, dict) and t.get("url")]
            return []

        # 1. Collect content sections from both single-column and two-column layouts
        sections: list[dict[str, Any]] = []
        contents = response.get("contents")
        if isinstance(contents, dict):
            single = contents.get("singleColumnBrowseResultsRenderer")
            if isinstance(single, dict):
                for tab in single.get("tabs") or []:
                    if not isinstance(tab, dict):
                        continue
                    tr = tab.get("tabRenderer")
                    if not isinstance(tr, dict):
                        continue
                    sl = (tr.get("content") or {}).get("sectionListRenderer")
                    if isinstance(sl, dict) and isinstance(sl.get("contents"), list):
                        sections.extend(
                            s for s in sl["contents"] if isinstance(s, dict)
                        )

            two_col = contents.get("twoColumnBrowseResultsRenderer")
            if isinstance(two_col, dict):
                for tab in two_col.get("tabs") or []:
                    if not isinstance(tab, dict):
                        continue
                    tr = tab.get("tabRenderer")
                    if not isinstance(tr, dict):
                        continue
                    sl = (tr.get("content") or {}).get("sectionListRenderer")
                    if isinstance(sl, dict) and isinstance(sl.get("contents"), list):
                        sections.extend(
                            s for s in sl["contents"] if isinstance(s, dict)
                        )
                sec_sl = (two_col.get("secondaryContents") or {}).get("sectionListRenderer")
                if isinstance(sec_sl, dict) and isinstance(sec_sl.get("contents"), list):
                    sections.extend(
                        s for s in sec_sl["contents"] if isinstance(s, dict)
                    )

        # 2. Locate artist header (top-level response['header'] OR inside two-column left pane)
        header_obj: dict[str, Any] = {}
        header_keys = (
            "musicImmersiveHeaderRenderer",
            "musicVisualHeaderRenderer",
            "musicResponsiveHeaderRenderer",
            "musicDetailHeaderRenderer",
        )
        raw_header = response.get("header")
        if isinstance(raw_header, dict):
            for hk in header_keys:
                if isinstance(raw_header.get(hk), dict):
                    header_obj = raw_header[hk]
                    break
            if not header_obj:
                for val in raw_header.values():
                    if isinstance(val, dict) and (val.get("title") or val.get("thumbnail")):
                        header_obj = val
                        break

        if not header_obj:
            for sec in sections:
                for hk in header_keys:
                    if isinstance(sec.get(hk), dict):
                        header_obj = sec[hk]
                        break
                if header_obj:
                    break

        micro = (
            (response.get("microformat") or {}).get("microformatDataRenderer")
            if isinstance(response.get("microformat"), dict)
            else {}
        )
        if not isinstance(micro, dict):
            micro = {}

        name = _runs_text(header_obj.get("title")) or str(micro.get("title") or "").strip()
        if name.endswith(" - YouTube Music"):
            name = name[: -len(" - YouTube Music")].strip()
        if name.endswith(" - Topic"):
            name = name[: -len(" - Topic")].strip()

        thumbnails = _extract_thumbs(header_obj) or _extract_thumbs(micro)

        sub_btn = (
            (header_obj.get("subscriptionButton") or {}).get("subscribeButtonRenderer")
            if isinstance(header_obj.get("subscriptionButton"), dict)
            else {}
        )
        if not isinstance(sub_btn, dict):
            sub_btn = {}
        subscribers = (
            _runs_text(sub_btn.get("subscriberCountText"))
            or _runs_text(sub_btn.get("longSubscriberCountText"))
            or _runs_text(header_obj.get("monthlyListenerCount"))
            or None
        )
        if subscribers:
            subscribers = subscribers.replace(" monthly audience", "").strip() or None

        description: str | None = _runs_text(header_obj.get("description")) or None
        views: str | None = None
        for sec in sections:
            desc_shelf = sec.get("musicDescriptionShelfRenderer")
            if isinstance(desc_shelf, dict):
                shelf_desc = _runs_text(desc_shelf.get("description"))
                if shelf_desc:
                    description = shelf_desc
                shelf_sub = _runs_text(desc_shelf.get("subheader"))
                if shelf_sub:
                    views = shelf_sub
                break
        if not description and isinstance(micro.get("description"), str):
            desc_m = micro["description"].strip()
            if desc_m and not desc_m.lower().startswith("share your videos"):
                description = desc_m

        artist: dict[str, Any] = {
            "channelId": str(sub_btn.get("channelId") or cid),
            "name": name or None,
            "description": description,
            "subscribers": subscribers,
            "views": views,
            "thumbnails": thumbnails,
            "songs": {"browseId": None, "results": []},
        }

        # 3. Parse top songs shelf (`musicShelfRenderer`)
        try:
            from ytmusicapi.parsers.playlists import parse_playlist_items
        except Exception:  # noqa: BLE001
            parse_playlist_items = None

        for sec in sections:
            shelf = sec.get("musicShelfRenderer")
            if not isinstance(shelf, dict):
                continue
            title_obj = shelf.get("title")
            if isinstance(title_obj, dict):
                runs = title_obj.get("runs")
                if isinstance(runs, list) and runs and isinstance(runs[0], dict):
                    bid = (
                        (runs[0].get("navigationEndpoint") or {})
                        .get("browseEndpoint", {})
                        .get("browseId")
                    )
                    if bid:
                        artist["songs"]["browseId"] = str(bid).strip()
            if not artist["songs"]["browseId"]:
                bot_bid = (
                    (shelf.get("bottomEndpoint") or {})
                    .get("browseEndpoint", {})
                    .get("browseId")
                )
                if bot_bid:
                    artist["songs"]["browseId"] = str(bot_bid).strip()

            raw_items = shelf.get("contents")
            if isinstance(raw_items, list) and raw_items and parse_playlist_items is not None:
                try:
                    parsed = parse_playlist_items(raw_items)
                    if parsed:
                        artist["songs"]["results"] = parsed
                        break
                except Exception:  # noqa: BLE001
                    # Per-item salvage if one row has an unexpected shape
                    salvaged: list[dict[str, Any]] = []
                    for item in raw_items:
                        try:
                            chunk = parse_playlist_items([item])
                            if chunk:
                                salvaged.extend(chunk)
                        except Exception as item_err:  # noqa: BLE001
                            logger.debug("Skipped malformed artist song item: %s", item_err)
                            continue
                    if salvaged:
                        artist["songs"]["results"] = salvaged
                        break

        # 4. Parse carousels (Albums, Singles & EPs, Videos, Related) tolerating missing headers
        try:
            parser_obj = getattr(self._yt, "parser", None)
            if parser_obj is not None and hasattr(parser_obj, "parse_channel_contents"):
                parsed_carousels = parser_obj.parse_channel_contents(sections)
                if isinstance(parsed_carousels, dict):
                    artist.update(parsed_carousels)
        except Exception as e:  # noqa: BLE001
            logger.debug("Artist carousel parser fallback skipped for %s: %s", cid, e)

        # Manual lightweight carousel fallback if parser_obj missed albums/singles
        if "albums" not in artist or "singles" not in artist:
            for sec in sections:
                car = sec.get("musicCarouselShelfRenderer")
                if not isinstance(car, dict):
                    continue
                hdr = (
                    (car.get("header") or {}).get("musicCarouselShelfBasicHeaderRenderer")
                    if isinstance(car.get("header"), dict)
                    else None
                )
                if not isinstance(hdr, dict):
                    continue
                cat_title = _runs_text(hdr.get("title")).lower()
                target_key: str | None = None
                if "album" in cat_title and "albums" not in artist:
                    target_key = "albums"
                elif ("single" in cat_title or "ep" in cat_title) and "singles" not in artist:
                    target_key = "singles"
                if not target_key:
                    continue

                rel_items: list[dict[str, Any]] = []
                for entry in car.get("contents") or []:
                    mtr = entry.get("musicTwoRowItemRenderer") if isinstance(entry, dict) else None
                    if not isinstance(mtr, dict):
                        continue
                    rel_title = _runs_text(mtr.get("title"))
                    rel_bid = (
                        (mtr.get("navigationEndpoint") or {})
                        .get("browseEndpoint", {})
                        .get("browseId")
                    )
                    if not rel_bid:
                        t_runs = (mtr.get("title") or {}).get("runs")
                        if isinstance(t_runs, list) and t_runs and isinstance(t_runs[0], dict):
                            rel_bid = (
                                (t_runs[0].get("navigationEndpoint") or {})
                                .get("browseEndpoint", {})
                                .get("browseId")
                            )
                    if not rel_title or not rel_bid:
                        continue
                    sub_txt = _runs_text(mtr.get("subtitle"))
                    year = None
                    rel_type = "Single" if target_key == "singles" else "Album"
                    for part in [p.strip() for p in sub_txt.split("•") if p.strip()]:
                        if part.isdigit() and len(part) == 4:
                            year = part
                        elif part.lower() in ("album", "single", "ep"):
                            rel_type = part
                    rel_items.append(
                        {
                            "title": rel_title,
                            "browseId": str(rel_bid).strip(),
                            "year": year,
                            "type": rel_type,
                            "thumbnails": _extract_thumbs(mtr.get("thumbnailRenderer") or mtr),
                        }
                    )
                if rel_items:
                    artist[target_key] = {"browseId": None, "results": rel_items}

        return artist

    def _fetch_artist_data_sync(self, channel_id: str) -> dict[str, Any]:
        """Fetch raw artist dict via ytmusicapi.get_artist with resilient raw-browse fallback."""
        try:
            data = self._yt.get_artist(channel_id)
            if isinstance(data, dict) and data:
                return dict(data)
        except Exception as e:  # noqa: BLE001
            fallback = self._parse_artist_browse_fallback(channel_id)
            if fallback and (
                fallback.get("name")
                or (fallback.get("songs") or {}).get("results")
                or (fallback.get("albums") or {}).get("results")
                or (fallback.get("singles") or {}).get("results")
            ):
                logger.info(
                    "Recovered artist %s via resilient browse parser after get_artist error: %s",
                    channel_id,
                    e,
                )
                return fallback
            logger.warning("get_artist failed for %s: %s", channel_id, e)
        return {}

    def _get_artist_sync(self, channel_id: str) -> dict[str, Any]:
        artist_dict = self._fetch_artist_data_sync(channel_id)
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
        raw_name = (name or "").strip()
        search_name = raw_name

        def _search_artist_hit(query_name: str, exclude_id: str | None = None) -> dict[str, Any] | None:
            if not query_name:
                return None
            try:
                artist_hits = self._yt.search(query=query_name, filter="artists", limit=6) or []
            except Exception:  # noqa: BLE001
                return None
            lower_target = query_name.lower()
            best_hit = None
            for hit in artist_hits:
                if not isinstance(hit, dict) or not hit.get("browseId"):
                    continue
                bid = str(hit["browseId"]).strip()
                if exclude_id and bid == exclude_id:
                    continue
                hit_name = str(hit.get("artist") or hit.get("title") or "").strip().lower()
                if hit_name == lower_target:
                    return hit
                if best_hit is None:
                    best_hit = hit
            return best_hit

        search_hit_thumb: str | None = None
        if not resolved_id and search_name:
            best_hit = _search_artist_hit(search_name)
            if not best_hit and "," in search_name:
                fallback_name = search_name.split(",")[0].strip()
                if fallback_name:
                    search_name = fallback_name
                    best_hit = _search_artist_hit(search_name)
            if best_hit and best_hit.get("browseId"):
                resolved_id = str(best_hit["browseId"]).strip()
                search_hit_thumb = extract_thumbnail_url(best_hit.get("thumbnails"))

        artist_data: dict[str, Any] = {}
        if resolved_id:
            artist_data = self._fetch_artist_data_sync(resolved_id)

        # If a raw Topic/channel ID yielded no browse data, try resolving canonical artist browseId by name
        if (
            not artist_data
            and search_name
        ):
            alt_hit = _search_artist_hit(search_name, exclude_id=resolved_id)
            if alt_hit and alt_hit.get("browseId"):
                alt_id = str(alt_hit["browseId"]).strip()
                search_hit_thumb = search_hit_thumb or extract_thumbnail_url(alt_hit.get("thumbnails"))
                alt_data = self._fetch_artist_data_sync(alt_id)
                if alt_data:
                    resolved_id = alt_id
                    artist_data = alt_data

        display_name = (
            str(artist_data.get("name") or "").strip()
            or search_name
            or "Unknown Artist"
        )
        description = str(artist_data.get("description") or "").strip() or None
        subscribers = str(artist_data.get("subscribers") or "").strip() or None
        views = str(artist_data.get("views") or "").strip() or None
        thumbnail_url = extract_thumbnail_url(artist_data.get("thumbnails")) or search_hit_thumb

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

        # Supplement Albums/Singles via search when browse returned a partial Topic/channel page
        if not albums and not singles and display_name and display_name != "Unknown Artist":
            try:
                album_hits = self._yt.search(query=display_name, filter="albums", limit=12) or []
            except Exception:  # noqa: BLE001
                album_hits = []
            artist_lc = display_name.lower()
            seen_rel_ids: set[str] = set()
            for raw_rel in album_hits:
                if not isinstance(raw_rel, dict):
                    continue
                bid = str(raw_rel.get("browseId") or "").strip()
                title = str(raw_rel.get("title") or "").strip()
                if not bid or not title or bid in seen_rel_ids:
                    continue
                rel_artist = extract_artist(raw_rel.get("artists") or raw_rel.get("artist"))
                rel_artist_id = extract_artist_id(raw_rel.get("artists"))
                if artist_lc not in rel_artist.lower() and not (
                    resolved_id and rel_artist_id == resolved_id
                ):
                    continue
                seen_rel_ids.add(bid)
                rel_type = str(raw_rel.get("type") or "Album").strip()
                entry = {
                    "browse_id": bid,
                    "title": title,
                    "year": str(raw_rel.get("year") or "").strip() or None,
                    "type": rel_type,
                    "thumbnail_url": extract_thumbnail_url(raw_rel.get("thumbnails")),
                    "artist": display_name,
                    "artist_id": resolved_id,
                }
                if rel_type.lower() in ("single", "ep"):
                    singles.append(entry)
                else:
                    albums.append(entry)

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
                    "artists": [
                        a.model_dump() for a in extract_artist_credits(album_data.get("artists"))
                    ],
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
