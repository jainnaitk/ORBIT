"""Script to test search, gapless audio playback with libmpv, and measure RAM."""

from __future__ import annotations

import asyncio
import os
import sys

import psutil

from metrolite.audio.player import AudioPlayer, trim_memory
from metrolite.audio.resolver import StreamResolver
from metrolite.ytm.client import YTMusicClient


async def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "Never Gonna Give You Up"
    print(f"[*] Searching YouTube Music for: {query}")
    ytm = YTMusicClient()
    tracks = await ytm.search(query, filter_type="songs", limit=5)

    if not tracks:
        print(f"[!] No tracks found for '{query}'")
        return 1

    current = tracks[0]
    print(f"[+] Now Playing: '{current.title}' by '{current.artist}' ({current.video_id})")

    resolver = StreamResolver()
    player = AudioPlayer(resolver=resolver)

    try:
        print("[*] Resolving stream URL and starting playback...")
        await player.load_and_play(current)

        if len(tracks) > 1:
            next_track = tracks[1]
            print(
                f"[+] Preloading next track (gapless): '{next_track.title}' by '{next_track.artist}'"
            )
            await player.preload_next(next_track)

        print("[*] Playing for 15 seconds...")
        for i in range(1, 16):
            await asyncio.sleep(1)
            pos_str = f"{player.time_pos:.1f}s"
            dur_str = f"{player.duration:.1f}s" if player.duration else "loading..."
            print(
                f"\r    [{i:02d}/15s] Position: {pos_str} / {dur_str} | State: {player.state}",
                end="",
                flush=True,
            )

        print("\n[*] Measuring live memory usage...")
        trim_memory()
        await asyncio.sleep(0.5)
        proc = psutil.Process(os.getpid())
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        print("-" * 52)
        print(f"Process PID: {proc.pid:6d} | Memory RSS: {rss_mb:6.2f} MB")
        print(
            f"Target RAM budget: <= 65.0 MB | Result: {'PASSED' if rss_mb <= 65.0 else 'OVER BUDGET'}"
        )
        print("-" * 52)

        return 0 if rss_mb <= 65.0 else 2
    finally:
        player.stop()
        player.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
