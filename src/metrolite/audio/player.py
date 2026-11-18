"""Gapless audio player wrapping python-mpv for MetroLite."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from metrolite.audio.resolver import StreamResolver
from metrolite.config import get_config
from metrolite.ytm.client import Track

logger = logging.getLogger(__name__)


def trim_memory() -> None:
    """Run garbage collection and trim the process working set on Windows."""
    import gc

    gc.collect()
    if sys.platform == "win32":
        with suppress(Exception):
            import ctypes

            k32 = ctypes.windll.kernel32
            k32.SetProcessWorkingSetSize.argtypes = [
                ctypes.c_void_p,
                ctypes.c_size_t,
                ctypes.c_size_t,
            ]
            k32.SetProcessWorkingSetSize.restype = ctypes.c_int
            k32.SetProcessWorkingSetSize(
                k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1)
            )


def ensure_mpv_dll() -> None:
    """Ensure libmpv-2.dll directory is in PATH and DLL search directory on Windows."""
    import locale

    try:
        locale.setlocale(locale.LC_NUMERIC, "C")
    except locale.Error:
        pass

    if sys.platform == "win32":
        repo_bin = Path(__file__).resolve().parent.parent.parent.parent / "bin"
        cwd_bin = Path.cwd() / "bin"
        for candidate in (repo_bin, cwd_bin):
            if (candidate / "libmpv-2.dll").is_file():
                bin_str = str(candidate.resolve())
                if bin_str not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = bin_str + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(bin_str)
                    except OSError:
                        pass
                break


ensure_mpv_dll()
try:
    import mpv
except (ImportError, OSError) as _err:  # pragma: no cover
    mpv = None  # type: ignore[assignment]
    _mpv_load_error = _err
else:
    _mpv_load_error = None


class AudioPlayer:
    """Gapless audio player wrapping python-mpv."""

    def __init__(
        self,
        resolver: StreamResolver | None = None,
        mpv_instance: Any | None = None,
        on_time_pos: Callable[[float], None] | None = None,
        on_duration: Callable[[float], None] | None = None,
        on_track_ended: Callable[[], None] | None = None,
        on_state_changed: Callable[[str], None] | None = None,
    ) -> None:
        cfg = get_config()
        self.resolver = resolver or StreamResolver()
        self._on_time_pos = on_time_pos
        self._on_duration = on_duration
        self._on_track_ended = on_track_ended
        self._on_state_changed = on_state_changed

        self.current_track: Track | None = None
        self.next_track: Track | None = None
        self.time_pos: float = 0.0
        self.duration: float = 0.0
        self.volume: int = cfg.audio.default_volume
        self.state: str = "stopped"  # "stopped" | "playing" | "paused" | "buffering"
        self._terminated: bool = False

        if mpv_instance is not None:
            self._mpv = mpv_instance
        else:
            if mpv is None:
                raise RuntimeError(f"Failed to load python-mpv or libmpv-2.dll: {_mpv_load_error}")
            import locale

            try:
                locale.setlocale(locale.LC_NUMERIC, "C")
            except locale.Error:
                pass
            demuxer_max_bytes = cfg.audio.mpv_demuxer_max_mb * 1024 * 1024
            demuxer_max_back_bytes = cfg.audio.mpv_demuxer_back_mb * 1024 * 1024
            mpv_kwargs: dict[str, Any] = {
                "video": False,
                "ytdl": False,
                "gapless_audio": "yes",
                "demuxer_max_bytes": demuxer_max_bytes,
                "demuxer_max_back_bytes": demuxer_max_back_bytes,
                "volume": float(self.volume),
            }
            if getattr(cfg.audio, "normalize_volume", False):
                mpv_kwargs["af"] = "dynaudnorm"
            self._mpv = mpv.MPV(**mpv_kwargs)

        self._setup_mpv_observers()

    def set_callbacks(
        self,
        on_time_pos: Callable[[float], None] | None = None,
        on_duration: Callable[[float], None] | None = None,
        on_track_ended: Callable[[], None] | None = None,
        on_state_changed: Callable[[str], None] | None = None,
    ) -> None:
        """Register or update player event callbacks."""
        if on_time_pos is not None:
            self._on_time_pos = on_time_pos
        if on_duration is not None:
            self._on_duration = on_duration
        if on_track_ended is not None:
            self._on_track_ended = on_track_ended
        if on_state_changed is not None:
            self._on_state_changed = on_state_changed

    def _setup_mpv_observers(self) -> None:
        """Register property observers and event listeners on the mpv instance."""
        try:

            @self._mpv.property_observer("time-pos")
            def _on_time_pos_change(_name: str, value: Any) -> None:
                if value is not None:
                    self.time_pos = float(value)
                    if self._on_time_pos:
                        try:
                            self._on_time_pos(self.time_pos)
                        except Exception as e:  # noqa: BLE001
                            logger.error("Error in on_time_pos callback: %s", e)

            @self._mpv.property_observer("duration")
            def _on_duration_change(_name: str, value: Any) -> None:
                if value is not None:
                    self.duration = float(value)
                    if self._on_duration:
                        try:
                            self._on_duration(self.duration)
                        except Exception as e:  # noqa: BLE001
                            logger.error("Error in on_duration callback: %s", e)

            @self._mpv.property_observer("pause")
            def _on_pause_change(_name: str, _value: Any) -> None:
                self._update_state()

            @self._mpv.property_observer("core-idle")
            def _on_core_idle_change(_name: str, _value: Any) -> None:
                self._update_state()

            @self._mpv.property_observer("idle-active")
            def _on_idle_active_change(_name: str, _value: Any) -> None:
                self._update_state()

            @self._mpv.event_callback("end-file")
            def _on_end_file(_event: Any) -> None:
                if self.next_track is not None:
                    self.current_track = self.next_track
                    self.next_track = None
                if self._on_track_ended:
                    try:
                        self._on_track_ended()
                    except Exception as e:  # noqa: BLE001
                        logger.error("Error in on_track_ended callback: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to set up some mpv observers (may be mocked): %s", e)

    def _update_state(self) -> None:
        """Derive and notify player state changes."""
        if self._terminated:
            return
        new_state = "stopped"
        try:
            is_idle = getattr(self._mpv, "idle_active", True)
            is_paused = getattr(self._mpv, "pause", False)
            is_core_idle = getattr(self._mpv, "core_idle", False)
        except (AttributeError, OSError):
            is_idle = True
            is_paused = False
            is_core_idle = False

        if is_idle or self.current_track is None:
            new_state = "stopped"
        elif is_paused:
            new_state = "paused"
        elif is_core_idle:
            new_state = "buffering"
        else:
            new_state = "playing"

        if new_state != self.state:
            self.state = new_state
            if self._on_state_changed:
                try:
                    self._on_state_changed(self.state)
                except Exception as e:  # noqa: BLE001
                    logger.error("Error in on_state_changed callback: %s", e)

    def play_source(self, source: str, track: Track | None = None) -> None:
        """Play a resolved audio source directly, replacing current playback."""
        self.current_track = track
        self.next_track = None
        self._mpv.loadfile(source, mode="replace")
        self._mpv.pause = False
        self._update_state()

    async def load_and_play(self, track: Track) -> None:
        """Resolve track audio source and start playback immediately."""
        source = await self.resolver.resolve(track.video_id)
        self.play_source(source, track)

    def preload_source(self, source: str, track: Track | None = None) -> None:
        """Preload a resolved audio source for gapless next-track transition."""
        self.next_track = track
        self._mpv.loadfile(source, mode="append")

    async def preload_next(self, track: Track) -> None:
        """Resolve and preload next track for gapless transition."""
        source = await self.resolver.resolve(track.video_id)
        self.preload_source(source, track)

    def pause(self) -> None:
        """Pause audio playback."""
        self._mpv.pause = True
        self._update_state()

    def resume(self) -> None:
        """Resume audio playback."""
        self._mpv.pause = False
        self._update_state()

    def seek(self, seconds: float) -> None:
        """Seek to absolute position in seconds."""
        self._mpv.seek(max(0.0, float(seconds)), reference="absolute")

    def set_volume(self, volume: int) -> None:
        """Set playback volume (0 to 100)."""
        self.volume = max(0, min(100, int(volume)))
        self._mpv.volume = float(self.volume)

    def stop(self) -> None:
        """Stop playback and clear current track."""
        with suppress(Exception):
            self._mpv.stop()
        self.current_track = None
        self.next_track = None
        self.time_pos = 0.0
        self.duration = 0.0
        self._update_state()

    def terminate(self) -> None:
        """Cleanly terminate the mpv player instance."""
        if self._terminated:
            return
        self._terminated = True
        with suppress(Exception):
            self._mpv.terminate()
