# ORBIT follow-up audit — resolution report

**Result: four critical findings reproduced before the fixes; all four passed their regression checks afterward.** This result applies to these findings, not to every possible defect in ORBIT.

| ID | Before the fix | Change and verification |
| --- | --- | --- |
| **F1 — stale transport commands** | Delaying the first buffering broadcast caused the native player to load the second selection and then the first, while the queue still showed the second. A delayed EOF history write could pause a newly loaded song. | [`BridgeController`](src/metrolite/bridge/server.py) checks command ownership after notification and history waits. [`AudioPlayer.load_and_play`](src/metrolite/audio/player.py) checks it after URL resolution, before changing native playback. Pause takes effect before its database wait. Tests cover Play, Next, Previous, pause/toggle, delayed resolution and old EOF callbacks. |
| **F2 — Play Next during radio lookup** | At the last track, adding an explicit successor invalidated the pending recommendations. The bridge interpreted zero recommendations as an empty queue and paused at index 0. | [`QueueManager._advance_after_radio`](src/metrolite/audio/queue.py) examines the live queue after refill and verifies the original entry and transport command. Natural EOF and multi-skip both advanced to the explicit song; a superseding pause left it queued. |
| **F3 — repeated plays merged in Stats** | Two 60-second listens of the same video produced one 120-second qualified event. | [`BridgeController._after_play_started` and `_rotate_play_session`](src/metrolite/bridge/server.py) track the playback occurrence separately from the video ID and the pause/resume command. Two 60-second listens yielded two events; two 20-second listens did not combine to pass the 30-second threshold. |
| **F4 — stale lyrics after refresh** | The refreshed line started at 1 second, but an older response replaced it with a 5-second timestamp in cache and at the offline writer. | [`resolve_lyrics`](src/metrolite/ytm/lyrics.py) coordinates publication per track, including sidecar repair. An already-running file write finishes before the newer write. [`BridgeApiClient.getLyrics`](ui/src/api/client.ts) only caches its current request. Tests read back the 1-second timestamp from memory, `.lrc` and `.lyrics.json`; the frontend check also reopened the refreshed value. |

## Verification

- Python: **172 tests passed**, with one Starlette TestClient deprecation warning. Ruff passed.
- UI: TypeScript and the production Vite build passed. A separate check against the actual frontend client delayed both prefetch and forced-refresh responses and checked rejection cleanup.
- Native smoke check: libmpv repeated a **3-second local WAV** using null audio output, a temporary database, and a **1-second test-only qualification threshold**. SQLite reported **two starts and two qualified events**. The final run observed **120 ms** from issuing Play to the first advancing playhead; this is a single local measurement, not streaming latency.
- Physical speaker output, live YouTube playback, and the 185 MB active / 60 MB tray RAM budgets were not measured in this follow-up.
