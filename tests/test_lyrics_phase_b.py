"""Unit tests for Phase B TTML/Enhanced LRC/LyricsPlus parsing, 5-provider cascade & variants."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from metrolite.audio.media_keys import MediaKeysListener
from metrolite.audio.player import AudioPlayer
from metrolite.audio.queue import QueueManager
from metrolite.bridge.server import BridgeController, WebSocketManager, create_app
from metrolite.library.downloader import DownloadManager
from metrolite.library.manager import LibraryManager
from metrolite.store.db import SCHEMA_SQL
from metrolite.ytm.client import Track, YTMusicClient
from metrolite.ytm.lyrics import (
    _LYRICS_CACHE,
    _combine_artists,
    _compute_alignment_offset,
    _has_valid_timestamps,
    _parse_lyricsplus_payload,
    _parse_musixmatch_richsync,
    _parse_paxsenix_payload,
    _pick_best_lrclib_match,
    _pick_lrclib_variants,
    format_lrc_lines,
    parse_lrc_lines,
    parse_ttml,
)


@pytest.fixture(autouse=True)
def clear_lyrics_caches() -> None:
    _LYRICS_CACHE.clear()


@pytest.fixture
def bridge_client(tmp_path: Path) -> tuple[TestClient, BridgeController, str]:
    db_file = tmp_path / "lyrics_phase_b.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.executescript(SCHEMA_SQL)
    conn.close()

    mock_mpv = MagicMock()
    mock_mpv.pause = False
    mock_mpv.idle_active = False
    mock_mpv.core_idle = False
    mock_mpv.time_pos = 0.0
    mock_mpv.duration = 210.0
    mock_mpv.volume = 80.0

    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock(side_effect=lambda vid: f"https://mock-stream/{vid}")
    mock_player = AudioPlayer(resolver=mock_resolver, mpv_instance=mock_mpv)

    mock_ytm = MagicMock(spec=YTMusicClient)
    mock_ytm._yt = None
    mock_ytm.search = AsyncMock(return_value=[])

    mock_downloader = MagicMock(spec=DownloadManager)
    mock_downloader.db_path = db_file
    mock_downloader.stop = MagicMock()

    controller = BridgeController(
        player=mock_player,
        queue=QueueManager(db_path=db_file, ytm_client=mock_ytm, auto_persist=False),
        library=LibraryManager(db_path=db_file),
        downloader=mock_downloader,
        ytm=mock_ytm,
        media_keys=MediaKeysListener(enabled=False),
        db_path=db_file,
        ws_manager=WebSocketManager(),
    )
    token = "phase-b-test-token"
    app = create_app(controller=controller, token=token)
    return TestClient(app), controller, token


# ---------------------------------------------------------------------------
# Enhanced LRC, TTML & LyricsPlus Parser Tests
# ---------------------------------------------------------------------------


def test_parse_enhanced_lrc_word_timestamps() -> None:
    lrc = (
        "[00:12.40] <00:12.40>Enter <00:12.85>the <00:13.10>orbit <00:13.90>\n"
        "[00:14.20] <00:14.20>Feel <00:14.60>the <00:15.00>gravity\n"
    )
    lines = parse_lrc_lines(lrc)
    assert len(lines) == 2

    first = lines[0]
    assert first["type"] == "line"
    assert first["time"] == 12.4
    assert first["text"] == "Enter the orbit"
    assert first["words"] == [
        {"time": 12.4, "end": 12.85, "text": "Enter"},
        {"time": 12.85, "end": 13.1, "text": "the"},
        {"time": 13.1, "end": 13.9, "text": "orbit"},
    ]

    second = lines[1]
    assert second["type"] == "line"
    assert second["text"] == "Feel the gravity"
    assert len(second["words"]) == 3
    assert second["words"][0] == {"time": 14.2, "end": 14.6, "text": "Feel"}
    assert second["words"][1] == {"time": 14.6, "end": 15.0, "text": "the"}
    assert second["words"][2]["time"] == 15.0
    assert second["words"][2]["end"] > 15.0
    assert second["words"][2]["text"] == "gravity"


def test_parse_ttml_word_sync_duet_and_embedded_translation() -> None:
    ttml = """<?xml version="1.0" encoding="UTF-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml"
        xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
        xml:lang="ja">
      <head>
        <metadata>
          <songwriters><songwriter>Kenshi Yonezu</songwriter></songwriters>
        </metadata>
      </head>
      <body>
        <div>
          <p begin="00:01.000" end="00:03.000" ttm:agent="v1">
            <span begin="00:01.000" end="00:01.800">夢</span>
            <span begin="00:01.800" end="00:03.000">ならば</span>
            <span ttm:role="x-roman">Yume naraba</span>
            <span ttm:role="x-translation" xml:lang="en">If it were a dream</span>
          </p>
          <p begin="00:03.500" end="00:05.500" ttm:agent="v2">
            <span begin="00:03.500" end="00:04.300">どれ</span>
            <span begin="00:04.300" end="00:05.500">ほど</span>
            <span ttm:role="x-roman">dore hodo</span>
            <span ttm:role="x-translation" xml:lang="en">how wonderful</span>
          </p>
        </div>
      </body>
    </tt>"""

    synced, sync_type, writers, xml_lang, native_tr = parse_ttml(ttml)
    assert sync_type == "word"
    assert writers == ["Kenshi Yonezu"]
    assert xml_lang == "ja"
    assert native_tr == {"en": ["If it were a dream", "how wonderful"]}
    assert len(synced) == 2
    assert synced[0]["align"] == "left"
    assert synced[0]["romanized"] == "Yume naraba"
    assert len(synced[0]["words"]) == 2
    assert synced[1]["align"] == "right"
    assert synced[1]["romanized"] == "dore hodo"
    assert len(synced[1]["words"]) == 2


def test_parse_lyricsplus_word_sync_and_duration_guard() -> None:
    payload = {
        "type": "Word",
        "metadata": {
            "source": "Apple",
            "songWriters": ["Arijit Singh"],
            "language": "hi",
            "totalDuration": "3:27.000",
        },
        "lyrics": [
            {
                "time": 10000,
                "duration": 2200,
                "text": "तेरे बिना",
                "transliteration": {"text": "Tere bina"},
                "element": {"singer": "v1"},
                "syllabus": [
                    {"time": 10000, "duration": 1000, "text": "तेरे "},
                    {"time": 11000, "duration": 1200, "text": "बिना"},
                ],
            },
            {
                "time": 12600,
                "duration": 2000,
                "text": "दिल लगे ना",
                "transliteration": {"text": "Dil lage na"},
                "element": {"singer": "v2"},
                "syllabus": [
                    {"time": 12600, "duration": 900, "text": "दिल "},
                    {"time": 13500, "duration": 1100, "text": "लगे ना"},
                ],
            },
        ],
    }

    parsed = _parse_lyricsplus_payload(payload, duration=207, is_special_edit=False, custom_query=None)
    assert parsed is not None
    assert parsed["sync_type"] == "word"
    assert parsed["source"] == "LyricsPlus (Apple)"
    assert parsed["songwriters"] == ["Arijit Singh"]
    assert parsed["synced"][0]["align"] == "left"
    assert parsed["synced"][0]["romanized"] == "Tere bina"
    assert len(parsed["synced"][0]["words"]) == 2
    assert parsed["synced"][1]["align"] == "right"

    # Millisecond totalDuration (207000 ms == 207s) must be accepted, not rejected
    ms_payload = {
        **payload,
        "metadata": {**payload["metadata"], "totalDuration": 207000},
    }
    parsed_ms = _parse_lyricsplus_payload(
        ms_payload, duration=207, is_special_edit=False, custom_query=None
    )
    assert parsed_ms is not None
    assert parsed_ms["sync_type"] == "word"

    # Rejects duration mismatch (> 10s off)
    rejected = _parse_lyricsplus_payload(payload, duration=225, is_special_edit=False, custom_query=None)
    assert rejected is None


def test_parse_standard_lrc_backward_compatible() -> None:
    lrc = (
        "[00:05.00][00:10.00] Chorus line\n"
        "[00:02.50] Opening verse\n"
    )
    lines = parse_lrc_lines(lrc)
    assert len(lines) == 3
    assert [line["time"] for line in lines] == [2.5, 5.0, 10.0]
    for line in lines:
        assert line["type"] == "line"
        assert "words" not in line
        assert "align" not in line
    assert lines[0]["text"] == "Opening verse"
    assert lines[1]["text"] == "Chorus line"
    assert lines[2]["text"] == "Chorus line"


def test_parse_duet_singer_tags_and_alignment() -> None:
    lrc = (
        "[00:01.00] v1: First vocalist line\n"
        "[00:03.50] v2: Second vocalist reply\n"
        "[00:06.00] M: Male vocal phrase\n"
        "[00:08.50] F: <00:08.50>Female <00:09.10>harmony <00:09.80>\n"
    )
    lines = parse_lrc_lines(lrc)
    assert len(lines) == 4

    assert lines[0]["align"] == "left"
    assert lines[0]["text"] == "First vocalist line"
    assert lines[1]["align"] == "right"
    assert lines[1]["text"] == "Second vocalist reply"
    assert lines[2]["align"] == "left"
    assert lines[2]["text"] == "Male vocal phrase"
    assert lines[3]["align"] == "right"
    assert lines[3]["text"] == "Female harmony"
    assert lines[3]["words"][0]["text"] == "Female"
    assert lines[3]["words"][1]["text"] == "harmony"


def test_instrumental_break_insertion_threshold() -> None:
    lrc = (
        "[00:10.00] Line before short gap\n"
        "[00:15.00] Exactly 5.0s gap should NOT insert break\n"
        "[00:25.50] After 10.5s instrumental break\n"
    )
    lines = parse_lrc_lines(lrc)
    assert len(lines) == 4
    assert lines[0]["type"] == "line"
    assert lines[1]["type"] == "line"
    assert lines[2]["type"] == "instrumental"
    assert lines[2]["text"] == ""
    assert lines[1]["time"] < lines[2]["time"] < lines[3]["time"]
    assert lines[2]["duration"] > 5.0
    assert lines[3]["type"] == "line"


def test_no_false_instrumental_mid_line_for_normal_line_only_gaps() -> None:
    lrc_standard = (
        "[00:10.00] A slower vocal line that spans almost nine seconds\n"
        "[00:18.80] Immediately followed by the next vocal line\n"
    )
    parsed_std = parse_lrc_lines(lrc_standard)
    assert [ln["type"] for ln in parsed_std] == ["line", "line"]

    lrc_word = (
        "[00:10.00] <00:10.00>Slow <00:12.50>melodic <00:14.00>line <00:15.20>\n"
        "[00:15.80] <00:15.80>Next <00:16.50>line <00:17.20>\n"
    )
    parsed_word = parse_lrc_lines(lrc_word)
    assert [ln["type"] for ln in parsed_word] == ["line", "line"]


def test_lrclib_strict_artist_edit_duration_and_latin_priority() -> None:
    # 0. Rejects unrelated song title even if artist & duration match
    wrong_title_items = [
        {
            "trackName": "Completely Different Song",
            "artistName": "Revel, Simran Bumrah",
            "duration": 200,
            "syncedLyrics": "[00:01.00] Unrelated lyrics\n",
        }
    ]
    assert (
        _pick_best_lrclib_match(
            wrong_title_items, "Finding Her", ["Revel", "Simran Bumrah"], 200, require_artist=True
        )
        is None
    )

    # 1. Rejects unrelated artist sharing the same song title during auto-fetch
    wrong_artist_items = [
        {
            "trackName": "Finding Her",
            "artistName": "Unrelated Indie Band",
            "duration": 200,
            "syncedLyrics": "[00:01.00] Wrong English lyrics\n",
        }
    ]
    assert (
        _pick_best_lrclib_match(
            wrong_artist_items, "Finding Her", ["Revel", "Simran Bumrah"], 200, require_artist=True
        )
        is None
    )

    # 2. Rejects Slowed/Remix candidate when playing normal studio track
    slowed_items = [
        {
            "trackName": "Jhol (Slowed + Reverb)",
            "artistName": "Maanu, Annural Khalid",
            "duration": 212,
            "syncedLyrics": "[00:01.00] Slowed lyrics\n",
        }
    ]
    assert (
        _pick_best_lrclib_match(
            slowed_items, "Jhol", ["Maanu", "Annural Khalid"], 210, require_artist=True, raw_title="Jhol"
        )
        is None
    )

    # 3. Rejects 6s duration difference on auto-fetch (prevents 6s early/late sync drift!)
    drifted_items = [
        {
            "trackName": "Jhol",
            "artistName": "Maanu, Annural Khalid",
            "duration": 204,
            "syncedLyrics": "[00:01.00] Kiven tainu main vikhaavan\n",
        }
    ]
    assert (
        _pick_best_lrclib_match(
            drifted_items, "Jhol", ["Maanu", "Annural Khalid"], 210, require_artist=True, raw_title="Jhol"
        )
        is None
    )

    # 3b. Accepts tight ±3.5s match on studio track
    tight_items = [
        {
            "trackName": "Jhol",
            "artistName": "Maanu, Annural Khalid",
            "duration": 208,
            "syncedLyrics": "[00:01.00] Kiven tainu main vikhaavan\n",
        }
    ]
    assert (
        _pick_best_lrclib_match(
            tight_items, "Jhol", ["Maanu", "Annural Khalid"], 210, require_artist=True, raw_title="Jhol"
        )
        is not None
    )

    # 4. Prioritizes Latin/Romanized synced lyrics over Devanagari AND keeps Hindi variant
    mixed_script_items = [
        {
            "trackName": "Finding Her",
            "artistName": "Kushagra, Bharath, Saaheal",
            "duration": 207,
            "syncedLyrics": "[00:01.00] जाने किस हाल में है वो\n[00:05.00] दिल मेरा ढूंढे उसे\n",
        },
        {
            "trackName": "Finding Her",
            "artistName": "Kushagra, Bharath, Saaheal",
            "duration": 207,
            "syncedLyrics": "[00:01.00] Jaane kis haal mein hai woh\n[00:05.00] Dil mera dhoonde use\n",
        },
    ]
    picked, alternates, diff = _pick_lrclib_variants(
        mixed_script_items,
        "Finding Her",
        ["Kushagra", "Bharath", "Saaheal"],
        207,
        require_artist=True,
    )
    assert picked is not None
    assert diff == 0.0
    assert "Jaane kis haal" in picked["syncedLyrics"]
    assert len(alternates) == 1
    assert "जाने किस हाल" in alternates[0]["syncedLyrics"]


def test_format_lrc_lines_preserves_enhanced_words_and_skips_instrumental() -> None:
    lrc = (
        "[00:02.00] v2: <00:02.00>Night <00:02.50>sky <00:03.20>\n"
        "[00:14.00] Back again\n"
    )
    parsed = parse_lrc_lines(lrc)
    assert any(item["type"] == "instrumental" for item in parsed)

    serialized = format_lrc_lines(parsed)
    assert "v2:" in serialized
    assert "<00:02.00>Night" in serialized

    reparsed = parse_lrc_lines(serialized)
    lyric_lines = [item for item in reparsed if item["type"] == "line"]
    assert len(lyric_lines) == 2
    assert lyric_lines[0]["align"] == "right"
    assert len(lyric_lines[0]["words"]) == 2
    assert any(item["type"] == "instrumental" for item in reparsed)


# ---------------------------------------------------------------------------
# 5-Provider Cascade, Force/Query & Exact-Video Preference Tests
# ---------------------------------------------------------------------------


def test_five_provider_cascade_prefers_word_sync_and_stops_early(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    word_synced = parse_lrc_lines(
        "[00:01.00] <00:01.00>Hello <00:01.50>world <00:02.00>\n"
        "[00:02.50] <00:02.50>Second <00:03.00>line <00:03.60>\n"
    )
    unison_word = {
        "synced": word_synced,
        "lyrics": "Hello world\nSecond line",
        "source": "Unison",
        "sync_type": "word",
        "credit": "Unison",
        "songwriters": ["Writer A"],
        "offline": False,
        "_exact_video": True,
    }

    mock_unison = AsyncMock(return_value=unison_word)
    mock_lyricsplus = AsyncMock(return_value=None)
    mock_lrc_red = AsyncMock(return_value=None)
    mock_lrclib = AsyncMock(return_value=None)

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", mock_unison),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", mock_lyricsplus),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", mock_lrc_red),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", mock_lrclib),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
    ):
        res = client.get("/lyrics/wordSyncVid?title=Hello&artist=Artist", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["source"] == "Unison"
        assert body["sync_type"] == "word"
        assert body["songwriters"] == ["Writer A"]
        assert body["variants"] == []
        mock_lyricsplus.assert_not_called()
        mock_lrc_red.assert_not_called()
        mock_lrclib.assert_not_called()


def test_ytm_exact_video_sync_beats_drifted_lrclib(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    drifted_lrclib = {
        "synced": parse_lrc_lines("[00:01.00] Drifted line\n"),
        "lyrics": "Drifted line",
        "source": "LRCLIB",
        "sync_type": "line",
        "_duration_diff": 6.0,
        "offline": False,
    }
    exact_ytm = {
        "synced": parse_lrc_lines("[00:07.00] Exact video synced line\n"),
        "lyrics": "Exact video synced line",
        "source": "YouTube Music",
        "sync_type": "line",
        "_exact_video": True,
        "offline": False,
    }

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=drifted_lrclib)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics._fetch_ytm_lyrics_sync", return_value=exact_ytm),
    ):
        res = client.get("/lyrics/driftTestVid?title=Song&artist=Artist", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["source"] == "YouTube Music"
        assert body["synced"][0]["time"] == 7.0
        assert "_duration_diff" not in body
        assert "_exact_video" not in body


def test_get_lyrics_endpoint_force_and_query(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    client, controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    first_payload = {
        "synced": parse_lrc_lines("[00:01.00] Cached initial line\n"),
        "lyrics": "Cached initial line",
        "source": "LRCLIB",
        "offline": False,
    }
    refetched_payload = {
        "synced": parse_lrc_lines(
            "[00:01.00] v2: <00:01.00>Fresh <00:01.40>lyrics <00:02.00>\n"
            "[00:11.00] After solo\n"
        ),
        "lyrics": "Fresh lyrics\nAfter solo",
        "source": "LRCLIB",
        "offline": False,
    }
    custom_query_payload = {
        "synced": parse_lrc_lines("[00:02.00] Custom search hit\n"),
        "lyrics": "Custom search hit",
        "source": "LRCLIB",
        "offline": False,
    }

    mock_fetch = AsyncMock(side_effect=[first_payload, refetched_payload, custom_query_payload])

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", mock_fetch),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
    ):
        res1 = client.get(
            "/lyrics/dQw4w9WgXcQ?title=Original&artist=Artist",
            headers=headers,
        )
        assert res1.status_code == 200
        assert res1.json()["lyrics"] == "Cached initial line"
        assert mock_fetch.call_count == 1

        res2 = client.get("/lyrics/dQw4w9WgXcQ", headers=headers)
        assert res2.status_code == 200
        assert res2.json()["lyrics"] == "Cached initial line"
        assert mock_fetch.call_count == 1

        res3 = client.get(
            "/lyrics/dQw4w9WgXcQ?force=true&title=Original&artist=Artist",
            headers=headers,
        )
        assert res3.status_code == 200
        body3 = res3.json()
        assert body3["lyrics"] == "Fresh lyrics\nAfter solo"
        assert mock_fetch.call_count == 2
        synced3 = body3["synced"]
        assert synced3[0]["type"] == "line"
        assert synced3[0]["align"] == "right"
        assert len(synced3[0]["words"]) == 2
        assert synced3[1]["type"] == "instrumental"
        assert synced3[1]["duration"] > 0

        res4 = client.get(
            "/lyrics/dQw4w9WgXcQ?query=Kesariya+Arijit",
            headers=headers,
        )
        assert res4.status_code == 200
        assert res4.json()["lyrics"] == "Custom search hit"
        assert mock_fetch.call_count == 3
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("custom_query") == "Kesariya Arijit"

    controller.ytm.search = AsyncMock(
        return_value=[Track(video_id="fallbackVid1", title="Matched Song", artist="Singer")]
    )
    ytm_fallback_result = {
        "synced": [{"type": "line", "time": 3.0, "text": "From YTM fallback"}],
        "lyrics": "From YTM fallback",
        "source": "YouTube Music",
        "offline": False,
    }
    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
        patch(
            "metrolite.ytm.lyrics._fetch_ytm_lyrics_sync",
            return_value=ytm_fallback_result,
        ) as mock_ytm_lyrics,
    ):
        res5 = client.get(
            "/lyrics/dQw4w9WgXcQ?query=Rare+Track+Title",
            headers=headers,
        )
        assert res5.status_code == 200
        assert res5.json()["source"] == "YouTube Music"
        assert res5.json()["lyrics"] == "From YTM fallback"
        mock_ytm_lyrics.assert_called_once_with(controller.ytm, "fallbackVid1")


def test_no_machine_translation_endpoint_exists(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}
    res = client.post(
        "/lyrics/dQw4w9WgXcQ/translate",
        json={"target_lang": "hi", "lines": ["Hello world"]},
        headers=headers,
    )
    assert res.status_code == 404


def test_parallel_stage2_prefers_latin_over_unvoweled_urdu(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    urdu_line = {
        "synced": parse_lrc_lines("[00:01.00] میں بیٹھ لکھوں تُو آ بیٹھے\n"),
        "lyrics": "میں بیٹھ لکھوں تُو آ بیٹھے",
        "source": "lrc.red",
        "sync_type": "line",
        "offline": False,
    }
    latin_line = {
        "synced": parse_lrc_lines("[00:01.00] Main baith likhoon tu aa baithe\n"),
        "lyrics": "Main baith likhoon tu aa baithe",
        "source": "LRCLIB",
        "sync_type": "line",
        "offline": False,
    }

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=urdu_line)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=latin_line)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
    ):
        res = client.get("/lyrics/ishqVid1234?title=Ishq&artist=Faheem", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["source"] == "LRCLIB"
        assert "Main baith likhoon" in body["lyrics"]


def test_word_sync_never_downgraded_to_line_only_variant(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    word_urdu = {
        "synced": parse_lrc_lines(
            "[00:01.00] <00:01.00>میں <00:01.50>بیٹھ <00:02.00>لکھوں <00:02.60>\n"
            "[00:03.00] <00:03.00>تُو <00:03.50>آ <00:04.00>بیٹھے <00:04.80>\n"
        ),
        "lyrics": "میں بیٹھ لکھوں\nتُو آ بیٹھے",
        "source": "LyricsPlus (Apple)",
        "sync_type": "word",
        "offline": False,
    }
    line_latin = {
        "synced": parse_lrc_lines("[00:01.00] Main baith likhoon\n[00:03.00] Tu aa baithe\n"),
        "lyrics": "Main baith likhoon\nTu aa baithe",
        "source": "LRCLIB",
        "sync_type": "line",
        "offline": False,
    }

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=word_urdu)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=line_latin)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
    ):
        res = client.get("/lyrics/wordKeepVid?title=Ishq&artist=Faheem", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["sync_type"] == "word"
        assert body["source"] == "LyricsPlus (Apple)"
        assert body["variants"] == []


def test_word_sync_never_downgraded_to_line_sync(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    hindi_word = {
        "synced": parse_lrc_lines(
            "[00:01.00] <00:01.00>तेरे <00:01.50>बिना <00:02.00>\n"
            "[00:02.50] <00:02.50>दिल <00:03.00>लगे <00:03.60>\n"
        ),
        "lyrics": "तेरे बिना\nदिल लगे",
        "source": "Unison",
        "sync_type": "word",
        "offline": False,
    }
    latin_line = {
        "synced": parse_lrc_lines("[00:01.00] Tere bina\n[00:02.50] Dil lage\n"),
        "lyrics": "Tere bina\nDil lage",
        "source": "LRCLIB",
        "sync_type": "line",
        "offline": False,
    }

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=hindi_word)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=latin_line)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
    ):
        res = client.get("/lyrics/wordKeep123?title=Song&artist=Artist", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["source"] == "Unison"
        assert body["sync_type"] == "word"
        # Line-synced LRCLIB variant must NOT be attached to a word-synced primary
        assert body["variants"] == []


def test_word_sync_auto_aligned_to_exact_ytm_intro_offset(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    """Metadata word-synced lyrics (e.g. Apple Music) auto-align to YouTube video intro offset."""
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    # Apple Music word sync starts at 10.0s, 14.0s, 18.0s
    apple_word = {
        "synced": parse_lrc_lines(
            "[00:10.00] <00:10.00>Tera <00:10.60>mera <00:11.20>rishta <00:12.00>\n"
            "[00:14.00] <00:14.00>Kitna <00:14.70>pyara <00:15.40>hai <00:16.00>\n"
            "[00:18.00] <00:18.00>Yaad <00:18.60>teri <00:19.30>aati <00:20.00>\n"
        ),
        "lyrics": "Tera mera rishta\nKitna pyara hai\nYaad teri aati",
        "source": "LyricsPlus (Apple)",
        "sync_type": "word",
        "offline": False,
    }
    # YouTube Music video has a +3.2s longer intro -> starts at 13.2s, 17.2s, 21.2s
    ytm_exact = {
        "synced": parse_lrc_lines(
            "[00:13.20] Tera mera rishta\n"
            "[00:17.20] Kitna pyara hai\n"
            "[00:21.20] Yaad teri aati\n"
        ),
        "lyrics": "Tera mera rishta\nKitna pyara hai\nYaad teri aati",
        "source": "YouTube Music",
        "sync_type": "line",
        "_exact_video": True,
        "offline": False,
    }

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=apple_word)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics._fetch_ytm_lyrics_sync", return_value=ytm_exact),
    ):
        res = client.get("/lyrics/alignVid123?title=Rishta&artist=Artist", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["sync_type"] == "word"
        assert body["source"] == "LyricsPlus (Apple)"
        # Word timestamps shifted cleanly by +3.2s to lock onto the YouTube stream!
        assert abs(body["synced"][0]["time"] - 13.2) < 0.05
        assert abs(body["synced"][0]["words"][0]["time"] - 13.2) < 0.05
        assert abs(body["synced"][1]["time"] - 17.2) < 0.05
        assert abs(body["synced"][2]["time"] - 21.2) < 0.05


def test_featured_artists_extracted_from_title() -> None:
    assert _combine_artists("Revel", "Finding Her (feat. Simran Bumrah)") == [
        "Revel",
        "Simran Bumrah",
    ]
    assert _combine_artists("Afusic", "Pal Pal (with Talwiinder)") == [
        "Afusic",
        "Talwiinder",
    ]


def test_unaligned_drifted_word_sync_rejected_without_ytm_ref(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    """Word sync with >3.5s duration diff is kept ONLY when auto-aligned to exact YTM."""
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    drifted_word = {
        "synced": parse_lrc_lines(
            "[00:05.00] <00:05.00>Wrong <00:05.50>edit <00:06.00>\n"
            "[00:07.00] <00:07.00>Drifted <00:07.50>line <00:08.00>\n"
        ),
        "lyrics": "Wrong edit\nDrifted line",
        "source": "LyricsPlus (Apple)",
        "sync_type": "word",
        "_duration_diff": 6.0,
        "offline": False,
    }

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=drifted_word)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics._fetch_ytm_lyrics_sync", return_value=None),
    ):
        res = client.get("/lyrics/unalignedVid?title=Song&artist=Artist", headers=headers)
        assert res.status_code == 200
        assert res.json()["synced"] is None


def test_parse_paxsenix_syllable_json_and_simpmusic_alignment(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    """Paxsenix Apple Music syllable JSON parses to word sync and aligns via SimpMusic anchor."""
    import json

    pax_json = json.dumps(
        {
            "type": "Syllable",
            "content": [
                {
                    "timestamp": 10000,
                    "endtime": 12000,
                    "oppositeTurn": False,
                    "text": [
                        {"text": "Tera", "timestamp": 10000, "endtime": 10600, "part": False},
                        {"text": "mera", "timestamp": 10600, "endtime": 11200, "part": False},
                        {"text": "rishta", "timestamp": 11200, "endtime": 12000, "part": False},
                    ],
                },
                {
                    "timestamp": 14000,
                    "endtime": 16000,
                    "oppositeTurn": True,
                    "text": [
                        {"text": "Kitna", "timestamp": 14000, "endtime": 14700, "part": False},
                        {"text": "pyara", "timestamp": 14700, "endtime": 15400, "part": False},
                        {"text": "hai", "timestamp": 15400, "endtime": 16000, "part": False},
                    ],
                },
                {
                    "timestamp": 18000,
                    "endtime": 20000,
                    "oppositeTurn": False,
                    "text": [
                        {"text": "Yaad", "timestamp": 18000, "endtime": 18600, "part": False},
                        {"text": "teri", "timestamp": 18600, "endtime": 19300, "part": False},
                        {"text": "aati", "timestamp": 19300, "endtime": 20000, "part": False},
                    ],
                },
            ],
        }
    )
    parsed = _parse_paxsenix_payload(pax_json, duration=200, dur_diff=4.0, custom_query=None)
    assert parsed is not None
    assert parsed["sync_type"] == "word"
    assert parsed["source"] == "Apple Music"
    assert parsed["synced"][1]["align"] == "right"
    assert len(parsed["synced"][0]["words"]) == 3

    # Verify SimpMusic exact video_id anchors Paxsenix when YouTube Music returns None
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}
    simpmusic_anchor = {
        "synced": parse_lrc_lines(
            "[00:12.50] Tera mera rishta\n"
            "[00:16.50] Kitna pyara hai\n"
            "[00:20.50] Yaad teri aati\n"
        ),
        "lyrics": "Tera mera rishta\nKitna pyara hai\nYaad teri aati",
        "source": "SimpMusic",
        "sync_type": "line",
        "_exact_video": True,
        "offline": False,
    }
    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=parsed)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=simpmusic_anchor)),
        patch("metrolite.ytm.lyrics._fetch_ytm_lyrics_sync", return_value=None),
    ):
        res = client.get("/lyrics/paxSimpVid1?title=Rishta&artist=Artist", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["source"] == "Apple Music"
        assert body["sync_type"] == "word"
        assert abs(body["synced"][0]["time"] - 12.5) < 0.05
        assert abs(body["synced"][0]["words"][0]["time"] - 12.5) < 0.05


def test_parse_lrc_offset_tag_shifts_timestamps() -> None:
    lrc = (
        "[offset:+500]\n"
        "[00:10.00] First line\n"
        "[00:14.00] Second line\n"
    )
    lines = parse_lrc_lines(lrc)
    assert len(lines) == 2
    assert lines[0]["time"] == 9.5
    assert lines[1]["time"] == 13.5


def test_exact_video_ytm_beats_unaligned_lrclib_even_at_zero_duration_diff(
    bridge_client: tuple[TestClient, BridgeController, str],
) -> None:
    client, _controller, token = bridge_client
    headers = {"X-Bridge-Token": token}

    unaligned_lrclib = {
        "synced": parse_lrc_lines("[00:01.00] Different phrasing A\n[00:05.00] Different phrasing B\n"),
        "lyrics": "Different phrasing A\nDifferent phrasing B",
        "source": "LRCLIB",
        "sync_type": "line",
        "_duration_diff": 0.0,
        "offline": False,
    }
    exact_ytm = {
        "synced": parse_lrc_lines("[00:02.80] Exact stream line one\n[00:06.80] Exact stream line two\n"),
        "lyrics": "Exact stream line one\nExact stream line two",
        "source": "YouTube Music",
        "sync_type": "line",
        "_exact_video": True,
        "offline": False,
    }

    with (
        patch("metrolite.ytm.lyrics.fetch_unison_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_betterlyrics_api", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_paxsenix_apple_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lyricsplus_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrc_red_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics.fetch_lrclib_lyrics", AsyncMock(return_value=unaligned_lrclib)),
        patch("metrolite.ytm.lyrics.fetch_simpmusic_lyrics", AsyncMock(return_value=None)),
        patch("metrolite.ytm.lyrics._fetch_ytm_lyrics_sync", return_value=exact_ytm),
    ):
        res = client.get("/lyrics/exactBeatLrc?title=Song&artist=Artist", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["source"] == "YouTube Music"
        assert body["synced"][0]["time"] == 2.8


def test_lyricsplus_rejects_zero_timestamps_and_ttml_anchors_to_first_word() -> None:
    # 1. LyricsPlus all-zero timestamp payload must NEVER pose as synced (prevents jumping to last line!)
    zero_payload = {
        "type": "Line",
        "metadata": {"source": "Apple", "totalDuration": "3:20.000"},
        "lyrics": [
            {"time": 0, "duration": 0, "text": "Line one"},
            {"time": 0, "duration": 0, "text": "Line two"},
            {"time": 0, "duration": 0, "text": "Line three"},
            {"time": 0, "duration": 0, "text": "Last line"},
        ],
    }
    assert (
        _parse_lyricsplus_payload(zero_payload, duration=200, is_special_edit=False, custom_query=None)
        is None
    )
    assert _has_valid_timestamps([{"type": "line", "time": 0.0, "text": f"L{i}"} for i in range(6)]) is False

    # 2. TTML <p begin> 10s earlier than first word must anchor line start to words[0]["time"]
    preroll_ttml = """<?xml version="1.0" encoding="UTF-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml">
      <body><div>
        <p begin="00:05.000" end="00:08.000">
          <span begin="00:05.000" end="00:06.500">First </span>
          <span begin="00:06.500" end="00:08.000">verse</span>
        </p>
        <p begin="00:09.000" end="00:22.000">
          <span begin="00:19.000" end="00:20.500">Second </span>
          <span begin="00:20.500" end="00:22.000">verse</span>
        </p>
      </div></body>
    </tt>"""
    synced, sync_type, _, _, _ = parse_ttml(preroll_ttml)
    assert sync_type == "word"
    lyric_lines = [ln for ln in synced if ln.get("type") != "instrumental"]
    assert lyric_lines[1]["time"] == 19.0
    assert any(ln.get("type") == "instrumental" for ln in synced)


def test_cross_script_malayalam_to_latin_alignment_and_richsync_parser() -> None:
    # 1. Cross-script Malayalam word-sync (+16s YouTube video intro like Illuminati) aligns to Latin YTM
    mal_cand = [
        {"type": "line", "time": 5.0, "text": "നാടിൻ നന്മകനേ പൊന്മകനേ മുത്തായവനേ"},
        {"type": "line", "time": 9.0, "text": "മിന്നും സൂര്യനും ചന്ദിരനും ഒന്നായവനേ"},
        {"type": "line", "time": 13.0, "text": "കാലം കാത്തുവച്ച രക്ഷകനേ സംഹാരകനേ"},
        {"type": "line", "time": 17.0, "text": "ഭയമേ മാറി പോ നീ അണ്ണൻ വന്നാൽ"},
    ]
    latin_ytm_ref = [
        {"type": "line", "time": 21.0, "text": "Naadin nanmakane ponmakane muthaayavane"},
        {"type": "line", "time": 25.0, "text": "Minnum sooriyanum chanthiranum onnaayavane"},
        {"type": "line", "time": 29.0, "text": "Kaalam kaathuvacha rakshakane samhaarakane"},
        {"type": "line", "time": 33.0, "text": "Bhayame maari po nee annan vannaal"},
    ]
    offset = _compute_alignment_offset(mal_cand, latin_ytm_ref)
    assert offset is not None
    assert abs(offset - 16.0) < 0.05

    # 2. SimpMusic Musixmatch richSyncLyrics JSON parses to word-synced lines
    import json

    rich_json = json.dumps(
        [
            {
                "ts": 10.0,
                "te": 12.2,
                "x": "Hello world now",
                "l": [
                    {"c": "Hello", "o": 0.0},
                    {"c": "world", "o": 0.7},
                    {"c": "now", "o": 1.4},
                ],
            },
            {
                "ts": 13.0,
                "te": 15.0,
                "x": "Second line here",
                "l": [
                    {"c": "Second", "o": 0.0},
                    {"c": "line", "o": 0.6},
                    {"c": "here", "o": 1.2},
                ],
            },
        ]
    )
    rich_lines = _parse_musixmatch_richsync(rich_json)
    assert rich_lines is not None
    assert len(rich_lines) == 2
    assert len(rich_lines[0]["words"]) == 3
    assert rich_lines[0]["words"][1]["time"] == 10.7


def test_parse_ttml_normalizes_comma_spacing() -> None:
    ttml = """<?xml version="1.0" encoding="UTF-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml">
      <body><div>
        <p begin="00:01.000" end="00:04.000">
          <span begin="00:01.000" end="00:02.000">mmm,ga,re</span>
          <span begin="00:02.000" end="00:04.000">,sa,ni</span>
        </p>
      </div></body>
    </tt>"""
    synced, _, _, _, _ = parse_ttml(ttml)
    assert len(synced) == 1
    assert synced[0]["text"] == "mmm, ga, re, sa, ni"
