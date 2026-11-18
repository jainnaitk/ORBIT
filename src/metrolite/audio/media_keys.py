"""Global Windows media keys listener for MetroLite."""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

from metrolite.config import get_config

logger = logging.getLogger(__name__)

try:
    from pynput import keyboard
except ImportError:  # pragma: no cover
    keyboard = None  # type: ignore[assignment]


class MediaKeysListener:
    """Listens for global Windows media key events and dispatches callbacks."""

    def __init__(
        self,
        on_play_pause: Callable[[], Any] | None = None,
        on_next: Callable[[], Any] | None = None,
        on_prev: Callable[[], Any] | None = None,
        enabled: bool | None = None,
    ) -> None:
        cfg = get_config()
        self.enabled = enabled if enabled is not None else cfg.hotkeys.enabled
        self.on_play_pause = on_play_pause
        self.on_next = on_next
        self.on_prev = on_prev
        self._listener: Any | None = None
        self._running: bool = False

    def _on_press(self, key: Any) -> None:
        """Handle key press event."""
        if not self.enabled:
            return

        try:
            if key == getattr(keyboard.Key, "media_play_pause", None):
                logger.debug("MediaPlayPause pressed")
                if self.on_play_pause:
                    self.on_play_pause()
            elif key == getattr(keyboard.Key, "media_next", None):
                logger.debug("MediaNext pressed")
                if self.on_next:
                    self.on_next()
            elif key == getattr(keyboard.Key, "media_previous", None):
                logger.debug("MediaPrevious pressed")
                if self.on_prev:
                    self.on_prev()
        except Exception as e:  # noqa: BLE001
            logger.error("Error dispatching media key action: %s", e)

    def start(self) -> bool:
        """Start global keyboard listener in a background thread."""
        if not self.enabled:
            logger.info("Media keys disabled by configuration")
            return False

        if keyboard is None:
            logger.warning("pynput is not available; media keys cannot be listened to")
            return False

        if self._running and self._listener and self._listener.is_alive():
            return True

        try:
            self._listener = keyboard.Listener(on_press=self._on_press)
            self._listener.daemon = True
            self._listener.start()
            self._running = True
            logger.info("Global media keys listener started")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to start media keys listener: %s", e)
            self._running = False
            return False

    def stop(self) -> None:
        """Stop global keyboard listener."""
        self._running = False
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("Error stopping listener: %s", e)
            self._listener = None
        logger.info("Global media keys listener stopped")

    def is_alive(self) -> bool:
        """Return True if listener is active."""
        return bool(self._running and self._listener and self._listener.is_alive())

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()
