# UI/UX Specification — MetroLite Desktop

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
