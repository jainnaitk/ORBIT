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
    cfg = get_config(args.config)
    host = args.host or cfg.bridge.host
    port = args.port or cfg.bridge.port

    # Ensure database is initialized before starting service
    db_path = init_db_sync()
    print(f"Starting {cfg.app.name} backend on {host}:{port} (db: {db_path})")
    return 0


def play_command(args: argparse.Namespace) -> int:
    """Handle `metrolite play <query>` command."""
    cfg = get_config(args.config)
    print(f"[{cfg.app.name}] Playing query: {args.query}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not args.command:
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
