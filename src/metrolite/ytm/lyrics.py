"""Offline-first multi-provider time-synced lyrics resolver.

Multi-provider cascade ranked word (3) > line (2) > plain (1):
1. Unison (BetterLyrics)  — TTML / LRC / plain (exact video_id -> metadata, with 503 retry)
2. BetterLyrics Public    — api.betterlyrics.org cached Apple Music TTML word sync
3. Paxsenix Apple Music   — iTunes Search -> Apple Music TTML/Syllable word sync
4. LyricsPlus (KPOE v2)   — Apple Music / Musixmatch / Spotify word/syllable sync
5. lrc.red                — ISRC TTML (word or line + songwriters)
6. LRCLIB                 — Standard or Enhanced LRC / multi-script variants
7. SimpMusic + YT Music   — Exact video_id synced timestamps (also calibrates intro offset!)

Multi-language / multi-script variants are returned ONLY when real alternate versions
exist for the track (provider Latin + Hindi/native LRC, native TTML x-roman/x-translation).
No machine translation is used.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import weakref
import xml.etree.ElementTree as ET
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from metrolite.store.db import get_sync_connection

logger = logging.getLogger(__name__)

_LRC_LINE_RE = re.compile(r"\[(\d{1,2}):(\d{2}(?:\.\d{1,3})?)\]")
_LRC_OFFSET_RE = re.compile(r"^\s*\[offset\s*:\s*([+-]?\d+)\s*\]\s*$", re.IGNORECASE)
_ENHANCED_WORD_RE = re.compile(r"<(\d{1,2}):(\d{2}(?:\.\d{1,3})?)>")
_DUET_TAG_RE = re.compile(r"^\s*(v1|v2|f|m)\s*:\s*", re.IGNORECASE)
_BRACKET_RE = re.compile(r"\s*[\(\[\{].*?[\)\]\}]")
_FEAT_IN_TITLE_RE = re.compile(
    r"[\(\[\{]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]\}]+)[\)\]\}]",
    re.IGNORECASE,
)

_MAX_LYRICS_CACHE = 64
_LYRICS_CACHE_SCHEMA_VERSION = 5
_LYRICS_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_INFLIGHT_LYRICS: dict[tuple[str, str], asyncio.Task[dict[str, Any]]] = {}
_LYRICS_CLIENT_POOL: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = (
    weakref.WeakKeyDictionary()
)
_LYRICS_PREFETCH_SEMS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)

_INSTRUMENTAL_MIN_GAP_S = 5.0
_LINE_ONLY_INSTRUMENTAL_MIN_GAP_S = 10.0
_SYNC_RANK = {"word": 3, "line": 2, "plain": 1}

_LYRICSPLUS_BASES = (
    "https://lyricsplus.prjktla.my.id",
    "https://lyricsplus.prjktla.workers.dev",
    "https://lyricsplus.binimum.org",
    "https://lyrics-plus-backend.vercel.app",
    "https://lyricsplus-seven.vercel.app",
)

_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,|&|/|;|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bwith\b|\bx\b|·|\||×)\s*",
    re.IGNORECASE,
)
_ARTIST_STOPWORDS = {
    "the", "and", "feat", "ft", "featuring", "official", "topic",
    "music", "records", "unknown", "artist", "various", "artists", "vevo",
}
_EDIT_QUALIFIER_RE = re.compile(
    r"\b(radio\s*edit|remix|slowed|reverb|acoustic|unplugged|live|lofi|lo-fi|cover|"
    r"sped\s*up|mashup|trending\s*version|reprise|extended|short\s*version|instrumental|"
    r"karaoke|nightcore|8d)\b",
    re.IGNORECASE,
)
_EDIT_QUALIFIER_STRICT_RE = _EDIT_QUALIFIER_RE

_HTTP_HEADERS = {
    "User-Agent": "ORBIT/1.2.0 (https://github.com/jainnaitk/ORBIT)",
    "Lrclib-Client": "ORBIT v1.2.0",
    "Accept": "application/json",
}

_LANG_LABELS: dict[str, str] = {
    "roman": "Romanized",
    "en": "English",
    "hi": "Hindi",
    "pa": "Punjabi",
    "ml": "Malayalam",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "bn": "Bengali",
    "gu": "Gujarati",
    "mr": "Marathi",
    "ur": "Urdu",
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "tr": "Turkish",
    "el": "Greek",
    "id": "Indonesian",
}

_ENGLISH_STOPWORDS = {
    "the", "and", "you", "that", "this", "with", "for", "from", "have", "your",
    "not", "are", "was", "were", "all", "can", "will", "just", "like", "know",
    "when", "what", "there", "been", "into", "could", "would", "never", "love",
    "heart", "night", "time", "down", "back", "feel", "want", "need", "away",
}

_HINGLISH_MARKERS = {
    "tum", "tera", "teri", "tere", "mera", "meri", "mere", "dil", "pyar", "pyaar",
    "ishq", "yaad", "zindagi", "duniya", "khuda", "mohabbat", "saath", "raat",
    "baat", "aankhon", "sanam", "jaana", "chahun", "kaise", "kyun", "kya", "nahi",
    "nahin", "hum", "mein", "main", "hai", "hain", "hoon", "ko", "se", "bhi",
    "tujhe", "mujhe", "apna", "sapna", "dhadkan", "khwab", "waqt", "lamha",
}

_SCRIPT_LANG_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("hi", re.compile(r"[\u0900-\u097F]")),
    ("pa", re.compile(r"[\u0A00-\u0A7F]")),
    ("ml", re.compile(r"[\u0D00-\u0D7F]")),
    ("ta", re.compile(r"[\u0B80-\u0BFF]")),
    ("te", re.compile(r"[\u0C00-\u0C7F]")),
    ("kn", re.compile(r"[\u0C80-\u0CFF]")),
    ("bn", re.compile(r"[\u0980-\u09FF]")),
    ("gu", re.compile(r"[\u0A80-\u0AFF]")),
    ("ko", re.compile(r"[\uAC00-\uD7A3]")),
    ("ja", re.compile(r"[\u3040-\u30FF]")),
    ("zh", re.compile(r"[\u4E00-\u9FFF]")),
    ("ru", re.compile(r"[\u0400-\u04FF]")),
    ("el", re.compile(r"[\u0370-\u03FF]")),
    ("ar", re.compile(r"[\u0600-\u06FF]")),
]
_URDU_SPECIFIC_RE = re.compile(r"[\u0679\u0688\u0691\u06BA\u06D2\u067E\u0686\u0698\u06AF\u06A9]")


def _cache_put(video_id: str, data: dict[str, Any]) -> None:
    _LYRICS_CACHE[video_id] = data
    _LYRICS_CACHE.move_to_end(video_id)
    while len(_LYRICS_CACHE) > _MAX_LYRICS_CACHE:
        _LYRICS_CACHE.popitem(last=False)


@asynccontextmanager
async def _borrow_lyrics_client(timeout: float) -> AsyncIterator[httpx.AsyncClient]:
    """Reuse a keep-alive httpx client per event loop outside tests to avoid TLS handshakes."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=_HTTP_HEADERS,
        ) as temp_client:
            yield temp_client
        return

    loop = asyncio.get_running_loop()
    client = _LYRICS_CLIENT_POOL.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=_HTTP_HEADERS,
            limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
        )
        _LYRICS_CLIENT_POOL[loop] = client
    yield client


async def close_lyrics_clients() -> None:
    """Close pooled keep-alive HTTP clients on shutdown."""
    for loop_key, client in list(_LYRICS_CLIENT_POOL.items()):
        _LYRICS_CLIENT_POOL.pop(loop_key, None)
        with suppress(Exception):
            if not client.is_closed:
                await client.aclose()


def _norm_lang_code(raw: str | None) -> str | None:
    if not raw:
        return None
    base = raw.strip().lower().replace("_", "-").split("-")[0]
    if not base or len(base) > 5:
        return None
    return base


def _lang_label(code: str) -> str:
    return _LANG_LABELS.get(code.lower(), code.upper())


def _is_latin_script(text: str | None) -> bool:
    """Return True when text is predominantly Latin/Romanized script (English, Hinglish, etc.)."""
    sample = (text or "")[:1500]
    latin_count = len(re.findall(r"[A-Za-z]", sample))
    non_latin_count = sum(len(pat.findall(sample)) for _, pat in _SCRIPT_LANG_RULES)
    if latin_count == 0 and non_latin_count == 0:
        return True
    return latin_count >= non_latin_count * 2


def _is_abjad_script(text: str | None) -> bool:
    """Return True when text is predominantly unvoweled Arabic/Urdu script."""
    sample = (text or "")[:1500]
    ar_count = len(re.findall(r"[\u0600-\u06FF]", sample))
    latin_count = len(re.findall(r"[A-Za-z]", sample))
    return ar_count >= 6 and ar_count > latin_count


def _has_official_romanized(synced: list[dict[str, Any]] | None) -> bool:
    if not synced:
        return False
    lyric_lines = [ln for ln in synced if ln.get("type") != "instrumental"]
    if not lyric_lines:
        return False
    roman_lines = sum(1 for ln in lyric_lines if ln.get("romanized"))
    return roman_lines >= max(1, int(len(lyric_lines) * 0.5))


def _has_edit_qualifier(title: str | None) -> bool:
    return bool(title and _EDIT_QUALIFIER_STRICT_RE.search(title))


def _edit_qualifier_set(title: str | None) -> set[str]:
    if not title:
        return set()
    return {re.sub(r"\s+", " ", m.group(1).lower()) for m in _EDIT_QUALIFIER_RE.finditer(title)}


def _extract_featured_artists(title: str | None) -> list[str]:
    """Extract featured/collab artists embedded in titles like '(feat. Simran Bumrah)' or '(with Talwiinder)'."""
    if not title:
        return []
    found: list[str] = []
    for m in _FEAT_IN_TITLE_RE.finditer(title):
        raw = m.group(1)
        for seg in _ARTIST_SPLIT_RE.split(raw):
            name = seg.strip(" -–—.,")
            if name and name.lower() not in _ARTIST_STOPWORDS and name not in found:
                found.append(name)
    return found


def _combine_artists(artist: str | None, title: str | None) -> list[str]:
    """Return deduplicated list of track artists plus featured artists extracted from title."""
    out: list[str] = []
    seen_low: set[str] = set()
    for raw_name in [a.strip() for a in (artist or "").split(",") if a.strip()] + _extract_featured_artists(title):
        low = raw_name.lower()
        if low and low not in seen_low and low != "unknown artist":
            seen_low.add(low)
            out.append(raw_name)
    return out


def _extract_artist_phrases(artist: str | None) -> list[str]:
    raw = re.sub(r"\s*-\s*topic\b", "", str(artist or ""), flags=re.IGNORECASE)
    parts: list[str] = []
    for seg in _ARTIST_SPLIT_RE.split(raw):
        cleaned = re.sub(r"[^\w\s]", " ", seg.lower())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and cleaned not in _ARTIST_STOPWORDS:
            parts.append(cleaned)
    return parts


def _extract_artist_tokens(artist: str | None) -> set[str]:
    tokens: set[str] = set()
    for phrase in _extract_artist_phrases(artist):
        for w in phrase.split():
            if w not in _ARTIST_STOPWORDS and (
                len(w) >= 3 or (len(w) == 2 and len(phrase) == 2)
            ):
                tokens.add(w)
    return tokens


def _artists_match(expected_artist: str | None, candidate_artist: str | None) -> bool:
    """Verify candidate artist shares at least one artist name/token with expected track artist."""
    exp_phrases = _extract_artist_phrases(expected_artist)
    exp_tokens = _extract_artist_tokens(expected_artist)
    if not exp_phrases and not exp_tokens:
        return True

    cand_norm = re.sub(r"[^\w\s]", " ", str(candidate_artist or "").lower())
    cand_norm = re.sub(r"\s+", " ", cand_norm).strip()
    if not cand_norm:
        return False

    cand_phrases = _extract_artist_phrases(candidate_artist)
    for ep in exp_phrases:
        if ep in cand_norm:
            return True
        for cp in cand_phrases:
            if cp and (cp in ep or ep in cp):
                return True

    cand_tokens = _extract_artist_tokens(candidate_artist)
    return bool(exp_tokens & cand_tokens)


def _titles_match(expected_title: str | None, candidate_title: str | None) -> bool:
    """Verify candidate title matches expected title (exact, substring, or >=50% token overlap)."""
    nt = _norm_lyric_text(expected_title or "")
    if not nt:
        return True
    ht = _norm_lyric_text(candidate_title or "")
    if not ht:
        return False
    if nt == ht:
        return True
    if len(nt) >= 3 and len(ht) >= 3 and (nt in ht or ht in nt):
        return True
    nt_toks = {w for w in nt.split() if len(w) >= 2}
    ht_toks = {w for w in ht.split() if len(w) >= 2}
    if not nt_toks or not ht_toks:
        return False
    return (len(nt_toks & ht_toks) / len(nt_toks)) >= 0.5


def detect_lyrics_language(text: str, xml_lang: str | None = None) -> tuple[str, str]:
    """Detect primary language (code, label) of lyrics text."""
    norm_xml = _norm_lang_code(xml_lang)
    sample = (text or "")[:2000]

    best_script_code: str | None = None
    best_script_count = 0
    for code, pattern in _SCRIPT_LANG_RULES:
        count = len(pattern.findall(sample))
        if count > best_script_count:
            best_script_count = count
            best_script_code = code

    if best_script_code and best_script_count >= 4:
        if best_script_code == "ar" and _URDU_SPECIFIC_RE.search(sample):
            best_script_code = "ur"
        return best_script_code, _lang_label(best_script_code)

    if norm_xml and norm_xml in _LANG_LABELS:
        return norm_xml, _lang_label(norm_xml)

    words = re.findall(r"[A-Za-z']+", sample.lower())
    if words:
        hinglish_hits = sum(1 for w in words if w in _HINGLISH_MARKERS)
        english_hits = sum(1 for w in words if w in _ENGLISH_STOPWORDS)
        if hinglish_hits >= 3 and hinglish_hits >= english_hits:
            return "roman", "Romanized"
        if english_hits >= 3 and (english_hits / max(1, len(words))) >= 0.08:
            return "en", "English"

    return "roman", "Romanized"


def _variant_sort_key(v: dict[str, Any]) -> tuple[int, str]:
    """Priority: Romanized Latin (0) -> English (1) -> Hindi (2) -> other original (3)."""
    code = str(v.get("code") or "").lower()
    if code == "roman":
        return (0, code)
    if code == "en":
        return (1, code)
    if code == "hi":
        return (2, code)
    return (3, code)


def _merge_variants(
    primary: dict[str, Any],
    extras: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build deduplicated, priority-ordered real language/script variants for a track."""
    by_code: dict[str, dict[str, Any]] = {}

    def _add(entry: dict[str, Any]) -> None:
        code = str(entry.get("code") or "").lower().strip()
        if not code:
            return
        if not entry.get("synced") and not entry.get("lyrics"):
            return
        existing = by_code.get(code)
        if existing is None or _candidate_rank(entry) > _candidate_rank(existing):
            by_code[code] = entry

    prim_synced = primary.get("synced")
    prim_plain = primary.get("lyrics")
    prim_code = str(primary.get("original_lang") or "roman")
    prim_is_latin = _is_latin_script(prim_plain)
    _add(
        {
            "code": prim_code,
            "label": _lang_label(prim_code),
            "script": "latin" if prim_is_latin else "native",
            "synced": prim_synced,
            "lyrics": prim_plain,
            "sync_type": primary.get("sync_type") or _compute_sync_type(prim_synced),
            "source": primary.get("source"),
        }
    )

    for v in primary.get("variants") or []:
        if isinstance(v, dict):
            _add(v)
    for v in extras or []:
        if isinstance(v, dict):
            _add(v)

    ordered = sorted(by_code.values(), key=_variant_sort_key)
    return ordered


# ---------------------------------------------------------------------------
# LRC & TTML Parsers
# ---------------------------------------------------------------------------


def _extract_duet_tag(text: str) -> tuple[str, str | None]:
    """Detect and strip leading duet tags (v1:, v2:, F:, M:) -> (clean_text, align)."""
    m = _DUET_TAG_RE.match(text)
    if not m:
        return text, None
    tag = m.group(1).lower()
    align = "right" if tag in ("v2", "f") else "left"
    return text[m.end() :].strip(), align


def _parse_ttml_time(raw: str | None) -> float | None:
    s = str(raw or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        if s.endswith("ms"):
            return round(float(s[:-2]) / 1000.0, 3)
        if s.endswith("s"):
            return round(float(s[:-1]), 3)
        if s.endswith("m"):
            return round(float(s[:-1]) * 60.0, 3)
        if s.endswith("h"):
            return round(float(s[:-1]) * 3600.0, 3)
        parts = s.split(":")
        if len(parts) == 3:
            return round(int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]), 3)
        if len(parts) == 2:
            return round(int(parts[0]) * 60 + float(parts[1]), 3)
        return round(float(s), 3)
    except Exception:  # noqa: BLE001
        return None


def _parse_duration_seconds(raw: Any) -> float | None:
    """Parse provider duration in seconds, milliseconds (e.g. 207000), or MM:SS.mmm."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val <= 0:
            return None
        return round(val / 1000.0, 3) if val > 2000 else round(val, 3)
    s = str(raw).strip()
    if not s:
        return None
    parsed = _parse_ttml_time(s)
    if parsed is None or parsed <= 0:
        return None
    # Bare numeric string > 2000 (e.g. "207000" ms from LyricsPlus) -> convert ms to seconds
    if ":" not in s and not s.lower().endswith(("ms", "s", "m", "h")) and parsed > 2000:
        return round(parsed / 1000.0, 3)
    return parsed


def _xml_local(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def _xml_attr(el: ET.Element, name: str) -> str | None:
    if name in el.attrib:
        return el.attrib[name]
    suffix = "}" + name
    for k, v in el.attrib.items():
        if k.endswith(suffix):
            return v
    return None


def _fill_line_ends(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute realistic end timestamps for every synced lyric line."""
    for i, ln in enumerate(lines):
        if ln.get("type") == "instrumental" or ln.get("time") is None:
            continue
        cur_t = float(ln["time"])
        next_t: float | None = None
        for nxt in lines[i + 1 :]:
            if nxt.get("type") != "instrumental" and nxt.get("time") is not None:
                next_t = float(nxt["time"])
                break

        if ln.get("end") is not None:
            end_t = float(ln["end"])
            if next_t is not None and end_t > next_t:
                end_t = next_t
            ln["end"] = round(max(cur_t + 0.25, end_t), 3)
            continue

        words = ln.get("words") or []
        if words and words[-1].get("end") is not None:
            end_t = float(words[-1]["end"])
            if next_t is not None and end_t > next_t:
                end_t = next_t
            ln["end"] = round(max(cur_t + 0.25, end_t), 3)
            continue

        est = max(3.5, min(6.5, len(str(ln.get("text") or "")) * 0.10))
        if next_t is not None:
            gap = next_t - cur_t
            end_t = next_t if gap <= _LINE_ONLY_INSTRUMENTAL_MIN_GAP_S else min(next_t, cur_t + est)
        else:
            end_t = cur_t + est
        ln["end"] = round(max(cur_t + 0.25, end_t), 3)
    return lines


def _compute_sync_type(synced: list[dict[str, Any]] | None) -> str:
    if not synced or not _has_valid_timestamps(synced):
        return "plain"
    lyric_lines = [ln for ln in synced if ln.get("type") != "instrumental"]
    if not lyric_lines:
        return "plain"
    word_lines = sum(1 for ln in lyric_lines if len(ln.get("words") or []) >= 2)
    return "word" if word_lines >= 2 else "line"


def _has_valid_timestamps(synced: list[dict[str, Any]] | None) -> bool:
    """Reject flat/zero/broken synced lists (e.g. LyricsPlus returning time=0 for all lines)."""
    if not isinstance(synced, list) or not synced:
        return False
    times = [
        float(ln["time"])
        for ln in synced
        if isinstance(ln, dict)
        and ln.get("type") != "instrumental"
        and isinstance(ln.get("time"), (int, float))
    ]
    if not times:
        return False
    if len(times) == 1:
        return times[0] >= 0.5
    distinct = {round(t, 1) for t in times}
    max_t = max(times)
    nonzero = sum(1 for t in times if t > 0.05)
    if len(times) >= 4:
        return len(distinct) >= 3 and max_t >= 8.0 and nonzero >= max(2, len(times) // 2)
    return len(distinct) >= 2 and max_t >= 0.5


def _parse_enhanced_words(
    raw_content: str, line_time: float
) -> tuple[str, str | None, list[dict[str, Any]] | None, float | None]:
    """Parse Enhanced LRC inline `<mm:ss.xx>word` spans if present."""
    word_matches = list(_ENHANCED_WORD_RE.finditer(raw_content))
    if not word_matches:
        clean_text, align = _extract_duet_tag(raw_content.strip())
        return clean_text, align, None, None

    prefix = raw_content[: word_matches[0].start()].strip()
    prefix_clean, align = _extract_duet_tag(prefix)

    words: list[dict[str, Any]] = []
    explicit_end: float | None = None

    if prefix_clean:
        words.append(
            {"time": round(line_time, 3), "end": round(line_time, 3), "text": prefix_clean}
        )

    for idx, wm in enumerate(word_matches):
        w_time = round(int(wm.group(1)) * 60 + float(wm.group(2)), 3)
        seg_end = word_matches[idx + 1].start() if idx + 1 < len(word_matches) else len(raw_content)
        w_text = raw_content[wm.end() : seg_end].strip()

        if not w_text:
            if idx == len(word_matches) - 1 and words:
                explicit_end = w_time
            continue

        if align is None and not words:
            w_text, align = _extract_duet_tag(w_text)
            if not w_text:
                continue

        words.append({"time": w_time, "end": w_time, "text": w_text})

    if not words:
        return "", align, None, explicit_end

    for idx in range(len(words) - 1):
        words[idx]["end"] = round(max(words[idx]["time"] + 0.05, words[idx + 1]["time"]), 3)

    if explicit_end is not None:
        words[-1]["end"] = round(max(words[-1]["time"] + 0.05, explicit_end), 3)

    display_text = " ".join(w["text"] for w in words).strip()
    return display_text, align, words, explicit_end


def _inject_instrumental_breaks(
    lines: list[dict[str, Any]],
    explicit_ends: dict[int, float] | None = None,
    min_gap: float = _INSTRUMENTAL_MIN_GAP_S,
) -> list[dict[str, Any]]:
    """Finalize word/line end times FIRST, then insert instrumental breaks ONLY for true silent gaps."""
    if not lines:
        return []

    explicit_ends = explicit_ends or {}

    for idx, line in enumerate(lines):
        next_line = lines[idx + 1] if idx + 1 < len(lines) else None
        words = line.get("words")
        if isinstance(words, list) and words:
            last_w = words[-1]
            if idx not in explicit_ends and float(last_w.get("end", 0)) <= float(last_w["time"]):
                if next_line is not None:
                    gap_to_next = float(next_line["time"]) - float(last_w["time"])
                    if gap_to_next <= 3.0:
                        last_w["end"] = round(
                            max(float(last_w["time"]) + 0.05, float(next_line["time"])), 3
                        )
                    else:
                        last_w["end"] = round(float(last_w["time"]) + 1.2, 3)
                else:
                    last_w["end"] = round(float(last_w["time"]) + 0.8, 3)
            elif float(last_w["end"]) < float(last_w["time"]) + 0.05:
                last_w["end"] = round(float(last_w["time"]) + 0.05, 3)

    has_true_end: list[bool] = [
        bool(line.get("words") or line.get("end") is not None) for line in lines
    ]

    _fill_line_ends(lines)

    enriched: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        next_line = lines[idx + 1] if idx + 1 < len(lines) else None
        words = line.get("words")

        if not line.get("text") and not words:
            if next_line is not None:
                raw_gap = round(float(next_line["time"]) - float(line["time"]), 2)
                if raw_gap > min_gap:
                    enriched.append(
                        {
                            "type": "instrumental",
                            "time": round(float(line["time"]), 2),
                            "duration": raw_gap,
                            "text": "",
                        }
                    )
            continue

        enriched.append(line)

        if next_line is not None:
            raw_gap = round(float(next_line["time"]) - float(line["time"]), 2)
            if not has_true_end[idx] and raw_gap <= _LINE_ONLY_INSTRUMENTAL_MIN_GAP_S:
                continue
            line_end = float(line.get("end") if line.get("end") is not None else line["time"])
            if isinstance(words, list) and words:
                line_end = max(line_end, float(words[-1]["end"]))
            silent_gap = round(float(next_line["time"]) - line_end, 2)
            if silent_gap > min_gap:
                enriched.append(
                    {
                        "type": "instrumental",
                        "time": round(line_end, 2),
                        "duration": silent_gap,
                        "text": "",
                    }
                )

    return enriched


def parse_lrc_lines(lrc_text: str) -> list[dict[str, Any]]:
    """Parse standard and Enhanced LRC lyrics (including [offset:ms]) into sorted dicts."""
    raw_entries: list[tuple[dict[str, Any], float | None]] = []
    offset_sec = 0.0

    for raw in str(lrc_text or "").splitlines():
        off_m = _LRC_OFFSET_RE.match(raw)
        if off_m:
            try:
                off_ms = max(-15000, min(15000, int(off_m.group(1))))
                # LRC spec: positive [offset:+ms] shifts lyrics EARLIER (-ms), negative shifts LATER (+ms)
                offset_sec = -off_ms / 1000.0
            except ValueError:
                pass
            continue

        matches = list(_LRC_LINE_RE.finditer(raw))
        if not matches:
            continue
        content = raw[matches[-1].end() :]
        base_time = round(int(matches[0].group(1)) * 60 + float(matches[0].group(2)), 3)

        for m in matches:
            mins = int(m.group(1))
            secs = float(m.group(2))
            line_time = round(max(0.0, mins * 60 + secs + offset_sec), 3)
            time_shift = round(line_time - base_time, 3)

            text, align, words, explicit_end = _parse_enhanced_words(content, base_time)
            if words and time_shift != 0.0:
                words = [
                    {
                        "time": round(max(0.0, w["time"] + time_shift), 3),
                        "end": round(max(0.0, w["end"] + time_shift), 3),
                        "text": w["text"],
                    }
                    for w in words
                ]
                if explicit_end is not None:
                    explicit_end = round(max(0.0, explicit_end + time_shift), 3)

            entry: dict[str, Any] = {
                "type": "line",
                "time": round(line_time, 2) if line_time == round(line_time, 2) else line_time,
                "text": text,
            }
            if align is not None:
                entry["align"] = align
            if words is not None:
                if float(words[0]["time"]) > float(entry["time"]) + 0.15:
                    entry["time"] = words[0]["time"]
                entry["words"] = words

            raw_entries.append((entry, explicit_end))

    raw_entries.sort(key=lambda pair: pair[0]["time"])
    lines: list[dict[str, Any]] = []
    explicit_ends: dict[int, float] = {}
    for idx, (entry, exp_end) in enumerate(raw_entries):
        lines.append(entry)
        if exp_end is not None:
            explicit_ends[idx] = exp_end

    return _inject_instrumental_breaks(lines, explicit_ends=explicit_ends)


def parse_ttml(
    xml_text: str,
) -> tuple[list[dict[str, Any]], str, list[str], str | None, dict[str, list[str]]]:
    """Parse Apple Music / BetterLyrics / lrc.red TTML into ORBIT synced schema.

    Returns `(synced_lines, sync_type, songwriters, xml_lang, native_translations)`.
    """
    raw_xml = str(xml_text or "").strip().lstrip("\ufeff")
    if not raw_xml:
        return [], "plain", [], None, {}

    root = ET.fromstring(raw_xml)
    xml_lang = _norm_lang_code(_xml_attr(root, "lang"))

    songwriters: list[str] = []
    for el in root.iter():
        if _xml_local(el.tag) == "songwriter":
            name = (el.text or "").strip()
            if name and name not in songwriters:
                songwriters.append(name)

    def _collect_word_spans(parent: ET.Element) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for child in list(parent):
            if _xml_local(child.tag) != "span":
                continue
            role = (_xml_attr(child, "role") or "").lower()
            if role in ("x-translation", "x-roman"):
                continue
            child_timed = [
                c
                for c in list(child)
                if _xml_local(c.tag) == "span" and _xml_attr(c, "begin") is not None
            ]
            if child_timed:
                collected.extend(_collect_word_spans(child))
                if (
                    child.tail
                    and any(ch.isspace() for ch in child.tail)
                    and collected
                    and not collected[-1]["text"].endswith(" ")
                ):
                    collected[-1]["text"] += " "
                continue
            wb = _parse_ttml_time(_xml_attr(child, "begin"))
            if wb is None:
                continue
            we = _parse_ttml_time(_xml_attr(child, "end"))
            wtxt = re.sub(r"[\r\n]+", " ", "".join(child.itertext()))
            if not wtxt.strip():
                continue
            if child.tail and any(ch.isspace() for ch in child.tail) and not wtxt.endswith(" "):
                wtxt += " "
            collected.append({"time": wb, "end": we, "text": wtxt})
        return collected

    def _collect_inline_translations(parent: ET.Element) -> dict[str, str]:
        found: dict[str, str] = {}
        for child in list(parent):
            if _xml_local(child.tag) != "span":
                continue
            role = (_xml_attr(child, "role") or "").lower()
            if role == "x-translation":
                t_lang = _norm_lang_code(_xml_attr(child, "lang")) or "en"
                t_txt = re.sub(r"\s+", " ", "".join(child.itertext())).strip()
                if t_txt:
                    found[t_lang] = t_txt
        return found

    def _collect_inline_roman(parent: ET.Element) -> str | None:
        parts: list[str] = []
        for child in list(parent):
            if _xml_local(child.tag) != "span":
                continue
            role = (_xml_attr(child, "role") or "").lower()
            if role == "x-roman":
                r_txt = re.sub(r"\s+", " ", "".join(child.itertext())).strip()
                if r_txt:
                    parts.append(r_txt)
        return " ".join(parts).strip() or None

    body = None
    for el in root.iter():
        if _xml_local(el.tag) == "body":
            body = el
            break
    scope = body if body is not None else root

    primary_agent: str | None = None
    raw_lines: list[dict[str, Any]] = []
    explicit_ends: dict[int, float] = {}
    per_line_translations: list[dict[str, str]] = []
    seen_translation_langs: set[str] = set()

    for p in scope.iter():
        if _xml_local(p.tag) != "p":
            continue
        p_begin = _parse_ttml_time(_xml_attr(p, "begin"))
        p_end = _parse_ttml_time(_xml_attr(p, "end"))
        agent = (_xml_attr(p, "agent") or "").strip().lower()

        words = _collect_word_spans(p)
        if words:
            words[-1]["text"] = words[-1]["text"].rstrip()
            for idx_w, w in enumerate(words):
                if w["end"] is None:
                    if idx_w + 1 < len(words):
                        w["end"] = words[idx_w + 1]["time"]
                    elif p_end is not None:
                        w["end"] = p_end
                    else:
                        w["end"] = round(w["time"] + 0.35, 3)
                w["end"] = round(max(w["time"] + 0.05, float(w["end"])), 3)
                w["text"] = re.sub(r",(?=\S)", ", ", w["text"])
                if idx_w < len(words) - 1 and w["text"].endswith(","):
                    w["text"] += " "
                if idx_w > 0 and w["text"].lstrip().startswith(","):
                    words[idx_w - 1]["text"] = words[idx_w - 1]["text"].rstrip()
            text = re.sub(
                r"\s+,", ",", re.sub(r",(?=\S)", ", ", "".join(w["text"] for w in words).strip())
            )
            # Always anchor line start/end to actual first/last sung word so instrumental pre-roll
            # inside <p begin="..."> never activates a line 10 seconds before vocals begin!
            p_begin = words[0]["time"]
            p_end = words[-1]["end"]
        else:
            parts: list[str] = []
            if p.text:
                parts.append(p.text)
            for child in list(p):
                role = (_xml_attr(child, "role") or "").lower()
                if role not in ("x-translation", "x-roman"):
                    parts.append("".join(child.itertext()))
                if child.tail:
                    parts.append(child.tail)
            text = re.sub(
                r"\s+,", ",", re.sub(r",(?=\S)", ", ", re.sub(r"\s+", " ", "".join(parts)).strip())
            )

        if not text or p_begin is None:
            continue

        align: str | None = None
        if agent:
            if agent in ("v2", "f", "female"):
                align = "right"
            elif agent in ("v1", "m", "male"):
                align = "left"
            else:
                if primary_agent is None:
                    primary_agent = agent
                    align = "left"
                elif agent != primary_agent:
                    align = "right"
                else:
                    align = "left"

        entry: dict[str, Any] = {
            "type": "line",
            "time": p_begin,
            "end": p_end,
            "text": text,
        }
        if align is not None:
            entry["align"] = align
        if words:
            entry["words"] = words
            explicit_ends[len(raw_lines)] = words[-1]["end"]

        inline_roman = _collect_inline_roman(p)
        if inline_roman:
            entry["romanized"] = inline_roman

        inline_tr = _collect_inline_translations(p)
        for lc in inline_tr:
            seen_translation_langs.add(lc)
        per_line_translations.append(inline_tr)
        raw_lines.append(entry)

    paired = list(zip(raw_lines, per_line_translations, strict=True))
    paired.sort(key=lambda pair: float(pair[0]["time"]))

    sorted_lines: list[dict[str, Any]] = []
    sorted_explicit_ends: dict[int, float] = {}
    native_translations: dict[str, list[str]] = {lc: [] for lc in seen_translation_langs}

    for idx, (ln, tr_map) in enumerate(paired):
        sorted_lines.append(ln)
        if ln.get("words"):
            sorted_explicit_ends[idx] = float(ln["words"][-1]["end"])
        for lc in seen_translation_langs:
            native_translations[lc].append(tr_map.get(lc, ln["text"]))

    for lc in list(native_translations.keys()):
        covered = sum(1 for _, tr_map in paired if lc in tr_map)
        if covered < max(1, int(len(paired) * 0.3)):
            native_translations.pop(lc, None)

    synced = _inject_instrumental_breaks(sorted_lines, explicit_ends=sorted_explicit_ends)
    sync_type = _compute_sync_type(synced)
    return synced, sync_type, songwriters, xml_lang, native_translations


def _format_ts(seconds: float) -> str:
    t = max(0.0, float(seconds))
    mins = int(t // 60)
    secs = t - mins * 60
    return f"{mins:02d}:{secs:05.2f}"


def format_lrc_lines(synced: list[dict[str, Any]]) -> str:
    """Serialize synced entries into standard or Enhanced [MM:SS.xx] LRC format."""
    out: list[str] = []
    for item in synced:
        if item.get("type") == "instrumental":
            continue
        ts = _format_ts(float(item.get("time", 0.0)))
        align = item.get("align")
        prefix = "v2: " if align == "right" else ("v1: " if align == "left" else "")
        words = item.get("words")
        if isinstance(words, list) and words:
            parts = [
                f"<{_format_ts(float(w.get('time', 0.0)))}>{str(w.get('text') or '').strip()}"
                for w in words
            ]
            last_end = words[-1].get("end")
            last_time = words[-1].get("time")
            if (
                isinstance(last_end, (int, float))
                and isinstance(last_time, (int, float))
                and last_end > last_time
            ):
                parts.append(f"<{_format_ts(float(last_end))}>")
            out.append(f"[{ts}] {prefix}{' '.join(parts)}")
        else:
            txt = str(item.get("text") or "").strip()
            out.append(f"[{ts}] {prefix}{txt}")
    return "\n".join(out) + "\n"


def get_local_lrc_path(audio_path: Path | str) -> Path:
    """Return sidecar .lrc path alongside a downloaded audio file."""
    return Path(audio_path).with_suffix(".lrc")


def get_local_lyrics_json_path(audio_path: Path | str) -> Path:
    """Return rich sidecar .lyrics.json path preserving word/syllable timing & credits."""
    return Path(audio_path).with_suffix(".lyrics.json")


def _build_native_ttml_variants(
    synced: list[dict[str, Any]] | None,
    source: str | None,
    native_translations: dict[str, list[str]] | None,
) -> list[dict[str, Any]]:
    """Build real synced variants from TTML embedded x-translation tracks."""
    if not synced or not native_translations:
        return []
    out: list[dict[str, Any]] = []
    for lang_code, tr_lines in native_translations.items():
        norm = _norm_lang_code(lang_code)
        if not norm or not tr_lines:
            continue
        cursor = 0
        tr_synced: list[dict[str, Any]] = []
        plain_parts: list[str] = []
        for ln in synced:
            if ln.get("type") == "instrumental":
                tr_synced.append(dict(ln))
                continue
            txt = tr_lines[cursor] if cursor < len(tr_lines) else str(ln.get("text") or "")
            cursor += 1
            entry: dict[str, Any] = {
                "type": "line",
                "time": ln["time"],
                "end": ln.get("end"),
                "text": txt,
            }
            if ln.get("align"):
                entry["align"] = ln["align"]
            tr_synced.append(entry)
            if txt.strip():
                plain_parts.append(txt.strip())
        plain_str = "\n".join(plain_parts) or None
        out.append(
            {
                "code": norm,
                "label": _lang_label(norm),
                "script": "latin" if _is_latin_script(plain_str) else "native",
                "synced": tr_synced,
                "lyrics": plain_str,
                "sync_type": "line",
                "source": source,
            }
        )
    return out


def _enrich_lyrics_payload(
    payload: dict[str, Any],
    *,
    xml_lang: str | None = None,
    native_translations: dict[str, list[str]] | None = None,
    extra_variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach sync_type, original_lang, and genuine multi-language variants."""
    synced = payload.get("synced")
    if isinstance(synced, list) and synced and not _has_valid_timestamps(synced):
        # Demote broken all-zero timestamp lists to plain text so they never jump to the last line
        if not payload.get("lyrics"):
            payload["lyrics"] = (
                "\n".join(
                    str(ln.get("text") or "")
                    for ln in synced
                    if ln.get("type") != "instrumental" and ln.get("text")
                )
                or None
            )
        payload["synced"] = None
        payload["sync_type"] = "plain"
        synced = None

    plain = payload.get("lyrics")
    if not plain and isinstance(synced, list):
        plain = (
            "\n".join(
                str(ln.get("text") or "")
                for ln in synced
                if ln.get("type") != "instrumental" and ln.get("text")
            )
            or None
        )
        payload["lyrics"] = plain

    payload["sync_type"] = _compute_sync_type(synced) if synced else "plain"
    sample = plain or ""
    orig_code, _orig_label = detect_lyrics_language(sample, xml_lang=xml_lang)
    payload["original_lang"] = orig_code

    ttml_variants = _build_native_ttml_variants(synced, payload.get("source"), native_translations)
    all_extras = list(extra_variants or []) + ttml_variants
    merged = _merge_variants(payload, all_extras)
    payload["variants"] = merged if len(merged) > 1 else []
    payload.setdefault("songwriters", [])
    return payload


def read_local_lrc(audio_path: Path | str) -> dict[str, Any] | None:
    """Read and parse local sidecar .lyrics.json (preferred) or .lrc lyrics if present on disk."""
    import json

    json_path = get_local_lyrics_json_path(audio_path)
    if json_path.is_file():
        try:
            raw_json = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw_json, dict) and (raw_json.get("synced") or raw_json.get("lyrics")):
                raw_json["offline"] = True
                return _enrich_lyrics_payload(raw_json)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed reading local .lyrics.json %s: %s", json_path, e)

    lrc_path = get_local_lrc_path(audio_path)
    if not lrc_path.is_file():
        return None
    try:
        raw = lrc_path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return None
        synced = parse_lrc_lines(raw)
        if synced:
            plain = "\n".join(
                line["text"] for line in synced if line.get("type") != "instrumental" and line["text"]
            )
            return _enrich_lyrics_payload(
                {
                    "synced": synced,
                    "lyrics": plain or None,
                    "source": "Offline (.lrc)",
                    "offline": True,
                }
            )
        return _enrich_lyrics_payload(
            {
                "synced": None,
                "lyrics": raw,
                "source": "Offline (.lrc)",
                "offline": True,
            }
        )
    except OSError as e:
        logger.debug("Failed reading local .lrc %s: %s", lrc_path, e)
        return None


def save_local_lrc(
    audio_path: Path | str,
    synced: list[dict[str, Any]] | None,
    plain: str | None = None,
    full_payload: dict[str, Any] | None = None,
) -> Path | None:
    """Persist rich .lyrics.json (preserving exact word timings) + .lrc alongside downloaded audio."""
    import json

    audio = Path(audio_path)
    if not audio.parent.is_dir():
        return None
    lrc_path = get_local_lrc_path(audio)
    json_path = get_local_lyrics_json_path(audio)
    try:
        if full_payload and (synced or (plain and plain.strip())):
            to_save = {
                k: v
                for k, v in full_payload.items()
                if not str(k).startswith("_")
            }
            to_save["offline"] = True
            to_save["schema_v"] = _LYRICS_CACHE_SCHEMA_VERSION
            json_path.write_text(
                json.dumps(to_save, ensure_ascii=False), encoding="utf-8", newline="\n"
            )
        if synced:
            lrc_path.write_text(format_lrc_lines(synced), encoding="utf-8", newline="\n")
            return lrc_path
        if plain and plain.strip():
            lrc_path.write_text(plain.strip() + "\n", encoding="utf-8", newline="\n")
            return lrc_path
    except OSError as e:
        logger.debug("Failed writing local lyrics %s: %s", lrc_path, e)
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


def _norm_lyric_text(s: str) -> str:
    cleaned = clean_track_title(s).lower()
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


# ---------------------------------------------------------------------------
# Provider 1: Unison (BetterLyrics) — TTML / LRC / Plain
# ---------------------------------------------------------------------------


async def fetch_unison_lyrics(
    video_id: str | None,
    title: str | None,
    artist: str | None,
    duration: int | None = None,
    custom_query: str | None = None,
    exact_video_only: bool = False,
    metadata_only: bool = False,
) -> dict[str, Any] | None:
    """Fetch word- or line-synced TTML/LRC from unison.betterlyrics.org."""
    clean_title = custom_query.strip() if custom_query else clean_track_title(title or "")
    artists = _combine_artists(artist, title)
    combined_artist_str = ", ".join(artists)
    is_special_edit = _has_edit_qualifier(title) and not custom_query

    urls: list[tuple[str, dict[str, str], bool]] = []
    if video_id and not custom_query and not metadata_only:
        urls.append(("https://unison.betterlyrics.org/lyrics", {"v": video_id}, True))
    if not exact_video_only:
        if clean_title and artists:
            for cand_artist in artists[:2]:
                meta_params: dict[str, str] = {"song": clean_title, "artist": cand_artist}
                if duration and duration > 10:
                    meta_params["duration"] = str(int(duration))
                urls.append(("https://unison.betterlyrics.org/lyrics", meta_params, False))
        elif custom_query and clean_title:
            urls.append(("https://unison.betterlyrics.org/lyrics", {"song": clean_title}, False))

    if not urls:
        return None

    req_timeout = 1.8 if exact_video_only else 2.6

    async def _fetch_one(
        client: httpx.AsyncClient, url: str, params: dict[str, str], is_exact_vid: bool
    ) -> dict[str, Any] | None:
        try:
            resp = await client.get(url, params=params, timeout=req_timeout)
            # Per Unison spec: 503 LOOKUP_PENDING means BiniLink is resolving a new ID
            if resp.status_code == 503 and is_exact_vid:
                await asyncio.sleep(0.3)
                resp = await client.get(url, params=params, timeout=req_timeout)
            if resp.status_code != 200:
                return None
            payload = resp.json()
        except Exception:  # noqa: BLE001
            return None

        if not isinstance(payload, dict) or payload.get("success") is False:
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            return None

        song_obj = data.get("song") if isinstance(data.get("song"), dict) else {}
        rec_dur_diff = 0.0

        # Guard metadata-only fallback against mismatched titles, artists, or extreme duration diffs.
        # Allow up to ±10s here so word-synced tracks can be auto-aligned to exact YTM timestamps;
        # _run_provider_cascade rejects unaligned matches >3.5s afterwards.
        if not is_exact_vid and not custom_query:
            rec_title = (
                data.get("title")
                or data.get("songName")
                or data.get("name")
                or song_obj.get("title")
                or song_obj.get("name")
            )
            if rec_title and not _titles_match(clean_title, str(rec_title)):
                return None
            rec_artist = (
                data.get("artist")
                or data.get("artistName")
                or song_obj.get("artist")
                or song_obj.get("artistName")
            )
            if not rec_artist:
                raw_artists = data.get("artists") or song_obj.get("artists")
                if isinstance(raw_artists, list):
                    rec_artist = ", ".join(
                        str(a.get("name") if isinstance(a, dict) else a)
                        for a in raw_artists
                        if a
                    )
            if rec_artist and combined_artist_str and not _artists_match(
                combined_artist_str, str(rec_artist)
            ):
                return None
            raw_dur = (
                data.get("duration")
                or song_obj.get("duration")
                or song_obj.get("durationMs")
            )
            rec_dur = _parse_duration_seconds(raw_dur)
            if duration and duration > 10:
                if rec_dur is not None and rec_dur > 10:
                    rec_dur_diff = abs(rec_dur - float(duration))
                    max_diff = 3.5 if is_special_edit else 10.0
                    if rec_dur_diff > max_diff:
                        return None
                elif is_special_edit:
                    return None

        raw = (
            data.get("lyrics")
            or data.get("content")
            or data.get("ttml")
            or data.get("lrc")
            or ""
        )
        if not isinstance(raw, str) or not raw.strip():
            return None

        fmt = str(data.get("format") or "").lower()
        synced: list[dict[str, Any]] | None = None
        plain: str | None = None
        sync_type = "plain"
        writers: list[str] = []
        xml_lang: str | None = None
        native_tr: dict[str, list[str]] = {}

        try:
            if fmt == "ttml" or raw.lstrip().startswith("<"):
                synced, sync_type, writers, xml_lang, native_tr = parse_ttml(raw)
            elif fmt == "lrc" or _LRC_LINE_RE.search(raw):
                synced = parse_lrc_lines(raw)
                sync_type = _compute_sync_type(synced)
            else:
                plain = raw.strip()
                sync_type = "plain"
        except Exception:  # noqa: BLE001
            return None

        if not synced and not plain:
            return None

        if (
            not is_exact_vid
            and not custom_query
            and duration
            and duration > 30
            and synced
        ):
            last_line_time = max(
                (
                    float(ln.get("time", 0.0))
                    for ln in synced
                    if ln.get("type") != "instrumental"
                ),
                default=0.0,
            )
            if last_line_time > float(duration) + 12.0:
                return None

        song_id = data.get("songId") or song_obj.get("id") or data.get("id") or ""
        credit_url = (
            f"https://unison.betterlyrics.org/song/{quote(str(song_id))}"
            if song_id
            else "https://unison.betterlyrics.org"
        )
        sub = data.get("submitter") if isinstance(data.get("submitter"), dict) else {}
        sub_name = str(sub.get("displayName") or sub.get("name") or "").strip() or None
        sub_id = str(sub.get("id") or sub.get("keyId") or "").strip()
        sub_url = (
            f"https://unison.betterlyrics.org/curator/{quote(sub_id)}" if sub_id else None
        )
        if isinstance(data.get("songwriters"), list) and data["songwriters"]:
            writers = [str(w).strip() for w in data["songwriters"] if str(w).strip()]

        return _enrich_lyrics_payload(
            {
                "synced": synced or None,
                "lyrics": plain,
                "source": "Unison",
                "sync_type": sync_type,
                "credit": "Unison",
                "credit_url": credit_url,
                "submitter": sub_name,
                "submitter_url": sub_url,
                "songwriters": writers,
                "offline": False,
                "_exact_video": is_exact_vid,
                "_duration_diff": rec_dur_diff,
            },
            xml_lang=xml_lang,
            native_translations=native_tr,
        )

    try:
        async with _borrow_lyrics_client(req_timeout) as client:
            if len(urls) == 1:
                return await _fetch_one(client, *urls[0])
            results = await asyncio.gather(
                *(_fetch_one(client, u, p, ev) for u, p, ev in urls),
                return_exceptions=True,
            )
            best_res: dict[str, Any] | None = None
            for res in results:
                if isinstance(res, dict) and (
                    best_res is None or _candidate_quality(res) > _candidate_quality(best_res)
                ):
                    best_res = res
            return best_res
    except Exception as e:  # noqa: BLE001
        logger.debug("Unison lookup failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Provider 2: LyricsPlus (KPOE v2) — Apple Music / Musixmatch / Spotify Word Sync
# ---------------------------------------------------------------------------


def _parse_lyricsplus_payload(
    data: dict[str, Any],
    duration: int | None,
    is_special_edit: bool,
    custom_query: str | None,
    expected_title: str | None = None,
    expected_artist: str | None = None,
) -> dict[str, Any] | None:
    """Parse LyricsPlus /v2/lyrics/get JSON into ORBIT synced schema."""
    raw_type = str(data.get("type") or "").strip().lower()
    if raw_type in ("none", "plain", "static", "unsynced"):
        return None

    raw_lines = data.get("lyrics")
    if not isinstance(raw_lines, list) or not raw_lines:
        return None

    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if not custom_query:
        rec_title = meta.get("title") or meta.get("song") or data.get("title")
        if rec_title and expected_title and not _titles_match(expected_title, str(rec_title)):
            return None
        rec_artist = meta.get("artist") or meta.get("artistName") or data.get("artist")
        if rec_artist and expected_artist and not _artists_match(expected_artist, str(rec_artist)):
            return None

    total_dur_raw = meta.get("totalDuration") or meta.get("duration")
    dur_diff = 0.0
    dur_verified = False
    if not custom_query and duration and duration > 10:
        parsed_dur = _parse_duration_seconds(total_dur_raw) if total_dur_raw is not None else None
        if parsed_dur is not None and parsed_dur > 10:
            dur_verified = True
            dur_diff = abs(parsed_dur - float(duration))
            # Allow up to ±12s for studio tracks so Apple Music word-sync can be
            # auto-aligned against exact YTM timestamps (unaligned >3.5s is rejected later).
            max_diff = 3.5 if is_special_edit else 12.0
            if dur_diff > max_diff:
                return None
        else:
            # Missing totalDuration -> mark unverified so _align_or_filter requires exact-video alignment
            dur_diff = 5.0

    xml_lang = _norm_lang_code(str(meta.get("language") or ""))
    writers_raw = meta.get("songWriters") or meta.get("songwriters") or []
    writers = [str(w).strip() for w in writers_raw if str(w).strip()] if isinstance(writers_raw, list) else []
    src_name = str(meta.get("source") or "LyricsPlus").strip()
    source_label = f"LyricsPlus ({src_name})" if src_name.lower() != "lyricsplus" else "LyricsPlus"

    primary_singer: str | None = None
    parsed_lines: list[dict[str, Any]] = []
    explicit_ends: dict[int, float] = {}

    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        t_ms = item.get("time")
        if t_ms is None:
            continue
        line_t = round(float(t_ms) / 1000.0, 3)
        dur_ms = item.get("duration")
        line_end = (
            round(line_t + max(0.25, float(dur_ms) / 1000.0), 3)
            if isinstance(dur_ms, (int, float)) and dur_ms > 0
            else None
        )

        # Parse word/syllable timing (`syllabus`)
        words: list[dict[str, Any]] = []
        raw_syllables = item.get("syllabus")
        if isinstance(raw_syllables, list) and raw_syllables:
            for syl in raw_syllables:
                if not isinstance(syl, dict):
                    continue
                st_ms = syl.get("time")
                stxt = str(syl.get("text") or "")
                if st_ms is None or not stxt.strip():
                    continue
                wt = round(float(st_ms) / 1000.0, 3)
                sd_ms = syl.get("duration")
                we = (
                    round(wt + max(0.05, float(sd_ms) / 1000.0), 3)
                    if isinstance(sd_ms, (int, float)) and sd_ms > 0
                    else round(wt + 0.35, 3)
                )
                words.append({"time": wt, "end": we, "text": stxt})

        text = str(item.get("text") or "").strip()
        if not text and words:
            text = "".join(w["text"] for w in words).strip()
        if not text:
            continue

        # Duet alignment via element.singer
        elem = item.get("element") if isinstance(item.get("element"), dict) else {}
        singer = str(elem.get("singer") or "").strip().lower()
        align: str | None = None
        if singer:
            if singer in ("v2", "f", "female"):
                align = "right"
            elif singer in ("v1", "m", "male"):
                align = "left"
            else:
                if primary_singer is None:
                    primary_singer = singer
                    align = "left"
                elif singer != primary_singer:
                    align = "right"
                else:
                    align = "left"

        if words:
            words[-1]["text"] = words[-1]["text"].rstrip()
            line_t = words[0]["time"]
            line_end = words[-1]["end"]

        entry: dict[str, Any] = {
            "type": "line",
            "time": line_t,
            "end": line_end,
            "text": text,
        }
        if align is not None:
            entry["align"] = align
        if words:
            entry["words"] = words
            explicit_ends[len(parsed_lines)] = words[-1]["end"]

        roman_obj = item.get("transliteration")
        if isinstance(roman_obj, dict) and roman_obj.get("text"):
            entry["romanized"] = str(roman_obj["text"]).strip()
        elif isinstance(roman_obj, str) and roman_obj.strip():
            entry["romanized"] = roman_obj.strip()

        parsed_lines.append(entry)

    if not parsed_lines or not _has_valid_timestamps(parsed_lines):
        return None

    parsed_lines.sort(key=lambda ln: float(ln["time"]))
    if not custom_query and duration and duration > 30:
        last_t = float(parsed_lines[-1]["time"])
        if last_t > float(duration) + 14.0:
            return None
        if not dur_verified and duration > 60 and last_t < float(duration) * 0.45:
            return None

    synced = _inject_instrumental_breaks(parsed_lines, explicit_ends=explicit_ends)
    sync_type = _compute_sync_type(synced)
    if sync_type == "plain":
        return None

    return _enrich_lyrics_payload(
        {
            "synced": synced,
            "lyrics": None,
            "source": source_label,
            "sync_type": sync_type,
            "credit": source_label,
            "credit_url": "https://github.com/ibratabian17/LyricsPlus",
            "submitter": None,
            "submitter_url": None,
            "songwriters": writers,
            "offline": False,
            "_duration_diff": dur_diff,
        },
        xml_lang=xml_lang,
    )


async def fetch_lyricsplus_lyrics(
    title: str | None,
    artist: str | None,
    duration: int | None,
    custom_query: str | None = None,
) -> dict[str, Any] | None:
    """Fetch Apple Music / Musixmatch / Spotify word-synced lyrics via LyricsPlus v2."""
    clean_title = custom_query.strip() if custom_query else clean_track_title(title or "")
    if not clean_title:
        return None
    artists = _combine_artists(artist, title)
    combined_artist = ", ".join(artists)
    is_special_edit = _has_edit_qualifier(title) and not custom_query

    # Try full artist list (e.g. "Afusic, Talwiinder") and primary artist (e.g. "Afusic")
    # so composer-first Bollywood/Punjabi credits still match Apple Music word sync.
    artist_variants: list[str] = []
    if not custom_query:
        if combined_artist:
            artist_variants.append(combined_artist)
        if artists and artists[0] != combined_artist:
            artist_variants.append(artists[0])
        if len(artists) > 1 and artists[1] not in artist_variants:
            artist_variants.append(artists[1])
    if not artist_variants:
        artist_variants.append("")

    try:
        async with _borrow_lyrics_client(2.8) as client:
            async def _query_one(
                base: str, cand_artist: str, include_duration: bool = True
            ) -> dict[str, Any] | None:
                params: dict[str, str] = {
                    "title": clean_title,
                    "source": "apple,lyricsplus,musixmatch-word,musixmatch,spotify",
                }
                if cand_artist:
                    params["artist"] = cand_artist
                if include_duration and duration and duration > 10:
                    params["duration"] = str(int(duration))
                try:
                    resp = await client.get(f"{base}/v2/lyrics/get", params=params, timeout=2.8)
                    if resp.status_code != 200:
                        return None
                    data = resp.json()
                    if not isinstance(data, dict):
                        return None
                    return _parse_lyricsplus_payload(
                        data,
                        duration=duration,
                        is_special_edit=is_special_edit,
                        custom_query=custom_query,
                        expected_title=clean_title,
                        expected_artist=combined_artist or artist,
                    )
                except Exception:  # noqa: BLE001
                    return None

            # Query primary KPOE servers with duration so KPOE matches the exact edit
            reqs: list[tuple[str, str, bool]] = [
                (_LYRICSPLUS_BASES[0], artist_variants[0], True),
                (_LYRICSPLUS_BASES[1], artist_variants[0], True),
                (_LYRICSPLUS_BASES[2], artist_variants[0], True),
            ]
            if len(artist_variants) > 1:
                reqs.append((_LYRICSPLUS_BASES[0], artist_variants[1], True))
            if len(artist_variants) > 2:
                reqs.append((_LYRICSPLUS_BASES[1], artist_variants[2], True))
            reqs.append((_LYRICSPLUS_BASES[3], artist_variants[0], True))

            tasks = [asyncio.create_task(_query_one(b, a, inc_d)) for b, a, inc_d in reqs]
            best_lp: dict[str, Any] | None = None
            try:
                for fut in asyncio.as_completed(tasks):
                    res = await fut
                    if res:
                        if best_lp is None or _candidate_quality(res) > _candidate_quality(best_lp):
                            best_lp = res
                        if _candidate_rank(best_lp) >= 3:
                            for t in tasks:
                                if not t.done():
                                    t.cancel()
                            return best_lp
                return best_lp
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()
    except Exception as e:  # noqa: BLE001
        logger.debug("LyricsPlus lookup failed for %r: %s", clean_title, e)

    return None


# ---------------------------------------------------------------------------
# Provider 2b: BetterLyrics Public Cache API (api.betterlyrics.org/getLyrics)
# ---------------------------------------------------------------------------


async def fetch_betterlyrics_api(
    title: str | None,
    artist: str | None,
    duration: int | None,
    custom_query: str | None = None,
) -> dict[str, Any] | None:
    """Fetch cached Apple Music / BetterLyrics TTML word-synced lyrics (keyless cache path)."""
    clean_title = custom_query.strip() if custom_query else clean_track_title(title or "")
    if not clean_title:
        return None
    artists = _combine_artists(artist, title)
    primary_artist = artists[0] if artists else ""
    if not primary_artist and not custom_query:
        return None
    is_special_edit = _has_edit_qualifier(title) and not custom_query

    params_list: list[dict[str, str]] = []
    if primary_artist:
        p1: dict[str, str] = {"s": clean_title, "a": primary_artist}
        if duration and duration > 10:
            p1["d"] = str(int(duration))
        params_list.append(p1)
        if duration and duration > 10 and not is_special_edit:
            params_list.append({"s": clean_title, "a": primary_artist})
    else:
        params_list.append({"s": clean_title, "a": ""})

    try:
        async with _borrow_lyrics_client(2.6) as client:
            async def _fetch_candidate(params: dict[str, str]) -> tuple[bool, dict[str, Any]] | None:
                try:
                    resp = await client.get(
                        "https://api.betterlyrics.org/getLyrics", params=params, timeout=2.6
                    )
                    if resp.status_code != 200:
                        return None
                    data = resp.json()
                    if not isinstance(data, dict):
                        return None
                    raw_ttml = data.get("ttml") or data.get("lyrics") or ""
                    if not isinstance(raw_ttml, str) or not raw_ttml.strip():
                        return None
                    if raw_ttml.lstrip().startswith("<"):
                        synced, sync_type, writers, xml_lang, native_tr = parse_ttml(raw_ttml)
                    elif _LRC_LINE_RE.search(raw_ttml):
                        synced = parse_lrc_lines(raw_ttml)
                        sync_type = _compute_sync_type(synced)
                        writers, xml_lang, native_tr = [], None, {}
                    else:
                        return None
                    if not synced or sync_type == "plain" or not _has_valid_timestamps(synced):
                        return None
                    if not custom_query and duration and duration > 30:
                        last_t = max(
                            (
                                float(ln.get("time", 0.0))
                                for ln in synced
                                if ln.get("type") != "instrumental"
                            ),
                            default=0.0,
                        )
                        if last_t > float(duration) + 14.0:
                            return None
                    return (
                        "d" in params,
                        _enrich_lyrics_payload(
                            {
                                "synced": synced,
                                "lyrics": None,
                                "source": "BetterLyrics",
                                "sync_type": sync_type,
                                "credit": "BetterLyrics",
                                "credit_url": "https://better-lyrics.boidu.dev",
                                "submitter": None,
                                "submitter_url": None,
                                "songwriters": writers,
                                "offline": False,
                                # Durationless fallback MUST align to exact-video YTM/SimpMusic
                                # (prevents 16s-early movie-intro drift like Illuminati!)
                                "_duration_diff": 0.0 if "d" in params else 6.0,
                            },
                            xml_lang=xml_lang,
                            native_translations=native_tr,
                        ),
                    )
                except Exception as inner_err:  # noqa: BLE001
                    logger.debug("BetterLyrics candidate skipped: %s", inner_err)
                    return None

            if len(params_list) == 1:
                single = await _fetch_candidate(params_list[0])
                return single[1] if single else None
            cand_results = await asyncio.gather(
                *(_fetch_candidate(p) for p in params_list),
                return_exceptions=True,
            )
            fallback_res: dict[str, Any] | None = None
            for item in cand_results:
                if isinstance(item, tuple):
                    has_dur, payload = item
                    if has_dur:
                        return payload
                    if fallback_res is None:
                        fallback_res = payload
            return fallback_res
    except Exception as e:  # noqa: BLE001
        logger.debug("BetterLyrics API lookup failed for %r: %s", clean_title, e)
    return None


# ---------------------------------------------------------------------------
# Provider 2c: Paxsenix Apple Music TTML / Syllable Word-Sync (via iTunes ID)
# ---------------------------------------------------------------------------


def _parse_paxsenix_payload(
    raw_body: str,
    duration: int | None,
    dur_diff: float,
    custom_query: str | None,
) -> dict[str, Any] | None:
    """Parse Paxsenix Apple Music response (TTML XML or Syllable/Word JSON) into ORBIT schema."""
    import json

    text = str(raw_body or "").strip()
    if not text:
        return None

    # 1. Raw TTML XML response
    if text.startswith("<"):
        try:
            synced, sync_type, writers, xml_lang, native_tr = parse_ttml(text)
            if synced and sync_type != "plain":
                return _enrich_lyrics_payload(
                    {
                        "synced": synced,
                        "lyrics": None,
                        "source": "Apple Music",
                        "sync_type": sync_type,
                        "credit": "Apple Music",
                        "offline": False,
                        "songwriters": writers,
                        "_duration_diff": dur_diff,
                    },
                    xml_lang=xml_lang,
                    native_translations=native_tr,
                )
        except Exception:  # noqa: BLE001
            return None
        return None

    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None

    # 2. JSON wrapper carrying embedded TTML string
    ttml_str = data.get("ttml") or data.get("ttmlContent")
    if isinstance(ttml_str, str) and ttml_str.lstrip().startswith("<"):
        try:
            synced, sync_type, writers, xml_lang, native_tr = parse_ttml(ttml_str)
            if synced and sync_type != "plain":
                return _enrich_lyrics_payload(
                    {
                        "synced": synced,
                        "lyrics": None,
                        "source": "Apple Music",
                        "sync_type": sync_type,
                        "credit": "Apple Music",
                        "offline": False,
                        "songwriters": writers,
                        "_duration_diff": dur_diff,
                    },
                    xml_lang=xml_lang,
                    native_translations=native_tr,
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("Paxsenix TTML parse skipped: %s", e)

    # 3. Paxsenix structured `content` array (Syllable / Word / Line JSON)
    content = data.get("content")
    if not isinstance(content, list) or not content:
        return None

    parsed_lines: list[dict[str, Any]] = []
    explicit_ends: dict[int, float] = {}

    for item in content:
        if not isinstance(item, dict):
            continue
        raw_t = item.get("timestamp")
        raw_end = item.get("endtime")
        opposite = bool(item.get("oppositeTurn"))
        align = "right" if opposite else None

        words: list[dict[str, Any]] = []
        raw_text = item.get("text")
        if isinstance(raw_text, list):
            for seg in raw_text:
                if not isinstance(seg, dict):
                    continue
                st_ms = seg.get("timestamp")
                se_ms = seg.get("endtime")
                stxt = str(seg.get("text") or "")
                if st_ms is None or not stxt.strip():
                    continue
                wt = round(float(st_ms) / 1000.0, 3)
                we = (
                    round(max(wt + 0.05, float(se_ms) / 1000.0), 3)
                    if isinstance(se_ms, (int, float)) and se_ms > 0
                    else round(wt + 0.35, 3)
                )
                is_part = bool(seg.get("part"))
                if not is_part and not stxt.endswith(" "):
                    stxt += " "
                words.append({"time": wt, "end": we, "text": stxt})
            if words:
                words[-1]["text"] = words[-1]["text"].rstrip()
            line_text = "".join(w["text"] for w in words).strip()
            line_t = words[0]["time"] if words else (
                round(float(raw_t) / 1000.0, 3) if isinstance(raw_t, (int, float)) else None
            )
            line_end = words[-1]["end"] if words else (
                round(float(raw_end) / 1000.0, 3)
                if isinstance(raw_end, (int, float)) and raw_end > 0
                else None
            )
        else:
            line_text = str(raw_text or "").strip()
            line_t = round(float(raw_t) / 1000.0, 3) if isinstance(raw_t, (int, float)) else None
            line_end = (
                round(float(raw_end) / 1000.0, 3)
                if isinstance(raw_end, (int, float)) and raw_end > 0
                else None
            )

        if not line_text or line_t is None:
            continue

        entry: dict[str, Any] = {
            "type": "line",
            "time": line_t,
            "end": line_end,
            "text": re.sub(r"\s+,", ",", re.sub(r",(?=\S)", ", ", line_text)),
        }
        if align is not None:
            entry["align"] = align
        if words:
            entry["words"] = words
            explicit_ends[len(parsed_lines)] = words[-1]["end"]
        parsed_lines.append(entry)

    if not parsed_lines or not _has_valid_timestamps(parsed_lines):
        return None
    parsed_lines.sort(key=lambda ln: float(ln["time"]))
    if (
        not custom_query
        and duration
        and duration > 30
        and float(parsed_lines[-1]["time"]) > float(duration) + 14.0
    ):
        return None

    synced = _inject_instrumental_breaks(parsed_lines, explicit_ends=explicit_ends)
    sync_type = _compute_sync_type(synced)
    if sync_type == "plain":
        return None

    return _enrich_lyrics_payload(
        {
            "synced": synced,
            "lyrics": None,
            "source": "Apple Music",
            "sync_type": sync_type,
            "credit": "Apple Music",
            "offline": False,
            "_duration_diff": dur_diff,
        }
    )


async def fetch_paxsenix_apple_lyrics(
    title: str | None,
    artist: str | None,
    duration: int | None,
    custom_query: str | None = None,
) -> dict[str, Any] | None:
    """Resolve Apple Music trackId via iTunes Search and fetch word-synced TTML via Paxsenix."""
    clean_title = custom_query.strip() if custom_query else clean_track_title(title or "")
    if not clean_title:
        return None
    artists = _combine_artists(artist, title)
    combined_artist = ", ".join(artists)
    primary_artist = artists[0] if artists else ""
    is_special_edit = _has_edit_qualifier(title) and not custom_query
    term = clean_title if custom_query else f"{clean_title} {primary_artist}".strip()

    try:
        async with _borrow_lyrics_client(3.0) as client:
            async def _itunes_search(country: str) -> list[dict[str, Any]]:
                try:
                    r = await client.get(
                        "https://itunes.apple.com/search",
                        params={"term": term, "entity": "song", "limit": "6", "country": country},
                        timeout=3.0,
                    )
                    if r.status_code != 200:
                        return []
                    arr = r.json().get("results")
                    return [x for x in arr if isinstance(x, dict)] if isinstance(arr, list) else []
                except Exception:  # noqa: BLE001
                    return []

            # Query India + US iTunes storefronts concurrently so Bollywood/Punjabi & global tracks both resolve
            in_res, us_res = await asyncio.gather(
                _itunes_search("IN"),
                _itunes_search("US"),
                return_exceptions=True,
            )
            results: list[dict[str, Any]] = []
            seen_ids: set[int] = set()
            for bucket in (in_res, us_res):
                if isinstance(bucket, list):
                    for item in bucket:
                        tid = item.get("trackId")
                        if isinstance(tid, int) and tid not in seen_ids:
                            seen_ids.add(tid)
                            results.append(item)
            if not results:
                return None

            dur_val = float(duration or 0)
            max_dur_diff = 3.5 if is_special_edit else 10.0
            best_id: int | None = None
            best_score = -1.0
            best_diff = 0.0

            for cand in results:
                if not isinstance(cand, dict) or not cand.get("trackId"):
                    continue
                ctitle = str(cand.get("trackName") or "")
                cartist = str(cand.get("artistName") or "")
                if not custom_query:
                    if not _titles_match(clean_title, ctitle):
                        continue
                    if combined_artist and not _artists_match(combined_artist, cartist):
                        continue
                cdur = float(cand.get("trackTimeMillis") or 0) / 1000.0
                cur_diff = abs(cdur - dur_val) if (dur_val > 10 and cdur > 10) else 0.0
                if not custom_query and dur_val > 10 and cdur > 10 and cur_diff > max_dur_diff:
                    continue

                score = 20.0
                if _norm_lyric_text(ctitle) == _norm_lyric_text(clean_title):
                    score += 40.0
                if combined_artist and _artists_match(combined_artist, cartist):
                    score += 30.0
                if dur_val > 10 and cdur > 10:
                    score += max(0.0, 20.0 - cur_diff * 2.0)

                if score > best_score:
                    best_score = score
                    best_id = int(cand["trackId"])
                    best_diff = cur_diff

            if best_id is None:
                return None

            lyr_res = await client.get(
                "https://lyrics.paxsenix.org/apple-music/lyrics",
                params={"id": str(best_id), "ttml": "true", "v": "2"},
                headers={"Accept": "*/*"},
                timeout=3.0,
            )
            if lyr_res.status_code != 200 or not lyr_res.text.strip():
                return None
            return _parse_paxsenix_payload(
                lyr_res.text, duration=duration, dur_diff=best_diff, custom_query=custom_query
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("Paxsenix Apple Music lookup failed for %r: %s", clean_title, e)
    return None


# ---------------------------------------------------------------------------
# Provider 2d: SimpMusic — Exact YouTube video_id Synced + Musixmatch RichSync
# ---------------------------------------------------------------------------


def _parse_musixmatch_richsync(raw_json: str) -> list[dict[str, Any]] | None:
    """Parse Musixmatch richSyncLyrics JSON array into ORBIT word-synced lines."""
    import json

    try:
        items = json.loads(raw_json)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(items, list) or not items:
        return None

    parsed_lines: list[dict[str, Any]] = []
    explicit_ends: dict[int, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = item.get("ts")
        te = item.get("te")
        if not isinstance(ts, (int, float)):
            continue
        base_t = float(ts)
        end_t = float(te) if isinstance(te, (int, float)) and te > base_t else base_t + 2.5
        tokens = item.get("l")
        words: list[dict[str, Any]] = []
        if isinstance(tokens, list) and tokens:
            for idx_tok, tok in enumerate(tokens):
                if not isinstance(tok, dict):
                    continue
                ch = str(tok.get("c") or "")
                off = tok.get("o")
                if not ch.strip() or not isinstance(off, (int, float)):
                    continue
                wt = round(max(0.0, base_t + float(off)), 3)
                # Look ahead for next token offset to compute end time
                we = round(end_t, 3)
                for nxt in tokens[idx_tok + 1 :]:
                    if (
                        isinstance(nxt, dict)
                        and isinstance(nxt.get("o"), (int, float))
                        and str(nxt.get("c") or "").strip()
                    ):
                        we = round(max(wt + 0.05, base_t + float(nxt["o"])), 3)
                        break
                words.append({"time": wt, "end": max(wt + 0.05, we), "text": ch.strip()})

        text = str(item.get("x") or "").strip()
        if not text and words:
            text = " ".join(w["text"] for w in words).strip()
        if not text:
            continue

        entry: dict[str, Any] = {
            "type": "line",
            "time": words[0]["time"] if words else round(base_t, 3),
            "end": words[-1]["end"] if words else round(end_t, 3),
            "text": text,
        }
        if words:
            entry["words"] = words
            explicit_ends[len(parsed_lines)] = words[-1]["end"]
        parsed_lines.append(entry)

    if not parsed_lines or not _has_valid_timestamps(parsed_lines):
        return None
    parsed_lines.sort(key=lambda ln: float(ln["time"]))
    return _inject_instrumental_breaks(parsed_lines, explicit_ends=explicit_ends)


async def fetch_simpmusic_lyrics(
    video_id: str | None, duration: int | None = None
) -> dict[str, Any] | None:
    """Fetch exact YouTube video_id synced/word-synced lyrics from api-lyrics.simpmusic.org."""
    if not video_id:
        return None
    try:
        async with _borrow_lyrics_client(2.6) as client:
            resp = await client.get(
                f"https://api-lyrics.simpmusic.org/v1/{quote(str(video_id))}",
                timeout=2.6,
            )
            if resp.status_code != 200:
                return None
            payload = resp.json()
            if not isinstance(payload, dict) or payload.get("success") is False:
                return None
            items = payload.get("data")
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list) or not items:
                return None

            best_cand: dict[str, Any] | None = None
            for rec in items[:6]:
                if not isinstance(rec, dict):
                    continue
                rec_dur = _parse_duration_seconds(
                    rec.get("duration") or rec.get("durationSeconds")
                )
                dur_diff = 0.0
                if duration and duration > 10 and rec_dur is not None and rec_dur > 10:
                    dur_diff = abs(rec_dur - float(duration))
                    if dur_diff > 3.5:
                        continue

                rich_raw = rec.get("richSyncLyrics")
                sync_raw = rec.get("syncedLyrics")
                plain_raw = rec.get("plainLyric") or rec.get("plainLyrics")

                synced: list[dict[str, Any]] | None = None
                if isinstance(rich_raw, str) and rich_raw.strip():
                    if rich_raw.lstrip().startswith("[") and "{" in rich_raw[:80]:
                        synced = _parse_musixmatch_richsync(rich_raw)
                    else:
                        cand_lrc = parse_lrc_lines(rich_raw)
                        if _has_valid_timestamps(cand_lrc):
                            synced = cand_lrc
                if synced is None and isinstance(sync_raw, str) and sync_raw.strip():
                    cand_lrc = parse_lrc_lines(sync_raw)
                    if _has_valid_timestamps(cand_lrc):
                        synced = cand_lrc

                if synced and duration and duration > 30:
                    last_t = max(
                        (
                            float(ln.get("time", 0.0))
                            for ln in synced
                            if ln.get("type") != "instrumental"
                        ),
                        default=0.0,
                    )
                    if last_t > float(duration) + 8.0:
                        synced = None

                plain = (
                    plain_raw.strip()
                    if isinstance(plain_raw, str) and plain_raw.strip()
                    else None
                )
                if not synced and not plain:
                    continue
                cand = _enrich_lyrics_payload(
                    {
                        "synced": synced,
                        "lyrics": plain,
                        "source": "SimpMusic",
                        "credit": "SimpMusic",
                        "offline": False,
                        "_exact_video": dur_diff <= 2.5,
                        "_duration_diff": dur_diff,
                    }
                )
                if best_cand is None or _candidate_quality(cand) > _candidate_quality(best_cand):
                    best_cand = cand
            return best_cand
    except Exception as e:  # noqa: BLE001
        logger.debug("SimpMusic lookup failed for %s: %s", video_id, e)
    return None


# ---------------------------------------------------------------------------
# Provider 3: lrc.red — ISRC TTML + Songwriters
# ---------------------------------------------------------------------------


async def fetch_lrc_red_lyrics(
    title: str | None,
    artist: str | None,
    duration: int | None,
    custom_query: str | None = None,
) -> dict[str, Any] | None:
    """Fetch word- or line-synced TTML from lrc.red scored by title, artist, and ±3.5s duration."""
    clean_title = custom_query.strip() if custom_query else clean_track_title(title or "")
    if not clean_title:
        return None
    artists = _combine_artists(artist, title)
    combined_artist = ", ".join(artists)
    primary_artist = artists[0] if artists else ""
    is_special_edit = _has_edit_qualifier(title) and not custom_query
    queries = (
        [clean_title]
        if custom_query
        else [f"{clean_title} {a}".strip() for a in artists[:2]] or [clean_title]
    )

    try:
        async with _borrow_lyrics_client(2.8) as client:
            hits: list[Any] = []
            for q_str in queries:
                resp = await client.get(
                    "https://lrc.red/search.json", params={"q": q_str}, timeout=2.8
                )
                if resp.status_code != 200:
                    continue
                search = resp.json()
                cand_hits = search.get("hits") if isinstance(search, dict) else None
                if isinstance(cand_hits, list) and cand_hits:
                    hits = cand_hits
                    break
            if not hits:
                return None

            nt = _norm_lyric_text(clean_title)
            na = _norm_lyric_text(primary_artist) if not custom_query else ""
            require_artist = bool(not custom_query and _extract_artist_tokens(combined_artist))
            dur_val = float(duration or 0)
            max_dur_diff = 3.5 if is_special_edit else 9.0
            best_hit: dict[str, Any] | None = None
            best_score = -1.0
            best_dur_diff = 0.0

            for h in hits[:10]:
                if not isinstance(h, dict) or not h.get("isrc"):
                    continue
                ht = _norm_lyric_text(str(h.get("title") or ""))
                raw_ha = str(h.get("artist") or "")
                ha = _norm_lyric_text(raw_ha)
                hdur = float(h.get("duration") or 0)
                cur_diff = abs(hdur - dur_val) if (dur_val > 10 and hdur > 10) else 0.0
                if dur_val > 10 and hdur > 10 and cur_diff > max_dur_diff:
                    continue
                if require_artist and not _artists_match(combined_artist, raw_ha):
                    continue

                score = 0.0
                if nt and ht == nt:
                    score += 50.0
                elif nt and (nt in ht or ht in nt):
                    score += 28.0
                else:
                    nt_toks = set(nt.split())
                    ht_toks = set(ht.split())
                    if nt_toks and ht_toks:
                        overlap = len(nt_toks & ht_toks) / max(1, len(nt_toks))
                        if overlap < 0.5:
                            continue
                        score += overlap * 24.0
                    else:
                        continue

                if na and ha:
                    if na == ha or na in ha or ha in na:
                        score += 25.0
                    elif set(na.split()) & set(ha.split()):
                        score += 12.0

                if dur_val > 10 and hdur > 10:
                    score += max(0.0, 12.0 - cur_diff * 2.0)

                if score > best_score:
                    best_score = score
                    best_hit = h
                    best_dur_diff = cur_diff

            if not best_hit or best_score < 22.0:
                return None

            isrc = re.sub(r"[^A-Za-z0-9]", "", str(best_hit.get("isrc") or ""))
            if not isrc:
                return None

            writers: list[str] = []
            meta_res, ttml_res = await asyncio.gather(
                client.get(f"https://lrc.red/s/{isrc}.json", timeout=2.8),
                client.get(
                    f"https://lrc.red/s/{isrc}.ttml",
                    headers={"Accept": "*/*"},
                    timeout=2.8,
                ),
                return_exceptions=True,
            )
            if not isinstance(meta_res, Exception) and meta_res.status_code == 200:
                with suppress(Exception):
                    meta = meta_res.json()
                    if isinstance(meta, dict):
                        if isinstance(meta.get("songwriters"), list):
                            writers = [
                                str(w).strip() for w in meta["songwriters"] if str(w).strip()
                            ]
                        if str(meta.get("sync") or "").lower() == "none":
                            return None

            if isinstance(ttml_res, Exception) or ttml_res.status_code != 200 or not ttml_res.text.strip():
                return None

            synced, sync_type, ttml_writers, xml_lang, native_tr = parse_ttml(ttml_res.text)
            if not synced:
                return None

            return _enrich_lyrics_payload(
                {
                    "synced": synced,
                    "lyrics": None,
                    "source": "lrc.red",
                    "sync_type": sync_type,
                    "credit": "lrc.red",
                    "credit_url": f"https://lrc.red/s/{isrc}",
                    "submitter": None,
                    "submitter_url": None,
                    "songwriters": writers or ttml_writers,
                    "offline": False,
                    "_duration_diff": best_dur_diff,
                },
                xml_lang=xml_lang,
                native_translations=native_tr,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("lrc.red lookup failed for %r: %s", clean_title, e)
        return None


# ---------------------------------------------------------------------------
# Provider 4: LRCLIB — Standard / Enhanced LRC / Multi-Script Variants
# ---------------------------------------------------------------------------


def _score_lrclib_item(
    item: dict[str, Any],
    clean_title: str,
    artists: list[str],
    duration: int | None,
    require_artist: bool = False,
    raw_title: str | None = None,
) -> tuple[int, bool, bool, float] | None:
    """Score an LRCLIB record -> `(score, has_synced, is_latin, duration_diff)` or None."""
    synced = item.get("syncedLyrics")
    plain = item.get("plainLyrics")
    has_synced = isinstance(synced, str) and bool(synced.strip())
    has_plain = isinstance(plain, str) and bool(plain.strip())
    if not has_synced and not has_plain:
        return None

    title_low = clean_title.lower()
    expected_artist_str = ", ".join(a for a in artists if a)
    enforce_artist = bool(require_artist and _extract_artist_tokens(expected_artist_str))

    rec_title_raw = str(item.get("trackName") or item.get("name") or "")
    rec_title = rec_title_raw.lower()
    rec_artist = str(item.get("artistName") or "")

    if clean_title and not _titles_match(clean_title, rec_title_raw):
        return None

    if enforce_artist and not _artists_match(expected_artist_str, rec_artist):
        return None

    # Guard against edit-type mismatches (e.g. normal song vs Slowed/Remix/Acoustic)
    expected_edits = _edit_qualifier_set(raw_title or clean_title)
    cand_edits = _edit_qualifier_set(rec_title_raw)
    is_special_edit = bool(expected_edits) or _has_edit_qualifier(raw_title)
    # Reject Slowed/Remix/Acoustic/Live/Cover when playing the original studio version
    if require_artist and not expected_edits and cand_edits:
        return None

    rec_dur = item.get("duration")
    diff: float = 0.0
    has_diff = False
    if duration and duration > 10:
        if isinstance(rec_dur, (int, float)) and rec_dur > 10:
            diff = abs(float(rec_dur) - float(duration))
            has_diff = True
            # Strict ±2.0s/2.5s duration match on auto-fetch prevents LRCLIB early/late drift
            max_allowed = 2.0 if is_special_edit else 2.5
            if require_artist and diff > max_allowed:
                return None
        elif require_artist:
            # Missing duration on LRCLIB -> mark unverified so exact-video alignment is required
            diff = 4.0

    score = 120 if has_synced else 20
    if rec_title == title_low:
        score += 50
    elif title_low and (title_low in rec_title or rec_title in title_low):
        score += 25

    if expected_artist_str and _artists_match(expected_artist_str, rec_artist):
        score += 45

    sample_body = str(synced if has_synced else plain or "")
    is_latin = _is_latin_script(sample_body)
    if is_latin:
        score += 40 if has_synced else 15

    if has_diff:
        if diff <= 2.0:
            score += 60
        elif diff <= 4.5:
            score += 35
        elif diff <= 8.5:
            score += 10
        elif diff <= 16.0:
            score -= 45
        else:
            score -= 130

    if score < 20:
        return None
    return score, has_synced, is_latin, diff


def _pick_best_lrclib_match(
    items: list[Any],
    clean_title: str,
    artists: list[str],
    duration: int | None,
    require_artist: bool = False,
    raw_title: str | None = None,
) -> dict[str, Any] | None:
    best_item: dict[str, Any] | None = None
    best_score = -10_000

    for item in items:
        if not isinstance(item, dict):
            continue
        scored = _score_lrclib_item(
            item,
            clean_title,
            artists,
            duration,
            require_artist=require_artist,
            raw_title=raw_title,
        )
        if scored is None:
            continue
        score, _, _, _ = scored
        if score > best_score:
            best_score = score
            best_item = item

    return best_item


def _pick_lrclib_variants(
    items: list[Any],
    clean_title: str,
    artists: list[str],
    duration: int | None,
    require_artist: bool = False,
    raw_title: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], float]:
    """Return `(best_record, alternate_language_records, best_duration_diff)`."""
    best_by_lang: dict[str, tuple[int, float, dict[str, Any]]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        scored = _score_lrclib_item(
            item,
            clean_title,
            artists,
            duration,
            require_artist=require_artist,
            raw_title=raw_title,
        )
        if scored is None:
            continue
        score, has_synced, _is_latin, diff = scored
        body = str(item.get("syncedLyrics") if has_synced else item.get("plainLyrics") or "")
        lang_code, _ = detect_lyrics_language(body)
        prev = best_by_lang.get(lang_code)
        if prev is None or score > prev[0]:
            best_by_lang[lang_code] = (score, diff, item)

    if not best_by_lang:
        return None, [], 0.0

    ranked = sorted(best_by_lang.values(), key=lambda triple: triple[0], reverse=True)
    best_diff = ranked[0][1]
    best_item = ranked[0][2]
    alternates = [rec for _, _, rec in ranked[1:]]
    return best_item, alternates, best_diff


def _format_lrclib_record(
    data: dict[str, Any],
    alternates: list[dict[str, Any]] | None = None,
    duration_diff: float = 0.0,
) -> dict[str, Any] | None:
    synced_raw = data.get("syncedLyrics")
    plain_raw = data.get("plainLyrics")
    synced = (
        parse_lrc_lines(synced_raw)
        if isinstance(synced_raw, str) and synced_raw.strip()
        else None
    )
    plain = plain_raw.strip() if isinstance(plain_raw, str) and plain_raw.strip() else None
    if not (synced or plain):
        return None

    extra_variants: list[dict[str, Any]] = []
    for alt in alternates or []:
        alt_sync_raw = alt.get("syncedLyrics")
        alt_plain_raw = alt.get("plainLyrics")
        alt_synced = (
            parse_lrc_lines(alt_sync_raw)
            if isinstance(alt_sync_raw, str) and alt_sync_raw.strip()
            else None
        )
        alt_plain = (
            alt_plain_raw.strip()
            if isinstance(alt_plain_raw, str) and alt_plain_raw.strip()
            else None
        )
        if not alt_plain and alt_synced:
            alt_plain = "\n".join(
                ln["text"] for ln in alt_synced if ln.get("type") != "instrumental" and ln["text"]
            )
        if alt_synced or alt_plain:
            alt_code, _ = detect_lyrics_language(alt_plain or "")
            extra_variants.append(
                {
                    "code": alt_code,
                    "label": _lang_label(alt_code),
                    "script": "latin" if _is_latin_script(alt_plain) else "native",
                    "synced": alt_synced,
                    "lyrics": alt_plain,
                    "sync_type": _compute_sync_type(alt_synced),
                    "source": "LRCLIB",
                }
            )

    return _enrich_lyrics_payload(
        {
            "synced": synced,
            "lyrics": plain,
            "source": "LRCLIB",
            "credit": "LRCLIB",
            "credit_url": "https://lrclib.net",
            "offline": False,
            "_duration_diff": duration_diff,
        },
        extra_variants=extra_variants,
    )


async def fetch_lrclib_lyrics(
    title: str | None,
    artist: str | None,
    duration: int | None,
    custom_query: str | None = None,
) -> dict[str, Any] | None:
    """Fetch synced or plain lyrics (plus alternate script variants) from lrclib.net."""
    if not title and not custom_query:
        return None

    clean_title = custom_query.strip() if custom_query else clean_track_title(title or "")
    artists = _combine_artists(artist, title)
    primary_artist = artists[0] if artists else ""

    try:
        async with _borrow_lyrics_client(3.0) as client:
            if custom_query:
                resp = await client.get(
                    "https://lrclib.net/api/search",
                    params={"q": custom_query.strip()},
                    timeout=3.0,
                )
                if resp.status_code == 200:
                    arr = resp.json()
                    if isinstance(arr, list) and arr:
                        best, alts, diff = _pick_lrclib_variants(
                            arr, clean_title, artists, duration
                        )
                        if best:
                            return _format_lrclib_record(best, alternates=alts, duration_diff=diff)
                return None

            collected_items: list[dict[str, Any]] = []

            async def _run_exact_get() -> list[dict[str, Any]]:
                if not (clean_title and primary_artist):
                    return []
                params: dict[str, Any] = {
                    "track_name": clean_title,
                    "artist_name": primary_artist,
                }
                if duration and duration > 0:
                    params["duration"] = int(duration)
                try:
                    r = await client.get("https://lrclib.net/api/get", params=params, timeout=3.0)
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict):
                            return [data]
                except Exception as inner_err:  # noqa: BLE001
                    logger.debug("LRCLIB exact lookup skipped: %s", inner_err)
                return []

            raw_attempts: list[dict[str, str]] = []
            if clean_title and primary_artist:
                raw_attempts.append({"q": f"{clean_title} {primary_artist}"})
                raw_attempts.append({"track_name": clean_title, "artist_name": primary_artist})
            if clean_title and len(artists) > 1:
                raw_attempts.append({"track_name": clean_title, "artist_name": artists[1]})
            if clean_title:
                raw_attempts.append({"track_name": clean_title})

            seen_keys: set[tuple[tuple[str, str], ...]] = set()
            search_attempts: list[dict[str, str]] = []
            for sp in raw_attempts:
                key = tuple(sorted(sp.items()))
                if key not in seen_keys:
                    seen_keys.add(key)
                    search_attempts.append(sp)
                if len(search_attempts) >= 4:
                    break

            async def _run_raw_search(sp: dict[str, str]) -> list[dict[str, Any]]:
                try:
                    r = await client.get("https://lrclib.net/api/search", params=sp, timeout=3.0)
                    if r.status_code != 200:
                        return []
                    arr = r.json()
                    return [x for x in arr if isinstance(x, dict)] if isinstance(arr, list) else []
                except Exception:  # noqa: BLE001
                    return []

            tasks = [_run_exact_get(), *(_run_raw_search(sp) for sp in search_attempts)]
            chunks = await asyncio.gather(*tasks, return_exceptions=True)
            for chunk in chunks:
                if isinstance(chunk, list):
                    collected_items.extend(chunk)

            if not collected_items:
                return None

            best, alts, diff = _pick_lrclib_variants(
                collected_items,
                clean_title,
                artists,
                duration,
                require_artist=True,
                raw_title=title,
            )
            if best:
                return _format_lrclib_record(best, alternates=alts, duration_diff=diff)
            return None
    except Exception as e:  # noqa: BLE001
        logger.debug("LRCLIB lookup failed for %r: %s", clean_title, e)
        return None


# ---------------------------------------------------------------------------
# Provider 5: YouTube Music (ytmusicapi — exact video_id)
# ---------------------------------------------------------------------------


def _clean_source_label(raw_source: Any) -> str:
    txt = str(raw_source or "YouTube Music").strip()
    txt = re.sub(r"^\s*source\s*:\s*", "", txt, flags=re.IGNORECASE).strip()
    return txt or "YouTube Music"


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

    source = _clean_source_label(data.get("source"))
    raw_lyrics = data.get("lyrics")

    if isinstance(raw_lyrics, list) and raw_lyrics:
        synced: list[dict[str, Any]] = []
        plain_parts: list[str] = []
        for entry in raw_lyrics:
            if isinstance(entry, dict):
                txt = str(entry.get("text") or "").strip()
                start_ms = entry.get("start_time")
                end_ms = entry.get("end_time")
            else:
                txt = str(getattr(entry, "text", "") or "").strip()
                start_ms = getattr(entry, "start_time", None)
                end_ms = getattr(entry, "end_time", None)
            if start_ms is not None:
                clean_txt, align = _extract_duet_tag(txt)
                entry_dict: dict[str, Any] = {
                    "type": "line",
                    "time": round(float(start_ms) / 1000.0, 3),
                    "text": clean_txt,
                }
                if end_ms is not None:
                    entry_dict["end"] = round(float(end_ms) / 1000.0, 3)
                if align is not None:
                    entry_dict["align"] = align
                synced.append(entry_dict)
            if txt:
                plain_parts.append(txt)
        if synced:
            synced.sort(key=lambda item: item["time"])
            synced = _inject_instrumental_breaks(synced)
            return _enrich_lyrics_payload(
                {
                    "synced": synced,
                    "lyrics": "\n".join(plain_parts) or None,
                    "source": source,
                    "credit": source,
                    "offline": False,
                    "_exact_video": True,
                }
            )

    if isinstance(raw_lyrics, str) and raw_lyrics.strip():
        return _enrich_lyrics_payload(
            {
                "synced": None,
                "lyrics": raw_lyrics.strip(),
                "source": source,
                "credit": source,
                "offline": False,
                "_exact_video": True,
            }
        )

    return None


async def _search_ytm_lyrics_by_query(ytm_client: Any, query: str) -> dict[str, Any] | None:
    """Fallback manual search on YouTube Music when providers miss."""
    if not ytm_client or not hasattr(ytm_client, "search"):
        return None
    try:
        candidates = await ytm_client.search(query, filter_type="songs", limit=3)
    except Exception:  # noqa: BLE001
        return None
    for cand in candidates or []:
        cand_vid = getattr(cand, "video_id", None) or (
            cand.get("video_id") if isinstance(cand, dict) else None
        )
        if not cand_vid:
            continue
        res = await asyncio.to_thread(_fetch_ytm_lyrics_sync, ytm_client, str(cand_vid))
        if res and (res.get("synced") or res.get("lyrics")):
            return res
    return None


# ---------------------------------------------------------------------------
# 5-Provider Ranked Cascade Resolver
# ---------------------------------------------------------------------------


def _candidate_rank(cand: dict[str, Any] | None) -> int:
    if not cand or not (cand.get("synced") or cand.get("lyrics")):
        return 0
    st = cand.get("sync_type") or _compute_sync_type(cand.get("synced"))
    return _SYNC_RANK.get(st, 1)


def _candidate_quality(cand: dict[str, Any] | None) -> tuple[int, int, int, int, float]:
    """Composite quality key strictly preserving word (3) > line (2) > plain (1).

    Tuple: `(sync_rank, exact_video_or_aligned, tight_sync, script_readability, -duration_diff)`
    - Word-synced lyrics ALWAYS beat line-synced lyrics (romanized in-place on UI when non-Latin
      so word-by-word timing is NEVER discarded for a line-only track).
    - Within line-synced tracks, exact video_id matches (YouTube Music / SimpMusic / YTM-aligned
      LRCLIB) ALWAYS outrank unaligned LRCLIB metadata guesses so stream timing stays locked.
    """
    rank = _candidate_rank(cand)
    if rank == 0 or cand is None:
        return (0, 0, 0, 0, -999.0)
    plain = cand.get("lyrics")
    synced = cand.get("synced")
    is_latin = _is_latin_script(plain) or _has_official_romanized(synced)
    is_abjad = (not is_latin) and _is_abjad_script(plain)
    script_score = 2 if is_latin else (0 if is_abjad else 1)
    exact_vid = 1 if cand.get("_exact_video") else 0
    dur_diff = float(cand.get("_duration_diff") or 0.0)
    tight_sync = 1 if (exact_vid or dur_diff <= 1.5) else 0

    return (rank, exact_vid, tight_sync, script_score, -dur_diff)


# Compact consonant-skeleton phonetic encoder for cross-script alignment (Indic <-> Latin)
_INDIC_CONSONANT_BASE: dict[int, str] = {
    0x15: "k", 0x16: "k", 0x17: "g", 0x18: "g", 0x19: "n",
    0x1A: "c", 0x1B: "c", 0x1C: "j", 0x1D: "j", 0x1E: "n",
    0x1F: "t", 0x20: "t", 0x21: "d", 0x22: "d", 0x23: "n",
    0x24: "t", 0x25: "t", 0x26: "d", 0x27: "d", 0x28: "n", 0x29: "n",
    0x2A: "p", 0x2B: "p", 0x2C: "b", 0x2D: "b", 0x2E: "m",
    0x2F: "y", 0x30: "r", 0x31: "r", 0x32: "l", 0x33: "l", 0x34: "l",
    0x35: "v", 0x36: "s", 0x37: "s", 0x38: "s", 0x39: "h", 0x3A: "t",
    0x58: "k", 0x59: "k", 0x5A: "g", 0x5B: "j", 0x5C: "r", 0x5D: "r", 0x5E: "p", 0x5F: "y",
    0x7A: "n", 0x7B: "n", 0x7C: "r", 0x7D: "l", 0x7E: "l", 0x7F: "k",
}
_INDIC_BLOCKS = (0x0900, 0x0980, 0x0A00, 0x0A80, 0x0B80, 0x0C00, 0x0C80, 0x0D00)


def _phonetic_skeleton(text: str) -> str:
    """Fold Latin & Indic scripts into a shared consonant skeleton for cross-script sync."""
    out: list[str] = []
    for ch in str(text or "").lower():
        cp = ord(ch)
        if "a" <= ch <= "z" or ch.isspace():
            out.append(ch)
            continue
        if 0x0900 <= cp <= 0x0D7F:
            blk = cp & ~0x7F
            if blk in _INDIC_BLOCKS:
                off = cp - blk
                if off == 0x02:
                    # Malayalam anusvara (ം) is 'm'; Devanagari/other Indic anusvara is 'n'
                    out.append("m" if blk == 0x0D00 else "n")
                else:
                    mapped = _INDIC_CONSONANT_BASE.get(off)
                    if mapped:
                        out.append(mapped)
        elif ch.isdigit():
            out.append(ch)
    s = "".join(out)
    # Normalize Latin digraphs & spelling variants before stripping vowels
    s = re.sub(r"(chh|ch|sh|zh|th|dh|bh|ph|kh|gh|jh)", lambda m: m.group(0)[0], s)
    s = s.replace("x", "ks").replace("w", "v").replace("q", "k").replace("z", "j").replace("f", "p")
    s = re.sub(r"[aeiou]", "", s)
    s = re.sub(r"([a-z])\1+", r"\1", s)
    return s


def _line_tokens(line: dict[str, Any] | str) -> set[str]:
    raw = (
        str(line.get("romanized") or line.get("text") or "")
        if isinstance(line, dict)
        else str(line or "")
    )
    skel = _phonetic_skeleton(raw)
    return {w for w in skel.split() if len(w) >= 2}


def _consensus_median(
    votes: list[tuple[int, float]],
    window: float,
    min_distinct: int,
) -> float | None:
    """RANSAC consensus: find the tightest offset cluster supported by >= min_distinct candidate lines."""
    if len(votes) < min_distinct:
        return None
    best_cluster: list[float] = []
    best_distinct = 0
    for _, center in votes:
        seen_idx: set[int] = set()
        cluster: list[float] = []
        for c_idx, val in votes:
            if abs(val - center) <= window and c_idx not in seen_idx:
                seen_idx.add(c_idx)
                cluster.append(val)
        if len(seen_idx) > best_distinct:
            best_distinct = len(seen_idx)
            best_cluster = cluster

    if best_distinct < min_distinct:
        return None
    best_cluster.sort()
    median = best_cluster[len(best_cluster) // 2]
    mad_vals = sorted(abs(d - median) for d in best_cluster)
    mad = mad_vals[len(mad_vals) // 2]
    if mad <= window and abs(median) <= 28.0:
        return round(median, 3) if abs(median) >= 0.15 else 0.0
    return None


def _compute_alignment_offset(
    cand_synced: list[dict[str, Any]],
    ref_synced: list[dict[str, Any]],
) -> float | None:
    """Compute intro/master time offset (ref - cand) between metadata lyrics and exact-video
    timestamps using cross-script phonetic matching + global RANSAC consensus voting.
    """
    cand_lines = [ln for ln in cand_synced if ln.get("type") != "instrumental" and ln.get("text")]
    ref_lines = [ln for ln in ref_synced if ln.get("type") != "instrumental" and ln.get("text")]
    if len(cand_lines) < 3 or len(ref_lines) < 3:
        return None

    # 1. Cross-script phonetic text consensus (works across Latin, Malayalam, Hindi, Punjabi, etc.)
    cand_toks = [_line_tokens(c) for c in cand_lines[:45]]
    ref_toks = [_line_tokens(r) for r in ref_lines[:50]]
    text_votes: list[tuple[int, float]] = []

    for i, ctoks in enumerate(cand_toks):
        if len(ctoks) < 2:
            continue
        best_j = -1
        best_sim = 0.0
        for j, rtoks in enumerate(ref_toks):
            if not rtoks:
                continue
            inter = len(ctoks & rtoks)
            if inter == 0:
                continue
            sim = inter / max(1, len(ctoks | rtoks))
            if sim > best_sim:
                best_sim = sim
                best_j = j
        if best_j >= 0 and best_sim >= 0.45:
            delta = float(ref_lines[best_j]["time"]) - float(cand_lines[i]["time"])
            if abs(delta) <= 28.0:
                text_votes.append((i, delta))

    text_offset = _consensus_median(text_votes, window=0.55, min_distinct=3)
    if text_offset is not None:
        return text_offset

    # 2. Global rhythm-gap consensus fallback (works even for Urdu/CJK vs Latin!)
    cand_gaps = [
        float(cand_lines[i + 1]["time"]) - float(cand_lines[i]["time"])
        for i in range(min(35, len(cand_lines) - 1))
    ]
    ref_gaps = [
        float(ref_lines[j + 1]["time"]) - float(ref_lines[j]["time"])
        for j in range(min(40, len(ref_lines) - 1))
    ]
    rhythm_votes: list[tuple[int, float]] = []
    for i, cg in enumerate(cand_gaps):
        if cg < 1.2:
            continue
        for j, rg in enumerate(ref_gaps):
            if abs(cg - rg) <= 0.30:
                # Require next consecutive gap to also match if available to kill false positives
                if (
                    i + 1 < len(cand_gaps)
                    and j + 1 < len(ref_gaps)
                    and abs(cand_gaps[i + 1] - ref_gaps[j + 1]) > 0.45
                ):
                    continue
                delta = float(ref_lines[j]["time"]) - float(cand_lines[i]["time"])
                if abs(delta) <= 28.0:
                    rhythm_votes.append((i, delta))

    return _consensus_median(rhythm_votes, window=0.45, min_distinct=4)


def _shift_synced_lines(synced: list[dict[str, Any]], delta: float) -> list[dict[str, Any]]:
    """Shift all line and word timestamps by `delta` seconds (clamped >= 0)."""
    shifted: list[dict[str, Any]] = []
    for ln in synced:
        item = dict(ln)
        if item.get("time") is not None:
            item["time"] = round(max(0.0, float(item["time"]) + delta), 3)
        if item.get("end") is not None:
            item["end"] = round(max(float(item.get("time") or 0.0), float(item["end"]) + delta), 3)
        words = item.get("words")
        if isinstance(words, list) and words:
            item["words"] = [
                {
                    **w,
                    "time": round(max(0.0, float(w["time"]) + delta), 3),
                    "end": round(
                        max(
                            max(0.0, float(w["time"]) + delta) + 0.05,
                            float(w["end"]) + delta,
                        ),
                        3,
                    ),
                }
                for w in words
            ]
        shifted.append(item)
    return shifted


async def _run_provider_cascade(
    ytm_client: Any,
    video_id: str,
    title: str | None,
    artist: str | None,
    duration: int | None,
    custom_query: str | None = None,
) -> dict[str, Any]:
    """Run fast 2-stage provider cascade: Stage 1 Unison -> Stage 2 parallel fan-out."""
    best: dict[str, Any] | None = None
    extra_variants: list[dict[str, Any]] = []
    exact_ytm_ref: dict[str, Any] | None = None

    def _collect_as_variant(cand: dict[str, Any]) -> None:
        code = str(cand.get("original_lang") or "roman")
        extra_variants.append(
            {
                "code": code,
                "label": _lang_label(code),
                "script": "latin" if _is_latin_script(cand.get("lyrics")) else "native",
                "synced": cand.get("synced"),
                "lyrics": cand.get("lyrics"),
                "sync_type": cand.get("sync_type") or _compute_sync_type(cand.get("synced")),
                "source": cand.get("source"),
            }
        )
        for v in cand.get("variants") or []:
            if isinstance(v, dict):
                extra_variants.append(v)

    def _consider(cand: dict[str, Any] | None) -> bool:
        nonlocal best
        if not cand or not (cand.get("synced") or cand.get("lyrics")):
            return False
        cand = _enrich_lyrics_payload(dict(cand))
        if best is None:
            best = cand
        else:
            cand_q = _candidate_quality(cand)
            best_q = _candidate_quality(best)
            best_diff = float(best.get("_duration_diff") or 0.0)
            cand_exact = bool(cand.get("_exact_video"))
            # Within line-sync, exact video_id YouTube Music sync beats drifted metadata (>2.0s)
            if (
                _candidate_rank(cand) == _candidate_rank(best) == 2
                and cand_exact
                and not best.get("_exact_video")
                and best_diff > 2.0
            ):
                _collect_as_variant(best)
                best = cand
            elif cand_q > best_q:
                if _candidate_rank(best) >= 2:
                    _collect_as_variant(best)
                best = cand
            elif _candidate_rank(cand) >= 2:
                _collect_as_variant(cand)
        # Stop early ONLY if Unison matched exact video_id with word sync;
        # metadata-only word matches still grab YTM exact-video timestamps to align intro offset.
        return _candidate_rank(best) >= 3 and bool(best.get("_exact_video"))

    def _align_or_filter(cand: dict[str, Any] | None) -> dict[str, Any] | None:
        """Align metadata candidates to exact YTM timestamps; reject unaligned >3.5s drifted matches."""
        if not cand or not (cand.get("synced") or cand.get("lyrics")):
            return None
        cand = dict(cand)
        dur_diff = float(cand.get("_duration_diff") or 0.0)
        is_exact = bool(cand.get("_exact_video"))

        if (
            not is_exact
            and not custom_query
            and cand.get("synced")
            and exact_ytm_ref is not None
            and exact_ytm_ref.get("synced")
        ):
            offset = _compute_alignment_offset(cand["synced"], exact_ytm_ref["synced"])
            if offset is not None:
                if abs(offset) >= 0.15:
                    logger.debug(
                        "Auto-aligned %s lyrics for %s by %+.2fs using exact YTM timestamps",
                        cand.get("source"),
                        video_id,
                        offset,
                    )
                    cand["synced"] = _shift_synced_lines(cand["synced"], offset)
                    for v in cand.get("variants") or []:
                        if isinstance(v, dict) and v.get("synced"):
                            v["synced"] = _shift_synced_lines(v["synced"], offset)
                cand["_duration_diff"] = 0.0
                cand["_exact_video"] = True
                return cand

        # Without exact YTM alignment, reject metadata candidates drifted past strict thresholds
        max_unaligned = 2.0 if _candidate_rank(cand) < 3 else 3.5
        if not is_exact and not custom_query and dur_diff > max_unaligned:
            return None
        return cand

    # Stage 1: Fast exact video_id check on Unison (stops immediately if exact-video word sync!)
    unison_exact = await fetch_unison_lyrics(
        video_id,
        title,
        artist,
        duration=duration,
        custom_query=custom_query,
        exact_video_only=not custom_query,
    )
    if not _consider(unison_exact):
        async def _safe_ytm() -> dict[str, Any] | None:
            if custom_query:
                return await _search_ytm_lyrics_by_query(ytm_client, custom_query)
            return await asyncio.to_thread(_fetch_ytm_lyrics_sync, ytm_client, video_id)

        # Stage 2: Full parallel fan-out across ALL word-sync & exact-video providers
        unison_meta_task = (
            fetch_unison_lyrics(
                None,
                title,
                artist,
                duration=duration,
                custom_query=custom_query,
                metadata_only=True,
            )
            if not custom_query
            else asyncio.sleep(0, result=None)
        )
        simpmusic_task = (
            fetch_simpmusic_lyrics(video_id, duration=duration)
            if not custom_query
            else asyncio.sleep(0, result=None)
        )
        (
            unison_meta,
            betterlyrics_res,
            paxsenix_res,
            lp_res,
            lrc_red_res,
            lrclib_res,
            simpmusic_res,
            ytm_res,
        ) = await asyncio.gather(
            unison_meta_task,
            fetch_betterlyrics_api(title, artist, duration, custom_query=custom_query),
            fetch_paxsenix_apple_lyrics(title, artist, duration, custom_query=custom_query),
            fetch_lyricsplus_lyrics(title, artist, duration, custom_query=custom_query),
            fetch_lrc_red_lyrics(title, artist, duration, custom_query=custom_query),
            fetch_lrclib_lyrics(title, artist, duration, custom_query=custom_query),
            simpmusic_task,
            _safe_ytm(),
            return_exceptions=True,
        )
        # Prefer exact-video YouTube Music synced timestamps as alignment anchor;
        # fall back to exact-video SimpMusic synced timestamps when YTM only has plain text!
        if isinstance(ytm_res, dict) and ytm_res.get("synced") and ytm_res.get("_exact_video"):
            exact_ytm_ref = ytm_res
        elif (
            isinstance(simpmusic_res, dict)
            and simpmusic_res.get("synced")
            and simpmusic_res.get("_exact_video")
        ):
            exact_ytm_ref = simpmusic_res

        for item in (
            unison_meta,
            betterlyrics_res,
            paxsenix_res,
            lp_res,
            lrc_red_res,
            lrclib_res,
            simpmusic_res,
            ytm_res,
        ):
            if isinstance(item, dict):
                aligned = _align_or_filter(item)
                if aligned is not None:
                    _consider(aligned)

    if best is not None:
        best_rank = _candidate_rank(best)
        # Keep only same-or-higher sync rank variants so UI never downgrades word-sync to line-sync
        compatible_variants = [v for v in extra_variants if _candidate_rank(v) >= best_rank]
        if compatible_variants:
            merged = _merge_variants(best, compatible_variants)
            best["variants"] = merged if len(merged) > 1 else []
        else:
            best["variants"] = [
                v for v in (best.get("variants") or []) if _candidate_rank(v) >= best_rank
            ]
            if len(best["variants"]) <= 1:
                best["variants"] = []
        best.pop("_duration_diff", None)
        best.pop("_exact_video", None)
        return best

    return {
        "synced": None,
        "lyrics": None,
        "source": None,
        "sync_type": "plain",
        "original_lang": "roman",
        "variants": [],
        "songwriters": [],
        "offline": False,
    }


async def _resolve_lyrics_impl(
    ytm_client: Any,
    video_id: str,
    title: str | None = None,
    artist: str | None = None,
    duration: int | None = None,
    custom_query: str | None = None,
    query: str | None = None,
    force: bool = False,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve lyrics: Local .lrc -> LRU Cache -> Unison -> LyricsPlus -> lrc.red -> LRCLIB -> YTM."""
    track_row = await asyncio.to_thread(_lookup_track_local_info, db_path, video_id)
    local_audio = track_row.get("local_path") if track_row else None

    if force:
        _LYRICS_CACHE.pop(video_id, None)

    eff_title = title or (track_row.get("title") if track_row else None)
    eff_artist = artist or (track_row.get("artist") if track_row else None)
    eff_dur = duration or (track_row.get("duration_s") if track_row else None)

    effective_query = (query or custom_query or "").strip()
    if effective_query:
        result_custom = await _run_provider_cascade(
            ytm_client,
            video_id=video_id,
            title=eff_title,
            artist=eff_artist,
            duration=eff_dur,
            custom_query=effective_query,
        )
        if result_custom.get("synced") or result_custom.get("lyrics"):
            if local_audio and Path(local_audio).is_file():
                saved = await asyncio.to_thread(
                    save_local_lrc,
                    local_audio,
                    result_custom.get("synced"),
                    result_custom.get("lyrics"),
                    result_custom,
                )
                if saved:
                    result_custom["offline"] = True
            _cache_put(video_id, result_custom)
        return result_custom

    # 1. Offline-first check: sidecar .lyrics.json or .lrc next to local audio file
    if not force and local_audio and Path(local_audio).is_file():
        offline_lrc = await asyncio.to_thread(read_local_lrc, local_audio)
        if offline_lrc:
            # Auto-upgrade any legacy .lrc or pre-v5 .lyrics.json cache once when online
            cache_ver = int(offline_lrc.get("schema_v") or 0)
            has_current_rich = (
                get_local_lyrics_json_path(local_audio).is_file()
                and cache_ver >= _LYRICS_CACHE_SCHEMA_VERSION
            )
            if video_id not in _LYRICS_CACHE and not has_current_rich:
                try:
                    upgraded = await asyncio.wait_for(
                        _run_provider_cascade(
                            ytm_client,
                            video_id=video_id,
                            title=eff_title,
                            artist=eff_artist,
                            duration=eff_dur,
                        ),
                        timeout=5.5,
                    )
                    if upgraded.get("synced") or upgraded.get("lyrics"):
                        chosen = (
                            upgraded
                            if _candidate_quality(upgraded) >= _candidate_quality(offline_lrc)
                            else offline_lrc
                        )
                        saved = await asyncio.to_thread(
                            save_local_lrc,
                            local_audio,
                            chosen.get("synced"),
                            chosen.get("lyrics"),
                            chosen,
                        )
                        if saved:
                            chosen["offline"] = True
                        _cache_put(video_id, chosen)
                        return chosen
                except Exception as e:  # noqa: BLE001
                    logger.debug("Offline cache upgrade skipped for %s: %s", video_id, e)
            _cache_put(video_id, offline_lrc)
            return offline_lrc

    # 2. Memory LRU cache hit
    if video_id in _LYRICS_CACHE:
        _LYRICS_CACHE.move_to_end(video_id)
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
                save_local_lrc,
                local_audio_path,
                cached.get("synced"),
                cached.get("lyrics"),
                cached,
            )
            if saved:
                cached["offline"] = True
                _cache_put(video_id, cached)
        elif not has_local_audio and cached.get("offline"):
            cached["offline"] = False
            _cache_put(video_id, cached)
        return cached

    # 3. 5-provider ranked cascade
    result = await _run_provider_cascade(
        ytm_client,
        video_id=video_id,
        title=eff_title,
        artist=eff_artist,
        duration=eff_dur,
    )

    if result.get("synced") or result.get("lyrics"):
        if local_audio and Path(local_audio).is_file():
            saved = await asyncio.to_thread(
                save_local_lrc, local_audio, result.get("synced"), result.get("lyrics"), result
            )
            if saved:
                result["offline"] = True
        _cache_put(video_id, result)

    return result


async def resolve_lyrics(
    ytm_client: Any,
    video_id: str,
    title: str | None = None,
    artist: str | None = None,
    duration: int | None = None,
    custom_query: str | None = None,
    query: str | None = None,
    force: bool = False,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve lyrics with in-flight task deduplication so concurrent fetches share one cascade."""
    eff_query = (query or custom_query or "").strip()
    if not force and not eff_query and video_id in _LYRICS_CACHE:
        _LYRICS_CACHE.move_to_end(video_id)
        cached = dict(_LYRICS_CACHE[video_id])
        if cached.get("synced") or cached.get("lyrics"):
            return cached

    inflight_key = (video_id, eff_query)
    loop = asyncio.get_running_loop()
    if not force:
        existing = _INFLIGHT_LYRICS.get(inflight_key)
        if existing is not None and not existing.done() and existing.get_loop() is loop:
            return dict(await asyncio.shield(existing))

    task = asyncio.create_task(
        _resolve_lyrics_impl(
            ytm_client,
            video_id=video_id,
            title=title,
            artist=artist,
            duration=duration,
            custom_query=custom_query,
            query=query,
            force=force,
            db_path=db_path,
        )
    )
    if not force:
        _INFLIGHT_LYRICS[inflight_key] = task

        def _cleanup(done_task: asyncio.Task[dict[str, Any]], key: tuple[str, str] = inflight_key) -> None:
            if _INFLIGHT_LYRICS.get(key) is done_task:
                _INFLIGHT_LYRICS.pop(key, None)
            if not done_task.cancelled():
                done_task.exception()

        task.add_done_callback(_cleanup)

    return dict(await asyncio.shield(task))


def _get_lyrics_prefetch_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _LYRICS_PREFETCH_SEMS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(2)
        _LYRICS_PREFETCH_SEMS[loop] = sem
    return sem


async def prefetch_lyrics(
    ytm_client: Any,
    tracks: Iterable[Any],
    db_path: Path | str | None = None,
    max_items: int = 4,
) -> int:
    """Silently warm _LYRICS_CACHE for upcoming/hovered tracks without blocking playback."""
    candidates: list[tuple[str, str | None, str | None, int | None]] = []
    seen: set[str] = set()
    for item in tracks:
        if item is None:
            continue
        if isinstance(item, dict):
            vid = str(item.get("video_id") or "").strip()
            title = item.get("title")
            artist = item.get("artist")
            dur = item.get("duration_s") or item.get("duration")
        else:
            vid = str(getattr(item, "video_id", "") or "").strip()
            title = getattr(item, "title", None)
            artist = getattr(item, "artist", None)
            dur = getattr(item, "duration_s", None)
        if not vid or vid in seen or vid in _LYRICS_CACHE:
            if vid in _LYRICS_CACHE:
                _LYRICS_CACHE.move_to_end(vid)
            continue
        seen.add(vid)
        eff_dur = round(float(dur)) if isinstance(dur, (int, float)) and dur > 0 else None
        candidates.append((vid, title, artist, eff_dur))
        if len(candidates) >= max_items:
            break

    if not candidates:
        return 0

    sem = _get_lyrics_prefetch_semaphore()
    warmed = 0

    async def _warm_one(vid: str, title: str | None, artist: str | None, dur: int | None) -> None:
        nonlocal warmed
        async with sem:
            with suppress(Exception):
                res = await resolve_lyrics(
                    ytm_client,
                    video_id=vid,
                    title=title,
                    artist=artist,
                    duration=dur,
                    db_path=db_path,
                )
                if res.get("synced") or res.get("lyrics"):
                    warmed += 1

    await asyncio.gather(*(_warm_one(*c) for c in candidates), return_exceptions=True)
    return warmed


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
