# AGENTS.md
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
- [x] P0 scaffold       - [x] P1 audio & YTM core   - [x] P2 downloads & library
- [x] P3 bridge & SMTC  - [ ] P4 UI design          - [ ] P5 Tauri player UI
- [ ] P6 tray & RAM opt - [ ] P7 ship

## Handoff (when another agent takes over)
Read this file, inspect the files of the first unchecked phase, finish what is missing,
run its Done-when checks (including RAM measurement), then update the checklist above.
