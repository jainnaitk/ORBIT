"""setup_repo.py - creates every file of the metrolite repo in the current folder.

Usage (PowerShell, inside an empty project folder):
    python setup_repo.py            # create files, skip ones that already exist
    python setup_repo.py --force    # overwrite existing files
Needs only Python 3.8+. Safe to run twice.
"""
import sys
from pathlib import Path

FILES = {
    'AGENTS.md': r'''# AGENTS.md
Project: metrolite. Lightweight, ultra-smooth Windows desktop YouTube Music client (Metrolist-inspired).
Single user, local-first, no login required (optional browser cookies for personal library sync).

## Hard constraints
- Target hardware: Windows 11, Intel i3 U-series, 8GB RAM, integrated graphics only.
- Strict RAM budget:
  - Active window open + streaming: <= 185 MB total (Python + libmpv + Tauri + WebView2).
  - Minimized/closed to system tray (background playback): <= 60 MB total.
- Core backend: Python 3.11, uv, asyncio, FastAPI + WebSocket bound strictly to 127.0.0.1.
- Music & audio engine: ytmusicapi (search, browse, radio/up-next), yt-dlp (stream URL extraction & offline downloads), python-mpv / libmpv (gapless native audio playback). No ffmpeg process spawning during playback.
- UI: Tauri 2 + React + TypeScript + Tailwind built with Vite. No CDN. Fonts bundled (@fontsource/inter).
- Every track list (Search, Liked, Playlist, Downloads, Queue) MUST use `@tanstack/react-virtual`. Never render >50 DOM rows at once.
- Close-to-tray MUST destroy the WebView2 window (`destroy_webview_on_tray: true`) while keeping the Rust tray icon and Python audio daemon alive, freeing ~100 MB RAM in background.
- Settings, paths, limits and hotkeys live in config.yaml. Never hardcode them.

## Design rules
- Follow docs/UI_SPEC.md. Tokens live in ui/src/tokens.css.
- Windows wording and native feel: custom frameless titlebar with Windows caption controls, native media keys (SMTC), system tray menu.
- Smooth 60fps UI rules: animate ONLY `transform` and `opacity`. Never use `backdrop-filter: blur()` on scrolling lists or large surfaces (kills iGPU frame rates and spikes WebView2 VRAM).

## Privacy & safety rules
- Never read, print, log or echo cookies, auth headers, or local bridge tokens.
- Never open `.env*`, `*cookies*.txt`, `*.pem` or `*.key` files.
- Local WebSocket and REST bind to `127.0.0.1` only and require the per-run token from `%LOCALAPPDATA%\metrolite\bridge.json`.
- Downloaded audio and SQLite database live under `%LOCALAPPDATA%\metrolite\`.

## Working rules
- One phase at a time. Stop when the Done-when checks pass and show the output.
- Before using any third-party library (`ytmusicapi`, `yt-dlp`, `python-mpv`, `Tauri 2`), inspect its current API/signatures. Do not guess.
- Mock network, yt-dlp, and libmpv calls in unit tests. Small commits. Run `ruff` and `pytest` before saying done.
- Report measured numbers (active RAM MB, tray RAM MB, track-start latency ms), not guesses.
- Prompts live in docs/PHASES.md. When asked "Do Phase X step Y", follow that prompt and its Done-when checks exactly.

## Progress (update after every phase)
- [ ] P0 scaffold       - [ ] P1 audio & YTM core   - [ ] P2 downloads & library
- [ ] P3 bridge & SMTC  - [ ] P4 UI design          - [ ] P5 Tauri player UI
- [ ] P6 tray & RAM opt - [ ] P7 ship

## Handoff (when another agent takes over)
Read this file, inspect the files of the first unchecked phase, finish what is missing,
run its Done-when checks (including RAM measurement), then update the checklist above.
''',
    'CLAUDE.md': r'''@AGENTS.md
''',
    'README.md': r'''# metrolite

Lightweight, 60fps Windows desktop YouTube Music player under 185 MB RAM (~50 MB in tray).
Supports streaming, gapless queue, radio autoplay, liked songs, custom playlists, and offline downloads.

## Prerequisites
1. Python 3.11 or 3.12 + `uv` (`winget install astral-sh.uv`)
2. Rust + Node.js 20+ (for Tauri 2 UI in Phase 5)
3. `libmpv-2.dll`: place in `bin/libmpv-2.dll` (download from shinchiro/mpv-winbuild-cmake or `scoop install mpv` and add to PATH).

## Start here
1. Read `AGENTS.md` and `docs/PHASES.md`.
2. Check your environment: `uv run python scripts/check_env.py`.
3. Build step by step: tell your agent `"Do Phase 0 from docs/PHASES.md"`.
''',
    '.gitignore': r'''# secrets & cookies
.env
.env.*
!.env.example
*cookies*.txt
headers_auth.json
*.pem
*.key

# python
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
dist/
build/
*.egg-info/

# binaries, audio, local data
bin/*.dll
bin/*.exe
*.opus
*.m4a
*.mp3
*.webm
*.db
*.sqlite

# ui
node_modules/
ui/dist/
ui/src-tauri/target/

# os
Thumbs.db
''',
    '.env.example': r'''# .env.example (placeholders only; optional — MetroLite works without signing in)
YTM_COOKIES_FILE=
''',
    'pyproject.toml': r'''[project]
name = "metrolite"
version = "0.1.0"
description = "Lightweight Windows desktop YouTube Music player under 185 MB RAM"
requires-python = ">=3.11,<3.13"
dependencies = ["pyyaml", "pydantic-settings", "httpx", "psutil"]

[project.scripts]
metrolite = "metrolite.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/metrolite"]

# Install groups phase by phase:
#   Phase 1: uv sync --group dev --group p1
#   Phase 2: uv sync --group dev --group p1 --group p2
#   Phase 3: uv sync --group dev --group p1 --group p2 --group p3
[dependency-groups]
dev = ["pytest", "pytest-asyncio", "ruff"]
p1 = ["ytmusicapi", "yt-dlp", "python-mpv"]
p2 = ["mutagen", "aiosqlite"]
p3 = ["fastapi", "uvicorn", "websockets", "pynput"]

[tool.uv]
default-groups = ["dev"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
''',
    'config.yaml': r'''app:
  name: MetroLite
  data_dir: "%LOCALAPPDATA%\\metrolite"
  downloads_dir: "%LOCALAPPDATA%\\metrolite\\downloads"
  ram_budget_active_mb: 185
  ram_budget_tray_mb: 60

audio:
  quality: high                    # high (128-160kbps opus) | medium (64-96kbps) | low (48kbps)
  format_selector: "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio"
  gapless: true
  normalize_volume: false          # true adds mpv dynaudnorm filter
  default_volume: 80               # 0 - 100
  mpv_demuxer_max_mb: 15           # keeps libmpv RAM under ~22 MB
  mpv_demuxer_back_mb: 5
  stream_url_ttl_s: 18000          # refresh expiring googlevideo URLs before 5h

downloads:
  preferred_ext: opus              # opus | m4a
  embed_metadata: true
  embed_thumbnail: true
  max_concurrent: 2

queue:
  autoplay_radio: true             # auto-fetch YTM watch playlist when queue ends
  history_limit: 500

hotkeys:
  enabled: true
  play_pause: media_play_pause
  next_track: media_next
  prev_track: media_previous

bridge:
  host: "127.0.0.1"
  port: 8766

ui:
  theme: dark                      # dark | oled
  close_to_tray: true
  destroy_webview_on_tray: true    # frees ~100 MB RAM when minimized/closed to tray
  thumbnail_cache_mb: 40
''',
    'scripts/check_env.py': r'''"""Quick health check for MetroLite. Prints OK/FAIL lines and baseline RAM."""
import ctypes
import os
import sys
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def ok(label: str, good: bool, extra: str = "") -> bool:
    print(("[ OK ] " if good else "[FAIL] ") + label + ((" - " + extra) if extra else ""))
    return good


def find_libmpv() -> str | None:
    local_dll = ROOT / "bin" / "libmpv-2.dll"
    if local_dll.exists():
        return str(local_dll)
    for name in ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll"):
        try:
            ctypes.CDLL(name)
            return name
        except OSError:
            continue
    return None


def main() -> None:
    ok("Python 3.11 or 3.12", (3, 11) <= sys.version_info[:2] < (3, 13), sys.version.split()[0])

    mpv_path = find_libmpv()
    ok(
        "libmpv shared library",
        bool(mpv_path),
        mpv_path or "place libmpv-2.dll in bin/ or add mpv to PATH",
    )

    for pkg in ("ytmusicapi", "yt_dlp", "mpv", "mutagen", "fastapi"):
        try:
            __import__(pkg)
            ok(f"Package {pkg}", True)
        except ImportError:
            ok(f"Package {pkg}", False, "install via uv sync --group p1 --group p2 --group p3")

    local_app = Path(os.environ.get("LOCALAPPDATA", ROOT / ".localdata")) / "metrolite"
    try:
        local_app.mkdir(parents=True, exist_ok=True)
        probe = local_app / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok("AppData writable", True, str(local_app))
    except Exception as exc:  # noqa: BLE001
        ok("AppData writable", False, str(exc))

    rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
    ok("Baseline process RAM", rss_mb < 60, f"{rss_mb:.1f} MB")


if __name__ == "__main__":
    main()
''',
    'scripts/measure_ram.py': r'''"""Measure live RAM (Working Set / RSS) of metrolite Python + Tauri/WebView2 processes."""
import psutil


def main() -> None:
    total_mb = 0.0
    found = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmd = " ".join(proc.info["cmdline"] or []).lower()
            is_backend = "metrolite" in cmd
            is_ui = "metrolite" in name or ("msedgewebview2" in name and "metrolite" in cmd)
            if is_backend or is_ui:
                mb = proc.info["memory_info"].rss / (1024 * 1024)
                total_mb += mb
                found += 1
                print(f"PID {proc.info['pid']:6d} | {proc.info['name']:22s} | {mb:6.1f} MB")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    print("-" * 52)
    print(f"Matched processes: {found} | Total RAM: {total_mb:.1f} MB")
    if total_mb > 200:
        print("WARNING: Over 200 MB target!")


if __name__ == "__main__":
    main()
''',
    'docs/UI_SPEC.md': r'''# UI/UX Specification — MetroLite Desktop

## 1. Performance & Smoothness Rules (Non-Negotiable)
- **Zero-jank scrolling**: Every song table and playlist view uses `@tanstack/react-virtual` (fixed row height `56px`, overscan `6`). Scrolling 5,000 tracks must stay locked at 60fps.
- **No expensive GPU effects**: Do NOT use `backdrop-filter: blur()` on panels, modals, or the bottom player bar. Use solid/opaque layered surfaces (`#0B0B0F`, `#121218`, `#181820`) with crisp `1px` borders (`rgba(255,255,255,0.08)`).
- **CSS animations only**: Animate `transform` and `opacity` only (`150ms cubic-bezier(0.2, 0, 0, 1)`).
- **Optimistic UI**: Clicking Like, Add to Queue, or Play updates React state immediately while the background WebSocket confirms.
- **Image discipline**: Request `w120-h120` thumbnails for list rows and `w300-h300` for the active player cover—never load 1200px artwork into small tiles. Use `loading="lazy"` and `decoding="async"`.

## 2. Design Tokens (`ui/src/tokens.css`)
| Token | Dark Value | OLED Value | Usage |
| :--- | :--- | :--- | :--- |
| `--bg-base` | `#0B0B0F` | `#000000` | Main window canvas |
| `--bg-sidebar` | `#0E0E13` | `#050507` | Left navigation rail (`220px`) |
| `--bg-surface` | `#14141B` | `#0D0D11` | Cards, playlist headers, modals |
| `--bg-hover` | `#1C1C26` | `#16161D` | Row hover & active pill |
| `--bg-player` | `#101016` | `#08080B` | Persistent bottom bar (`76px`) |
| `--border` | `rgba(255,255,255,0.08)` | `rgba(255,255,255,0.10)` | Hairline dividers |
| `--text-1` | `#F8FAFC` | `#FFFFFF` | Song titles, headings |
| `--text-2` | `#94A3B8` | `#94A3B8` | Artists, albums, durations |
| `--accent` | `#38BDF8` | `#38BDF8` | Active play state, scrubber fill, focus ring |
| `--liked` | `#FB7185` | `#FB7185` | Heart / Liked icon fill |
| `--downloaded` | `#34D399` | `#34D399` | Offline badge |
| `--font-sans` | `Inter, system-ui, sans-serif` | — | UI typography |
| `--font-mono` | `JetBrains Mono, Consolas, monospace` | — | Timestamps (`02:45 / 04:12`) |

## 3. Window Layout (`1060x680` default, min `840x560`)
1. **Top Bar (`44px`, draggable titlebar)**:
   - Back / Forward navigation buttons, centered global Search input (`Ctrl+K` shortcut, instant debounce `250ms`), Settings gear, and custom Windows Minimize / Maximize / Close-to-Tray buttons.
2. **Left Sidebar (`220px`)**:
   - Primary nav: `Home` (Quick Picks & Moods), `Search`, `Library`.
   - Library shortcuts: `Liked Songs`, `Downloads` (offline-ready filter), `History`.
   - Scrollable `Playlists` list + `+ New Playlist` button.
3. **Main Content Area (fluid)**:
   - **Home**: Compact horizontal shelves (`Quick Picks`, `Trending`, `Recently Played`) with 2-click play.
   - **Search**: Filter chips (`All`, `Songs`, `Albums`, `Playlists`, `Artists`) + virtualized track rows with Play, Queue Next, Add to Playlist, Like, and Download actions.
   - **Playlist / Liked / Downloads View**: Compact header (cover mosaic, title, track count, total duration, `Play All`, `Shuffle`, `Download All` buttons) + virtualized track list.
4. **Right Queue Drawer (`300px`, collapsible)**:
   - `Now Playing` card, `Up Next` drag-reorderable (or move up/down) queue list, `Clear Queue` button, and `Autoplay Radio` toggle.
5. **Bottom Player Bar (`76px`, fixed)**:
   - **Left**: `48x48` cover art, scrolling/truncated Title + Artist, Like heart button, Offline indicator.
   - **Center**: Shuffle, Previous, Play/Pause (`40px` solid circle), Next, Repeat (`off | all | one`) + smooth `60fps` scrubber bar with elapsed/remaining monospace timestamps.
   - **Right**: Queue drawer toggle (with badge count), Download track button, Mute + Volume slider (`0–100`).

## 4. Keyboard Shortcuts
- `Space`: Play / Pause (when not typing in an input)
- `Ctrl+K` or `/`: Focus Search
- `Ctrl+Right` / `Ctrl+Left`: Next / Previous track
- `Ctrl+L`: Like / Unlike current track
- `Esc`: Close modal or Queue drawer
''',
    'docs/PHASES.md': r'''# Phase Prompts — MetroLite (`docs/PHASES.md`)

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
''',
    'src/metrolite/__init__.py': r'''"""metrolite: lightweight Windows desktop YouTube Music client."""
''',
    'src/metrolite/audio/__init__.py': r'''''',
    'src/metrolite/ytm/__init__.py': r'''''',
    'src/metrolite/library/__init__.py': r'''''',
    'src/metrolite/bridge/__init__.py': r'''''',
    'src/metrolite/store/__init__.py': r'''''',
}

DIRS = ['bin', 'ui/design', 'schemas', 'tests', 'tests/fixtures']


def main() -> None:
    force = "--force" in sys.argv
    root = Path.cwd()
    created = skipped = 0
    for rel, content in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not force:
            print("skip    " + rel + " (already exists)")
            skipped += 1
            continue
        path.write_text(content, encoding="utf-8", newline="\n")
        print("created " + rel)
        created += 1
    for d in DIRS:
        folder = root / d
        folder.mkdir(parents=True, exist_ok=True)
        if not any(folder.iterdir()):
            (folder / ".gitkeep").write_text("", encoding="utf-8")
    print()
    print(f"Done: {created} created, {skipped} skipped in {root}")
    print(
        "Next: git init; git add -A; git commit -m 'chore: bootstrap'; "
        "uv sync; uv run python scripts/check_env.py"
    )


if __name__ == "__main__":
    main()