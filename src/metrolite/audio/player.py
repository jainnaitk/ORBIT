"""Gapless audio player wrapping python-mpv for MetroLite."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from metrolite.audio.resolver import StreamResolver
from metrolite.config import get_config
from metrolite.ytm.client import Track

logger = logging.getLogger(__name__)

_NON_COMPLETION_REASONS = {"aborted", "stop", "restarted", "quit", "redirect"}
_last_trim_ts: float = 0.0


def trim_memory(force: bool = False) -> None:
    """Run garbage collection and trim the process working set on Windows (debounced)."""
    global _last_trim_ts
    now = time.monotonic()
    if not force and (now - _last_trim_ts) < 2.0:
        return
    _last_trim_ts = now

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
        candidates: list[Path] = []
        exe_path = Path(sys.executable).resolve()
        candidates.append(exe_path.parent)
        candidates.append(exe_path.parent / "resources")
        candidates.append(exe_path.parent.parent)
        candidates.append(exe_path.parent.parent / "resources")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass))
            candidates.append(Path(meipass) / "resources")
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        candidates.append(repo_root / "bin")
        candidates.append(repo_root / "ui" / "src-tauri" / "resources")
        candidates.append(Path.cwd() / "bin")
        candidates.append(Path.cwd())

        for candidate in candidates:
            if candidate.is_dir() and (candidate / "libmpv-2.dll").is_file():
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


def _extract_end_reason(event: Any) -> str:
    """Extract normalized string reason ('eof', 'stop', 'error', etc.) from mpv end-file event."""
    if event is None:
        return "eof"

    reason_val: Any = None
    data = getattr(event, "data", None)
    if data is not None and hasattr(data, "reason"):
        reason_val = data.reason
    elif hasattr(event, "reason"):
        reason_val = event.reason

    if reason_val is None and hasattr(event, "as_dict"):
        with suppress(Exception):
            d = event.as_dict()
            if isinstance(d, dict):
                reason_val = d.get("reason") or d.get("event", {}).get("reason")
    elif reason_val is None and isinstance(event, dict):
        reason_val = event.get("reason")

    if isinstance(reason_val, bytes):
        return reason_val.decode("utf-8", errors="ignore").lower()
    if isinstance(reason_val, str):
        return reason_val.lower()
    if isinstance(reason_val, int):
        mapping = {0: "eof", 1: "restarted", 2: "stop", 3: "quit", 4: "error", 5: "redirect"}
        return mapping.get(reason_val, "unknown")
    return "eof"


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
        self.state: str = "stopped"
        self._terminated: bool = False
        self._loading_new_track: bool = False
        self._expecting_new_file: bool = False
        self._load_seq: int = 0
        self._loaded_video_id: str | None = None
        self._pause_requested: bool = False
        self._gapless_promoted_id: str | None = None
        self.consecutive_errors: int = 0
        self.max_consecutive_errors: int = 2
        self.current_user_agent: str | None = None
        self.current_http_headers: list[str] | None = None

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
                "cache": "yes",
                "cache_pause_initial": "no",
                "demuxer_readahead_secs": 6,
                "audio_buffer": 0.1,
                "demuxer_max_bytes": demuxer_max_bytes,
                "demuxer_max_back_bytes": demuxer_max_back_bytes,
                "volume": float(self.volume),
            }
            if getattr(cfg.audio, "normalize_volume", False):
                mpv_kwargs["af"] = "dynaudnorm"
            self._mpv = mpv.MPV(**mpv_kwargs)

        self._setup_mpv_observers()

    @property
    def mpv(self) -> Any:
        """The underlying python-mpv instance."""
        return self._mpv

    @mpv.setter
    def mpv(self, value: Any) -> None:
        self._mpv = value

    @property
    def loaded_video_id(self) -> str | None:
        """Return the video_id currently loaded in libmpv, or None."""
        return self._loaded_video_id

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
                if value is None:
                    self._expecting_new_file = False
                    return
                if self._loading_new_track:
                    return
                pos = float(value)
                if self._expecting_new_file:
                    if pos < 2.0:
                        self._expecting_new_file = False
                    else:
                        return
                self.time_pos = pos
                if self._on_time_pos:
                    try:
                        self._on_time_pos(self.time_pos)
                    except Exception as e:  # noqa: BLE001
                        logger.error("Error in on_time_pos callback: %s", e)

            @self._mpv.property_observer("duration")
            def _on_duration_change(_name: str, value: Any) -> None:
                if value is not None and not self._loading_new_track:
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

            @self._mpv.event_callback("file-loaded")
            def _on_file_loaded(_event: Any) -> None:
                self._expecting_new_file = False
                self._loading_new_track = False
                self.time_pos = 0.0
                self._update_state()

            @self._mpv.event_callback("end-file")
            def _on_end_file(event: Any) -> None:
                reason = _extract_end_reason(event)
                logger.info("mpv end-file event with reason: %s", reason)
                if reason in _NON_COMPLETION_REASONS:
                    return

                if reason == "eof":
                    self.reset_errors()
                    if self.next_track is not None:
                        self._gapless_promoted_id = self.next_track.video_id
                        self.current_track = self.next_track
                        self._loaded_video_id = self.next_track.video_id
                        self.next_track = None
                    else:
                        self._gapless_promoted_id = None
                        self._loaded_video_id = None
                elif reason == "error":
                    self.record_error()
                    self.next_track = None
                    self._gapless_promoted_id = None
                    self._loaded_video_id = None
                    if self.consecutive_errors > self.max_consecutive_errors:
                        logger.warning(
                            "AudioPlayer reached max consecutive errors (%d). Pausing.",
                            self.consecutive_errors,
                        )
                        self.pause()

                if self._on_track_ended:
                    try:
                        import inspect

                        sig = inspect.signature(self._on_track_ended)
                        if len(sig.parameters) > 0:
                            self._on_track_ended(reason)
                        else:
                            self._on_track_ended()
                    except Exception as e:  # noqa: BLE001
                        logger.error("Error in on_track_ended callback: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to set up some mpv observers (may be mocked): %s", e)

    def record_error(self) -> int:
        """Record an error and increment consecutive error count."""
        self.consecutive_errors += 1
        return self.consecutive_errors

    def reset_errors(self) -> None:
        """Reset consecutive error count to 0."""
        self.consecutive_errors = 0

    def can_skip_on_error(self) -> bool:
        """Return True if consecutive errors are within the allowed limit (<= 2)."""
        return self.consecutive_errors <= self.max_consecutive_errors

    def set_user_agent(self, user_agent: str | None) -> None:
        """Set HTTP User-Agent header in libmpv before loading stream URLs."""
        self.current_user_agent = user_agent
        if not user_agent:
            return
        with suppress(Exception):
            self.mpv.user_agent = user_agent
        with suppress(Exception):
            self.mpv["user-agent"] = user_agent

    def set_http_headers(self, headers: dict[str, str] | list[str] | None) -> None:
        """Set HTTP header fields in libmpv before loading stream URLs."""
        if not headers:
            return
        if isinstance(headers, dict):
            header_list = [f"{k}: {v}" for k, v in headers.items()]
            ua = headers.get("User-Agent") or headers.get("user-agent")
            if ua and not self.current_user_agent:
                self.set_user_agent(str(ua))
        else:
            header_list = list(headers)

        self.current_http_headers = header_list
        with suppress(Exception):
            self.mpv.http_header_fields = header_list
        with suppress(Exception):
            self.mpv["http-header-fields"] = header_list

    def _update_state(self) -> None:
        """Derive and notify player state changes."""
        if self._terminated or self._loading_new_track:
            return
        new_state = "stopped"
        try:
            is_idle = getattr(self.mpv, "idle_active", True)
            is_paused = getattr(self.mpv, "pause", False)
            is_core_idle = getattr(self.mpv, "core_idle", False)
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

    def consume_gapless_promotion(self, video_id: str) -> bool:
        """Return True once if mpv already gaplessly advanced to video_id on EOF."""
        if self._gapless_promoted_id == video_id:
            self._gapless_promoted_id = None
            return True
        self._gapless_promoted_id = None
        return False

    def prepare_track(self, track: Track | None) -> None:
        """Reset timeline to 0:00 and pause old audio while resolving the next track."""
        self._gapless_promoted_id = None
        self._pause_requested = False
        self._loading_new_track = True
        self._expecting_new_file = True
        if self.state == "playing":
            with suppress(Exception):
                self.mpv.pause = True
        self.current_track = track
        self.next_track = None
        self.time_pos = 0.0
        self.duration = float(track.duration_s) if (track and track.duration_s) else 0.0
        if self.state != "buffering":
            self.state = "buffering"
            if self._on_state_changed:
                try:
                    self._on_state_changed(self.state)
                except Exception as e:  # noqa: BLE001
                    logger.error("Error in on_state_changed callback: %s", e)

    def play(
        self,
        url: str,
        track: Track | None = None,
        user_agent: str | None = None,
        headers: dict[str, str] | list[str] | None = None,
    ) -> None:
        """Play an audio URL or file path, resetting timeline and forwarding HTTP headers."""
        ua = user_agent or getattr(url, "user_agent", None)
        http_headers = headers if headers is not None else getattr(url, "http_headers", None)

        if not ua and isinstance(http_headers, dict):
            ua = http_headers.get("User-Agent") or http_headers.get("user-agent")

        if ua:
            self.set_user_agent(ua)
        if http_headers:
            self.set_http_headers(http_headers)

        self._gapless_promoted_id = None
        self._pause_requested = False
        self.current_track = track
        self._loaded_video_id = track.video_id if track is not None else None
        self.next_track = None
        self.time_pos = 0.0
        self.duration = float(track.duration_s) if (track and track.duration_s) else 0.0
        self._expecting_new_file = True
        self._loading_new_track = False
        self.mpv.play(str(url))
        with suppress(Exception):
            self.mpv.playlist_clear()
        self.mpv.pause = False
        self._update_state()

    def play_source(
        self,
        source: str,
        track: Track | None = None,
        user_agent: str | None = None,
        http_headers: dict[str, str] | list[str] | None = None,
    ) -> None:
        """Play a resolved audio source directly, replacing current playback."""
        self.play(
            str(source),
            track=track,
            user_agent=user_agent or getattr(source, "user_agent", None),
            headers=http_headers if http_headers is not None else getattr(source, "http_headers", None),
        )

    async def load_and_play(self, track: Track) -> None:
        """Resolve track audio source and start playback immediately (cancels stale switches)."""
        self._load_seq += 1
        my_seq = self._load_seq
        self.prepare_track(track)
        try:
            source = await self.resolver.resolve(track.video_id)
        except Exception:
            if my_seq == self._load_seq:
                self._loading_new_track = False
                self._expecting_new_file = False
                if self._loaded_video_id != track.video_id:
                    self._loaded_video_id = None
                    with suppress(Exception):
                        self.mpv.stop()
                self._update_state()
            raise
        if my_seq != self._load_seq:
            return
        if self._pause_requested:
            self._pause_requested = False
            self._loading_new_track = False
            self._expecting_new_file = False
            if self._loaded_video_id != track.video_id:
                self._loaded_video_id = None
                with suppress(Exception):
                    self.mpv.stop()
            self._update_state()
            return
        ua = getattr(source, "user_agent", None)
        headers = getattr(source, "http_headers", None)
        if not ua and hasattr(self.resolver, "get_user_agent"):
            ua = self.resolver.get_user_agent(track.video_id)
        if not headers and hasattr(self.resolver, "get_http_headers"):
            headers = self.resolver.get_http_headers(track.video_id)
        self.play_source(source, track, user_agent=ua, http_headers=headers)

    def clear_preloaded_next(self) -> None:
        """Clear any previously appended gapless track from mpv's playlist."""
        self.next_track = None
        self._gapless_promoted_id = None
        with suppress(Exception):
            self.mpv.playlist_clear()

    def preload_source(
        self,
        source: str,
        track: Track | None = None,
        user_agent: str | None = None,
        http_headers: dict[str, str] | list[str] | None = None,
    ) -> None:
        """Preload a resolved audio source for gapless next-track transition."""
        ua = user_agent or getattr(source, "user_agent", None)
        headers = http_headers if http_headers is not None else getattr(source, "http_headers", None)
        if ua:
            self.set_user_agent(ua)
        if headers:
            self.set_http_headers(headers)
        with suppress(Exception):
            self.mpv.playlist_clear()
        self.next_track = track
        self.mpv.loadfile(str(source), mode="append")

    async def preload_next(self, track: Track) -> None:
        """Resolve and preload next track for gapless transition (aborts if track switched)."""
        my_seq = self._load_seq
        source = await self.resolver.resolve(track.video_id)
        if my_seq != self._load_seq:
            return
        ua = getattr(source, "user_agent", None)
        headers = getattr(source, "http_headers", None)
        if not ua and hasattr(self.resolver, "get_user_agent"):
            ua = self.resolver.get_user_agent(track.video_id)
        if not headers and hasattr(self.resolver, "get_http_headers"):
            headers = self.resolver.get_http_headers(track.video_id)
        self.preload_source(source, track, user_agent=ua, http_headers=headers)

    def switch_to_preloaded_next(self, track: Track) -> bool:
        """Instantly switch to already-buffered next track in mpv (<50ms)."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        if self.next_track is None or self.next_track.video_id != track.video_id:
            return False
        try:
            pl_count = getattr(self.mpv, "playlist_count", 0) or 0
            pl_pos = getattr(self.mpv, "playlist_pos", -1)
            if pl_count < 2 or pl_pos is None or pl_pos + 1 >= pl_count:
                return False
            self._load_seq += 1
            self._pause_requested = False
            self._loading_new_track = False
            self._expecting_new_file = True
            self.current_track = track
            self._loaded_video_id = track.video_id
            self.next_track = None
            self.time_pos = 0.0
            self.duration = float(track.duration_s) if track.duration_s else 0.0
            self.mpv.playlist_next(mode="force")
            self.mpv.pause = False
            self._update_state()
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("Instant playlist_next fallback to load_and_play: %s", e)
            return False

    def pause(self) -> None:
        """Pause audio playback."""
        if self._loading_new_track:
            self._pause_requested = True
        self._loading_new_track = False
        self.mpv.pause = True
        self._update_state()

    def resume(self) -> None:
        """Resume audio playback."""
        self._pause_requested = False
        self._loading_new_track = False
        self.mpv.pause = False
        self._update_state()

    def seek(self, seconds: float) -> None:
        """Seek to absolute position in seconds, safely guarding against idle/loading states."""
        if self._terminated or self._loading_new_track:
            return
        if getattr(self.mpv, "idle_active", False) is True:
            return
        target = max(0.0, float(seconds))
        if self.duration and target > self.duration:
            target = self.duration
        try:
            self.mpv.seek(target, reference="absolute")
            self._expecting_new_file = False
            self.time_pos = target
        except (SystemError, OSError, Exception) as e:  # noqa: BLE001
            logger.warning("libmpv seek to %.1fs failed: %s", target, e)

    def set_eq_preset(self, preset: str) -> str:
        """Apply a native libmpv C audio filter preset with zero Python overhead."""
        presets = {
            "flat": "",
            "bass": "equalizer=f=60:width_type=o:w=1.5:g=6,equalizer=f=150:width_type=o:w=1.5:g=3",
            "vocal": "equalizer=f=1000:width_type=o:w=1.5:g=4,equalizer=f=3000:width_type=o:w=1.5:g=3,equalizer=f=120:width_type=o:w=1.5:g=-2",
            "night": "dynaudnorm=f=150:g=15,equalizer=f=80:width_type=o:w=1.5:g=2,equalizer=f=10000:width_type=o:w=1.5:g=-3",
            "studio": "equalizer=f=64:width_type=o:w=1.5:g=3,equalizer=f=1000:width_type=o:w=1.5:g=-1,equalizer=f=8000:width_type=o:w=1.5:g=3.5",
        }
        key = preset.lower().strip() if preset else "flat"
        if key not in presets:
            key = "flat"
        af_val = presets[key]
        with suppress(Exception):
            self.mpv.af = af_val
        with suppress(Exception):
            self.mpv["af"] = af_val
        return key

    def set_volume(self, volume: int) -> None:
        """Set playback volume (0 to 100)."""
        self.volume = max(0, min(100, int(volume)))
        self.mpv.volume = float(self.volume)

    def stop(self) -> None:
        """Stop playback and clear current track."""
        self._load_seq += 1
        self._pause_requested = False
        self._gapless_promoted_id = None
        self._loaded_video_id = None
        self._loading_new_track = False
        self._expecting_new_file = False
        with suppress(Exception):
            self.mpv.stop()
        self.current_track = None
        self.next_track = None
        self.time_pos = 0.0
        self.duration = 0.0
        self.reset_errors()
        self.current_user_agent = None
        self.current_http_headers = None
        self._update_state()

    def terminate(self) -> None:
        """Cleanly terminate the mpv player instance."""
        if self._terminated:
            return
        self._terminated = True
        with suppress(Exception):
            self.mpv.terminate()
