"""GitHub Releases auto-updater and Windows installer launcher for ORBIT."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import yaml

from metrolite.config import get_config

logger = logging.getLogger(__name__)

_GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "ORBIT-Desktop-Updater/1.2.0",
    "X-GitHub-Api-Version": "2022-11-28",
}

_TRUSTED_ASSET_HOSTS = (
    "github.com",
    "githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)
_EXACT_TRUSTED_HOSTS = ("orbit-music.pages.dev",)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_SEMVER_RE = re.compile(r"\d+")
_SHA256_HEX_RE = re.compile(r"^[a-fA-F0-9]{64}$")


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


def _format_http_error(exc: Exception) -> str:
    """Return a concise, never-empty description for httpx/network failures."""
    if isinstance(exc, httpx.ConnectTimeout):
        return "Connection timed out"
    if isinstance(exc, httpx.ReadTimeout):
        return "Response timed out"
    if isinstance(exc, httpx.TimeoutException):
        return str(exc).strip() or "Request timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.ConnectError):
        return str(exc).strip() or "Connection failed"
    return str(exc).strip() or type(exc).__name__


def _is_trusted_download_url(
    url: str,
    extra_exact_hosts: tuple[str, ...] = (),
) -> bool:
    """Allow only HTTPS URLs pointing to GitHub release hosts or trusted ORBIT mirrors."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in _EXACT_TRUSTED_HOSTS or host in extra_exact_hosts:
        return True
    return any(host == dom or host.endswith(f".{dom}") for dom in _TRUSTED_ASSET_HOSTS)


def _pick_windows_installer(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the best Windows installer asset from a GitHub Release."""
    if not assets:
        return None

    def _score(asset: dict[str, Any]) -> int:
        name = str(asset.get("name") or "").lower()
        has_url = bool(asset.get("browser_download_url") or asset.get("mirror_download_url"))
        if not name or not has_url:
            return -1
        if name.endswith(".exe"):
            if "setup" in name or "install" in name or "x64" in name:
                return 110 if any(ch.isdigit() for ch in name) else 100
            return 85
        if name.endswith(".msi"):
            return 75
        if name.endswith(".zip") and ("win" in name or "x64" in name):
            return 40
        return -1

    ranked = sorted(assets, key=_score, reverse=True)
    return ranked[0] if ranked and _score(ranked[0]) > 0 else None


def _local_installer_filename(asset_name: str, latest_version: str | None = None) -> str:
    """Map generic installer names like ORBIT-Setup.exe to versioned local filenames."""
    base = Path(asset_name).name
    if base.lower() == "orbit-setup.exe" and latest_version:
        ver = latest_version.strip().lstrip("vV")
        if ver:
            base = f"ORBIT-v{ver}-Setup.exe"
    clean = _SAFE_FILENAME_RE.sub("_", base)
    return clean or "ORBIT-setup.exe"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_BULLET_PREFIX_RE = re.compile(r"^(?:[-*+•]|\d+[.)])\s+")
_CHANGELOG_SKIP_RE = re.compile(
    r"^(?:full changelog|what'?s changed|sha256|checksum|https?://)",
    re.IGNORECASE,
)
_CHANGELOG_COMPARE_RE = re.compile(r"github\.com/[^/\s]+/[^/\s]+/compare/", re.IGNORECASE)
_CHANGELOG_HEADING_RE = re.compile(r"^##\s+\[?v?([0-9]+\.[0-9]+\.[0-9]+[^\]]*)\]?", re.IGNORECASE)


def _extract_changelog_bullets(notes: str | None, max_items: int = 4) -> list[str]:
    """Extract up to `max_items` concise changelog bullets, stripping GitHub compare links."""
    if not notes:
        return []
    items: list[str] = []
    for raw in str(notes).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.match(r"^[-*_]{3,}$", line):
            continue
        cleaned = _BULLET_PREFIX_RE.sub("", line)
        cleaned = _MD_LINK_RE.sub(r"\1", cleaned)
        for ch in ("*", "_", "`", "~"):
            cleaned = cleaned.replace(ch, "")
        cleaned = " ".join(cleaned.split()).strip()
        if len(cleaned) < 4:
            continue
        if _CHANGELOG_SKIP_RE.match(cleaned) or _CHANGELOG_COMPARE_RE.search(cleaned):
            continue
        items.append(cleaned[:93].rstrip() + "..." if len(cleaned) > 96 else cleaned)
        if len(items) >= max_items:
            break
    return items


def _parse_changelog_markdown(markdown: str, version: str | None, max_items: int = 4) -> list[str]:
    """Parse up to `max_items` bullets for `version` (or newest entry) from CHANGELOG.md."""
    if not markdown:
        return []
    target = str(version or "").strip().lstrip("vV")
    in_target = False
    captured_first = False
    collected: list[str] = []

    for raw in str(markdown).splitlines():
        line = raw.strip()
        heading = _CHANGELOG_HEADING_RE.match(line)
        if heading:
            if in_target or (captured_first and not target):
                break
            heading_ver = heading.group(1).strip()
            if target and heading_ver == target:
                in_target = True
            elif not target and not captured_first:
                in_target = True
                captured_first = True
            continue
        if in_target and line.startswith(("-", "*", "•")):
            collected.append(line)
            if len(collected) >= max_items:
                break

    return _extract_changelog_bullets("\n".join(collected), max_items=max_items)


class UpdateManager:
    """Checks GitHub Releases (with Cloudflare mirror fallback), downloads installers, and launches upgrades."""

    def __init__(
        self,
        github_repo: str | None = None,
        current_version: str | None = None,
        updates_dir: Path | str | None = None,
        mirror_base_url: str | None = None,
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
        raw_mirror = (
            mirror_base_url
            if mirror_base_url is not None
            else (getattr(upd_cfg, "mirror_base_url", None) if upd_cfg else None)
        )
        cleaned_mirror = str(raw_mirror or "").strip().rstrip("/")
        # Only use default ORBIT mirror when checking the official repo unless explicitly overridden.
        if (
            mirror_base_url is None
            and self.github_repo.lower() != "jainnaitk/orbit"
        ):
            cleaned_mirror = ""
        self.mirror_base_url: str | None = cleaned_mirror or None
        self._extra_trusted_hosts: tuple[str, ...] = ()
        if self.mirror_base_url:
            parsed_mirror = urlparse(self.mirror_base_url)
            if parsed_mirror.scheme == "https" and parsed_mirror.hostname:
                self._extra_trusted_hosts = (parsed_mirror.hostname.lower().rstrip("."),)
            else:
                self.mirror_base_url = None

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
            "changelog": [],
            "published_at": None,
            "html_url": f"https://github.com/{self.github_repo}/releases",
            "asset_name": None,
            "asset_size": 0,
            "asset_sha256": None,
            "download_url": None,
            "fallback_download_url": None,
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

    def _is_trusted_url(self, url: str) -> bool:
        return _is_trusted_download_url(url, extra_exact_hosts=self._extra_trusted_hosts)

    def _build_mirror_download_url(self, asset_name: str | None) -> str | None:
        if not self.mirror_base_url or not asset_name:
            return None
        return f"{self.mirror_base_url}/api/download?asset={quote(asset_name)}"

    def _safe_target_path(self, asset_name: str) -> Path:
        clean = _SAFE_FILENAME_RE.sub("_", Path(asset_name).name)
        if not clean:
            clean = "ORBIT-setup.exe"
        target = (self.updates_dir / clean).resolve()
        root = self.updates_dir.resolve()
        if root not in target.parents:
            raise ValueError("Resolved installer path escaped updates directory")
        return target

    async def _fetch_from_mirror_release(
        self, client: httpx.AsyncClient
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Query the ORBIT mirror `/api/release` endpoint. Returns (release_json, no_release_msg)."""
        if not self.mirror_base_url:
            return None, None

        release_url = f"{self.mirror_base_url}/api/release"
        try:
            resp = await client.get(release_url, headers={"Accept": "application/json"})
            if resp.status_code == 404:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "json" in ctype:
                    return None, "No GitHub releases published yet for this repository."
                return None, None
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                return None, None
            data = resp.json()
            if isinstance(data, dict) and data.get("tag_name"):
                return data, None
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Mirror /api/release check failed (%s): %s", release_url, _format_http_error(exc))
        return None, None

    async def _fetch_from_raw_and_mirror_download(
        self, client: httpx.AsyncClient
    ) -> dict[str, Any] | None:
        """Last-resort fallback using raw.githubusercontent.com config.yaml + mirror /api/download HEAD."""
        if not self.mirror_base_url:
            return None

        raw_cfg_url = f"https://raw.githubusercontent.com/{self.github_repo}/main/config.yaml"
        dl_url = f"{self.mirror_base_url}/api/download"
        try:
            cfg_res, dl_res = await asyncio.gather(
                client.get(raw_cfg_url),
                client.head(dl_url),
                return_exceptions=True,
            )
            if isinstance(cfg_res, BaseException) or isinstance(dl_res, BaseException):
                return None
            if cfg_res.status_code != 200 or dl_res.status_code != 200:
                return None

            ctype = (dl_res.headers.get("content-type") or "").lower()
            if "text/html" in ctype:
                return None

            parsed = yaml.safe_load(cfg_res.text)
            if not isinstance(parsed, dict):
                return None
            updates_block = parsed.get("updates")
            if not isinstance(updates_block, dict):
                return None
            latest_ver = str(updates_block.get("current_version") or "").strip().lstrip("vV")
            if not latest_ver:
                return None

            size = int(dl_res.headers.get("content-length") or 0)
            if size <= 0:
                return None

            return {
                "tag_name": f"v{latest_ver}",
                "name": f"ORBIT v{latest_ver}",
                "body": "",
                "published_at": None,
                "html_url": f"https://github.com/{self.github_repo}/releases/latest",
                "assets": [
                    {
                        "name": "ORBIT-Setup.exe",
                        "size": size,
                        "browser_download_url": (
                            f"https://github.com/{self.github_repo}/releases/latest/download/ORBIT-Setup.exe"
                        ),
                        "mirror_download_url": dl_url,
                    }
                ],
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("Raw/mirror fallback failed: %s", _format_http_error(exc))
            return None

    async def _fetch_release_info(
        self,
    ) -> tuple[dict[str, Any] | None, bool, str | None, str | None]:
        """Return (release_json, used_mirror, no_release_msg, error_detail)."""
        api_url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
        timeout = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
        primary_error: str | None = None

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=_GITHUB_API_HEADERS,
        ) as client:
            try:
                resp = await client.get(api_url)
                if resp.status_code == 404:
                    return None, False, "No GitHub releases published yet for this repository.", None
                resp.raise_for_status()
                release = resp.json()
                if isinstance(release, dict) and release.get("tag_name"):
                    return release, False, None, None
                primary_error = "Invalid GitHub Releases payload"
            except (httpx.HTTPError, ValueError) as exc:
                primary_error = _format_http_error(exc)
                logger.debug("GitHub update check failed for %s: %s", self.github_repo, primary_error)

            mirror_release, no_rel_msg = await self._fetch_from_mirror_release(client)
            if no_rel_msg:
                return None, True, no_rel_msg, None
            if mirror_release:
                return mirror_release, True, None, None

            raw_fallback = await self._fetch_from_raw_and_mirror_download(client)
            if raw_fallback:
                return raw_fallback, True, None, None

        return None, False, None, primary_error or "Unable to reach GitHub Releases"

    async def _fetch_changelog_fallback(self, version: str) -> list[str]:
        """Fetch CHANGELOG.md from raw.githubusercontent.com when release body lacks bullets."""
        if not self.github_repo or "/" not in self.github_repo:
            return []
        url = f"https://raw.githubusercontent.com/{self.github_repo}/main/CHANGELOG.md"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=4.0, read=6.0, write=4.0, pool=4.0),
                follow_redirects=True,
                headers={"User-Agent": _GITHUB_API_HEADERS["User-Agent"]},
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
                bullets = _parse_changelog_markdown(resp.text, version, max_items=4)
                return bullets or _parse_changelog_markdown(resp.text, None, max_items=4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("CHANGELOG.md fallback failed: %s", _format_http_error(exc))
            return []

    async def check_for_updates(self, force: bool = False) -> dict[str, Any]:
        """Query GitHub Releases API (with mirror fallback) for the latest ORBIT version."""
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

        release, used_mirror, no_rel_msg, err_detail = await self._fetch_release_info()
        if no_rel_msg:
            self._last_check_ts = now
            self._state.update(
                {
                    "status": "up_to_date",
                    "latest_version": self.current_version,
                    "update_available": False,
                    "release_name": f"ORBIT v{self.current_version}",
                    "release_notes": no_rel_msg,
                    "html_url": f"https://github.com/{self.github_repo}/releases",
                    "error": None,
                }
            )
            self._emit()
            return self.get_state()

        if release is None:
            self._state.update(
                {
                    "status": "error",
                    "error": f"Could not reach GitHub Releases: {err_detail or 'Unknown network error'}",
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
        raw_sha256 = str(installer.get("sha256") or "").strip().lower() if installer else ""
        asset_sha256 = raw_sha256 if _SHA256_HEX_RE.match(raw_sha256) else None

        browser_url = (
            str(installer.get("browser_download_url") or "").strip() or None
            if installer
            else None
        )
        mirror_url = (
            (str(installer.get("mirror_download_url") or "").strip() or None)
            if installer
            else None
        ) or self._build_mirror_download_url(asset_name)

        if used_mirror:
            download_url = mirror_url or browser_url
            fallback_url = browser_url if mirror_url and browser_url != mirror_url else None
        else:
            download_url = browser_url or mirror_url
            fallback_url = mirror_url if mirror_url and mirror_url != download_url else None

        existing_path: str | None = None
        if asset_name and asset_size > 0:
            local_name = _local_installer_filename(asset_name, latest_clean)
            candidate = self._safe_target_path(local_name)
            if (
                candidate.is_file()
                and candidate.stat().st_size == asset_size
                and (not asset_sha256 or _file_sha256(candidate) == asset_sha256)
            ):
                existing_path = str(candidate)

        next_status = "up_to_date"
        if has_update:
            next_status = "ready" if existing_path else "available"

        raw_changelog = release.get("changelog")
        bullets = (
            _extract_changelog_bullets("\n".join(str(x) for x in raw_changelog), max_items=4)
            if isinstance(raw_changelog, list)
            else []
        )
        if not bullets:
            bullets = _extract_changelog_bullets(str(release.get("body") or ""), max_items=4)
        if not bullets:
            bullets = await self._fetch_changelog_fallback(latest_clean)

        clean_notes = "\n".join(f"- {item}" for item in bullets) if bullets else None

        self._state.update(
            {
                "status": next_status,
                "latest_version": latest_clean,
                "update_available": has_update,
                "release_name": str(release.get("name") or f"ORBIT v{latest_clean}"),
                "release_notes": clean_notes,
                "changelog": bullets,
                "published_at": release.get("published_at"),
                "html_url": str(
                    release.get("html_url")
                    or f"https://github.com/{self.github_repo}/releases/latest"
                ),
                "asset_name": asset_name,
                "asset_size": asset_size,
                "asset_sha256": asset_sha256,
                "download_url": download_url,
                "fallback_download_url": fallback_url,
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
        if self._state.get("status") != "ready":
            self._state["status"] = (
                "downloading" if self._state.get("download_url") else "checking"
            )
            self._state["error"] = None
            self._emit()
        self._download_task = asyncio.create_task(self.download_update(auto_install=auto_install))
        return self._download_task

    async def _stream_installer_url(
        self,
        url: str,
        target: Path,
        part_path: Path,
        expected_size: int,
        expected_sha256: str | None,
    ) -> int:
        async with (
            httpx.AsyncClient(
                timeout=httpx.Timeout(connect=8.0, read=60.0, write=15.0, pool=15.0),
                follow_redirects=True,
                headers={"User-Agent": _GITHUB_API_HEADERS["User-Agent"]},
            ) as client,
            client.stream("GET", url) as resp,
        ):
            resp.raise_for_status()
            if not self._is_trusted_url(str(resp.url)):
                raise ValueError("Redirected to untrusted download host")
            ctype = (resp.headers.get("content-type") or "").lower()
            if "text/html" in ctype:
                raise ValueError("Download endpoint returned HTML instead of installer binary")

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

        final_size = part_path.stat().st_size
        if final_size <= 0:
            raise ValueError("Downloaded installer file is empty")
        if expected_size > 0 and final_size != expected_size:
            raise ValueError(f"Incomplete download ({final_size} of {expected_size} bytes)")
        if expected_sha256:
            actual_sha256 = _file_sha256(part_path)
            if actual_sha256 != expected_sha256:
                raise ValueError("Installer SHA-256 checksum mismatch")

        part_path.replace(target)
        return final_size

    async def download_update(self, auto_install: bool = True) -> dict[str, Any]:
        """Download the Windows installer from GitHub Releases (or mirror) and optionally launch it."""
        if not self._state.get("download_url") or not self._state.get("asset_name"):
            await self.check_for_updates(force=True)

        primary_url = self._state.get("download_url")
        fallback_url = self._state.get("fallback_download_url")
        asset_name = self._state.get("asset_name")
        latest_version = str(self._state.get("latest_version") or "")
        if not primary_url or not asset_name:
            self._state["status"] = "error"
            self._state["error"] = (
                self._state.get("error")
                or "No Windows installer (.exe / .msi) was found in the latest GitHub Release."
            )
            self._emit()
            return self.get_state()

        candidates: list[str] = []
        for candidate_url in (primary_url, fallback_url):
            if candidate_url and candidate_url not in candidates:
                candidates.append(candidate_url)

        trusted_urls = [u for u in candidates if self._is_trusted_url(u)]
        if not trusted_urls:
            self._state["status"] = "error"
            self._state["error"] = "Blocked untrusted update download URL."
            self._emit()
            return self.get_state()

        local_name = _local_installer_filename(str(asset_name), latest_version)
        target = self._safe_target_path(local_name)
        expected_size = int(self._state.get("asset_size") or 0)
        expected_sha256 = self._state.get("asset_sha256")
        if (
            target.is_file()
            and expected_size > 0
            and target.stat().st_size == expected_size
            and (not expected_sha256 or _file_sha256(target) == expected_sha256)
        ):
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

        last_err: str | None = None
        for idx, url in enumerate(trusted_urls):
            try:
                final_size = await self._stream_installer_url(
                    url=url,
                    target=target,
                    part_path=part_path,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                )
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
                last_err = _format_http_error(exc)
                if idx + 1 < len(trusted_urls):
                    logger.warning(
                        "Installer download failed from %s (%s); trying fallback mirror.",
                        url,
                        last_err,
                    )
                else:
                    logger.error("Failed downloading update installer: %s", last_err)

        self._state.update(
            {
                "status": "error",
                "error": f"Download failed: {last_err or 'Unknown download error'}",
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
                cmd = (
                    ["msiexec", "/i", str(target)]
                    if ext == ".msi"
                    else [str(target), "/P", "/UPDATE", "/R"]
                )
                detached_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
                )
                breakaway_flag = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
                try:
                    subprocess.Popen(
                        cmd,
                        close_fds=True,
                        creationflags=detached_flags | breakaway_flag,
                    )
                except OSError:
                    try:
                        subprocess.Popen(cmd, close_fds=True, creationflags=detached_flags)
                    except OSError:
                        if ext == ".msi":
                            raise
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
