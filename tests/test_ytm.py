"""Unit tests for YouTube Music client, normalization, and error handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from ytmusicapi.exceptions import YTMusicError

from metrolite.ytm.client import (
    Track,
    YTMError,
    YTMNetworkError,
    YTMusicClient,
    extract_album,
    extract_artist,
    extract_thumbnail_url,
    extract_title,
    normalize_track,
    parse_duration,
    try_normalize_track,
)


def test_parse_duration() -> None:
    """Test duration parsing from various formats."""
    assert parse_duration(None) == 0
    assert parse_duration(214) == 214
    assert parse_duration(214.8) == 214
    assert parse_duration("45") == 45
    assert parse_duration("03:34") == 214
    assert parse_duration("3:34") == 214
    assert parse_duration("01:02:03") == 3723
    assert parse_duration("invalid") == 0
    assert parse_duration("") == 0


def test_extract_thumbnail_url() -> None:
    """Test thumbnail selection preferring ~120-240px."""
    assert extract_thumbnail_url(None) is None
    assert extract_thumbnail_url("") is None
    assert extract_thumbnail_url("https://example.com/art.jpg") == "https://example.com/art.jpg"
    assert (
        extract_thumbnail_url({"url": "https://example.com/one.jpg"})
        == "https://example.com/one.jpg"
    )

    thumbs = [
        {"url": "https://example.com/60.jpg", "width": 60, "height": 60},
        {"url": "https://example.com/120.jpg", "width": 120, "height": 120},
        {"url": "https://example.com/544.jpg", "width": 544, "height": 544},
    ]
    assert extract_thumbnail_url(thumbs) == "https://example.com/120.jpg"

    large_only = [
        {"url": "https://example.com/800.jpg", "width": 800, "height": 800},
        {"url": "https://example.com/1200.jpg", "width": 1200, "height": 1200},
    ]
    # Closest to 120 is 800
    assert extract_thumbnail_url(large_only) == "https://example.com/800.jpg"

    urls_only = ["https://example.com/fallback.jpg"]
    assert extract_thumbnail_url(urls_only) == "https://example.com/fallback.jpg"


def test_extract_artist_and_album() -> None:
    """Test artist and album name extraction."""
    assert extract_artist(None) == "Unknown Artist"
    assert extract_artist("") == "Unknown Artist"
    assert extract_artist("Solo Artist") == "Solo Artist"
    assert extract_artist([{"name": "Artist A"}, {"name": "Artist B"}]) == "Artist A, Artist B"
    assert extract_artist(["Artist 1", "Artist 2"]) == "Artist 1, Artist 2"
    assert extract_artist({"name": "Single Dict Artist"}) == "Single Dict Artist"

    assert extract_album(None) is None
    assert extract_album("") is None
    assert extract_album("Direct Album") == "Direct Album"
    assert extract_album({"name": "Dict Album"}) == "Dict Album"
    assert extract_album({"title": "Dict Album Title"}) == "Dict Album Title"


def test_extract_title() -> None:
    """Test title extraction from strings or dict structures."""
    assert extract_title(None) == "Unknown Title"
    assert extract_title("Song Title") == "Song Title"
    assert extract_title({"text": "Object Title"}) == "Object Title"
    assert extract_title([{"text": "Run Title"}]) == "Run Title"


def test_normalize_track_search_format() -> None:
    """Test normalizing a standard ytmusicapi search response."""
    raw = {
        "videoId": "lYBUbBu4W08",
        "title": "Never Gonna Give You Up",
        "artists": [{"id": "UCwZEU0wAwIyZb4x5G_KJp2w", "name": "Rick Astley"}],
        "album": {"id": "MPREb_dcYZhAh5urI", "name": "Whenever You Need Somebody"},
        "duration": "3:34",
        "duration_seconds": 214,
        "thumbnails": [
            {"height": 60, "url": "https://example.com/60.jpg", "width": 60},
            {"height": 120, "url": "https://example.com/120.jpg", "width": 120},
        ],
    }
    track = normalize_track(raw)
    assert isinstance(track, Track)
    assert track.video_id == "lYBUbBu4W08"
    assert track.title == "Never Gonna Give You Up"
    assert track.artist == "Rick Astley"
    assert track.album == "Whenever You Need Somebody"
    assert track.duration_s == 214
    assert track.thumbnail_url == "https://example.com/120.jpg"


def test_normalize_track_watch_radio_format() -> None:
    """Test normalizing a watch playlist item."""
    raw = {
        "videoId": "abc12345",
        "title": "Radio Track",
        "artists": [{"name": "Radio Artist"}],
        "album": None,
        "length": "4:15",
        "thumbnail": [{"url": "https://example.com/radio120.jpg", "width": 120}],
    }
    track = normalize_track(raw, default_album="Fallback Album")
    assert track.video_id == "abc12345"
    assert track.title == "Radio Track"
    assert track.artist == "Radio Artist"
    assert track.album == "Fallback Album"
    assert track.duration_s == 255
    assert track.thumbnail_url == "https://example.com/radio120.jpg"


def test_normalize_track_invalid_video_id() -> None:
    """Test missing or empty videoId raises ValueError or returns None safely."""
    with pytest.raises(ValueError, match="missing videoId"):
        normalize_track({"title": "No ID"})

    with pytest.raises(ValueError, match="Empty videoId"):
        normalize_track({"videoId": "   ", "title": "Whitespace ID"})

    assert try_normalize_track({"title": "No ID"}) is None
    assert try_normalize_track("not a dict") is None


@pytest.mark.asyncio
async def test_ytm_client_search() -> None:
    """Test async search method with mocked YTMusic."""
    mock_yt = MagicMock()
    mock_yt.search.return_value = [
        {
            "videoId": "vid_1",
            "title": "Track 1",
            "artists": [{"name": "Artist 1"}],
            "duration": "2:30",
            "thumbnails": [{"url": "https://example.com/1.jpg", "width": 120}],
        },
        {"title": "Missing video ID"},  # should be safely skipped
    ]

    client = YTMusicClient(yt_instance=mock_yt)
    results = await client.search("test query", limit=10)

    assert len(results) == 1
    assert results[0].video_id == "vid_1"
    assert results[0].title == "Track 1"
    assert results[0].duration_s == 150
    mock_yt.search.assert_called_once_with(query="test query", filter="songs", limit=10)


@pytest.mark.asyncio
async def test_ytm_client_get_home_sections() -> None:
    """Test async get_home_sections with mocked YTMusic."""
    mock_yt = MagicMock()
    mock_yt.get_home.return_value = [
        {
            "title": "Quick picks",
            "contents": [
                {
                    "videoId": "vid_home",
                    "title": "Home Song",
                    "artists": [{"name": "Home Artist"}],
                    "thumbnails": [{"url": "https://example.com/home.jpg"}],
                },
                {"playlistId": "pl_1", "title": "Home Playlist"},
            ],
        }
    ]

    client = YTMusicClient(yt_instance=mock_yt)
    sections = await client.get_home_sections()

    assert len(sections) == 1
    assert sections[0]["title"] == "Quick picks"
    contents = sections[0]["contents"]
    assert isinstance(contents[0], Track)
    assert contents[0].video_id == "vid_home"
    # Playlist remains unparsed dict
    assert isinstance(contents[1], dict)
    assert contents[1]["playlistId"] == "pl_1"


@pytest.mark.asyncio
async def test_ytm_client_get_album() -> None:
    """Test async get_album normalizes tracks."""
    mock_yt = MagicMock()
    mock_yt.get_album.return_value = {
        "title": "Album Title",
        "artists": [{"name": "Album Artist"}],
        "year": "2024",
        "thumbnails": [{"url": "https://example.com/album.jpg", "width": 120}],
        "tracks": [
            {
                "videoId": "t1",
                "title": "Album Song 1",
                "duration": "3:00",
            }
        ],
    }

    client = YTMusicClient(yt_instance=mock_yt)
    album = await client.get_album("browse_123")

    assert album["title"] == "Album Title"
    assert album["artist"] == "Album Artist"
    assert album["thumbnail_url"] == "https://example.com/album.jpg"
    assert len(album["tracks"]) == 1
    assert isinstance(album["tracks"][0], Track)
    assert album["tracks"][0].video_id == "t1"
    assert album["tracks"][0].album == "Album Title"


@pytest.mark.asyncio
async def test_ytm_client_get_artist() -> None:
    """Test async get_artist normalizes discography tracks."""
    mock_yt = MagicMock()
    mock_yt.get_artist.return_value = {
        "name": "Artist Name",
        "thumbnails": [{"url": "https://example.com/art.jpg", "width": 120}],
        "songs": {
            "results": [
                {
                    "videoId": "art_song_1",
                    "title": "Top Hit",
                    "artists": [{"name": "Artist Name"}],
                }
            ]
        },
    }

    client = YTMusicClient(yt_instance=mock_yt)
    artist = await client.get_artist("channel_123")

    assert artist["name"] == "Artist Name"
    songs = artist["songs"]["results"]
    assert len(songs) == 1
    assert isinstance(songs[0], Track)
    assert songs[0].video_id == "art_song_1"


@pytest.mark.asyncio
async def test_ytm_client_get_watch_radio() -> None:
    """Test async get_watch_radio for Up-Next recommendations."""
    mock_yt = MagicMock()
    mock_yt.get_watch_playlist.return_value = {
        "tracks": [
            {
                "videoId": "rec_1",
                "title": "Rec 1",
                "artists": [{"name": "Artist 1"}],
                "length": "3:10",
            },
            {
                "videoId": "rec_2",
                "title": "Rec 2",
                "artists": [{"name": "Artist 2"}],
                "length": "4:00",
            },
        ]
    }

    client = YTMusicClient(yt_instance=mock_yt)
    radio_tracks = await client.get_watch_radio("seed_vid", limit=1)

    assert len(radio_tracks) == 1
    assert radio_tracks[0].video_id == "rec_1"
    mock_yt.get_watch_playlist.assert_called_once_with(videoId="seed_vid", limit=1, radio=True)


@pytest.mark.asyncio
async def test_ytm_client_network_error_handling() -> None:
    """Test network errors raise YTMNetworkError."""
    mock_yt = MagicMock()
    mock_yt.search.side_effect = requests.exceptions.ConnectionError("Failed to connect")

    client = YTMusicClient(yt_instance=mock_yt)
    with pytest.raises(YTMNetworkError, match="Network error during _search_sync"):
        await client.search("query")


@pytest.mark.asyncio
async def test_ytm_client_api_error_handling() -> None:
    """Test YTMusicError raises YTMError."""
    mock_yt = MagicMock()
    mock_yt.get_home.side_effect = YTMusicError("Server returned 500")

    client = YTMusicClient(yt_instance=mock_yt)
    with pytest.raises(YTMError, match="YouTube Music API error during _get_home_sections_sync"):
        await client.get_home_sections()


@pytest.mark.asyncio
async def test_ytm_client_get_artist_recovers_from_missing_header_keyerror() -> None:
    """When ytmusicapi.get_artist raises KeyError('header') on two-column pages, fallback recovers full profile."""
    mock_yt = MagicMock()
    mock_yt.get_artist.side_effect = KeyError("header")
    mock_yt.parser = None
    mock_yt.search.return_value = []
    mock_yt._send_request.return_value = {
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [
                    {
                        "tabRenderer": {
                            "content": {
                                "sectionListRenderer": {
                                    "contents": [
                                        {
                                            "musicResponsiveHeaderRenderer": {
                                                "title": {"runs": [{"text": "Shashwat Sachdev"}]},
                                                "thumbnail": {
                                                    "musicThumbnailRenderer": {
                                                        "thumbnail": {
                                                            "thumbnails": [
                                                                {
                                                                    "url": "https://example.com/shashwat.jpg",
                                                                    "width": 226,
                                                                }
                                                            ]
                                                        }
                                                    }
                                                },
                                                "subscriptionButton": {
                                                    "subscribeButtonRenderer": {
                                                        "channelId": "UCtIiZrzDsXk_rWeynT1ho4g",
                                                        "subscriberCountText": {
                                                            "runs": [{"text": "420K"}]
                                                        },
                                                    }
                                                },
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                ],
                "secondaryContents": {
                    "sectionListRenderer": {
                        "contents": [
                            {
                                "musicCarouselShelfRenderer": {
                                    "contents": []
                                }
                            },
                            {
                                "musicCarouselShelfRenderer": {
                                    "header": {
                                        "musicCarouselShelfBasicHeaderRenderer": {
                                            "title": {"runs": [{"text": "Albums"}]}
                                        }
                                    },
                                    "contents": [
                                        {
                                            "musicTwoRowItemRenderer": {
                                                "title": {"runs": [{"text": "Dhurandhar"}]},
                                                "subtitle": {
                                                    "runs": [{"text": "Album • 2026"}]
                                                },
                                                "navigationEndpoint": {
                                                    "browseEndpoint": {
                                                        "browseId": "MPREb_dhurandhar"
                                                    }
                                                },
                                                "thumbnailRenderer": {
                                                    "musicThumbnailRenderer": {
                                                        "thumbnail": {
                                                            "thumbnails": [
                                                                {
                                                                    "url": "https://example.com/alb.jpg",
                                                                    "width": 226,
                                                                }
                                                            ]
                                                        }
                                                    }
                                                },
                                            }
                                        }
                                    ],
                                }
                            },
                            {
                                "musicDescriptionShelfRenderer": {
                                    "description": {"runs": [{"text": "Indian composer."}]},
                                    "subheader": {"runs": [{"text": "1B views"}]},
                                }
                            },
                        ]
                    }
                },
            }
        }
    }

    client = YTMusicClient(yt_instance=mock_yt)
    profile = await client.get_artist_profile(
        name="Shashwat Sachdev", channel_id="UCtIiZrzDsXk_rWeynT1ho4g"
    )

    assert profile["name"] == "Shashwat Sachdev"
    assert profile["subscribers"] == "420K"
    assert profile["views"] == "1B views"
    assert profile["description"] == "Indian composer."
    assert profile["thumbnail_url"] == "https://example.com/shashwat.jpg"
    assert len(profile["albums"]) == 1
    assert profile["albums"][0]["browse_id"] == "MPREb_dhurandhar"
    assert profile["albums"][0]["year"] == "2026"


def test_ytm_client_auth_fallback() -> None:
    """Test that auth initialization failure gracefully falls back to unauthenticated."""
    with patch("metrolite.ytm.client.YTMusic") as mock_yt_cls:
        # First call with auth raises, second call with auth=None succeeds
        mock_yt_cls.side_effect = [Exception("Bad cookies"), MagicMock()]
        client = YTMusicClient(cookies_file="nonexistent_cookies.txt")
        assert client._yt is not None
        assert mock_yt_cls.call_count == 2
        mock_yt_cls.assert_called_with(auth=None, language="en")


def test_normalize_track_preserves_collaborating_artist_credits() -> None:
    """Test normalizing a collaboration retains per-artist IDs and names."""
    raw = {
        "videoId": "collab_1",
        "title": "Duet Track",
        "artists": [
            {"id": "UC_ARTIST_A", "name": "Artist A"},
            {"id": "UC_ARTIST_B", "name": "Artist B"},
        ],
        "duration": "3:10",
    }
    track = normalize_track(raw)
    assert track.artist == "Artist A, Artist B"
    assert track.artist_id == "UC_ARTIST_A"
    assert [(a.name, a.id) for a in track.artists] == [
        ("Artist A", "UC_ARTIST_A"),
        ("Artist B", "UC_ARTIST_B"),
    ]


@pytest.mark.asyncio
async def test_get_artist_profile_preserves_comma_artist_name() -> None:
    """Test artist lookup tries exact name (e.g. 'Tyler, The Creator') before comma splitting."""
    mock_yt = MagicMock()
    mock_yt.search.return_value = [
        {"browseId": "UC_TYLER", "artist": "Tyler, The Creator", "thumbnails": []}
    ]
    mock_yt.get_artist.return_value = {
        "name": "Tyler, The Creator",
        "songs": {"results": []},
    }

    client = YTMusicClient(yt_instance=mock_yt)
    profile = await client.get_artist_profile(name="Tyler, The Creator")
    assert profile["name"] == "Tyler, The Creator"
    mock_yt.search.assert_any_call(query="Tyler, The Creator", filter="artists", limit=6)
