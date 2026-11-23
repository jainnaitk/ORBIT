"""Quick health check for MetroLite. Prints OK/FAIL lines and baseline RAM."""

import ctypes
import os
import sys
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BIN_DIR = ROOT / "bin"
if BIN_DIR.exists():
    os.environ["PATH"] = str(BIN_DIR) + os.pathsep + os.environ.get("PATH", "")
    os.add_dll_directory(str(BIN_DIR))


def ok(label: str, good: bool, extra: str = "") -> bool:
    print(("[ OK ] " if good else "[FAIL] ") + label + ((" - " + extra) if extra else ""))
    return good


def find_libmpv() -> str | None:
    local_dll = ROOT / "bin" / "libmpv-2.dll"
    if local_dll.exists():
        return str(local_dll)
    for name in ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll"):
        try:
            ctypes.CDLL(name)
            return name
        except OSError:
            continue
    return None


def main() -> None:
    ok("Python 3.11 or 3.12", (3, 11) <= sys.version_info[:2] < (3, 13), sys.version.split()[0])

    mpv_path = find_libmpv()
    ok(
        "libmpv shared library",
        bool(mpv_path),
        mpv_path or "place libmpv-2.dll in bin/ or add mpv to PATH",
    )

    for pkg in ("ytmusicapi", "yt_dlp", "mpv", "mutagen", "fastapi"):
        try:
            __import__(pkg)
            ok(f"Package {pkg}", True)
        except ImportError:
            ok(f"Package {pkg}", False, "install via uv sync --group p1 --group p2 --group p3")

    local_app = Path(os.environ.get("LOCALAPPDATA", ROOT / ".localdata")) / "metrolite"
    try:
        local_app.mkdir(parents=True, exist_ok=True)
        probe = local_app / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok("AppData writable", True, str(local_app))
    except Exception as exc:  # noqa: BLE001
        ok("AppData writable", False, str(exc))

    rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
    ok("Baseline process RAM", rss_mb < 60, f"{rss_mb:.1f} MB")


if __name__ == "__main__":
    main()
