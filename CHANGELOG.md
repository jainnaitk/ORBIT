# ORBIT Changelog

## v1.4.0
- Import playlists and albums from YouTube, YouTube Music, Spotify, and Apple Music.
- Browse listening stats in History, clear listening activity, and revisit searches and queued songs.
- Keep the full playlist in Play All and Shuffle, and honor Play Next during radio lookups.
- Count repeat listens separately, preserve refreshed lyrics offline, and verify installer checksums.

## v1.3.0
- ORBIT v1.3.0 Phase C, full multi-shelf homepage & artist library
- Performance, stability, and updater improvements

## v1.2.4
- Instant track switching with hover & queue neighbor stream prefetching
- Instant synced lyrics loading with prefetching, LRU caching, and zero-flash refresh
- Optimistic switch lock to prevent stale WebSocket flicker on rapid skips
- Enabled ORBIT track right-click menus across all Home screen cards

## v1.2.3
- Seamless in-place app updates without uninstall prompts
- Auto-relaunch ORBIT after installer completes upgrade
- Disabled default browser right-click context menu
- Preserved custom track menus and text input copy/paste

## v1.2.2
- Individually clickable collaborating and featured artist links
- Mini multi-artist picker in track context menus
- Preserved per-artist YouTube Music IDs and comma artist names
- Added subtle static pinpoint stars in Minimal OLED mode

## v1.2.1
- Added Cloudflare release mirror fallback for in-app updates
- Added 4-pill changelog summary in update modal and popover
- Fixed update timeout errors and failed-check status badge
- Hardened installer SHA-256 verification and sidecar shutdown

## v1.2.0
- Word & line-synced lyrics with romanization and stage view
- Custom tray album popup with live playback controls
- Liquid-glass track loader and faster artist loading
- Gapless queue and sidecar playback stability fixes

## v1.1.1
- Fixed Chrome `.exe` download integrity via same-origin proxy
- Fixed in-app updater Job Object teardown before NSIS launch
- Added clean shutdown handoff during desktop upgrade

## v1.1.0
- Added Artist Library, Album View, and smart radio queue
- Added movable pinned Dynamic Island and gesture support
- Polished liquid-glass UI and playback transitions

## v1.0.0
- Initial public Windows desktop release of ORBIT
- Gapless `libmpv` audio playback under 185 MB RAM
- Close-to-tray WebView2 memory release under 60 MB RAM
- Offline downloads with embedded artwork and metadata
