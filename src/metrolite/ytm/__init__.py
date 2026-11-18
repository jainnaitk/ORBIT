"""YouTube Music integration module for MetroLite."""

from metrolite.ytm.client import (
    Track,
    YTMError,
    YTMNetworkError,
    YTMParseError,
    YTMusicClient,
    extract_album,
    extract_artist,
    extract_thumbnail_url,
    extract_title,
    normalize_track,
    parse_duration,
    try_normalize_track,
)

__all__ = [
    "Track",
    "YTMError",
    "YTMNetworkError",
    "YTMParseError",
    "YTMusicClient",
    "extract_album",
    "extract_artist",
    "extract_thumbnail_url",
    "extract_title",
    "normalize_track",
    "parse_duration",
    "try_normalize_track",
]
