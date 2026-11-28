# ORBIT follow-up audit context

This handoff covers the **four findings from the follow-up audit after the History and Search changes**. Its F1–F4 identifiers refer to that audit, not the earlier 15-finding report. All four follow-up fixes were approved and applied to the working tree.

| Area | Runtime path | Behavior to preserve |
| --- | --- | --- |
| Playback controls | React → FastAPI `BridgeController` → `AudioPlayer` → libmpv | The latest selection or pause wins, even while a previous command is notifying the UI, resolving a URL, or flushing history. Native EOF belongs to the audio that produced it. |
| Queue and radio | `QueueManager.next_track`, `step_tracks`, `refill_radio` | An obsolete radio response cannot replace user edits. If the current queue entry is still the same, an explicit upcoming track remains playable. |
| Listening stats | Bridge play sessions → SQLite `play_events` → History / Stats | Each start of a track qualifies independently. Pause and resume continue the same listen; Repeat One starts a new listen. |
| Lyrics | Drawer and fullscreen → `BridgeApiClient.getLyrics` → `resolve_lyrics` → memory, `.lyrics.json` and `.lrc` | Prefetch, manual refresh, offline reads, and download-time cache repair must agree on the latest published lyrics for a song. |

The implementation and before/after evidence are in [AUDIT_REPORT.md](AUDIT_REPORT.md). Windows verification and launch commands are in [PLAN.md](PLAN.md). Tests use isolated data and mocked network providers; the separate native playback check used a local WAV and null audio output.
