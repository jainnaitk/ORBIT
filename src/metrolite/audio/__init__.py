"""Audio playback, queue management, stream resolution, and media keys for MetroLite."""

from metrolite.audio.media_keys import MediaKeysListener
from metrolite.audio.player import AudioPlayer, ensure_mpv_dll, trim_memory
from metrolite.audio.queue import QueueManager, RepeatMode
from metrolite.audio.resolver import StreamResolutionError, StreamResolver

__all__ = [
    "AudioPlayer",
    "MediaKeysListener",
    "QueueManager",
    "RepeatMode",
    "StreamResolutionError",
    "StreamResolver",
    "ensure_mpv_dll",
    "trim_memory",
]
