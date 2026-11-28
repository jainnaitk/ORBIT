---
name: orbit-bug-audit
description: Perform a deep, adversarial, multi-pass two-phase critical bug audit of the ORBIT codebase inside the ORBIT/ folder. Use when the user asks to audit ORBIT, run a bug check on ORBIT/, hunt for subtle or ship-blocking crashes, concurrency/race conditions, cross-layer UI/backend state desyncs, SQLite corruption, libmpv/yt-dlp/LRCLIB failures, Windows file-lock or RAM ceiling leaks, or execute a read-only Phase 1 deep audit followed by approval-gated Phase 2 surgical fixes.
---
# Orbit Bug Audit

Perform a deep, adversarial, multi-pass two-phase critical bug audit of ORBIT inside the `ORBIT/` folder. Hunt aggressively for real, subtle, ship-blocking defects across full execution paths—crashes, race conditions, data corruption, deadlocks, resource leaks, and security flaws—while maintaining zero false positives. Apply fixes only after explicit user approval.

## When to Use

  - The user asks to audit ORBIT or run a deep/critical bug check on the `ORBIT/` folder.
  - The user wants to hunt for crashes, concurrency races, cross-layer state desyncs, SQLite corruption, `libmpv`/`yt-dlp`/LRCLIB failures, deadlocks, RAM ceiling leaks, Windows file/subprocess bugs, or security defects.
  - The user wants a read-only Phase 1 audit report followed by approval-gated Phase 2 surgical fixes inside `ORBIT/`.

## Strict Audit Rules

Follow these five rules to hit as deep as possible without introducing noise or false positives.

### 1. Earned Zero-Findings Pass Rule

  - There is no quota for findings. Returning 0 issues is valid **only after** exhausting full-codebase reading and all four adversarial audit passes below—never after a superficial skim.
  - If no genuine critical bugs survive verification after exhaustive tracing, explicitly report `AUDIT PASSED: 0 critical bugs found.` and stop. Do not invent, inflate, or scrape for issues.
  - Never lower severity standards to fill a report: hit deeper on real runtime bugs, not stylistic nits.

### 2. Strict Inclusion Criteria

Report only issues that meet at least one of these criteria:

  - **Active Crashes / Unhandled Exceptions:** Code paths reachable during real usage (including rapid clicks, reconnects, or bad external payloads) that throw unhandled exceptions, crash the Python backend/desktop shell, or white-screen the React UI.
  - **Data Loss or State Corruption:** Bugs that delete, overwrite, or corrupt SQLite library/queue/settings records, break atomic transactions, corrupt downloaded/cached audio files, or leave backend/UI playback state permanently stuck or desynced.
  - **Broken Core Functionality, Races & Deadlocks:** Essential flows (gapless `libmpv` playback, queue mutations, `yt-dlp`/YouTube Music radio streaming, LRCLIB lyrics sync, WebSocket sync) that fail, hang indefinitely, deadlock, or break under realistic concurrent or rapid user actions.
  - **Severe Resource Leaks / Hard Freezes:** Unbounded memory, file-handle, subprocess, listener, Blob URL, or thread/task leaks that breach ORBIT's 185 MB runtime RAM ceiling or visibly freeze/OOM-crash the app.
  - **Real Security / Destructive System Bugs:** Exploitable command/argument injection in `yt-dlp`/`libmpv` subprocesses, path traversal or unsafe path resolution in download/cache handling, accidental destructive file ops, or exposed secrets.

### 3. ORBIT Known-Safe / Ignore List

Never flag these intentional ORBIT patterns unless you prove an active runtime failure:

  - **Localhost REST/WebSocket Bridge (`ORBIT/src` and `ORBIT/ui`):** Loopback binding (`127.0.0.1` / `localhost`) with validated local origins is intentional for a single-user desktop app. Do not flag missing HTTPS, external auth tokens, or rate-limiting on local endpoints.
  - **Process-Tree Memory Trimming (Under 185 MB RAM Budget):** Intentional working-set trimming, manual GC triggers, and bounded caches are deliberate optimizations. Flag them only if they cause active playback stutters, corruption, or crashes.
  - **Direct Ref / DOM Updates for 60fps UI (`ORBIT/ui`):** Intentional `useRef` DOM updates (`DynamicIsland` scrubbing, `StarfieldBg` pausing, virtualized lists) are deliberate performance patterns. Flag them only if they cause state desyncs, listener/RAF leaks, or rendering crashes.
  - **Single-User SQLite Patterns:** Local SQLite connections and synchronous queries on small tables are intentional. Flag unclosed write transactions, partial non-atomic writes, SQL injection from untrusted metadata, or real cross-thread `database is locked` deadlocks.
  - **Gracefully Handled External Failures:** Caught network timeouts, missing LRCLIB lyrics, or unavailable `yt-dlp`/radio streams that degrade cleanly and keep playback/UI responsive are not bugs. **Do** flag swallowed exceptions (`except Exception: pass`) that silently leave UI/queue state hung or inconsistent.
  - **Non-App Root Files:** Ignore standalone showcase `.html` files, `scratch_*.txt`, `.pytest_cache`, and `.ruff_cache`.

### 4. Strict Exclusion List

Never report any of the following:

  - Dead code, unused imports/variables, or cleanup suggestions.
  - Code style, naming conventions, formatting, refactoring, or architectural opinions.
  - Micro-optimizations or theoretical performance tweaks with no measurable freeze, leak, or crash impact.
  - Hypothetical, contrived, or impossible edge cases (such as manually corrupted internal files or redundant null checks).
  - Missing tests, comments, docstrings, type annotations, or extra logging.
  - Defensive hardening or best-practice advice where nothing actually breaks.

### 5. Prove-It Gate & Sibling Sweep

  - Verify every reported finding directly in the `ORBIT/` source code you actually read with 95%+ confidence.
  - Trace the exact failure chain: **Concrete Trigger -> Exact Code Path (across files/layers if applicable) -> Real User-Visible Failure**. If a guard elsewhere prevents the failure, drop the finding.
  - **Sibling Sweep:** Whenever a real bug is confirmed, inspect every sibling handler, endpoint, component, and callback in `ORBIT/` for the same defect pattern before finalizing the report.

## Deep-Audit Hunting Protocol (Mandatory in Phase 1)

Never stop at single-file surface checks. Execute all four passes across the full `ORBIT/` runtime codebase before forming a verdict:

1.  **Pass 1 — Exhaustive Inventory & Cross-Layer Contract Trace:**
      - Read **all** active source and config files across `ORBIT/src`, `ORBIT/ui/src`, `ORBIT/pyproject.toml`, and `ORBIT/config.yaml` (batching with `read_many_files`). Do not skim or skip files.
      - Trace end-to-end call chains across `React UI <-> WebSocket/REST <-> Python backend <-> libmpv / yt-dlp / LRCLIB / SQLite`.
      - Catch payload/type mismatches, unhandled rejection paths, silent `except` blocks that freeze state machines, and broken WebSocket reconnect/rehydration flows.
2.  **Pass 2 — Adversarial Concurrency, Race & State-Machine Stress Test:**
      - **Rapid user input:** Rapidly spamming next/previous/play/pause, scrubbing while buffering, deleting or reordering the currently playing track, or switching shuffle/repeat/radio mid-transition.
      - **Out-of-order async/thread completions:** Slow `yt-dlp` stream resolution or LRCLIB lyric fetches finishing after the user switched tracks and overwriting the active track's audio, metadata, or lyrics.
      - **`libmpv` thread safety & gapless races:** C-level mpv callbacks/property observers touching Python/asyncio state without thread-safe locking or `call_soon_threadsafe`; `end-file`/`eof-reached`/`append-play` transition races, infinite skip loops on consecutive dead tracks, and expired YouTube stream URLs failing on resume after long pauses or system sleep.
3.  **Pass 3 — Lifecycle, Persistence, Windows & 185 MB RAM Ceiling Hunt:**
      - **Leaks & teardown:** Zombie `yt-dlp`/FFmpeg subprocesses on cancellation or app exit, leaked mpv observers, unclosed sockets/file descriptors, growing unbounded caches/queues, and React `useEffect`/WebSocket/`requestAnimationFrame`/`URL.createObjectURL` leaks on unmount or track changes.
      - **SQLite integrity:** Multi-step mutations without atomic transactions, leaked locks on exceptions, cross-thread connection misuse, and untrusted track metadata reaching queries.
      - **Windows-specific traps:** `PermissionError` when deleting/overwriting cached audio files still open in `libmpv`, illegal Windows path characters (`<>:"/\|?*`), reserved device names (`CON`, `NUL`, etc.), Unicode/long-path failures in downloads/cache, and subprocess argument injection.
4.  **Pass 4 — Devil's Advocate Filter & Sibling Sweep:**
      - Re-verify every candidate finding against callers, locks, guards, and error boundaries. Ruthlessly cut anything that cannot be proven to fail at runtime.
      - For every surviving bug, sweep sibling files for duplicate instances of the same root cause.

## Steps

### Phase 1: Read-Only Analysis

Do not edit or create any files during Phase 1.

1.  Call `list_files` with `subpath="ORBIT"` and `recursive=true`. Strictly stay inside `ORBIT/` and ignore all other folders.
2.  Call `read_many_files` (in as many batches as needed) to read **every** active runtime file inside `ORBIT/src`, `ORBIT/ui/src`, `ORBIT/pyproject.toml`, and `ORBIT/config.yaml`.
3.  Execute all four passes of the Deep-Audit Hunting Protocol.
4.  Output a concise, hard-hitting Audit Report directly in chat containing:
      - **Audit Verdict:** Either `PASS - 0 Critical Bugs Found` (with `AUDIT PASSED: 0 critical bugs found.`) or `ACTION REQUIRED - [N] Critical Bug(s) Found`.
      - **Coverage & Architecture Summary:** 3-5 bullet points covering tech stack, entry points, core data flow, and exact subsystems/layers audited across the 4 passes.
      - **Quick Approval Summary (Feature-Oriented, whenever bugs are found):** Place this right before the detailed findings and list every finding ID (`F1`, `F2`, `F3`, ...) in simple, plain language so the user can scan and approve fixes without reading the full technical paragraphs. Keep each finding strictly to **2-3 lines max**:
          - **Line 1 — What's Broken:** Plain-language description of the bug and what it is currently causing in that app feature.
          - **Line 2 — After the Fix:** What changes in that function or feature and how it will behave once fixed.
      - **Critical Findings Only (Detailed Breakdown):** Assign each verified critical bug a numbered ID (`F1`, `F2`, ...) matching the Quick Approval Summary, with:
          - **Severity:** Blocker or Critical only (never list Low or Medium items).
          - **Location:** Exact `ORBIT/...` file path(s), line/symbol, function name, and any sibling occurrences found.
          - **Failure Trace:** Concrete Trigger -> Exact Cross-Layer/Runtime Code Path -> Real User-Visible Impact.
          - **Root Cause:** Concise, precise technical explanation.
          - **Minimal Surgical Fix:** Exact targeted change needed (no unrelated refactors).
          - **Regression Risk:** Low, Medium, or High.
5.  Stop and wait for user input. Do not call `write_file`, `write_many_files`, or `edit_file` yet.
      - If 0 critical bugs were found, confirm `ORBIT/` is healthy and ask if the user still wants `CONTEXT.md`, `AUDIT_REPORT.md`, and `PLAN.md` saved inside `ORBIT/`.
      - If critical bugs were found, ask which Fix IDs the user approves to apply, and whether they want `CONTEXT.md`, `AUDIT_REPORT.md`, and `PLAN.md` saved inside `ORBIT/`.

### Phase 2: Apply Approved Changes

Run Phase 2 only after the user explicitly replies with approved Fix IDs or documentation requests.

1.  Apply only the approved minimal fixes inside `ORBIT/` using `edit_file`. Do not overwrite entire files, refactor surrounding code, or touch files outside `ORBIT/`.
2.  If the user requested the Markdown docs, create `CONTEXT.md`, `AUDIT_REPORT.md`, and `PLAN.md` inside `ORBIT/` in a single `write_many_files` call.
3.  Read back each modified section to verify there are no syntax errors, broken imports, missed sibling occurrences, or unintended side effects.
4.  Provide a concise summary of exact patches applied and the exact Windows PowerShell command(s) to run and verify ORBIT.

## Gotchas

  - Never declare `AUDIT PASSED` after skimming a subset of files—every active runtime module in `ORBIT/` must be traced through all four passes first.
  - Never stop at the first instance of a bug; always sweep sibling handlers, endpoints, and components for the same flaw.
  - Never jump into Phase 2 without explicit user approval, even when fixes look obvious.
  - Never inspect or modify files outside `ORBIT/` to avoid unintended scope creep.
  - Never confuse "hit harder" with "report noise": intentional ORBIT optimizations (localhost bridge, RAM trimming, `useRef` 60fps updates, single-user SQLite) and stylistic nitpicks remain strictly excluded unless proven to break at runtime.
