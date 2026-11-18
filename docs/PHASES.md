# Phase Prompts — MetroLite (`docs/PHASES.md`)

Feed **one prompt at a time**. Start every session with:

~~~text
Read AGENTS.md and docs/UI_SPEC.md. Do Phase N step X from docs/PHASES.md, exactly as written there. First give me a 10-line plan and the list of files you will touch, then wait for my OK.
~~~

---

## Phase 0. Scaffold, Config & SQLite Schema

**Paste to agent**

~~~text
The bootstrap files already exist (created by setup_repo.py): AGENTS.md, config.yaml, pyproject.toml, scripts/check_env.py, scripts/measure_ram.py. Do not overwrite them. Add:
1. src/metrolite/config.py using pydantic-settings to load config.yaml and expand %LOCALAPPDATA% paths.
2. src/metrolite/store/db.py using sqlite3/aiosqlite under %LOCALAPPDATA%\metrolite\metrolite.db with tables: tracks (video_id PK, title, artist, album, duration_s, thumbnail_url, is_liked, liked_at, local_path, file_size_bytes, downloaded_at, play_count, last_played_at), playlists (id PK, name, description, created_at, updated_at), playlist_tracks (playlist_id, video_id, position, added_at), queue_state (id PK=1, current_index, shuffle, repeat_mode, items_json), and settings (key PK, value_json).
3. src/metrolite/__main__.py and src/metrolite/main.py with CLI commands (`metrolite --help`, `metrolite run`, `metrolite play <query>`).
4. tests/test_smoke.py testing config loading and SQLite table creation in a temp folder.
Run ruff and pytest and report RSS RAM.
~~~

**Done when:** `uv run pytest` passes, `uv run python -m metrolite --help` works, and RSS RAM is <45 MB.

---

## Phase 1. YouTube Music Client & `libmpv` Audio Engine

**1a. YouTube Music Search & Metadata (`ytmusicapi`)**

~~~text
Implement src/metrolite/ytm/client.py wrapping ytmusicapi (unauthenticated mode by default, optional cookies file from config). Provide async-friendly methods: search(query, filter_type="songs", limit=25), get_home_sections(), get_album(browse_id), get_artist(channel_id), get_watch_radio(video_id, limit=25) for Up-Next recommendations, and normalize every track into a clean Track pydantic model (video_id, title, artist, album, duration_s, thumbnail_url). Handle network errors gracefully. Unit-test normalization and error handling with mocked ytmusicapi responses.
~~~

**1b. Stream Resolver & `libmpv` Gapless Player**

~~~text
Implement src/metrolite/audio/resolver.py and src/metrolite/audio/player.py.
- resolver.py: resolve a video_id to a playable audio source: check SQLite tracks.local_path first (instant offline playback if file exists); otherwise use yt-dlp (`format_selector` from config.yaml, `quiet=True`, `noplaylist=True`) to extract the direct audio stream URL and cache it in memory with stream_url_ttl_s expiry.
- player.py: wrap python-mpv (loading libmpv-2.dll from bin/ or PATH). Configure video=False, demuxer_max_bytes and demuxer_max_back_bytes from config.yaml, gapless_audio="yes", volume=default_volume. Support load_and_play(track), preload_next(track) for gapless transitions, pause(), resume(), seek(seconds), set_volume(0-100), and callbacks for time_pos, duration, track_ended, and state_changed.
Add scripts/try_play.py '<song name>' to search, play for 15 seconds, and print measured RSS RAM. Mock mpv and yt-dlp in unit tests.
~~~

**1c. Queue Manager & Persistence**

~~~text
Implement src/metrolite/audio/queue.py managing current_index, track list, shuffle (Fisher-Yates preserving current track at index 0), repeat modes (off | all | one), add_next, append, remove, move(from_idx, to_idx), clear, and automatic radio refill via ytm.get_watch_radio when queue.autoplay_radio is true and the last song finishes. Persist queue state to SQLite queue_state so reopening the app restores the exact queue. Unit-test shuffle, repeat, move, and radio refill.
~~~

**Done when:** `scripts/try_play.py` plays any song cleanly with gapless next-track preload, and Python + `libmpv` total RAM stays under 65 MB.

---

## Phase 2. Offline Downloads, Liked Songs & Playlists

**Paste to agent**

~~~text
Implement src/metrolite/library/manager.py and src/metrolite/library/downloader.py:
- manager.py: toggle_like(video_id, track_meta), list_liked(search_query=None), create_playlist(name, description=""), rename_playlist, delete_playlist, add_to_playlist(playlist_id, tracks), remove_from_playlist, reorder_playlist, list_playlists(), get_playlist_tracks(playlist_id), and record_play(video_id) for history.
- downloader.py: asyncio background download queue (max_concurrent from config.yaml) using yt-dlp to download audio into downloads_dir as `<video_id>.opus` (or m4a), embed title/artist/album/cover via mutagen when enabled, emit progress events (0-100%), and update tracks.local_path and file_size_bytes in SQLite. Support delete_download(video_id) and list_downloads().
Unit-test library CRUD and download state transitions with mocked yt-dlp.
~~~

**Done when:** Liking songs, creating playlists, and downloading tracks offline work and survive app restarts; downloaded tracks play with Wi-Fi turned off.

---

## Phase 3. Local Bridge (FastAPI/WS) & Windows Media Keys

**Paste to agent**

~~~text
Implement src/metrolite/bridge/server.py and src/metrolite/audio/media_keys.py:
- server.py: FastAPI app bound strictly to 127.0.0.1:8766 (auto-increment port if busy), secured by a random per-run token written to %LOCALAPPDATA%\metrolite\bridge.json.
  - WebSocket `/ws?token=...`: broadcasts throttled playback_tick (every 250ms while playing), track_changed, queue_changed, download_progress, and library_changed.
  - REST endpoints: GET /health, GET /home, GET /search?q=&filter=, GET/POST /player/play, POST /player/pause, POST /player/next, POST /player/prev, POST /player/seek, POST /player/volume, GET/POST /queue, POST /liked/{video_id}, GET /liked, GET/POST /playlists, GET/POST/DELETE /playlists/{id}/tracks, POST /downloads/{video_id}, DELETE /downloads/{video_id}, GET /downloads, GET/PUT /settings.
- media_keys.py: global Windows media key listener (MediaPlayPause, MediaNext, MediaPrev) via pynput (or Windows SMTC if available) wired directly to the player controller.
Add tests/test_bridge.py using FastAPI TestClient and WebSocket test client.
~~~

**Done when:** Bridge tests pass, media keys control playback in the background, and backend RAM remains <70 MB.

---

## Phase 4. UI Visual Reference (Stitch / HTML Prototype)

**Goal:** Validate the exact layout before writing Tauri/React code.

**Prompt to generate `ui/design/player.html` (or build directly as static Tailwind HTML)**

~~~text
Create a single-file dark-mode HTML/Tailwind prototype at ui/design/player.html matching docs/UI_SPEC.md: 1060x680 Windows desktop window, #0B0B0F base, #0E0E13 220px left sidebar (Home, Search, Library, Liked Songs, Downloads, Playlists), 44px custom titlebar with Ctrl+K search bar and Windows caption buttons, main area showing a Playlist/Liked track table with 56px rows, right 300px collapsible Queue drawer, and fixed 76px bottom player bar with cover art, scrubber, volume, and Like/Download icons. No backdrop-blur anywhere; crisp 1px borders and #38BDF8 accent.
~~~

**Done when:** `ui/design/player.html` renders cleanly in Edge with zero blur lag and matches every token in `docs/UI_SPEC.md`.

---

## Phase 5. Tauri 2 + React Smooth Desktop UI

**5a. Tauri 2 + Vite + Virtualized Shell**

~~~text
Scaffold ui/ using Tauri 2 + React + TypeScript + Tailwind via Vite (no CDN; bundle @fontsource/inter and lucide-react; install @tanstack/react-virtual). Create ui/src/tokens.css from docs/UI_SPEC.md. Configure src-tauri/tauri.conf.json with decorations: false, transparent: false, resizable: true, width: 1060, height: 680, minWidth: 840, minHeight: 560. Implement a Tauri command `get_bridge_config` that reads port and token from %LOCALAPPDATA%\metrolite\bridge.json. Connect a typed WebSocket + REST client hook in ui/src/api/client.ts with auto-reconnect.
~~~

**5b. Views, Virtualized Track Lists & Bottom Player**

~~~text
Build the full MetroLite React UI in ui/src/:
- Custom Titlebar (drag region, back/forward, Ctrl+K search input, minimize/maximize/close).
- Sidebar (Home, Search, Liked Songs, Downloads, History, Playlists + create playlist modal).
- Virtualized TrackList component using @tanstack/react-virtual (56px rows, hover play button, Like heart, Download status/progress ring, Add to Queue, Add to Playlist menu).
- Home view (Quick Picks & shelves), Search view (filter tabs + virtualized results), Library views (Liked, Downloads, Playlist detail).
- Collapsible Right Queue Drawer (Up Next list, reorder/remove, Autoplay Radio toggle).
- Fixed Bottom Player Bar (smooth 60fps scrubber using local interpolation between 250ms server ticks, Play/Pause, Next/Prev, Shuffle, Repeat, Volume, Like, Download).
Ensure zero backdrop-filter blur and smooth 60fps scrolling on 1,000+ rows.
~~~

**Done when:** All core flows (Search, Play, Queue, Radio, Like, Playlists, Offline Downloads) work end-to-end in the desktop window.

---

## Phase 6. System Tray, Destroy-on-Close & RAM Tuning

**Paste to agent**

~~~text
Implement true lightweight background execution in ui/src-tauri/src/main.rs and src/metrolite/:
1. Add a Windows System Tray icon (with tooltip showing current track) and tray menu: Open MetroLite, Play/Pause, Next Track, Quit.
2. When the user clicks the window Close button (and ui.close_to_tray is true), destroy the WebView2 window (`window.close()`) without exiting the Tauri process so WebView2 memory drops to 0 MB while music keeps playing. Clicking the tray icon recreates the main window cleanly.
3. Add a Tauri single-instance plugin so launching the app while in tray focuses/recreates the existing window.
4. Bound image/HTTP caches in Python and configure WebView2 browser args (`--disable-features=msWebOOUI,Translate` and low memory profile).
Run scripts/measure_ram.py during active playback and while minimized to tray, and report exact numbers.
~~~

**Done when:**
- Active window + streaming audio measures **<= 185 MB** total RAM.
- Closed to tray + streaming audio measures **<= 60 MB** total RAM.
- Reopening from tray takes < 300 ms and restores current playback position seamlessly.

---

## Phase 7. Packaging (Single Installer)

**Paste to agent**

~~~text
Package the Python backend with PyInstaller (`--onedir` or `--onefile` excluding unused stdlib/test modules to keep size and RAM minimal), bundle it as a Tauri sidecar alongside libmpv-2.dll, add startup cleanup and crash-safe SQLite WAL mode, and build the Windows NSIS installer via `npm run tauri build`. Verify clean install and RAM numbers on Windows 11.
~~~

**Done when:** Installed `.exe` runs standalone without a terminal window, plays music in tray under 60 MB RAM, and stays under 185 MB when open.
