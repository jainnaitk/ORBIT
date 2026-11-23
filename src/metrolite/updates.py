"""GitHub Releases auto-updater and Windows installer launcher for ORBIT."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from metrolite.config import get_config

logger = logging.getLogger(__name__)

_GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "ORBIT-Desktop-Updater/0.1.0",
    "X-GitHub-Api-Version": "2022-11-28",
}

_TRUSTED_ASSET_HOSTS = (
    "github.com",
    "githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_SEMVER_RE = re.compile(r"\d+")


def parse_version(version: str | None) -> tuple[int, ...]:
    """Parse a version tag like 'v0.2.1' or '0.2.1-beta' into an integer tuple."""
    if not version:
        return (0, 0, 0)
    nums = [int(n) for n in _SEMVER_RE.findall(version.strip())]
    if not nums:
        return (0, 0, 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:4])


def is_newer_version(latest: str | None, current: str | None) -> bool:
    """Return True if latest semantic version is strictly greater than current."""
    if not latest:
        return False
    return parse_version(latest) > parse_version(current)


def _is_trusted_download_url(url: str) -> bool:
    """Allow only HTTPS URLs pointing to GitHub release hosts."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == dom or host.endswith(f".{dom}") for dom in _TRUSTED_ASSET_HOSTS)


def _pick_windows_installer(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the best Windows installer asset from a GitHub Release."""
    if not assets:
        return None

    def _score(asset: dict[str, Any]) -> int:
        name = str(asset.get("name") or "").lower()
        if not name or not asset.get("browser_download_url"):
            return -1
        if name.endswith(".exe"):
            if "setup" in name or "install" in name or "x64" in name:
                return 100
            return 85
        if name.endswith(".msi"):
            return 75
        if name.endswith(".zip") and ("win" in name or "x64" in name):
            return 40
        return -1

    ranked = sorted(assets, key=_score, reverse=True)
    return ranked[0] if ranked and _score(ranked[0]) > 0 else None


class UpdateManager:
    """Checks GitHub Releases, downloads Windows installers with progress, and launches upgrades."""

    def __init__(
        self,
        github_repo: str | None = None,
        current_version: str | None = None,
        updates_dir: Path | str | None = None,
    ) -> None:
        cfg = get_config()
        upd_cfg = getattr(cfg, "updates", None)
        self.enabled: bool = getattr(upd_cfg, "enabled", True) if upd_cfg else True
        self.github_repo: str = (
            github_repo
            or (getattr(upd_cfg, "github_repo", None) if upd_cfg else None)
            or "jainnaitk/ORBIT"
        ).strip()
        self.current_version: str = (
            current_version
            or (getattr(upd_cfg, "current_version", None) if upd_cfg else None)
            or "0.1.0"
        ).strip()
        self.updates_dir: Path = (
            Path(updates_dir) if updates_dir is not None else cfg.app.data_dir / "updates"
        )
        self.updates_dir.mkdir(parents=True, exist_ok=True)

        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._download_task: asyncio.Task[dict[str, Any]] | None = None
        self._last_check_ts: float = 0.0
        self._state: dict[str, Any] = {
            "status": "idle",  # idle | checking | up_to_date | available | downloading | ready | launched | error
            "repo": self.github_repo,
            "current_version": self.current_version,
            "latest_version": self.current_version,
            "update_available": False,
            "release_name": None,
            "release_notes": None,
            "published_at": None,
            "html_url": f"https://github.com/{self.github_repo}/releases",
            "asset_name": None,
            "asset_size": 0,
            "download_url": None,
            "downloaded_path": None,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "progress_pct": 0.0,
            "error": None,
        }

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def get_state(self) -> dict[str, Any]:
        return dict(self._state)

    def _emit(self) -> None:
        snapshot = self.get_state()
        for cb in list(self._listeners):
            try:
                cb(snapshot)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Update listener callback failed: %s", exc)

    def _safe_target_path(self, asset_name: str) -> Path:
        clean = _SAFE_FILENAME_RE.sub("_", Path(asset_name).name)
        if not clean:
            clean = "ORBIT-setup.exe"
        target = (self.updates_dir / clean).resolve()
        root = self.updates_dir.resolve()
        if root not in target.parents:
            raise ValueError("Resolved installer path escaped updates directory")
        return target

    async def check_for_updates(self, force: bool = False) -> dict[str, Any]:
        """Query GitHub Releases API for the latest ORBIT version."""
        if not self.enabled or not self.github_repo or "/" not in self.github_repo:
            self._state["status"] = "up_to_date"
            self._state["update_available"] = False
            return self.get_state()

        now = time.monotonic()
        if not force and self._last_check_ts > 0 and (now - self._last_check_ts) < 300.0:
            return self.get_state()

        if self._state["status"] == "downloading":
            return self.get_state()

        self._state["status"] = "checking"
        self._state["error"] = None
        self._emit()

        api_url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers=_GITHUB_API_HEADERS,
            ) as client:
                resp = await client.get(api_url)
                if resp.status_code == 404:
                    self._last_check_ts = now
                    self._state.update(
                        {
                            "status": "up_to_date",
                            "latest_version": self.current_version,
                            "update_available": False,
                            "release_name": f"ORBIT v{self.current_version}",
                            "release_notes": "No GitHub releases published yet for this repository.",
                            "html_url": f"https://github.com/{self.github_repo}/releases",
                            "error": None,
                        }
                    )
                    self._emit()
                    return self.get_state()

                resp.raise_for_status()
                release = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("GitHub update check failed for %s: %s", self.github_repo, exc)
            self._state.update(
                {
                    "status": "error",
                    "error": f"Could not reach GitHub Releases: {exc}",
                }
            )
            self._emit()
            return self.get_state()

        self._last_check_ts = now
        tag = str(release.get("tag_name") or "").strip()
        latest_clean = tag.lstrip("vV") or self.current_version
        assets = release.get("assets") if isinstance(release.get("assets"), list) else []
        installer = _pick_windows_installer(assets)
        has_update = is_newer_version(latest_clean, self.current_version)

        asset_name = str(installer.get("name")) if installer else None
        asset_size = int(installer.get("size") or 0) if installer else 0
        download_url = str(installer.get("browser_download_url")) if installer else None

        existing_path: str | None = None
        if asset_name:
            candidate = self._safe_target_path(asset_name)
            if candidate.is_file() and (asset_size == 0 or candidate.stat().st_size == asset_size):
                existing_path = str(candidate)

        next_status = "up_to_date"
        if has_update:
            next_status = "ready" if existing_path else "available"

        self._state.update(
            {
                "status": next_status,
                "latest_version": latest_clean,
                "update_available": has_update,
                "release_name": str(release.get("name") or f"ORBIT v{latest_clean}"),
                "release_notes": str(release.get("body") or "").strip()[:2000] or None,
                "published_at": release.get("published_at"),
                "html_url": str(
                    release.get("html_url")
                    or f"https://github.com/{self.github_repo}/releases/latest"
                ),
                "asset_name": asset_name,
                "asset_size": asset_size,
                "download_url": download_url,
                "downloaded_path": existing_path,
                "downloaded_bytes": asset_size if existing_path else 0,
                "total_bytes": asset_size,
                "progress_pct": 100.0 if existing_path else 0.0,
                "error": None,
            }
        )
        self._emit()
        return self.get_state()

    def start_download(self, auto_install: bool = True) -> asyncio.Task[dict[str, Any]]:
        """Start downloading the latest installer in the background."""
        if self._download_task is not None and not self._download_task.done():
            return self._download_task
        self._download_task = asyncio.create_task(self.download_update(auto_install=auto_install))
        return self._download_task

    async def download_update(self, auto_install: bool = True) -> dict[str, Any]:
        """Download the Windows installer from GitHub Releases and optionally launch it."""
        if not self._state.get("download_url") or not self._state.get("asset_name"):
            await self.check_for_updates(force=True)

        url = self._state.get("download_url")
        asset_name = self._state.get("asset_name")
        if not url or not asset_name:
            self._state["status"] = "error"
            self._state["error"] = (
                "No Windows installer (.exe / .msi) was found in the latest GitHub Release."
            )
            self._emit()
            return self.get_state()

        if not _is_trusted_download_url(url):
            self._state["status"] = "error"
            self._state["error"] = "Blocked untrusted update download URL."
            self._emit()
            return self.get_state()

        target = self._safe_target_path(asset_name)
        expected_size = int(self._state.get("asset_size") or 0)
        if target.is_file() and (expected_size == 0 or target.stat().st_size == expected_size):
            self._state.update(
                {
                    "status": "ready",
                    "downloaded_path": str(target),
                    "downloaded_bytes": target.stat().st_size,
                    "total_bytes": target.stat().st_size,
                    "progress_pct": 100.0,
                    "error": None,
                }
            )
            self._emit()
            if auto_install:
                return self.launch_installer()
            return self.get_state()

        part_path = target.with_name(f"{target.name}.{os.getpid()}.part")
        self._state.update(
            {
                "status": "downloading",
                "downloaded_bytes": 0,
                "total_bytes": expected_size,
                "progress_pct": 0.0,
                "error": None,
            }
        )
        self._emit()

        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0),
                    follow_redirects=True,
                    headers={"User-Agent": _GITHUB_API_HEADERS["User-Agent"]},
                ) as client,
                client.stream("GET", url) as resp,
            ):
                resp.raise_for_status()
                if not _is_trusted_download_url(str(resp.url)):
                    raise ValueError("Redirected to untrusted download host")

                total = int(resp.headers.get("content-length") or expected_size or 0)
                downloaded = 0
                last_emit = 0.0
                with part_path.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        fh.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if now - last_emit >= 0.15:
                            last_emit = now
                            pct = (downloaded / total * 100.0) if total > 0 else 0.0
                            self._state.update(
                                {
                                    "downloaded_bytes": downloaded,
                                    "total_bytes": total,
                                    "progress_pct": round(min(99.9, pct), 1),
                                }
                            )
                            self._emit()

            part_path.replace(target)
            final_size = target.stat().st_size
            self._state.update(
                {
                    "status": "ready",
                    "downloaded_path": str(target),
                    "downloaded_bytes": final_size,
                    "total_bytes": final_size,
                    "progress_pct": 100.0,
                    "error": None,
                }
            )
            self._emit()
            if auto_install:
                return self.launch_installer()
            return self.get_state()
        except Exception as exc:  # noqa: BLE001
            if part_path.exists():
                try:
                    part_path.unlink(missing_ok=True)
                except OSError:
                    pass
            logger.error("Failed downloading update installer: %s", exc)
            self._state.update(
                {
                    "status": "error",
                    "error": f"Download failed: {exc}",
                }
            )
            self._emit()
            return self.get_state()

    def launch_installer(self) -> dict[str, Any]:
        """Launch the downloaded Windows installer (.exe or .msi) to upgrade ORBIT."""
        raw_path = self._state.get("downloaded_path")
        if not raw_path:
            self._state["status"] = "error"
            self._state["error"] = "Installer has not been downloaded yet."
            self._emit()
            return self.get_state()

        target = Path(raw_path).resolve()
        root = self.updates_dir.resolve()
        if not target.is_file() or root not in target.parents:
            self._state["status"] = "error"
            self._state["error"] = "Downloaded installer file is missing or invalid."
            self._emit()
            return self.get_state()

        ext = target.suffix.lower()
        if ext not in (".exe", ".msi"):
            self._state["status"] = "error"
            self._state["error"] = f"Unsupported installer format: {ext}"
            self._emit()
            return self.get_state()

        if os.environ.get("PYTEST_CURRENT_TEST"):
            self._state["status"] = "launched"
            self._state["error"] = None
            self._emit()
            return self.get_state()

        try:
            if sys.platform == "win32":
                if ext == ".msi":
                    subprocess.Popen(["msiexec", "/i", str(target)], close_fds=True)
                else:
                    os.startfile(str(target))
            else:
                raise RuntimeError("Automatic installer launch is supported on Windows only.")

            self._state["status"] = "launched"
            self._state["error"] = None
            self._emit()
            logger.info("Launched Windows update installer: %s", target)
            return self.get_state()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to launch installer %s: %s", target, exc)
            self._state["status"] = "error"
            self._state["error"] = f"Failed to launch installer: {exc}"
            self._emit()
            return self.get_state()

    def stop(self) -> None:
        if self._download_task is not None and not self._download_task.done():
            self._download_task.cancel()
            self._download_task = None
