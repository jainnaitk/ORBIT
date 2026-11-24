"""Verify the production ORBIT/website/index.html is lean, minimal, and free of clutter."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "website" / "index.html"
PROXY = ROOT / "website" / "functions" / "api" / "download.js"

FORBIDDEN_TOKENS = (
    ".msi",
    "Developer CLI",
    "SmartScreen",
    "SHA256SUMS",
    "Keyboard Shortcuts",
    "Python",
    "FastAPI",
    "Tauri",
    "libmpv",
    "SQLite",
    "three.min.js",
)

REQUIRED_TOKENS = (
    "Created by Naitik Jain",
    "Download for Windows",
    "jainnaitk",
    "ORBIT",
    "/api/download",
)


def main() -> None:
    if not SITE.is_file():
        raise FileNotFoundError(f"Missing production site: {SITE}")
    html = SITE.read_text(encoding="utf-8")

    found_bad = [tok for tok in FORBIDDEN_TOKENS if tok in html]
    if found_bad:
        raise ValueError(f"Forbidden clutter tokens found in website/index.html: {found_bad}")

    missing = [tok for tok in REQUIRED_TOKENS if tok not in html]
    if missing:
        raise ValueError(f"Missing required tokens in website/index.html: {missing}")

    if PROXY.is_file():
        proxy_js = PROXY.read_text(encoding="utf-8")
        if ".msi" in proxy_js:
            raise ValueError("Unexpected .msi reference in website/functions/api/download.js")

    print(f"[OK] {SITE.relative_to(ROOT)} verified clean ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
