"""Local bridge server (FastAPI & WebSocket) for MetroLite."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from metrolite.audio.media_keys import MediaKeysListener
from metrolite.audio.player import AudioPlayer, trim_memory
from metrolite.audio.queue import QueueManager
from metrolite.config import get_config
from metrolite.library.downloader import DownloadManager, DownloadProgress
from metrolite.library.manager import LibraryManager
from metrolite.store.db import get_connection, get_default_db_path, init_db
from metrolite.ytm.client import Track, YTMusicClient

logger = logging.getLogger(__name__)


def find_available_port(
    host: str = "127.0.0.1", start_port: int = 8766, max_attempts: int = 100
) -> int:
    """Find an available TCP port starting from start_port on host."""
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"No available port found on {host} between {start_port} and {start_port + max_attempts}"
    )


def write_bridge_file(bridge_path: Path, host: str, port: int, token: str) -> None:
    """Atomically write host, port, and security token to bridge.json.

    Never logs or echoes the token value.
    """
    bridge_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": host,
        "port": port,
        "token": token,
    }
    tmp_path = bridge_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(bridge_path)
    logger.info("Bridge configuration written to %s", bridge_path)


def remove_bridge_file(bridge_path: Path) -> None:
    """Safely remove bridge.json on server shutdown."""
    try:
        if bridge_path.is_file():
            bridge_path.unlink(missing_ok=True)
            logger.info("Bridge configuration removed from %s", bridge_path)
    except OSError as e:
        logger.debug("Failed to remove bridge file %s: %s", bridge_path, e)


class WebSocketManager:
    """Manages active WebSocket connections and broadcasting."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        """Register an accepted WebSocket connection."""
        async with self._lock:
            self.active_connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        """Unregister a WebSocket connection."""
        async with self._lock:
            self.active_connections.discard(ws)

    async def broadcast(self, event: str, data: Any) -> None:
        """Broadcast an event and JSON payload to all active clients."""
        payload = json.dumps({"event": event, "data": data})
        async with self._lock:
            snapshot = list(self.active_connections)

        stale: list[WebSocket] = []
        for ws in snapshot:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                stale.append(ws)

        if stale:
            async with self._lock:
                for ws in stale:
                    self.active_connections.discard(ws)

    def broadcast_sync(
        self, event: str, data: Any, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        """Thread-safe synchronous broadcast dispatch."""
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(event, data), loop)


class BridgeController:
    """Orchestrates audio player, queue, library, downloads, and websockets."""

    def __init__(
        self,
        player: AudioPlayer | None = None,
        queue: QueueManager | None = None,
        library: LibraryManager | None = None,
        downloader: DownloadManager | None = None,
        ytm: YTMusicClient | None = None,
        media_keys: MediaKeysListener | None = None,
        db_path: Path | None = None,
        ws_manager: WebSocketManager | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self.ws_manager = ws_manager or WebSocketManager()
        self.player = player or AudioPlayer()
        self.queue = queue or QueueManager(db_path=self.db_path, auto_persist=True)
        self.library = library or LibraryManager(db_path=self.db_path)
        self.downloader = downloader or DownloadManager(db_path=self.db_path)
        self.ytm = ytm or YTMusicClient()
        self.media_keys = media_keys
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._shutdown: bool = False

        self._bind_callbacks()

    def _bind_callbacks(self) -> None:
        """Wire component callbacks to bridge controller."""
        self.player.set_callbacks(
            on_time_pos=self._on_player_time_pos,
            on_duration=self._on_player_duration,
            on_track_ended=self._on_player_track_ended,
            on_state_changed=self._on_player_state_changed,
        )
        self.queue._on_queue_changed = self._on_queue_changed
        if hasattr(self.downloader, "add_progress_listener"):
            self.downloader.add_progress_listener(self._on_download_progress)

        if self.media_keys is not None:
            self.media_keys.on_play_pause = self._on_media_key_play_pause
            self.media_keys.on_next = self._on_media_key_next
            self.media_keys.on_prev = self._on_media_key_prev

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Assign active asyncio event loop for thread-safe dispatches."""
        self.loop = loop

    def run_coro_threadsafe(self, coro: Any) -> None:
        """Run coroutine thread-safely on the bridge event loop."""
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                pass

    def _on_player_time_pos(self, _pos: float) -> None:
        pass

    def _on_player_duration(self, _dur: float) -> None:
        pass

    def _on_player_state_changed(self, _new_state: str) -> None:
        tick_data = self.get_playback_tick_data()
        self.ws_manager.broadcast_sync("playback_tick", tick_data, self.loop)

    def _on_player_track_ended(self) -> None:
        self.run_coro_threadsafe(self.handle_track_ended())

    def _on_queue_changed(self) -> None:
        q_data = self.get_queue_data()
        self.ws_manager.broadcast_sync("queue_changed", q_data, self.loop)

    def _on_download_progress(self, progress: DownloadProgress) -> None:
        p_data = progress.model_dump()
        self.ws_manager.broadcast_sync("download_progress", p_data, self.loop)

    def _on_media_key_play_pause(self) -> None:
        self.run_coro_threadsafe(self.toggle_play_pause())

    def _on_media_key_next(self) -> None:
        self.run_coro_threadsafe(self.next_track())

    def _on_media_key_prev(self) -> None:
        self.run_coro_threadsafe(self.prev_track())

    def get_playback_tick_data(self) -> dict[str, Any]:
        """Return current playback tick snapshot."""
        return {
            "time_pos": round(self.player.time_pos, 2),
            "duration": round(self.player.duration, 2),
            "state": self.player.state,
            "volume": self.player.volume,
            "current_track": self.player.current_track.model_dump()
            if self.player.current_track
            else None,
        }

    def get_queue_data(self) -> dict[str, Any]:
        """Return current queue state snapshot."""
        return {
            "current_index": self.queue.current_index,
            "items": [t.model_dump() for t in self.queue.items],
            "shuffle": self.queue.shuffle,
            "repeat_mode": self.queue.repeat_mode,
            "autoplay_radio": self.queue.autoplay_radio,
            "current_track": self.queue.current_track.model_dump()
            if self.queue.current_track
            else None,
        }

    async def handle_track_ended(self) -> None:
        """Handle track completion: advance queue, play, and preload next."""
        next_track = await self.queue.next_track()
        if next_track:
            try:
                await self.player.load_and_play(next_track)
                preview = self.queue.next_track_preview
                if preview:
                    with suppress(Exception):
                        await self.player.preload_next(preview)
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to play next track after end: %s", e)
            await self.ws_manager.broadcast(
                "track_changed",
                {"track": next_track.model_dump(), "index": self.queue.current_index},
            )
        else:
            self.player.pause()
            await self.ws_manager.broadcast(
                "track_changed",
                {"track": None, "index": self.queue.current_index},
            )
        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())

    async def toggle_play_pause(self) -> dict[str, Any]:
        """Toggle playback state."""
        if self.player.state == "playing":
            self.player.pause()
        elif self.player.state == "paused":
            self.player.resume()
        else:
            curr = self.queue.current_track
            if curr:
                await self.player.load_and_play(curr)
                preview = self.queue.next_track_preview
                if preview:
                    with suppress(Exception):
                        await self.player.preload_next(preview)
                await self.ws_manager.broadcast(
                    "track_changed",
                    {"track": curr.model_dump(), "index": self.queue.current_index},
                )
        return self.get_playback_tick_data()

    async def play(
        self, video_id: str | None = None, track: Track | None = None
    ) -> dict[str, Any]:
        """Play requested track or resume current playback."""
        target: Track | None = track
        if target is None and video_id:
            # Check existing queue
            idx = next(
                (i for i, t in enumerate(self.queue.items) if t.video_id == video_id),
                None,
            )
            if idx is not None:
                self.queue.current_index = idx
                target = self.queue.items[idx]
            else:
                target = Track(video_id=video_id, title=video_id)
                self.queue.append(target)
                self.queue.current_index = len(self.queue.items) - 1

        if target is not None:
            await self.player.load_and_play(target)
            preview = self.queue.next_track_preview
            if preview:
                with suppress(Exception):
                    await self.player.preload_next(preview)
            await self.ws_manager.broadcast(
                "track_changed",
                {"track": target.model_dump(), "index": self.queue.current_index},
            )
            await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
        elif self.player.state == "paused":
            self.player.resume()
        else:
            curr = self.queue.current_track
            if curr:
                await self.player.load_and_play(curr)
                preview = self.queue.next_track_preview
                if preview:
                    with suppress(Exception):
                        await self.player.preload_next(preview)
                await self.ws_manager.broadcast(
                    "track_changed",
                    {"track": curr.model_dump(), "index": self.queue.current_index},
                )
        return self.get_playback_tick_data()

    async def next_track(self) -> dict[str, Any]:
        """Skip to next track in queue."""
        nxt = await self.queue.next_track()
        if nxt:
            await self.player.load_and_play(nxt)
            preview = self.queue.next_track_preview
            if preview:
                with suppress(Exception):
                    await self.player.preload_next(preview)
            await self.ws_manager.broadcast(
                "track_changed",
                {"track": nxt.model_dump(), "index": self.queue.current_index},
            )
        else:
            self.player.pause()
            await self.ws_manager.broadcast(
                "track_changed",
                {"track": None, "index": self.queue.current_index},
            )
        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
        return self.get_playback_tick_data()

    async def prev_track(self) -> dict[str, Any]:
        """Skip to previous track in queue."""
        prv = self.queue.prev_track()
        if prv:
            await self.player.load_and_play(prv)
            preview = self.queue.next_track_preview
            if preview:
                with suppress(Exception):
                    await self.player.preload_next(preview)
            await self.ws_manager.broadcast(
                "track_changed",
                {"track": prv.model_dump(), "index": self.queue.current_index},
            )
        await self.ws_manager.broadcast("queue_changed", self.get_queue_data())
        return self.get_playback_tick_data()

    def start_tick_loop(self) -> None:
        """Start 250ms playback_tick broadcast background task."""
        if self._tick_task is None or self._tick_task.done():
            self._shutdown = False
            self._tick_task = asyncio.create_task(self._playback_tick_loop())

    def stop_tick_loop(self) -> None:
        """Stop background tick loop."""
        self._shutdown = True
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()
            self._tick_task = None

    async def _playback_tick_loop(self) -> None:
        """Emit playback_tick every 250ms while playing."""
        last_state = ""
        tick_count = 0
        while not self._shutdown:
            try:
                curr_state = self.player.state
                if curr_state == "playing" or last_state == "playing":
                    data = self.get_playback_tick_data()
                    await self.ws_manager.broadcast("playback_tick", data)
                    last_state = curr_state
                tick_count += 1
                if tick_count % 40 == 0:  # Every 10 seconds
                    trim_memory()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.debug("Playback tick broadcast error: %s", e)
            await asyncio.sleep(0.25)

    def start_media_keys(self) -> None:
        """Start media keys listener if available."""
        if self.media_keys is not None:
            self.media_keys.start()

    def stop_media_keys(self) -> None:
        """Stop media keys listener."""
        if self.media_keys is not None:
            self.media_keys.stop()

    def queue_download(
        self, video_id: str, track_meta: Track | dict[str, Any] | None = None
    ) -> Any:
        """Queue a track download in the background."""
        if hasattr(self.downloader, "queue_download"):
            return self.downloader.queue_download(video_id, track_meta)
        if hasattr(self.downloader, "download_track"):
            return asyncio.create_task(self.downloader.download_track(video_id, track_meta))
        return None

    def shutdown(self) -> None:
        """Shut down background threads and services."""
        self.stop_tick_loop()
        self.stop_media_keys()
        if hasattr(self.downloader, "stop"):
            self.downloader.stop()
        trim_memory()


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class PlayRequest(BaseModel):
    video_id: str | None = None
    track: Track | None = None


class SeekRequest(BaseModel):
    seconds: float = Field(ge=0.0)


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)


class QueueActionRequest(BaseModel):
    action: str = "set"
    items: list[Track] | None = None
    item: Track | None = None
    index: int | None = None
    from_idx: int | None = None
    to_idx: int | None = None
    shuffle: bool | None = None
    repeat_mode: str | None = None
    autoplay_radio: bool | None = None
    start_index: int = 0
    auto_play: bool = False


class LikeTrackRequest(BaseModel):
    track_meta: Track | dict[str, Any] | None = None


class PlaylistCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class PlaylistAddTracksRequest(BaseModel):
    tracks: list[Track | dict[str, Any] | str] = []
    track: Track | dict[str, Any] | str | None = None


class PlaylistDeleteTrackRequest(BaseModel):
    video_id: str | None = None


class DownloadTrackRequest(BaseModel):
    track_meta: Track | dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# FastAPI Application Factory
# ---------------------------------------------------------------------------


def create_app(
    controller: BridgeController | None = None,
    token: str | None = None,
) -> FastAPI:
    """Create and configure MetroLite FastAPI bridge application."""
    expected_token = token or secrets.token_urlsafe(32)
    ctrl = controller or BridgeController()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        ctrl.set_loop(loop)
        await init_db(ctrl.db_path)
        ctrl.start_tick_loop()
        ctrl.start_media_keys()
        trim_memory()
        app.state.controller = ctrl
        app.state.token = expected_token
        try:
            yield
        finally:
            ctrl.shutdown()

    app = FastAPI(
        title="MetroLite Bridge API",
        version="0.1.0",
        description="Localhost REST & WebSocket audio bridge for MetroLite",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_pna_header(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network"):
            response.headers["access-control-allow-private-network"] = "true"
        return response

    app.state.controller = ctrl
    app.state.token = expected_token

    # -----------------------------------------------------------------------
    # Authentication Dependency
    # -----------------------------------------------------------------------

    def verify_token(
        request: Request,
        x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token"),
        authorization: str | None = Header(default=None),
        token: str | None = Query(default=None),
    ) -> str:
        """Verify request authenticity using timing-safe token comparison.

        Never logs or echoes token values.
        """
        provided: str | None = None
        if x_bridge_token:
            provided = x_bridge_token
        elif authorization and authorization.startswith("Bearer "):
            provided = authorization[7:].strip()
        elif token:
            provided = token

        valid_token: str = app.state.token
        if not provided or not secrets.compare_digest(provided, valid_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: invalid or missing bridge token",
            )
        return provided

    # -----------------------------------------------------------------------
    # Health & Discovery (token-exempt for readiness checks)
    # -----------------------------------------------------------------------

    @app.get("/health")
    async def get_health() -> dict[str, Any]:
        """Health and readiness check endpoint."""
        return {
            "status": "ok",
            "app": "MetroLite",
            "version": "0.1.0",
            "state": ctrl.player.state,
        }

    # -----------------------------------------------------------------------
    # WebSocket Endpoint
    # -----------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        token: str | None = Query(default=None),
    ) -> None:
        """Full-duplex WebSocket broadcasting state and events."""
        valid_token: str = app.state.token
        if not token or not secrets.compare_digest(token, valid_token):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()
        await ctrl.ws_manager.connect(websocket)

        # Send initial state snapshot on connection
        try:
            await websocket.send_text(
                json.dumps({"event": "playback_tick", "data": ctrl.get_playback_tick_data()})
            )
            await websocket.send_text(
                json.dumps({"event": "queue_changed", "data": ctrl.get_queue_data()})
            )

            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
        except (WebSocketDisconnect, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug("WebSocket client disconnected: %s", e)
        finally:
            await ctrl.ws_manager.disconnect(websocket)

    # -----------------------------------------------------------------------
    # YouTube Music Metadata Endpoints
    # -----------------------------------------------------------------------

    @app.get("/home", dependencies=[Depends(verify_token)])
    async def get_home() -> list[dict[str, Any]]:
        """Fetch YouTube Music home recommendations and shelves."""
        try:
            sections = await ctrl.ytm.get_home_sections()
            return [s.model_dump() if hasattr(s, "model_dump") else s for s in sections]
        except Exception as e:
            logger.error("Failed to load home sections: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch home sections: {e}",
            ) from e

    @app.get("/search", dependencies=[Depends(verify_token)])
    async def get_search(
        q: str = Query(..., min_length=1),
        filter: str = Query(default="songs"),
    ) -> list[dict[str, Any]]:
        """Search YouTube Music by query and filter type."""
        try:
            results = await ctrl.ytm.search(query=q, filter_type=filter)
            return [t.model_dump() for t in results]
        except Exception as e:
            logger.error("Search failed for query %r: %s", q, e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Search failed: {e}",
            ) from e

    # -----------------------------------------------------------------------
    # Player Control Endpoints
    # -----------------------------------------------------------------------

    @app.api_route("/player/play", methods=["GET", "POST"], dependencies=[Depends(verify_token)])
    async def player_play(
        video_id: str | None = Query(default=None),
        body: PlayRequest | None = None,
    ) -> dict[str, Any]:
        """Start or resume playback."""
        target_video_id = body.video_id if body and body.video_id else video_id
        target_track = body.track if body else None
        return await ctrl.play(video_id=target_video_id, track=target_track)

    @app.post("/player/pause", dependencies=[Depends(verify_token)])
    async def player_pause() -> dict[str, Any]:
        """Pause playback."""
        ctrl.player.pause()
        tick_data = ctrl.get_playback_tick_data()
        await ctrl.ws_manager.broadcast("playback_tick", tick_data)
        return tick_data

    @app.post("/player/next", dependencies=[Depends(verify_token)])
    async def player_next() -> dict[str, Any]:
        """Skip to next track."""
        return await ctrl.next_track()

    @app.post("/player/prev", dependencies=[Depends(verify_token)])
    async def player_prev() -> dict[str, Any]:
        """Return to previous track."""
        return await ctrl.prev_track()

    @app.post("/player/seek", dependencies=[Depends(verify_token)])
    async def player_seek(
        seconds: float | None = Query(default=None),
        body: SeekRequest | None = None,
    ) -> dict[str, Any]:
        """Seek to target position in seconds."""
        target_sec = body.seconds if body is not None else seconds
        if target_sec is None or target_sec < 0:
            raise HTTPException(status_code=400, detail="Missing or invalid seconds parameter")
        ctrl.player.seek(target_sec)
        tick_data = ctrl.get_playback_tick_data()
        await ctrl.ws_manager.broadcast("playback_tick", tick_data)
        return {"time_pos": ctrl.player.time_pos}

    @app.post("/player/volume", dependencies=[Depends(verify_token)])
    async def player_volume(
        volume: int | None = Query(default=None),
        body: VolumeRequest | None = None,
    ) -> dict[str, Any]:
        """Set player volume level (0-100)."""
        target_vol = body.volume if body is not None else volume
        if target_vol is None or target_vol < 0 or target_vol > 100:
            raise HTTPException(status_code=400, detail="Volume must be between 0 and 100")
        ctrl.player.set_volume(target_vol)
        tick_data = ctrl.get_playback_tick_data()
        await ctrl.ws_manager.broadcast("playback_tick", tick_data)
        return {"volume": ctrl.player.volume}

    # -----------------------------------------------------------------------
    # Queue Management Endpoints
    # -----------------------------------------------------------------------

    @app.get("/queue", dependencies=[Depends(verify_token)])
    async def get_queue() -> dict[str, Any]:
        """Get current queue state."""
        return ctrl.get_queue_data()

    @app.post("/queue", dependencies=[Depends(verify_token)])
    async def update_queue(body: QueueActionRequest) -> dict[str, Any]:
        """Manipulate queue items, shuffle, repeat, or radio autoplay."""
        action = body.action.lower()

        if action == "set":
            tracks = body.items or ([body.item] if body.item else [])
            ctrl.queue.set_queue(tracks, start_index=body.start_index)
            if body.auto_play and ctrl.queue.current_track:
                await ctrl.play()

        elif action == "append":
            if body.items:
                ctrl.queue.append(body.items)
            elif body.item:
                ctrl.queue.append(body.item)

        elif action == "add_next":
            if body.item:
                ctrl.queue.add_next(body.item)
            elif body.items:
                for t in reversed(body.items):
                    ctrl.queue.add_next(t)

        elif action == "remove":
            if body.index is not None:
                ctrl.queue.remove(body.index)

        elif action == "move":
            if body.from_idx is not None and body.to_idx is not None:
                ctrl.queue.move(body.from_idx, body.to_idx)

        elif action == "clear":
            ctrl.queue.clear()

        elif action == "shuffle":
            new_shuf = body.shuffle if body.shuffle is not None else not ctrl.queue.shuffle
            ctrl.queue.set_shuffle(new_shuf)

        elif action == "repeat":
            if body.repeat_mode:
                ctrl.queue.set_repeat_mode(body.repeat_mode)
            else:
                ctrl.queue.toggle_repeat_mode()

        elif action == "autoplay_radio":
            new_radio = (
                body.autoplay_radio
                if body.autoplay_radio is not None
                else not ctrl.queue.autoplay_radio
            )
            ctrl.queue.autoplay_radio = new_radio
            ctrl.queue._notify()

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported queue action: {body.action}")

        q_data = ctrl.get_queue_data()
        await ctrl.ws_manager.broadcast("queue_changed", q_data)
        return q_data

    # -----------------------------------------------------------------------
    # Library & Liked Songs Endpoints
    # -----------------------------------------------------------------------

    @app.post("/liked/{video_id}", dependencies=[Depends(verify_token)])
    async def toggle_liked(
        video_id: str,
        body: LikeTrackRequest | None = None,
    ) -> dict[str, Any]:
        """Toggle like status for a track."""
        track_meta = body.track_meta if body else None
        is_liked = await ctrl.library.toggle_like(video_id, track_meta)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "liked", "video_id": video_id, "is_liked": is_liked},
        )
        return {"video_id": video_id, "is_liked": is_liked}

    @app.get("/liked", dependencies=[Depends(verify_token)])
    async def list_liked(q: str | None = Query(default=None)) -> list[dict[str, Any]]:
        """List all liked songs with optional search query."""
        return await ctrl.library.list_liked(q)

    # -----------------------------------------------------------------------
    # Playlists Endpoints
    # -----------------------------------------------------------------------

    @app.get("/playlists", dependencies=[Depends(verify_token)])
    async def list_playlists() -> list[dict[str, Any]]:
        """List all user playlists."""
        return await ctrl.library.list_playlists()

    @app.post("/playlists", dependencies=[Depends(verify_token)])
    async def create_playlist(body: PlaylistCreateRequest) -> dict[str, Any]:
        """Create a new playlist."""
        record = await ctrl.library.create_playlist(body.name, body.description)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "playlist_created", "playlist": record},
        )
        return record

    @app.get("/playlists/{playlist_id}/tracks", dependencies=[Depends(verify_token)])
    async def get_playlist_tracks(playlist_id: str) -> list[dict[str, Any]]:
        """List tracks in a playlist ordered by position."""
        return await ctrl.library.get_playlist_tracks(playlist_id)

    @app.post("/playlists/{playlist_id}/tracks", dependencies=[Depends(verify_token)])
    async def add_playlist_tracks(
        playlist_id: str,
        body: PlaylistAddTracksRequest,
    ) -> dict[str, Any]:
        """Add one or more tracks to a playlist."""
        items: list[Any] = []
        if body.tracks:
            items.extend(body.tracks)
        if body.track:
            items.append(body.track)
        added = await ctrl.library.add_to_playlist(playlist_id, items)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "playlist_tracks_added", "playlist_id": playlist_id, "added": added},
        )
        return {"status": "ok", "added": added}

    @app.delete("/playlists/{playlist_id}/tracks", dependencies=[Depends(verify_token)])
    async def delete_playlist_track(
        playlist_id: str,
        video_id: str | None = Query(default=None),
        body: PlaylistDeleteTrackRequest | None = None,
    ) -> dict[str, Any]:
        """Remove a track from a playlist."""
        target_video_id = body.video_id if body and body.video_id else video_id
        if not target_video_id:
            raise HTTPException(status_code=400, detail="Missing video_id parameter")
        removed = await ctrl.library.remove_from_playlist(playlist_id, target_video_id)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {
                "type": "playlist_tracks_removed",
                "playlist_id": playlist_id,
                "video_id": target_video_id,
            },
        )
        return {"status": "ok", "removed": removed}

    # -----------------------------------------------------------------------
    # Downloads Endpoints
    # -----------------------------------------------------------------------

    @app.post("/downloads/{video_id}", dependencies=[Depends(verify_token)])
    async def queue_download(
        video_id: str,
        body: DownloadTrackRequest | None = None,
    ) -> dict[str, Any]:
        """Queue a track for offline background download."""
        track_meta = body.track_meta if body else None
        ctrl.queue_download(video_id, track_meta)
        return {"status": "queued", "video_id": video_id}

    @app.delete("/downloads/{video_id}", dependencies=[Depends(verify_token)])
    async def delete_download(video_id: str) -> dict[str, Any]:
        """Delete local downloaded track and clear SQLite reference."""
        deleted = await ctrl.downloader.delete_download(video_id)
        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "download_deleted", "video_id": video_id},
        )
        return {"status": "deleted" if deleted else "not_found", "video_id": video_id}

    @app.get("/downloads", dependencies=[Depends(verify_token)])
    async def list_downloads() -> list[dict[str, Any]]:
        """List all verified offline downloaded tracks."""
        return await ctrl.downloader.list_downloads()

    # -----------------------------------------------------------------------
    # Settings Endpoints (SQLite-backed)
    # -----------------------------------------------------------------------

    @app.get("/settings", dependencies=[Depends(verify_token)])
    async def get_settings() -> dict[str, Any]:
        """Fetch all persisted application settings."""
        async with get_connection(ctrl.db_path) as conn:
            cursor = await conn.execute("SELECT key, value_json FROM settings;")
            rows = await cursor.fetchall()
            result: dict[str, Any] = {}
            for row in rows:
                with suppress(json.JSONDecodeError):
                    result[row["key"]] = json.loads(row["value_json"])
            return result

    @app.put("/settings", dependencies=[Depends(verify_token)])
    async def update_settings(settings_dict: dict[str, Any]) -> dict[str, Any]:
        """Update or insert application settings."""
        async with get_connection(ctrl.db_path) as conn:
            for k, v in settings_dict.items():
                await conn.execute(
                    """
                    INSERT INTO settings (key, value_json)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json;
                    """,
                    (k, json.dumps(v)),
                )
            await conn.commit()

        await ctrl.ws_manager.broadcast(
            "library_changed",
            {"type": "settings_updated", "settings": settings_dict},
        )
        return settings_dict

    return app


# ---------------------------------------------------------------------------
# Bridge Server Runner
# ---------------------------------------------------------------------------


class BridgeServer:
    """Manages the lifecycle of the FastAPI bridge and background workers."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        token: str | None = None,
        controller: BridgeController | None = None,
        bridge_file_path: Path | None = None,
    ) -> None:
        cfg = get_config()
        self.host = host
        self.target_port = port if port is not None else cfg.bridge.port
        self.port: int = find_available_port(self.host, self.target_port)
        self.token: str = token or secrets.token_urlsafe(32)
        self.bridge_file: Path = (
            Path(bridge_file_path)
            if bridge_file_path is not None
            else cfg.app.data_dir / "bridge.json"
        )
        self.controller = controller or BridgeController()
        self.app = create_app(controller=self.controller, token=self.token)
        self._uvicorn_server: uvicorn.Server | None = None

    def start_sync(self) -> None:
        """Synchronously start the bridge server and block until termination."""
        write_bridge_file(self.bridge_file, self.host, self.port, self.token)
        uv_config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            workers=1,
            loop="asyncio",
        )
        self._uvicorn_server = uvicorn.Server(uv_config)
        try:
            self._uvicorn_server.run()
        finally:
            remove_bridge_file(self.bridge_file)

    async def start(self) -> None:
        """Start the bridge server asynchronously within an existing event loop."""
        write_bridge_file(self.bridge_file, self.host, self.port, self.token)
        uv_config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            workers=1,
            loop="asyncio",
        )
        self._uvicorn_server = uvicorn.Server(uv_config)
        await self._uvicorn_server.serve()

    def stop(self) -> None:
        """Signal uvicorn and background services to shut down."""
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        remove_bridge_file(self.bridge_file)
        self.controller.shutdown()
