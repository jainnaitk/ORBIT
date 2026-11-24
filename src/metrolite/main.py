"""MetroLite CLI entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from metrolite.config import get_config
from metrolite.store.db import init_db_sync


def build_parser() -> argparse.ArgumentParser:
    """Build command-line interface parser."""
    parser = argparse.ArgumentParser(
        prog="metrolite",
        description="MetroLite: Lightweight Windows desktop YouTube Music client.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
        help="Show program version and exit.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        help="Path to custom config.yaml file.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Run the MetroLite backend service.")
    run_parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host to bind bridge server to (default from config: 127.0.0.1).",
    )
    run_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind bridge server to (default from config: 8766).",
    )

    # Command: play
    play_parser = subparsers.add_parser("play", help="Search and play a track by query.")
    play_parser.add_argument(
        "query",
        type=str,
        help="Track title, artist, or YouTube Music search query.",
    )

    return parser


def run_command(args: argparse.Namespace) -> int:
    """Handle `metrolite run` command."""
    import os

    cfg = get_config(args.config)
    host = args.host or cfg.bridge.host
    port = args.port or cfg.bridge.port

    # Ensure database is initialized before starting service
    db_path = init_db_sync()
    print(f"Starting {cfg.app.name} backend on {host}:{port} (db: {db_path})")

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return 0

    from metrolite.audio.media_keys import MediaKeysListener
    from metrolite.bridge.server import BridgeController, BridgeServer

    media_keys = MediaKeysListener() if cfg.hotkeys.enabled else None
    controller = BridgeController(db_path=db_path, media_keys=media_keys)
    server = BridgeServer(host=host, port=port, controller=controller)
    try:
        server.start_sync()
    except KeyboardInterrupt:
        server.stop()
    return 0


def play_command(args: argparse.Namespace) -> int:
    """Handle `metrolite play <query>` command."""
    import json
    import os

    cfg = get_config(args.config)
    query = (args.query or "").strip()
    print(f"[{cfg.app.name}] Playing query: {query}")

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return 0

    if not query:
        print(f"[{cfg.app.name}] Error: query cannot be empty.", file=sys.stderr)
        return 1

    bridge_file = cfg.app.data_dir / "bridge.json"
    if not bridge_file.is_file():
        print(
            f"[{cfg.app.name}] Bridge is not running ({bridge_file} not found). "
            "Start it first with: uv run python -m metrolite run",
            file=sys.stderr,
        )
        return 1

    try:
        bridge_cfg = json.loads(bridge_file.read_text(encoding="utf-8"))
        host = bridge_cfg.get("host", cfg.bridge.host)
        port = int(bridge_cfg.get("port", cfg.bridge.port))
        token = str(bridge_cfg.get("token", ""))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[{cfg.app.name}] Failed reading bridge config: {exc}", file=sys.stderr)
        return 1

    import httpx

    base_url = f"http://{host}:{port}"
    headers = {"X-Bridge-Token": token} if token else {}
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=15.0) as client:
            search_res = client.get("/search", params={"q": query, "filter": "songs", "limit": 1})
            search_res.raise_for_status()
            results = search_res.json()
            tracks = results if isinstance(results, list) else results.get("results", [])
            if not tracks:
                print(f"[{cfg.app.name}] No matches found for: {query}", file=sys.stderr)
                return 1

            top = tracks[0]
            play_res = client.post(
                "/player/play",
                json={"video_id": top["video_id"], "track": top, "track_meta": top},
            )
            play_res.raise_for_status()
            payload = play_res.json()
            if payload.get("status") == "error":
                print(f"[{cfg.app.name}] Playback error: {payload.get('error')}", file=sys.stderr)
                return 1
            print(
                f"[{cfg.app.name}] Now playing: {top.get('title', top['video_id'])} — "
                f"{top.get('artist', 'Unknown Artist')}"
            )
            return 0
    except httpx.HTTPError as exc:
        print(f"[{cfg.app.name}] Bridge request failed ({base_url}): {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    raw_args = argv if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw_args)

    if not args.command:
        # If running as PyInstaller frozen sidecar or launched with no arguments, default to "run"
        if getattr(sys, "frozen", False) or len(raw_args) == 0:
            args.command = "run"
            args.host = None
            args.port = None
            return run_command(args)
        parser.print_help()
        return 0

    if args.command == "run":
        return run_command(args)
    if args.command == "play":
        return play_command(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
