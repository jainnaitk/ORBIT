"""Audio playback, queue management, and stream resolution module for MetroLite."""

from metrolite.audio.player import AudioPlayer, ensure_mpv_dll, trim_memory
from metrolite.audio.queue import QueueManager, RepeatMode
from metrolite.audio.resolver import StreamResolutionError, StreamResolver

__all__ = [
    "AudioPlayer",
    "QueueManager",
    "RepeatMode",
    "StreamResolutionError",
    "StreamResolver",
    "ensure_mpv_dll",
    "trim_memory",
]
