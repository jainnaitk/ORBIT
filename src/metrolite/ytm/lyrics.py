"""Offline-first time-synced lyrics resolver (Local .lrc -> LRCLIB -> YouTube Music)."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from metrolite.store.db import get_sync_connection

logger = logging.getLogger(__name__)

_LRC_LINE_RE = re.compile(r"\[(\d{1,2}):(\d{2}(?:\.\d{1,3})?)\]")
_BRACKET_RE = re.compile(r"\s*[\(\[\{].*?[\)\]\}]")
_LYRICS_CACHE: dict[str, dict[str, Any]] = {}

_LRCLIB_HEADERS = {
    "User-Agent": "ORBIT/1.0.0 (https://github.com/orbit-desktop)",
    "Lrclib-Client": "ORBIT v1.0.0",
    "Accept": "application/json",
}


def parse_lrc_lines(lrc_text: str) -> list[dict[str, Any]]:
    """Parse LRC timestamped lyrics into sorted {time, text} dicts."""
    lines: list[dict[str, Any]] = []
    for raw in lrc_text.splitlines():
        matches = list(_LRC_LINE_RE.finditer(raw))
        if not matches:
            continue
        text = raw[matches[-1].end() :].strip()
        for m in matches:
            mins = int(m.group(1))
            secs = float(m.group(2))
            lines.append({"time": round(mins * 60 + secs, 2), "text": text})
    lines.sort(key=lambda item: item["time"])
    return lines


def format_lrc_lines(synced: list[dict[str, Any]]) -> str:
    """Serialize [{time, text}] entries into standard [MM:SS.xx] LRC format."""
    out: list[str] = []
    for item in synced:
        t = max(0.0, float(item.get("time", 0.0)))
        mins = int(t // 60)
        secs = t - mins * 60
        txt = str(item.get("text") or "").strip()
        out.append(f"[{mins:02d}:{secs:05.2f}] {txt}")
    return "\n".join(out) + "\n"


def get_local_lrc_path(audio_path: Path | str) -> Path:
    """Return sidecar .lrc path alongside a downloaded audio file."""
    return Path(audio_path).with_suffix(".lrc")


def read_local_lrc(audio_path: Path | str) -> dict[str, Any] | None:
    """Read and parse local sidecar .lrc lyrics if present on disk."""
    lrc_path = get_local_lrc_path(audio_path)
    if not lrc_path.is_file():
        return None
    try:
        raw = lrc_path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return None
        synced = parse_lrc_lines(raw)
        if synced:
            plain = "\n".join(line["text"] for line in synced if line["text"])
            return {
                "synced": synced,
                "lyrics": plain or None,
                "source": "Offline (.lrc)",
                "offline": True,
            }
        return {
            "synced": None,
            "lyrics": raw,
            "source": "Offline (.lrc)",
            "offline": True,
        }
    except OSError as e:
        logger.debug("Failed reading local .lrc %s: %s", lrc_path, e)
        return None


def save_local_lrc(
    audio_path: Path | str,
    synced: list[dict[str, Any]] | None,
    plain: str | None = None,
) -> Path | None:
    """Persist synced .lrc (or plain fallback) right alongside a downloaded audio file."""
    audio = Path(audio_path)
    if not audio.parent.is_dir():
        return None
    lrc_path = get_local_lrc_path(audio)
    try:
        if synced:
            lrc_path.write_text(format_lrc_lines(synced), encoding="utf-8", newline="\n")
            return lrc_path
        if plain and plain.strip():
            lrc_path.write_text(plain.strip() + "\n", encoding="utf-8", newline="\n")
            return lrc_path
    except OSError as e:
        logger.debug("Failed writing local .lrc %s: %s", lrc_path, e)
    return None


def _lookup_track_local_info(db_path: Path | str | None, video_id: str) -> dict[str, Any] | None:
    if not db_path:
        return None
    p = Path(db_path)
    if not p.is_file():
        return None
    try:
        with get_sync_connection(p) as conn:
            row = conn.execute(
                "SELECT video_id, title, artist, duration_s, local_path FROM tracks WHERE video_id = ?;",
                (video_id,),
            ).fetchone()
            return dict(row) if row else None
    except sqlite3.Error:
        return None


def clean_track_title(title: str) -> str:
    """Strip '(From ...)', '(Remix)', '[Official Video]', pipes, and subtitles."""
    t = title.split("|")[0].split(" - ")[0]
    t = _BRACKET_RE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—")
    return t or title.strip()


def _pick_best_lrclib_match(
    items: list[Any],
    clean_title: str,
    artists: list[str],
    duration: int | None,
) -> dict[str, Any] | None:
    title_low = clean_title.lower()
    artist_tokens = {a.lower() for a in artists if a}

    best_item: dict[str, Any] | None = None
    best_score = -10_000

    for item in items:
        if not isinstance(item, dict):
            continue
        synced = item.get("syncedLyrics")
        plain = item.get("plainLyrics")
        if not (isinstance(synced, str) and synced.strip()) and not (
            isinstance(plain, str) and plain.strip()
        ):
            continue

        score = 120 if (isinstance(synced, str) and synced.strip()) else 20
        rec_title = str(item.get("trackName") or item.get("name") or "").lower()
        rec_artist = str(item.get("artistName") or "").lower()

        if rec_title == title_low:
            score += 50
        elif title_low and (title_low in rec_title or rec_title in title_low):
            score += 25

        if any(tok in rec_artist for tok in artist_tokens):
            score += 45

        rec_dur = item.get("duration")
        if duration and duration > 0 and isinstance(rec_dur, (int, float)):
            diff = abs(float(rec_dur) - float(duration))
            if diff <= 3:
                score += 40
            elif diff <= 10:
                score += 25
            elif diff <= 25:
                score += 10
            else:
                score -= 15

        if score > best_score:
            best_score = score
            best_item = item

    return best_item


def _format_lrclib_record(data: dict[str, Any]) -> dict[str, Any] | None:
    synced_raw = data.get("syncedLyrics")
    plain_raw = data.get("plainLyrics")
    synced = (
        parse_lrc_lines(synced_raw)
        if isinstance(synced_raw, str) and synced_raw.strip()
        else None
    )
    plain = plain_raw.strip() if isinstance(plain_raw, str) and plain_raw.strip() else None
    if synced or plain:
        return {
            "synced": synced,
            "lyrics": plain,
            "source": "LRCLIB",
            "offline": False,
        }
    return None


async def fetch_lrclib_lyrics(
    title: str | None,
    artist: str | None,
    duration: int | None,
    custom_query: str | None = None,
) -> dict[str, Any] | None:
    """Fetch synced or plain lyrics from lrclib.net (Hindi/Punjabi/English)."""
    if not title and not custom_query:
        return None

    clean_title = custom_query.strip() if custom_query else clean_track_title(title or "")
    artists = [a.strip() for a in (artist or "").split(",") if a.strip()]
    primary_artist = artists[0] if artists else ""

    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=True,
            headers=_LRCLIB_HEADERS,
        ) as client:
            if custom_query:
                resp = await client.get(
                    "https://lrclib.net/api/search",
                    params={"q": custom_query.strip()},
                )
                if resp.status_code == 200:
                    arr = resp.json()
                    if isinstance(arr, list) and arr:
                        best = _pick_best_lrclib_match(arr, clean_title, artists, duration)
                        if best:
                            return _format_lrclib_record(best)
                return None

            plain_fallback: dict[str, Any] | None = None

            if clean_title and primary_artist:
                params: dict[str, Any] = {
                    "track_name": clean_title,
                    "artist_name": primary_artist,
                }
                if duration and duration > 0:
                    params["duration"] = int(duration)
                resp = await client.get("https://lrclib.net/api/get", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        formatted = _format_lrclib_record(data)
                        if formatted:
                            if formatted.get("synced"):
                                return formatted
                            plain_fallback = formatted

            raw_attempts: list[dict[str, str]] = []
            if clean_title and primary_artist:
                raw_attempts.append({"track_name": clean_title, "artist_name": primary_artist})
            if clean_title and len(artists) > 1:
                raw_attempts.append({"track_name": clean_title, "artist_name": artists[-1]})
            if clean_title:
                raw_attempts.append({"track_name": clean_title})
                raw_attempts.append({"q": clean_title})

            seen_keys: set[tuple[tuple[str, str], ...]] = set()
            search_attempts: list[dict[str, str]] = []
            for sp in raw_attempts:
                key = tuple(sorted(sp.items()))
                if key not in seen_keys:
                    seen_keys.add(key)
                    search_attempts.append(sp)
                if len(search_attempts) >= 3:
                    break

            async def _run_search(sp: dict[str, str]) -> dict[str, Any] | None:
                try:
                    r = await client.get("https://lrclib.net/api/search", params=sp)
                    if r.status_code != 200:
                        return None
                    arr = r.json()
                    if not isinstance(arr, list) or not arr:
                        return None
                    best = _pick_best_lrclib_match(arr, clean_title, artists, duration)
                    return _format_lrclib_record(best) if best else None
                except Exception:  # noqa: BLE001
                    return None

            if search_attempts:
                search_results = await asyncio.gather(
                    *(_run_search(sp) for sp in search_attempts),
                    return_exceptions=True,
                )
                for item in search_results:
                    if not isinstance(item, dict):
                        continue
                    if item.get("synced"):
                        return item
                    if plain_fallback is None and item.get("lyrics"):
                        plain_fallback = item

            return plain_fallback
    except Exception as e:  # noqa: BLE001
        logger.debug("LRCLIB lookup failed for %r: %s", clean_title, e)
        return None


def _fetch_ytm_lyrics_sync(ytm_client: Any, video_id: str) -> dict[str, Any] | None:
    yt = getattr(ytm_client, "_yt", None)
    if yt is None:
        return None
    try:
        watch = yt.get_watch_playlist(videoId=video_id, limit=5)
    except Exception:  # noqa: BLE001
        return None

    lyrics_id = watch.get("lyrics") if isinstance(watch, dict) else None
    if not lyrics_id:
        return None

    data: Any = None
    try:
        data = yt.get_lyrics(lyrics_id, timestamps=True)
    except Exception:  # noqa: BLE001
        try:
            data = yt.get_lyrics(lyrics_id)
        except Exception:  # noqa: BLE001
            return None

    if not isinstance(data, dict):
        return None

    source = data.get("source") or "YouTube Music"
    raw_lyrics = data.get("lyrics")

    if isinstance(raw_lyrics, list) and raw_lyrics:
        synced: list[dict[str, Any]] = []
        plain_parts: list[str] = []
        for entry in raw_lyrics:
            if isinstance(entry, dict):
                txt = str(entry.get("text") or "").strip()
                start_ms = entry.get("start_time")
            else:
                txt = str(getattr(entry, "text", "") or "").strip()
                start_ms = getattr(entry, "start_time", None)
            if start_ms is not None:
                synced.append({"time": round(float(start_ms) / 1000.0, 2), "text": txt})
            if txt:
                plain_parts.append(txt)
        if synced:
            return {
                "synced": synced,
                "lyrics": "\n".join(plain_parts) or None,
                "source": str(source),
                "offline": False,
            }

    if isinstance(raw_lyrics, str) and raw_lyrics.strip():
        return {
            "synced": None,
            "lyrics": raw_lyrics.strip(),
            "source": str(source),
            "offline": False,
        }

    return None


async def resolve_lyrics(
    ytm_client: Any,
    video_id: str,
    title: str | None = None,
    artist: str | None = None,
    duration: int | None = None,
    custom_query: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve lyrics: Local .lrc (100% offline) -> Memory Cache -> LRCLIB + YTM."""
    track_row = await asyncio.to_thread(_lookup_track_local_info, db_path, video_id)
    local_audio = track_row.get("local_path") if track_row else None

    if custom_query and custom_query.strip():
        res = await fetch_lrclib_lyrics(title, artist, duration, custom_query=custom_query.strip())
        if res and (res.get("synced") or res.get("lyrics")):
            result_custom = dict(res)
            if local_audio and Path(local_audio).is_file():
                saved = await asyncio.to_thread(
                    save_local_lrc, local_audio, res.get("synced"), res.get("lyrics")
                )
                if saved:
                    result_custom["offline"] = True
            if len(_LYRICS_CACHE) >= 30 and video_id not in _LYRICS_CACHE:
                _LYRICS_CACHE.pop(next(iter(_LYRICS_CACHE)), None)
            _LYRICS_CACHE[video_id] = result_custom
            return result_custom
        return {"synced": None, "lyrics": None, "source": None, "offline": False}

    # 1. Offline-first check: sidecar .lrc next to local audio file
    if local_audio and Path(local_audio).is_file():
        offline_lrc = await asyncio.to_thread(read_local_lrc, local_audio)
        if offline_lrc:
            return offline_lrc

    # 2. Memory cache hit
    if video_id in _LYRICS_CACHE:
        cached = dict(_LYRICS_CACHE[video_id])
        local_audio_path = Path(local_audio) if local_audio else None
        has_local_audio = bool(local_audio_path and local_audio_path.is_file())
        if (
            has_local_audio
            and local_audio_path is not None
            and not get_local_lrc_path(local_audio_path).is_file()
            and (cached.get("synced") or cached.get("lyrics"))
        ):
            saved = await asyncio.to_thread(
                save_local_lrc, local_audio_path, cached.get("synced"), cached.get("lyrics")
            )
            if saved:
                cached["offline"] = True
                _LYRICS_CACHE[video_id] = cached
        elif not has_local_audio and cached.get("offline"):
            cached["offline"] = False
            _LYRICS_CACHE[video_id] = cached
        return cached

    eff_title = title or (track_row.get("title") if track_row else None)
    eff_artist = artist or (track_row.get("artist") if track_row else None)
    eff_dur = duration or (track_row.get("duration_s") if track_row else None)

    # 3. Concurrent LRCLIB + YouTube Music fetch (return early as soon as synced lyrics arrive)
    lrc_task = asyncio.create_task(fetch_lrclib_lyrics(eff_title, eff_artist, eff_dur))
    ytm_task = asyncio.create_task(asyncio.to_thread(_fetch_ytm_lyrics_sync, ytm_client, video_id))

    lrc: dict[str, Any] | None = None
    ytm: dict[str, Any] | None = None

    done, pending = await asyncio.wait(
        {lrc_task, ytm_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    early_synced: dict[str, Any] | None = None
    for t in done:
        try:
            val = t.result()
        except Exception:  # noqa: BLE001
            val = None
        if t is lrc_task and isinstance(val, dict):
            lrc = val
        elif t is ytm_task and isinstance(val, dict):
            ytm = val
        if isinstance(val, dict) and val.get("synced"):
            early_synced = val

    if early_synced is not None:
        for p in pending:
            p.cancel()
            p.add_done_callback(lambda t: None if t.cancelled() else t.exception())
    elif pending:
        for p in pending:
            try:
                val = await p
            except Exception:  # noqa: BLE001
                val = None
            if p is lrc_task and isinstance(val, dict):
                lrc = val
            elif p is ytm_task and isinstance(val, dict):
                ytm = val

    result: dict[str, Any]
    if early_synced is not None:
        result = early_synced
    elif lrc and lrc.get("synced"):
        result = lrc
    elif ytm and ytm.get("synced"):
        result = ytm
    elif lrc and lrc.get("lyrics"):
        result = lrc
    elif ytm and ytm.get("lyrics"):
        result = ytm
    else:
        result = {"synced": None, "lyrics": None, "source": None, "offline": False}

    if result.get("synced") or result.get("lyrics"):
        # Auto-cache .lrc sidecar if the track is downloaded locally
        if local_audio and Path(local_audio).is_file():
            saved = await asyncio.to_thread(
                save_local_lrc, local_audio, result.get("synced"), result.get("lyrics")
            )
            if saved:
                result["offline"] = True

        if len(_LYRICS_CACHE) >= 30:
            _LYRICS_CACHE.pop(next(iter(_LYRICS_CACHE)), None)
        _LYRICS_CACHE[video_id] = result

    return result


async def cache_lyrics_for_download(
    ytm_client: Any,
    db_path: Path | str | None,
    video_id: str,
) -> bool:
    """Fetch and write sidecar .lrc next to a newly downloaded track."""
    track_row = await asyncio.to_thread(_lookup_track_local_info, db_path, video_id)
    if not track_row or not track_row.get("local_path"):
        return False
    audio_path = Path(track_row["local_path"])
    if not audio_path.is_file():
        return False
    if get_local_lrc_path(audio_path).is_file():
        return True

    res = await resolve_lyrics(
        ytm_client,
        video_id=video_id,
        title=track_row.get("title"),
        artist=track_row.get("artist"),
        duration=track_row.get("duration_s"),
        db_path=db_path,
    )
    return bool(res.get("synced") or res.get("lyrics"))


async def backfill_downloaded_lyrics(ytm_client: Any, db_path: Path | str | None) -> int:
    """Silently cache .lrc files for all already-downloaded tracks missing a sidecar .lrc."""
    if not db_path or not Path(db_path).is_file():
        return 0

    def _list_downloaded() -> list[dict[str, Any]]:
        with get_sync_connection(db_path) as conn:
            rows = conn.execute(
                "SELECT video_id, title, artist, duration_s, local_path "
                "FROM tracks WHERE local_path IS NOT NULL AND local_path != '';"
            ).fetchall()
            return [dict(r) for r in rows]

    try:
        rows = await asyncio.to_thread(_list_downloaded)
    except Exception:  # noqa: BLE001
        return 0

    cached_count = 0
    for row in rows:
        lp = row.get("local_path")
        if not lp:
            continue
        audio_path = Path(lp)
        if not audio_path.is_file() or get_local_lrc_path(audio_path).is_file():
            continue
        try:
            ok = await cache_lyrics_for_download(ytm_client, db_path, str(row["video_id"]))
            if ok:
                cached_count += 1
                logger.info("Cached offline .lrc lyrics for %s", row.get("title") or row["video_id"])
            await asyncio.sleep(0.35)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed caching offline .lrc lyrics for %s: %s", row.get("video_id"), e)
            continue
    return cached_count
