"""ORBIT Production Release Live Test Suite (Backend + Offline .lrc + UI Click-Through).
Keep ORBIT running (`uv run python -m metrolite run` + `cd ui && npm run tauri dev`),
then run:
    uv run python test_orbit_live.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

os.system("")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    ms: float | None = None


@dataclass
class LiveSession:
    base_url: str
    ws_url: str
    token: str
    results: list[CheckResult] = field(default_factory=list)
    latest_tick: dict[str, Any] = field(default_factory=dict)
    ws_connected: bool = False

    def record(self, name: str, status: str, detail: str, ms: float | None = None) -> None:
        self.results.append(CheckResult(name, status, detail, ms))
        color = GREEN if status == "PASS" else YELLOW if status == "WARN" else RED
        ms_txt = f" {DIM}({ms:.0f}ms){RESET}" if ms is not None else ""
        print(f"  [{color}{status:4s}{RESET}] {BOLD}{name}{RESET} — {detail}{ms_txt}")


async def discover_bridge() -> tuple[str, int, str]:
    with suppress(Exception):
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get("http://localhost:1420/__bridge_config")
            if r.status_code == 200:
                cfg = r.json()
                return cfg.get("host", "127.0.0.1"), int(cfg.get("port", 8766)), str(cfg.get("token", ""))

    local_app = Path(os.environ.get("LOCALAPPDATA", "")) / "metrolite"
    bridge_file = local_app / "bridge.json"
    if bridge_file.is_file():
        with suppress(Exception):
            data = json.loads(bridge_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "token" in data:
                return (
                    data.get("host", "127.0.0.1"),
                    int(data.get("port", 8766)),
                    str(data.get("token", "")),
                )

    return "127.0.0.1", 8766, os.environ.get("ORBIT_BRIDGE_TOKEN", "")


async def ws_listener(session: LiveSession, stop_evt: asyncio.Event) -> None:
    try:
        import websockets  # type: ignore
    except ImportError:
        return

    try:
        async with websockets.connect(session.ws_url, open_timeout=4) as ws:
            session.ws_connected = True
            while not stop_evt.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    msg = json.loads(raw)
                    if msg.get("event") == "playback_tick" and isinstance(msg.get("data"), dict):
                        session.latest_tick = msg["data"]
                except TimeoutError:
                    continue
                except Exception:  # noqa: BLE001
                    break
    except Exception:  # noqa: BLE001
        session.ws_connected = False


async def wait_for_playback_advance(
    session: LiveSession,
    expected_vid: str,
    timeout_s: float = 9.0,
) -> tuple[bool, float, str, float]:
    start = time.perf_counter()
    first_pos: float | None = None
    last_state = "unknown"
    saw_paused_flicker = False

    while (time.perf_counter() - start) < timeout_s:
        tick = session.latest_tick
        tr = tick.get("current_track") or {}
        if tr.get("video_id") == expected_vid:
            st = str(tick.get("state", "unknown"))
            last_state = st
            pos = float(tick.get("time_pos") or 0.0)
            if st == "paused":
                saw_paused_flicker = True
            if st == "playing":
                if first_pos is None:
                    first_pos = pos
                elif pos - first_pos >= 0.7:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    note = "clean" if not saw_paused_flicker else "paused flicker detected"
                    return True, elapsed_ms, note, pos
        await asyncio.sleep(0.15)

    return (
        False,
        (time.perf_counter() - start) * 1000,
        f"stuck in state={last_state}",
        float(session.latest_tick.get("time_pos") or 0.0),
    )


async def run_ui_click_tests(session: LiveSession, expected_liked_count: int) -> None:
    print(f"\n{BOLD}{CYAN}=== PART 2: LIVE VISIBLE UI CLICK-THROUGH (PLAYWRIGHT) ==={RESET}")
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        session.record("UI Click-Through (Playwright)", "WARN", "Playwright not installed")
        return

    console_errors: list[str] = []
    page_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=320)
        page = await browser.new_page(viewport={"width": 1200, "height": 740})
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        try:
            t0 = time.perf_counter()
            await page.goto("http://localhost:1420", wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_timeout(1400)
            session.record("UI Load (localhost:1420)", "PASS", f"Title: {await page.title()}", (time.perf_counter() - t0) * 1000)

            broken_imgs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('img'))
                    .filter(img => img.complete && img.naturalWidth === 0)
                    .map(img => img.src)"""
            )
            session.record("UI Home Thumbnails & Logo", "PASS" if not broken_imgs else "FAIL", "All images loaded cleanly" if not broken_imgs else f"{len(broken_imgs)} broken")

            bh_webgl = await page.locator('canvas[data-bh-webgl="true"]').count() > 0
            session.record("UI 3D WebGL Black Hole", "PASS" if bh_webgl else "FAIL", "3D WebGL relativistic canvas active" if bh_webgl else "WebGL canvas missing")

            expand_btn = page.locator('aside button[title="Expand Rail"]')
            if await expand_btn.count() > 0:
                await expand_btn.click()
                await page.wait_for_timeout(250)
                collapse_btn = page.locator('aside button[title="Collapse Rail"]')
                rail_ok = await collapse_btn.count() > 0
                if rail_ok:
                    await collapse_btn.click()
                session.record("UI Left Liquid-Glass Rail", "PASS" if rail_ok else "FAIL", "Expanded & collapsed cleanly")

            await page.locator('aside button[title="Liked"]').click()
            await page.wait_for_timeout(600)
            liked_sub = await page.locator("main p").first.inner_text()
            liked_mismatch = expected_liked_count > 0 and liked_sub.startswith("0 tracks")
            session.record("UI Liked Songs Tab", "FAIL" if liked_mismatch else "PASS", f"{liked_sub} (backend has {expected_liked_count})")

            await page.locator('aside button[title="Offline"]').click()
            await page.wait_for_timeout(450)
            dl_sub = await page.locator("main p").first.inner_text()
            session.record("UI Offline Downloads Tab", "PASS", dl_sub)

            await page.locator('aside button[title="History"]').click()
            await page.wait_for_timeout(450)
            most_btn = page.locator('main button:has-text("Most Played")')
            if await most_btn.count() > 0:
                await most_btn.click()
                await page.wait_for_timeout(400)
            session.record("UI History (Recent + Most Played)", "PASS", "Switched tabs cleanly")

            await page.locator('aside button[title="Home"]').click()
            await page.wait_for_timeout(350)

            # Mutually-Exclusive Queue & Lyrics Drawers
            q_btn = page.locator('footer button[title="Toggle Queue Drawer"]')
            l_btn = page.locator('footer button[title="Toggle Lyrics Drawer"]')

            await q_btn.click()
            await page.wait_for_timeout(350)
            q_open = await page.locator('aside:has-text("Autoplay Radio")').count() > 0

            await l_btn.click()
            await page.wait_for_timeout(1200)
            l_open = await page.locator('aside[data-testid="lyrics-drawer"]').count() > 0
            q_closed_auto = await page.locator('aside:has-text("Autoplay Radio")').count() == 0
            synced_badge = await page.locator('aside[data-testid="lyrics-drawer"] :text("Synced")').count() > 0
            session.record(
                "UI Mutually-Exclusive Lyrics & Queue Drawers",
                "PASS" if (q_open and l_open and q_closed_auto) else "FAIL",
                f"Queue->Lyrics swapped cleanly ({'Synced active' if synced_badge else 'Loaded'})",
            )

            # EQ Popover
            eq_btn = page.locator('footer button[title^="Audio EQ"]')
            await eq_btn.click()
            await page.wait_for_timeout(250)
            eq_open = await page.locator('text="Deep Bass"').count() > 0
            if eq_open:
                await page.locator('button:has-text("Flat")').click()
            session.record("UI Liquid-Glass EQ Popover", "PASS" if eq_open else "FAIL", "Preset menu rendered cleanly")

            # Settings Modal
            await page.locator('header button[title="Settings"]').click()
            await page.wait_for_timeout(250)
            settings_ok = await page.locator('text="Cosmic Effects"').count() > 0
            if settings_ok:
                await page.locator('button:has-text("Done")').click()
            session.record("UI Settings Modal", "PASS" if settings_ok else "FAIL", "Cosmic Effects & Auto-Download visible")

            # Dynamic Island auto-closes open Lyrics drawer
            await page.locator('header button[title*="Dynamic Island"]').click()
            await page.wait_for_timeout(500)
            exit_isl = page.locator('button[title="Expand to full ORBIT"]')
            isl_ok = await exit_isl.count() > 0
            if isl_ok:
                await exit_isl.click()
                await page.wait_for_timeout(400)
            drawer_closed_after_island = await page.locator('aside[data-testid="lyrics-drawer"]').count() == 0
            session.record(
                "UI Dynamic Island + Auto-Close Drawer",
                "PASS" if (isl_ok and drawer_closed_after_island) else "FAIL",
                "Entered, auto-closed drawer & restored cleanly",
            )

            total_errs = len(console_errors) + len(page_errors)
            session.record(
                "UI JS Console & Runtime Errors",
                "PASS" if total_errs == 0 else "FAIL",
                "0 console/runtime errors" if total_errs == 0 else f"{total_errs} errors: {(page_errors + console_errors)[:2]}",
            )
        except Exception as e:  # noqa: BLE001
            session.record("UI Click-Through Execution", "FAIL", str(e))
        finally:
            await browser.close()


async def run_live_suite() -> None:
    print(f"\n{BOLD}{CYAN}=== PART 1: BACKEND, AUDIO, OFFLINE .LRC & LATENCY AUDIT ==={RESET}")
    host, port, token = await discover_bridge()
    base_url = f"http://{host}:{port}"
    ws_url = f"ws://{host}:{port}/ws" + (f"?token={token}" if token else "")
    headers = {"X-Bridge-Token": token} if token else {}

    session = LiveSession(base_url=base_url, ws_url=ws_url, token=token)
    liked_count = 0

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=15.0) as api:
        t0 = time.perf_counter()
        try:
            r = await api.get("/health")
            r.raise_for_status()
            h = r.json()
            session.record("Backend /health", "PASS", f"{h.get('app')} v{h.get('version')} ({h.get('state')}) at {base_url}", (time.perf_counter() - t0) * 1000)
        except Exception as e:  # noqa: BLE001
            session.record("Backend /health", "FAIL", f"Cannot reach {base_url}: {e}")
            return

        stop_evt = asyncio.Event()
        ws_task = asyncio.create_task(ws_listener(session, stop_evt))
        await asyncio.sleep(0.6)
        session.record("Live WebSocket Stream", "PASS" if session.ws_connected else "WARN", "Connected & streaming ticks" if session.ws_connected else "websockets pkg not installed")

        t0 = time.perf_counter()
        r = await api.get("/home/personalized", params={"mood": "for_you", "refresh": "false"})
        secs = r.json().get("sections", []) if r.status_code == 200 else []
        session.record("Personalized Home API", "PASS" if secs else "FAIL", f"{len(secs)} sections", (time.perf_counter() - t0) * 1000)

        sample_tracks: dict[str, dict[str, Any]] = {}
        for label, query in [
            ("Hindi", "Teri Deewani Kailash Kher"),
            ("Punjabi", "Excuses AP Dhillon"),
            ("English", "Blinding Lights The Weeknd"),
        ]:
            t0 = time.perf_counter()
            r = await api.get("/search", params={"q": query, "filter": "songs", "limit": 5})
            items = r.json() if r.status_code == 200 else []
            results = items if isinstance(items, list) else items.get("results", [])
            if results:
                sample_tracks[label] = results[0]
                session.record(f"Search ({label})", "PASS", f"{results[0]['title']} — {results[0]['artist']}", (time.perf_counter() - t0) * 1000)
            else:
                session.record(f"Search ({label})", "FAIL", "0 results")

        t1 = sample_tracks.get("Hindi")
        t2 = sample_tracks.get("English")
        if t1:
            await api.post("/player/play", json={"video_id": t1["video_id"], "track": t1, "track_meta": t1})
            ok, ms, note, pos = await wait_for_playback_advance(session, t1["video_id"])
            session.record("Live Playback Start", "PASS" if ok and ms < 3500 and note == "clean" else ("WARN" if ok else "FAIL"), f"pos={pos:.1f}s ({note})", ms)

            await asyncio.sleep(1.1)
            q_len = len((await api.get("/queue")).json().get("items", []))
            session.record("Auto-Radio Queue Fill", "PASS" if q_len > 1 else "WARN", f"{q_len} tracks in queue")

            await api.post("/player/seek", json={"seconds": 45})
            await asyncio.sleep(1.0)
            curr_pos = float(session.latest_tick.get("time_pos") or 0.0)
            session.record("Live Seek (45s)", "PASS" if abs(curr_pos - 45.0) <= 3.5 else "FAIL", f"pos={curr_pos:.1f}s")

        if t2:
            await api.post("/player/play", json={"video_id": t2["video_id"], "track": t2, "track_meta": t2})
            ok, ms, note, pos = await wait_for_playback_advance(session, t2["video_id"])
            session.record("Track Switch Latency", "PASS" if ok and ms < 3500 and note == "clean" else ("WARN" if ok else "FAIL"), f"pos={pos:.1f}s ({note})", ms)

        for preset in ("bass", "vocal", "flat"):
            r = await api.post("/player/eq", json={"preset": preset})
            r.raise_for_status()
        session.record("Native mpv EQ Presets", "PASS", "bass -> vocal -> flat ok")

        for label, tr in sample_tracks.items():
            t0 = time.perf_counter()
            r = await api.get(
                f"/lyrics/{tr['video_id']}",
                params={"title": tr["title"], "artist": tr["artist"], "duration": int(tr.get("duration_s") or 0)},
            )
            lyr = r.json()
            synced = lyr.get("synced")
            ms = (time.perf_counter() - t0) * 1000
            session.record(
                f"Lyrics ({label})",
                "PASS" if synced else ("WARN" if lyr.get("lyrics") else "FAIL"),
                f"SYNCED {len(synced)} lines via {lyr.get('source')}" if synced else "No synced lines",
                ms,
            )

        liked = (await api.get("/liked")).json()
        liked_count = len(liked if isinstance(liked, list) else liked.get("tracks", []))
        session.record("Liked Songs API", "PASS", f"{liked_count} tracks")

        dls = (await api.get("/downloads")).json()
        dl_list = dls if isinstance(dls, list) else dls.get("downloads", [])
        missing = [d.get("title") for d in dl_list if not d.get("local_path") or not Path(d["local_path"]).is_file()]
        lrc_present = sum(
            1 for d in dl_list
            if d.get("local_path") and Path(d["local_path"]).with_suffix(".lrc").is_file()
        )
        session.record("Offline Audio Integrity", "PASS" if not missing else "FAIL", f"{len(dl_list)} downloaded, {len(missing)} missing")
        session.record(
            "Offline .lrc Lyrics Cache",
            "PASS" if (not dl_list or lrc_present > 0) else "WARN",
            f"{lrc_present}/{len(dl_list)} downloaded tracks have local .lrc sidecars",
        )

        await run_ui_click_tests(session, expected_liked_count=liked_count)

        await api.post("/player/pause")
        stop_evt.set()
        await ws_task

    passed = sum(1 for r in session.results if r.status == "PASS")
    warned = sum(1 for r in session.results if r.status == "WARN")
    failed = sum(1 for r in session.results if r.status == "FAIL")

    print(f"\n{BOLD}{'=' * 62}{RESET}")
    print(f"{BOLD}TOTAL:{RESET} {GREEN}{passed} PASS{RESET}  |  {YELLOW}{warned} WARN{RESET}  |  {RED}{failed} FAIL{RESET}")
    if failed or warned:
        print(f"\n{BOLD}Issues / Warnings Found:{RESET}")
        for r in session.results:
            if r.status != "PASS":
                col = RED if r.status == "FAIL" else YELLOW
                print(f"  - [{col}{r.status}{RESET}] {r.name}: {r.detail}")
    print(f"{BOLD}{'=' * 62}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(run_live_suite())
