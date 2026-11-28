"""URL-based playlist and album importer for YouTube, YouTube Music, Spotify, and Apple Music."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import BaseModel, Field

from metrolite.library.downloader import validate_video_id
from metrolite.library.manager import LibraryManager, split_artist_names
from metrolite.ytm.client import Track, YTMusicClient, parse_duration, try_normalize_track

logger = logging.getLogger(__name__)

MAX_IMPORT_TRACKS = 500
MATCH_CONCURRENCY = 4
MAX_RETAINED_JOBS = 5
PROGRESS_THROTTLE_S = 0.2

ImportPlatform = Literal["youtube", "youtube_music", "spotify", "apple_music"]
ImportDestination = Literal["new_playlist", "existing_playlist", "liked"]
ImportJobStatus = Literal[
    "queued",
    "fetching",
    "matching",
    "saving",
    "completed",
    "cancelled",
    "error",
]

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_VARIANT_PENALTY_TERMS: tuple[str, ...] = (
    "karaoke",
    "cover",
    "sped up",
    "slowed",
    "8d",
    "instrumental",
    "remix",
)

_NEXT_DATA_RE = re.compile(
    r"<script\b[^>]*\bid=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_JSON_LD_RE = re.compile(
    r"<script\b[^>]*\btype=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_APPLE_SERVER_DATA_RE = re.compile(
    r"<script\b[^>]*\bid=[\"']serialized-server-data[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_ISO_DURATION_RE = re.compile(
    r"^P(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)$",
    re.IGNORECASE,
)
_SPOTIFY_PATH_RE = re.compile(r"^/(?:intl-[^/]+/)?(playlist|album)/([A-Za-z0-9]+)(?:/|$)")
_APPLE_PATH_RE = re.compile(r"/(playlist|album)/(?:[^/]+/)?([A-Za-z0-9._-]+)(?:/|$)")
_YTM_BROWSE_RE = re.compile(r"^/browse/([^/?#]+)")


class ImportSourceError(ValueError):
    """Raised when an import URL is unsupported, private, or cannot be parsed."""


class ImportTrackSeed(BaseModel):
    """Normalized track metadata extracted from an external playlist or album URL."""

    title: str
    artist: str = "Unknown Artist"
    album: str | None = None
    duration_s: int = 0
    video_id: str | None = None
    thumbnail_url: str | None = None
    artist_id: str | None = None
    album_id: str | None = None


class ImportJobState(BaseModel):
    """Public state snapshot for a playlist/album URL import job."""

    job_id: str
    status: ImportJobStatus = "queued"
    source_platform: ImportPlatform
    playlist_name: str = "Imported Playlist"
    destination: ImportDestination = "new_playlist"
    target_playlist_id: str | None = None
    total: int = 0
    processed: int = 0
    matched: int = 0
    failed: int = 0
    unmatched_items: list[str] = Field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedImportTarget:
    """Detected platform and canonical source identifiers for a playlist/album URL."""

    platform: ImportPlatform
    kind: Literal["playlist", "album", "video"]
    item_id: str
    canonical_url: str


@dataclass(frozen=True, slots=True)
class FetchedImportSource:
    """Extracted source title and ordered track seeds prior to YTM matching."""

    title: str
    seeds: list[ImportTrackSeed]


def detect_import_target(raw_url: str) -> ParsedImportTarget:
    """Validate and classify a YouTube, YouTube Music, Spotify, or Apple Music URL."""
    trimmed = (raw_url or "").strip()
    if not trimmed:
        raise ImportSourceError("Please paste a playlist or album URL.")

    if "://" not in trimmed:
        if any(
            trimmed.lower().startswith(prefix)
            for prefix in (
                "youtube.com/",
                "www.youtube.com/",
                "m.youtube.com/",
                "youtu.be/",
                "music.youtube.com/",
                "open.spotify.com/",
                "music.apple.com/",
            )
        ):
            trimmed = f"https://{trimmed}"
        else:
            raise ImportSourceError(
                "Unsupported URL. Paste a YouTube, YouTube Music, Spotify, or Apple Music playlist/album link."
            )

    parsed = urlparse(trimmed)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ImportSourceError("Invalid URL format.")

    host = (parsed.hostname or "").lower().rstrip(".")
    qs = parse_qs(parsed.query)
    path = parsed.path or "/"

    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"):
        list_vals = [v.strip() for v in qs.get("list", []) if v.strip()]
        if list_vals:
            list_id = list_vals[0]
            return ParsedImportTarget(
                platform="youtube",
                kind="playlist",
                item_id=list_id,
                canonical_url=f"https://www.youtube.com/playlist?list={list_id}",
            )
        if host == "youtu.be":
            seg = path.strip("/").split("/")[0] if path.strip("/") else ""
            if seg:
                try:
                    vid = validate_video_id(seg)
                    return ParsedImportTarget(
                        platform="youtube",
                        kind="video",
                        item_id=vid,
                        canonical_url=f"https://www.youtube.com/watch?v={vid}",
                    )
                except ValueError:
                    pass
        raise ImportSourceError(
            "YouTube URLs must point to a playlist (e.g. youtube.com/playlist?list=...)."
        )

    if host == "music.youtube.com":
        list_vals = [v.strip() for v in qs.get("list", []) if v.strip()]
        if list_vals:
            list_id = list_vals[0]
            return ParsedImportTarget(
                platform="youtube_music",
                kind="playlist",
                item_id=list_id,
                canonical_url=f"https://music.youtube.com/playlist?list={list_id}",
            )
        browse_match = _YTM_BROWSE_RE.match(path)
        if browse_match:
            browse_id = browse_match.group(1).strip()
            if browse_id.startswith(("MPREb_", "OLAK5uy_")):
                return ParsedImportTarget(
                    platform="youtube_music",
                    kind="album",
                    item_id=browse_id,
                    canonical_url=f"https://music.youtube.com/browse/{browse_id}",
                )
            pl_id = browse_id.removeprefix("VL")
            return ParsedImportTarget(
                platform="youtube_music",
                kind="playlist",
                item_id=pl_id,
                canonical_url=f"https://music.youtube.com/playlist?list={pl_id}",
            )
        raise ImportSourceError(
            "YouTube Music URLs must be a playlist (?list=...) or album (/browse/...)."
        )

    if host == "open.spotify.com":
        match = _SPOTIFY_PATH_RE.match(path)
        if match:
            kind = "album" if match.group(1) == "album" else "playlist"
            item_id = match.group(2)
            return ParsedImportTarget(
                platform="spotify",
                kind=kind,
                item_id=item_id,
                canonical_url=f"https://open.spotify.com/embed/{kind}/{item_id}",
            )
        raise ImportSourceError("Spotify URLs must point to an open.spotify.com playlist or album.")

    if host == "music.apple.com":
        match = _APPLE_PATH_RE.search(path)
        if match:
            kind = "album" if match.group(1) == "album" else "playlist"
            item_id = match.group(2)
            clean_url = f"https://music.apple.com{path}"
            return ParsedImportTarget(
                platform="apple_music",
                kind=kind,
                item_id=item_id,
                canonical_url=clean_url,
            )
        raise ImportSourceError(
            "Apple Music URLs must point to a public playlist or album on music.apple.com."
        )

    raise ImportSourceError(
        "Only YouTube, YouTube Music, Spotify, and Apple Music playlist/album URLs are supported."
    )


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalize_compare(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _strip_accents(str(text)).lower()
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(cleaned.split())


def _base_title_compare(title: str | None) -> str:
    if not title:
        return ""
    # Strip trailing parentheticals/brackets like "(feat. ...)" or "[Remastered]"
    stripped = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", " ", str(title))
    stripped = re.split(r"\s+[-–]\s+(?:feat\.?|ft\.?)\s+", stripped, maxsplit=1, flags=re.IGNORECASE)[0]
    base = _normalize_compare(stripped)
    return base or _normalize_compare(title)


def _tokenize_artists(artist: str | None) -> set[str]:
    norm = _normalize_compare(artist)
    if not norm or norm == "unknown artist":
        return set()
    stop_tokens = {"feat", "ft", "featuring", "and", "with", "x"}
    return {tok for tok in norm.split() if len(tok) > 1 and tok not in stop_tokens}


def _contains_term(text: str, term: str) -> bool:
    return bool(re.search(rf"\b{re.escape(term)}\b", text))


def _parse_external_duration(val: Any, *, milliseconds_hint: bool = False) -> int:
    if val is None or isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        num = float(val)
        if num <= 0:
            return 0
        if milliseconds_hint or num >= 1800:
            return round(num / 1000.0)
        return round(num)
    if isinstance(val, str):
        raw = val.strip()
        if not raw:
            return 0
        iso_match = _ISO_DURATION_RE.match(raw)
        if iso_match:
            hours = float(iso_match.group("hours") or 0.0)
            minutes = float(iso_match.group("minutes") or 0.0)
            seconds = float(iso_match.group("seconds") or 0.0)
            return round(hours * 3600 + minutes * 60 + seconds)
        return parse_duration(raw)
    return 0


def score_match_candidate(seed: ImportTrackSeed, candidate: Track) -> float:
    """Score a YouTube Music candidate against an imported track seed."""
    src_full = _normalize_compare(seed.title)
    src_base = _base_title_compare(seed.title)
    cand_full = _normalize_compare(candidate.title)
    cand_base = _base_title_compare(candidate.title)

    if not src_full or not cand_full:
        return -100.0

    full_ratio = SequenceMatcher(None, src_full, cand_full).ratio()
    base_ratio = SequenceMatcher(None, src_base, cand_base).ratio()
    title_ratio = max(full_ratio, base_ratio)

    score = title_ratio * 45.0
    if src_full == cand_full:
        score += 18.0
    elif src_base == cand_base:
        score += 12.0
    elif title_ratio < 0.38:
        score -= 22.0

    src_artists = _tokenize_artists(seed.artist)
    cand_artists = _tokenize_artists(candidate.artist)
    if src_artists and cand_artists:
        overlap = len(src_artists & cand_artists)
        union = len(src_artists | cand_artists)
        if overlap > 0:
            score += (overlap / max(len(src_artists), 1)) * 20.0 + (overlap / max(union, 1)) * 8.0
        else:
            score -= 16.0

    if seed.duration_s > 0 and candidate.duration_s > 0:
        diff = abs(seed.duration_s - candidate.duration_s)
        if diff <= 12:
            score += (1.0 - (diff / 12.0)) * 15.0
        elif diff <= 25:
            score += 3.0
        elif diff > 40:
            score -= 12.0

    src_context = _normalize_compare(f"{seed.title} {seed.album or ''}")
    cand_context = _normalize_compare(f"{candidate.title} {candidate.album or ''}")
    for term in _VARIANT_PENALTY_TERMS:
        if _contains_term(cand_context, term) and not _contains_term(src_context, term):
            score -= 45.0

    return score


def parse_spotify_embed_html(html_text: str) -> FetchedImportSource:
    """Parse track seeds and title from Spotify embed HTML containing __NEXT_DATA__."""
    match = _NEXT_DATA_RE.search(html_text or "")
    if not match:
        raise ImportSourceError(
            "Unable to load Spotify playlist/album data. Ensure it is public and not region-blocked."
        )

    try:
        payload = json.loads(html.unescape(match.group(1).strip()))
    except json.JSONDecodeError as exc:
        raise ImportSourceError("Failed to parse Spotify playlist metadata.") from exc

    entity = _find_spotify_entity(payload)
    if not isinstance(entity, dict):
        raise ImportSourceError("Spotify playlist or album is private or unavailable.")

    playlist_title = str(entity.get("name") or entity.get("title") or "Spotify Import").strip()
    raw_items = _extract_spotify_raw_tracks(entity)
    if not raw_items:
        raise ImportSourceError("No tracks found in that Spotify playlist or album.")

    album_default = playlist_title if entity.get("type") == "album" else None
    seeds: list[ImportTrackSeed] = []
    for item in raw_items[:MAX_IMPORT_TRACKS]:
        if not isinstance(item, dict):
            continue
        track_obj = item.get("track") if isinstance(item.get("track"), dict) else item
        title = str(track_obj.get("title") or track_obj.get("name") or "").strip()
        if not title:
            continue

        artists_val = track_obj.get("artists")
        if isinstance(artists_val, list) and artists_val:
            artist_names = [
                str(a.get("name") or "").strip()
                for a in artists_val
                if isinstance(a, dict) and a.get("name")
            ]
            artist = ", ".join(artist_names) if artist_names else "Unknown Artist"
        else:
            subtitle = str(track_obj.get("subtitle") or track_obj.get("artist") or "").strip()
            # Spotify embed subtitles use non-breaking spaces / commas cleanly
            artist = subtitle.replace("\u00a0", " ") if subtitle else "Unknown Artist"

        dur_raw = (
            track_obj.get("duration_ms")
            if track_obj.get("duration_ms") is not None
            else track_obj.get("duration")
        )
        duration_s = _parse_external_duration(
            dur_raw, milliseconds_hint="duration_ms" in track_obj
        )
        album_obj = track_obj.get("album")
        album_name = (
            str(album_obj.get("name")).strip()
            if isinstance(album_obj, dict) and album_obj.get("name")
            else album_default
        )
        seeds.append(
            ImportTrackSeed(
                title=title,
                artist=artist,
                album=album_name,
                duration_s=duration_s,
            )
        )

    if not seeds:
        raise ImportSourceError("Spotify playlist or album appears to be empty.")

    return FetchedImportSource(title=playlist_title, seeds=seeds)


def _find_spotify_entity(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        props = payload.get("props")
        if isinstance(props, dict):
            entity = (
                props.get("pageProps", {})
                .get("state", {})
                .get("data", {})
                .get("entity")
            )
            if isinstance(entity, dict):
                return entity

        # Recursive fallback search for embed shapes
        if isinstance(payload.get("trackList"), list) or isinstance(payload.get("tracks"), dict):
            return payload
        for value in payload.values():
            found = _find_spotify_entity(value)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for entry in payload:
            found = _find_spotify_entity(entry)
            if found is not None:
                return found
    return None


def _extract_spotify_raw_tracks(entity: dict[str, Any]) -> list[Any]:
    track_list = entity.get("trackList")
    if isinstance(track_list, list) and track_list:
        return track_list
    tracks_obj = entity.get("tracks")
    if isinstance(tracks_obj, dict) and isinstance(tracks_obj.get("items"), list):
        return tracks_obj["items"]
    if isinstance(tracks_obj, list):
        return tracks_obj
    return []


def parse_apple_music_html(html_text: str) -> FetchedImportSource:
    """Parse track seeds and title from Apple Music JSON-LD or SSR JSON."""
    text = html_text or ""
    ld_objects: list[dict[str, Any]] = []
    for raw_block in _JSON_LD_RE.findall(text):
        cleaned = html.unescape(raw_block).strip()
        if not cleaned:
            continue
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        _collect_ld_dicts(parsed, ld_objects)

    for obj in ld_objects:
        obj_type = obj.get("@type")
        types = (
            [str(t).lower() for t in obj_type]
            if isinstance(obj_type, list)
            else [str(obj_type or "").lower()]
        )
        if not any(t in ("musicplaylist", "musicalbum") for t in types):
            continue

        is_album = "musicalbum" in types
        title = html.unescape(str(obj.get("name") or "Apple Music Import")).strip()
        default_artist = _extract_apple_ld_artist(obj.get("byArtist")) or "Unknown Artist"
        raw_tracks = obj.get("track") or obj.get("tracks") or []
        if isinstance(raw_tracks, dict):
            raw_tracks = raw_tracks.get("itemListElement") or [raw_tracks]

        seeds: list[ImportTrackSeed] = []
        if isinstance(raw_tracks, list):
            for entry in raw_tracks[:MAX_IMPORT_TRACKS]:
                if not isinstance(entry, dict):
                    continue
                rec = entry.get("item") if isinstance(entry.get("item"), dict) else entry
                track_title = html.unescape(str(rec.get("name") or "")).strip()
                if not track_title:
                    continue
                artist = _extract_apple_ld_artist(rec.get("byArtist")) or default_artist
                in_album = rec.get("inAlbum")
                album = (
                    html.unescape(str(in_album.get("name"))).strip()
                    if isinstance(in_album, dict) and in_album.get("name")
                    else (title if is_album else None)
                )
                duration_s = _parse_external_duration(rec.get("duration"))
                seeds.append(
                    ImportTrackSeed(
                        title=track_title,
                        artist=artist,
                        album=album,
                        duration_s=duration_s,
                    )
                )
        if seeds:
            return FetchedImportSource(title=title, seeds=seeds)

    # Fallback: serialized-server-data SSR JSON
    for raw_block in _APPLE_SERVER_DATA_RE.findall(text):
        with suppress(json.JSONDecodeError):
            ssr_payload = json.loads(html.unescape(raw_block).strip())
            ssr_source = _extract_apple_ssr_tracks(ssr_payload)
            if ssr_source is not None and ssr_source.seeds:
                return ssr_source

    raise ImportSourceError("Apple Music playlist or album has no accessible public tracks.")


def _collect_ld_dicts(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        if "@graph" in node and isinstance(node["@graph"], list):
            for child in node["@graph"]:
                _collect_ld_dicts(child, out)
        else:
            out.append(node)
    elif isinstance(node, list):
        for item in node:
            _collect_ld_dicts(item, out)


def _extract_apple_ld_artist(val: Any) -> str | None:
    if isinstance(val, str):
        cleaned = html.unescape(val).strip()
        return cleaned or None
    if isinstance(val, dict):
        name = val.get("name")
        if name:
            return html.unescape(str(name)).strip()
    if isinstance(val, list):
        names = [n for item in val if (n := _extract_apple_ld_artist(item))]
        if names:
            return ", ".join(names)
    return None


def _extract_apple_ssr_tracks(payload: Any) -> FetchedImportSource | None:
    seeds: list[ImportTrackSeed] = []
    seen_keys: set[tuple[str, str]] = set()
    header_title: list[str] = []

    def _walk(node: Any) -> None:
        if len(seeds) >= MAX_IMPORT_TRACKS:
            return
        if isinstance(node, dict):
            attrs = node.get("attributes")
            node_type = str(node.get("type") or "").lower()
            if (
                not header_title
                and node_type in ("playlists", "albums")
                and isinstance(attrs, dict)
                and attrs.get("name")
            ):
                header_title.append(str(attrs["name"]).strip())

            title_cand = (
                attrs.get("name") if isinstance(attrs, dict) else node.get("title")
            )
            artist_cand = (
                attrs.get("artistName") if isinstance(attrs, dict) else node.get("artistName")
            )
            dur_cand = (
                attrs.get("durationInMillis")
                if isinstance(attrs, dict)
                else node.get("duration")
            )
            if (
                title_cand
                and artist_cand
                and (node_type in ("songs", "library-songs", "music-videos") or dur_cand)
            ):
                t_str = html.unescape(str(title_cand)).strip()
                a_str = html.unescape(str(artist_cand)).strip()
                key = (t_str.lower(), a_str.lower())
                if t_str and key not in seen_keys:
                    seen_keys.add(key)
                    album_val = attrs.get("albumName") if isinstance(attrs, dict) else None
                    seeds.append(
                        ImportTrackSeed(
                            title=t_str,
                            artist=a_str,
                            album=str(album_val).strip() if album_val else None,
                            duration_s=_parse_external_duration(dur_cand, milliseconds_hint=True),
                        )
                    )
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    if not seeds:
        return None
    return FetchedImportSource(title=header_title[0] if header_title else "Apple Music Import", seeds=seeds)


def _fetch_ytdlp_playlist_sync(url: str) -> FetchedImportSource:
    import yt_dlp

    ydl_opts: dict[str, Any] = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "playlist_items": f"1-{MAX_IMPORT_TRACKS}",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise ImportSourceError("Unable to extract YouTube playlist metadata.")

    title = str(info.get("title") or "YouTube Playlist").strip()
    entries = info.get("entries")
    if entries is None and info.get("id"):
        entries = [info]
    if not isinstance(entries, list):
        entries = list(entries or [])

    seeds: list[ImportTrackSeed] = []
    for entry in entries[:MAX_IMPORT_TRACKS]:
        if not isinstance(entry, dict):
            continue
        raw_vid = entry.get("id") or entry.get("url")
        if not raw_vid:
            continue
        vid_str = str(raw_vid).strip()
        if "watch?v=" in vid_str:
            qs_vid = parse_qs(urlparse(vid_str).query).get("v", [])
            vid_str = qs_vid[0] if qs_vid else vid_str
        try:
            valid_vid = validate_video_id(vid_str)
        except ValueError:
            continue

        t_title = str(entry.get("title") or "Unknown Title").strip()
        if t_title in ("[Deleted video]", "[Private video]"):
            continue
        artist = str(
            entry.get("artist")
            or entry.get("uploader")
            or entry.get("channel")
            or "Unknown Artist"
        ).strip()
        album = str(entry.get("album")).strip() if entry.get("album") else None
        duration_s = parse_duration(entry.get("duration"))
        seeds.append(
            ImportTrackSeed(
                title=t_title,
                artist=artist,
                album=album,
                duration_s=duration_s,
                video_id=valid_vid,
            )
        )

    if not seeds:
        raise ImportSourceError("No playable videos found in that YouTube playlist.")
    return FetchedImportSource(title=title, seeds=seeds)


def _track_to_seed(track: Track) -> ImportTrackSeed:
    return ImportTrackSeed(
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration_s=track.duration_s,
        video_id=track.video_id,
        thumbnail_url=track.thumbnail_url,
        artist_id=track.artist_id,
    )


class PlaylistImportManager:
    """Coordinates async URL extraction, YTM track matching, deduplication, and storage."""

    def __init__(
        self,
        ytm: YTMusicClient,
        library: LibraryManager,
        on_progress: Callable[[ImportJobState], None] | None = None,
        on_completed: Callable[[ImportJobState], None] | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        ytdlp_fetcher: Callable[[str], FetchedImportSource] | None = None,
    ) -> None:
        self.ytm = ytm
        self.library = library
        self.on_progress = on_progress
        self.on_completed = on_completed
        self._http_client_factory = http_client_factory
        self._ytdlp_fetcher = ytdlp_fetcher or _fetch_ytdlp_playlist_sync

        self._jobs: dict[str, ImportJobState] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._last_emit_ts: dict[str, float] = {}

    def get_job(self, job_id: str) -> ImportJobState | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    def list_jobs(self) -> list[ImportJobState]:
        return [job.model_copy(deep=True) for job in self._jobs.values()]

    def cancel_job(self, job_id: str) -> ImportJobState | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status == "saving":
            return job.model_copy(deep=True)  # The commit has already started.
        ev = self._cancel_events.get(job_id)
        if ev is not None:
            ev.set()
        if job.status not in ("completed", "cancelled", "error"):
            job.status = "cancelled"
            self._emit_progress(job, force=True)
        return job.model_copy(deep=True)

    def cancel_all(self) -> None:
        for job_id, ev in list(self._cancel_events.items()):
            ev.set()
            task = self._tasks.get(job_id)
            if task and not task.done():
                task.cancel()

    def _prune_old_jobs(self) -> None:
        while len(self._jobs) >= MAX_RETAINED_JOBS:
            evict_id: str | None = None
            for jid, state in self._jobs.items():
                if state.status in ("completed", "cancelled", "error"):
                    evict_id = jid
                    break
            if evict_id is None:
                raise ImportSourceError("Wait for an active import to finish before starting another.")
            self._jobs.pop(evict_id, None)
            self._cancel_events.pop(evict_id, None)
            self._tasks.pop(evict_id, None)
            self._last_emit_ts.pop(evict_id, None)

    def _emit_progress(self, job: ImportJobState, *, force: bool = False) -> None:
        if self.on_progress is None:
            return
        now = time.monotonic()
        last = self._last_emit_ts.get(job.job_id, 0.0)
        if force or (now - last) >= PROGRESS_THROTTLE_S:
            self._last_emit_ts[job.job_id] = now
            with suppress(Exception):
                self.on_progress(job.model_copy(deep=True))

    async def start_import(
        self,
        url: str,
        destination: ImportDestination = "new_playlist",
        playlist_name: str | None = None,
        target_playlist_id: str | None = None,
        spawn_fn: Callable[[Any], asyncio.Task[Any]] | None = None,
    ) -> ImportJobState:
        target = detect_import_target(url)
        cleaned_override = (playlist_name or "").strip() or None
        if destination == "existing_playlist" and not (target_playlist_id or "").strip():
            raise ImportSourceError("Select an existing playlist to import into.")

        self._prune_old_jobs()
        job_id = uuid.uuid4().hex[:12]
        default_name = (
            cleaned_override
            or ("Liked Songs" if destination == "liked" else f"{target.platform.replace('_', ' ').title()} Import")
        )
        job = ImportJobState(
            job_id=job_id,
            status="queued",
            source_platform=target.platform,
            playlist_name=default_name,
            destination=destination,
            target_playlist_id=target_playlist_id.strip() if target_playlist_id else None,
        )
        cancel_ev = asyncio.Event()
        self._jobs[job_id] = job
        self._cancel_events[job_id] = cancel_ev
        self._emit_progress(job, force=True)

        coro = self._run_import_job(job, target, cleaned_override, cancel_ev)
        task = spawn_fn(coro) if spawn_fn is not None else asyncio.create_task(coro)
        self._tasks[job_id] = task
        return job.model_copy(deep=True)

    async def _run_import_job(
        self,
        job: ImportJobState,
        target: ParsedImportTarget,
        name_override: str | None,
        cancel_ev: asyncio.Event,
    ) -> None:
        try:
            job.status = "fetching"
            self._emit_progress(job, force=True)

            source = await self._fetch_source(target)
            if cancel_ev.is_set():
                job.status = "cancelled"
                self._emit_progress(job, force=True)
                return

            if name_override:
                job.playlist_name = name_override
            elif job.destination == "liked":
                job.playlist_name = source.title or "Liked Songs"
            else:
                job.playlist_name = source.title or job.playlist_name

            seeds = source.seeds[:MAX_IMPORT_TRACKS]
            job.total = len(seeds)
            job.status = "matching"
            self._emit_progress(job, force=True)

            resolved_slots: list[Track | None] = [None] * len(seeds)
            semaphore = asyncio.Semaphore(MATCH_CONCURRENCY)

            async def _resolve_slot(idx: int, seed: ImportTrackSeed) -> None:
                if cancel_ev.is_set():
                    return
                if seed.video_id:
                    resolved = Track(
                        video_id=seed.video_id,
                        title=seed.title,
                        artist=seed.artist,
                        artist_id=seed.artist_id,
                        album=seed.album,
                        album_id=seed.album_id,
                        duration_s=seed.duration_s,
                        thumbnail_url=seed.thumbnail_url,
                    )
                    resolved_slots[idx] = resolved
                    job.processed += 1
                    job.matched += 1
                    self._emit_progress(job)
                    return

                async with semaphore:
                    if cancel_ev.is_set():
                        return
                    matched_track = await self._match_seed(seed)
                    if cancel_ev.is_set():
                        return
                    job.processed += 1
                    if matched_track is not None:
                        resolved_slots[idx] = matched_track
                        job.matched += 1
                    else:
                        job.failed += 1
                        job.unmatched_items.append(f"{seed.title} — {seed.artist}")
                    self._emit_progress(job)

            await asyncio.gather(*(_resolve_slot(i, s) for i, s in enumerate(seeds)))

            if cancel_ev.is_set():
                job.status = "cancelled"
                self._emit_progress(job, force=True)
                return

            # Deduplicate matched tracks while preserving original playlist order
            deduped: list[Track] = []
            seen_vids: set[str] = set()
            duplicate_count = 0
            for item in resolved_slots:
                if item is None:
                    continue
                if item.video_id in seen_vids:
                    duplicate_count += 1
                    continue
                seen_vids.add(item.video_id)
                deduped.append(item)

            if duplicate_count > 0:
                job.matched = len(deduped)

            job.status = "saving"
            self._emit_progress(job, force=True)

            await self._persist_tracks(job, deduped)
            job.status = "completed"
            self._emit_progress(job, force=True)
            if self.on_completed is not None:
                with suppress(Exception):
                    self.on_completed(job.model_copy(deep=True))
        except asyncio.CancelledError:
            job.status = "cancelled"
            self._emit_progress(job, force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Playlist import failed (%s): %s", job.job_id, exc)
            job.status = "error"
            job.error = str(exc).strip() or "Failed to import playlist."
            self._emit_progress(job, force=True)

    async def _fetch_source(self, target: ParsedImportTarget) -> FetchedImportSource:
        if target.platform in ("youtube", "youtube_music"):
            return await self._fetch_youtube_source(target)
        if target.platform == "spotify":
            return await self._fetch_spotify_source(target.canonical_url)
        return await self._fetch_apple_music_source(target.canonical_url)

    async def _fetch_youtube_source(self, target: ParsedImportTarget) -> FetchedImportSource:
        if target.kind == "album":
            try:
                album_data = await self.ytm.get_album(target.item_id)
                tracks = [t for t in (album_data.get("tracks") or []) if isinstance(t, Track)]
                if tracks:
                    return FetchedImportSource(
                        title=str(album_data.get("title") or "YouTube Music Album").strip(),
                        seeds=[_track_to_seed(t) for t in tracks[:MAX_IMPORT_TRACKS]],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("YTM get_album failed for %s: %s", target.item_id, exc)

        if target.kind == "playlist":
            try:
                def _get_playlist_sync(playlist_id: str, limit: int) -> Any:
                    return self.ytm._yt.get_playlist(playlist_id, limit)

                raw_pl = await asyncio.to_thread(
                    self.ytm._run_sync,
                    _get_playlist_sync,
                    target.item_id,
                    MAX_IMPORT_TRACKS,
                )
                if isinstance(raw_pl, dict):
                    pl_title = str(raw_pl.get("title") or "YouTube Playlist").strip()
                    seeds: list[ImportTrackSeed] = []
                    for item in (raw_pl.get("tracks") or [])[:MAX_IMPORT_TRACKS]:
                        norm = try_normalize_track(item)
                        if norm is not None:
                            seeds.append(_track_to_seed(norm))
                    if seeds:
                        return FetchedImportSource(title=pl_title, seeds=seeds)
            except Exception as exc:  # noqa: BLE001
                logger.debug("YTM get_playlist fallback to yt-dlp for %s: %s", target.item_id, exc)

        return await asyncio.to_thread(self._ytdlp_fetcher, target.canonical_url)

    def _build_http_client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(
            headers=_DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        )

    async def _fetch_spotify_source(self, embed_url: str) -> FetchedImportSource:
        async with self._build_http_client() as client:
            resp = await client.get(embed_url)
        if resp.status_code in (401, 403, 404):
            raise ImportSourceError("Spotify playlist or album is private or could not be found.")
        if resp.status_code >= 400:
            raise ImportSourceError(f"Spotify returned HTTP {resp.status_code}.")
        return parse_spotify_embed_html(resp.text)

    async def _fetch_apple_music_source(self, page_url: str) -> FetchedImportSource:
        async with self._build_http_client() as client:
            resp = await client.get(page_url)
        if resp.status_code in (401, 403, 404):
            raise ImportSourceError("Apple Music playlist or album is private or could not be found.")
        if resp.status_code >= 400:
            raise ImportSourceError(f"Apple Music returned HTTP {resp.status_code}.")
        return parse_apple_music_html(resp.text)

    async def _match_seed(self, seed: ImportTrackSeed) -> Track | None:
        artist_part = "" if seed.artist.lower() == "unknown artist" else seed.artist
        query = f"{seed.title} {artist_part}".strip()
        if not query:
            return None

        try:
            candidates = await self.ytm.search(query, filter_type="songs", limit=5)
        except Exception as exc:  # noqa: BLE001
            logger.debug("YTM search failed during import for %r: %s", query, exc)
            return None

        if not candidates:
            return None

        best_track: Track | None = None
        best_score = 48.0
        artists = {_normalize_compare(name) for name in split_artist_names(seed.artist)}
        base_title = _base_title_compare(seed.title)
        for cand in candidates:
            credits = {_normalize_compare(name) for name in split_artist_names(cand.artist)}
            if artists and artists.isdisjoint(credits):
                continue
            if SequenceMatcher(None, base_title, _base_title_compare(cand.title)).ratio() < 0.70:
                continue
            if seed.duration_s and cand.duration_s and abs(seed.duration_s - cand.duration_s) > 40:
                continue
            sc = score_match_candidate(seed, cand)
            if sc > best_score:
                best_score = sc
                best_track = cand
        return best_track

    async def _persist_tracks(self, job: ImportJobState, tracks: list[Track]) -> None:
        if job.destination == "liked":
            await self.library.add_liked_tracks(tracks)
            return

        if job.destination == "existing_playlist":
            if not job.target_playlist_id:
                raise ImportSourceError("Missing target playlist ID.")
            await self.library.add_to_playlist(job.target_playlist_id, tracks)
            return

        created = await self.library.create_playlist(
            name=job.playlist_name or "Imported Playlist",
            description=f"Imported from {job.source_platform.replace('_', ' ').title()}",
            tracks=tracks,
        )
        job.target_playlist_id = str(created["id"])
