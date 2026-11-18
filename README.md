# metrolite

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
