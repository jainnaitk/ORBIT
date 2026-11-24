#!/usr/bin/env python3
"""ORBIT Icon Sync & NSIS Header Automation.

Restores ui/public/orbit-logo.png, generates a clean 1024x1024 RGBA master icon,
runs `npx tauri icon` to regenerate all platform/Windows icons, builds the
150x57 24-bit uncompressed NSIS header BMP on #05070E obsidian background, and
cleans up duplicate unused icon files.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
PUBLIC_LOGO = UI_DIR / "public" / "orbit-logo.png"
ICONS_DIR = UI_DIR / "src-tauri" / "icons"
SRC_ICON_LOGO = ICONS_DIR / "orbit-logo.png"
UNUSED_APP_ICON = ICONS_DIR / "app-icon.png"
NSIS_HEADER_BMP = ICONS_DIR / "nsis-header.bmp"

OBSIDIAN_RGB = (0x05, 0x07, 0x0E)  # #05070E


def ensure_pillow():
    """Import Pillow, installing it cleanly if missing; return Image module or None."""
    try:
        from PIL import Image

        return Image
    except ImportError:
        print("[sync_icons] Pillow not found; attempting clean install...")
        install_cmds: list[list[str]] = [
            [sys.executable, "-m", "pip", "install", "--quiet", "pillow"],
        ]
        uv_cmd = shutil.which("uv")
        if uv_cmd:
            install_cmds.append([uv_cmd, "pip", "install", "--quiet", "pillow"])

        for cmd in install_cmds:
            res = subprocess.run(cmd, check=False)
            if res.returncode == 0:
                try:
                    from PIL import Image

                    return Image
                except ImportError:
                    pass
    print("[sync_icons] Pillow unavailable; using stdlib fallback.")
    return None


def write_solid_bmp_24bit(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Stdlib fallback: write a valid 24-bit uncompressed Windows BMP."""
    r, g, b = rgb
    row_raw = bytes([b, g, r]) * width
    pad_len = (4 - (len(row_raw) % 4)) % 4
    row = row_raw + (b"\x00" * pad_len)
    pixel_data = row * height

    file_header_size = 14
    dib_header_size = 40
    pixel_offset = file_header_size + dib_header_size
    file_size = pixel_offset + len(pixel_data)

    bmp_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
    dib_header = struct.pack(
        "<IIIHHIIIIII",
        dib_header_size,
        width,
        height,
        1,  # planes
        24,  # bits per pixel
        0,  # BI_RGB (uncompressed)
        len(pixel_data),
        2835,  # ~72 DPI X
        2835,  # ~72 DPI Y
        0,
        0,
    )
    path.write_bytes(bmp_header + dib_header + pixel_data)


def build_master_and_header(
    image_mod,
    source_png: Path,
    master_out: Path,
    nsis_bmp_out: Path,
) -> None:
    """Create 1024x1024 RGBA master PNG and 150x57 24-bit uncompressed NSIS BMP."""
    if image_mod is None:
        shutil.copy2(source_png, master_out)
        write_solid_bmp_24bit(nsis_bmp_out, 150, 57, OBSIDIAN_RGB)
        return

    resampling = getattr(image_mod, "Resampling", image_mod).LANCZOS

    with image_mod.open(source_png) as raw_img:
        rgba = raw_img.convert("RGBA")

        # 1) 1024x1024 RGBA master PNG (centered, preserving aspect ratio without cropping)
        fitted = rgba.copy()
        fitted.thumbnail((1024, 1024), resampling)
        master = image_mod.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        offset_x = (1024 - fitted.width) // 2
        offset_y = (1024 - fitted.height) // 2
        master.paste(fitted, (offset_x, offset_y), fitted)
        master.save(master_out, format="PNG")

        # 2) 150x57 24-bit uncompressed BMP with ORBIT logo on #05070E obsidian background
        header = image_mod.new("RGB", (150, 57), OBSIDIAN_RGB)
        logo_small = rgba.copy()
        logo_small.thumbnail((45, 45), resampling)
        # Position cleanly on right-center of the 150x57 NSIS top-right header banner
        logo_x = 150 - logo_small.width - 14
        logo_y = (57 - logo_small.height) // 2
        header.paste(logo_small, (logo_x, logo_y), logo_small)
        header.save(nsis_bmp_out, format="BMP")


def run_tauri_icon(master_png: Path) -> None:
    """Invoke `npx tauri icon <master_png>` inside ORBIT/ui."""
    if sys.platform == "win32":
        npx_cmd = shutil.which("npx.cmd") or shutil.which("npx") or "npx.cmd"
    else:
        npx_cmd = shutil.which("npx") or "npx"
    cmd = [npx_cmd, "tauri", "icon", str(master_png)]
    print(f"[sync_icons] Running: {' '.join(cmd)} (cwd={UI_DIR})")
    res = subprocess.run(cmd, cwd=UI_DIR, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"`npx tauri icon` failed with exit code {res.returncode}")


def main() -> int:
    # Step 1: Locate logo and ensure ui/public/orbit-logo.png is restored
    if SRC_ICON_LOGO.is_file():
        PUBLIC_LOGO.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC_ICON_LOGO, PUBLIC_LOGO)
        print(f"[sync_icons] Copied {SRC_ICON_LOGO.relative_to(ROOT)} -> {PUBLIC_LOGO.relative_to(ROOT)}")
        source_logo = PUBLIC_LOGO
    elif PUBLIC_LOGO.is_file():
        print(f"[sync_icons] Using existing {PUBLIC_LOGO.relative_to(ROOT)}")
        source_logo = PUBLIC_LOGO
    else:
        print(
            f"[sync_icons] ERROR: Neither {SRC_ICON_LOGO} nor {PUBLIC_LOGO} exists.",
            file=sys.stderr,
        )
        return 1

    image_mod = ensure_pillow()

    # Step 2, 3 & 4: Build 1024x1024 master PNG, run `npx tauri icon`, generate 150x57 NSIS BMP
    with tempfile.TemporaryDirectory(prefix="orbit_icons_") as tmp_dir:
        master_png = Path(tmp_dir) / "orbit-master-1024.png"
        build_master_and_header(image_mod, source_logo, master_png, NSIS_HEADER_BMP)
        print(f"[sync_icons] Generated master PNG & {NSIS_HEADER_BMP.relative_to(ROOT)} (150x57 24-bit BMP)")
        run_tauri_icon(master_png)

    # Verify expected generated icon exists before removing duplicates
    expected_ico = ICONS_DIR / "icon.ico"
    if not expected_ico.is_file():
        print("[sync_icons] ERROR: icon.ico missing after generation.", file=sys.stderr)
        return 1

    # Step 5: Remove duplicate unused icon files after generation succeeds
    for dup in (SRC_ICON_LOGO, UNUSED_APP_ICON):
        if dup.is_file():
            dup.unlink()
            print(f"[sync_icons] Removed duplicate {dup.relative_to(ROOT)}")

    print("[sync_icons] All ORBIT app & NSIS installer icons synced cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
