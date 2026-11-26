"""Unit tests for GitHub auto-updater, mirror fallbacks, and bridge update routes."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

import metrolite.updates as updates_mod
from metrolite.bridge.server import BridgeController, create_app
from metrolite.updates import (
    UpdateManager,
    _extract_changelog_bullets,
    _is_trusted_download_url,
    _local_installer_filename,
    _parse_changelog_markdown,
    _pick_windows_installer,
    is_newer_version,
    parse_version,
)


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        *,
        json_data: Any = None,
        text_data: str = "",
        body_bytes: bytes = b"",
        headers: dict[str, str] | None = None,
        url: str = "https://example.com",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text_data
        self._body_bytes = body_bytes
        self.headers = httpx.Headers(headers or {})
        self.url = httpx.URL(url)
        self.request = httpx.Request("GET", url)

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    async def aiter_bytes(self, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        for i in range(0, len(self._body_bytes), chunk_size):
            yield self._body_bytes[i : i + chunk_size]


def _install_fake_httpx(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> None:
    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def _dispatch(self, method: str, url: str) -> _FakeResponse:
            handler = routes.get(f"{method} {url}") or routes.get(url)
            if handler is None:
                raise AssertionError(f"Unexpected HTTP {method} request to {url}")
            outcome = handler() if callable(handler) else handler
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            return await self._dispatch("GET", url)

        async def head(self, url: str, **kwargs: Any) -> _FakeResponse:
            return await self._dispatch("HEAD", url)

        @asynccontextmanager
        async def stream(self, method: str, url: str, **kwargs: Any) -> AsyncIterator[_FakeResponse]:
            yield await self._dispatch(method, url)

    monkeypatch.setattr(updates_mod.httpx, "AsyncClient", _FakeAsyncClient)


def test_version_parsing_and_comparison() -> None:
    assert parse_version("v0.1.0") == (0, 1, 0)
    assert parse_version("1.2.3") == (1, 2, 3)
    assert is_newer_version("v0.2.0", "0.1.0") is True
    assert is_newer_version("0.1.0", "0.1.0") is False
    assert is_newer_version("0.0.9", "0.1.0") is False


def test_trusted_download_url_validation() -> None:
    assert _is_trusted_download_url(
        "https://github.com/jainnaitk/ORBIT/releases/download/v1.0.0/ORBIT-Setup.exe"
    )
    assert _is_trusted_download_url(
        "https://objects.githubusercontent.com/github-production-release-asset/foo.exe"
    )
    assert _is_trusted_download_url(
        "https://orbit-music.pages.dev/api/download?asset=ORBIT-Setup.exe"
    )
    assert not _is_trusted_download_url("http://github.com/jainnaitk/ORBIT/foo.exe")
    assert not _is_trusted_download_url("https://evil.example.com/ORBIT-Setup.exe")
    assert not _is_trusted_download_url("https://evil.pages.dev/ORBIT-Setup.exe")
    assert not _is_trusted_download_url("https://fake-orbit-music.pages.dev/ORBIT-Setup.exe")


def test_pick_windows_installer_prefers_versioned_setup_exe() -> None:
    assets = [
        {"name": "ORBIT_1.0.0.AppImage", "browser_download_url": "https://github.com/a.AppImage"},
        {"name": "ORBIT_1.0.0_x64_en-US.msi", "browser_download_url": "https://github.com/a.msi"},
        {"name": "ORBIT-Setup.exe", "browser_download_url": "https://github.com/ORBIT-Setup.exe"},
        {"name": "ORBIT_1.0.0_x64-setup.exe", "browser_download_url": "https://github.com/a.exe"},
    ]
    picked = _pick_windows_installer(assets)
    assert picked is not None
    assert picked["name"] == "ORBIT_1.0.0_x64-setup.exe"


def test_local_installer_filename_versions_generic_setup() -> None:
    assert _local_installer_filename("ORBIT-Setup.exe", "1.2.0") == "ORBIT-v1.2.0-Setup.exe"
    assert (
        _local_installer_filename("ORBIT_1.2.0_x64-setup.exe", "1.2.0")
        == "ORBIT_1.2.0_x64-setup.exe"
    )


@pytest.mark.asyncio
async def test_check_for_updates_falls_back_to_mirror_on_github_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_httpx(
        monkeypatch,
        {
            "GET https://api.github.com/repos/jainnaitk/ORBIT/releases/latest": httpx.ConnectTimeout(
                ""
            ),
            "GET https://orbit-music.pages.dev/api/release": _FakeResponse(
                200,
                headers={"content-type": "application/json; charset=utf-8"},
                url="https://orbit-music.pages.dev/api/release",
                json_data={
                    "tag_name": "v1.2.0",
                    "name": "ORBIT v1.2.0",
                    "body": "- Synced lyrics\n- Tray popup",
                    "html_url": "https://github.com/jainnaitk/ORBIT/releases/tag/v1.2.0",
                    "assets": [
                        {
                            "name": "ORBIT_1.2.0_x64-setup.exe",
                            "size": 69961717,
                            "browser_download_url": "https://github.com/jainnaitk/ORBIT/releases/download/v1.2.0/ORBIT_1.2.0_x64-setup.exe",
                            "mirror_download_url": "https://orbit-music.pages.dev/api/download?asset=ORBIT_1.2.0_x64-setup.exe",
                        }
                    ],
                },
            ),
        },
    )

    mgr = UpdateManager(
        github_repo="jainnaitk/ORBIT",
        current_version="1.1.1",
        updates_dir=tmp_path / "updates",
        mirror_base_url="https://orbit-music.pages.dev",
    )
    state = await mgr.check_for_updates(force=True)
    assert state["status"] == "available"
    assert state["update_available"] is True
    assert state["latest_version"] == "1.2.0"
    assert state["asset_name"] == "ORBIT_1.2.0_x64-setup.exe"
    assert (
        state["download_url"]
        == "https://orbit-music.pages.dev/api/download?asset=ORBIT_1.2.0_x64-setup.exe"
    )
    assert (
        state["fallback_download_url"]
        == "https://github.com/jainnaitk/ORBIT/releases/download/v1.2.0/ORBIT_1.2.0_x64-setup.exe"
    )
    assert state["error"] is None


@pytest.mark.asyncio
async def test_check_for_updates_raw_and_mirror_head_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_httpx(
        monkeypatch,
        {
            "GET https://api.github.com/repos/jainnaitk/ORBIT/releases/latest": httpx.ConnectTimeout(
                ""
            ),
            "GET https://orbit-music.pages.dev/api/release": _FakeResponse(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text_data="<html>spa fallback</html>",
                url="https://orbit-music.pages.dev/api/release",
            ),
            "GET https://raw.githubusercontent.com/jainnaitk/ORBIT/main/config.yaml": _FakeResponse(
                200,
                headers={"content-type": "text/plain; charset=utf-8"},
                text_data="updates:\n  current_version: '1.2.0'\n",
                url="https://raw.githubusercontent.com/jainnaitk/ORBIT/main/config.yaml",
            ),
            "HEAD https://orbit-music.pages.dev/api/download": _FakeResponse(
                200,
                headers={
                    "content-type": "application/octet-stream",
                    "content-length": "69961717",
                },
                url="https://orbit-music.pages.dev/api/download",
            ),
            "GET https://raw.githubusercontent.com/jainnaitk/ORBIT/main/CHANGELOG.md": _FakeResponse(
                200,
                headers={"content-type": "text/plain; charset=utf-8"},
                text_data=(
                    "# ORBIT Changelog\n\n"
                    "## v1.2.0\n"
                    "- Synced lyrics with romanization\n"
                    "- Tray album popup controls\n"
                    "- Liquid-glass track loader\n"
                    "- Faster artist library loading\n"
                ),
                url="https://raw.githubusercontent.com/jainnaitk/ORBIT/main/CHANGELOG.md",
            ),
        },
    )

    mgr = UpdateManager(
        github_repo="jainnaitk/ORBIT",
        current_version="1.1.1",
        updates_dir=tmp_path / "updates",
        mirror_base_url="https://orbit-music.pages.dev",
    )
    state = await mgr.check_for_updates(force=True)
    assert state["status"] == "available"
    assert state["update_available"] is True
    assert state["latest_version"] == "1.2.0"
    assert state["asset_name"] == "ORBIT-Setup.exe"
    assert state["asset_size"] == 69961717
    assert state["download_url"] == "https://orbit-music.pages.dev/api/download"
    assert len(state["changelog"]) == 4
    assert state["error"] is None


def test_changelog_strips_full_changelog_link_and_parses_4_bullets() -> None:
    body = "**Full Changelog**: https://github.com/jainnaitk/ORBIT/compare/v1.1.1...v1.2.0"
    assert _extract_changelog_bullets(body, max_items=4) == []

    md = (
        "# ORBIT Changelog\n\n"
        "## v1.2.0\n"
        "- Bullet one\n"
        "- Bullet two\n"
        "- Bullet three\n"
        "- Bullet four\n"
        "- Bullet five\n"
    )
    assert _parse_changelog_markdown(md, "1.2.0", max_items=4) == [
        "Bullet one",
        "Bullet two",
        "Bullet three",
        "Bullet four",
    ]


@pytest.mark.asyncio
async def test_check_for_updates_replaces_compare_link_with_changelog_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_httpx(
        monkeypatch,
        {
            "GET https://api.github.com/repos/jainnaitk/ORBIT/releases/latest": _FakeResponse(
                200,
                headers={"content-type": "application/json"},
                url="https://api.github.com/repos/jainnaitk/ORBIT/releases/latest",
                json_data={
                    "tag_name": "v1.2.0",
                    "name": "v1.2.0",
                    "body": "**Full Changelog**: https://github.com/jainnaitk/ORBIT/compare/v1.1.1...v1.2.0",
                    "assets": [
                        {
                            "name": "ORBIT_1.2.0_x64-setup.exe",
                            "size": 69961717,
                            "browser_download_url": "https://github.com/jainnaitk/ORBIT/releases/download/v1.2.0/ORBIT_1.2.0_x64-setup.exe",
                        }
                    ],
                },
            ),
            "GET https://raw.githubusercontent.com/jainnaitk/ORBIT/main/CHANGELOG.md": _FakeResponse(
                200,
                headers={"content-type": "text/plain"},
                text_data=(
                    "## v1.2.0\n"
                    "- Synced word and line lyrics\n"
                    "- Custom tray popup controls\n"
                    "- Liquid-glass track loader\n"
                    "- Faster artist page loading\n"
                ),
                url="https://raw.githubusercontent.com/jainnaitk/ORBIT/main/CHANGELOG.md",
            ),
        },
    )

    mgr = UpdateManager(
        github_repo="jainnaitk/ORBIT",
        current_version="1.1.1",
        updates_dir=tmp_path / "updates",
        mirror_base_url="https://orbit-music.pages.dev",
    )
    state = await mgr.check_for_updates(force=True)
    assert state["changelog"] == [
        "Synced word and line lyrics",
        "Custom tray popup controls",
        "Liquid-glass track loader",
        "Faster artist page loading",
    ]
    assert "github.com" not in (state["release_notes"] or "")


@pytest.mark.asyncio
async def test_check_for_updates_formats_empty_connect_timeout_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_httpx(
        monkeypatch,
        {
            "GET https://api.github.com/repos/jainnaitk/ORBIT/releases/latest": httpx.ConnectTimeout(
                ""
            ),
            "GET https://orbit-music.pages.dev/api/release": httpx.ConnectTimeout(""),
            "GET https://raw.githubusercontent.com/jainnaitk/ORBIT/main/config.yaml": httpx.ConnectTimeout(
                ""
            ),
            "HEAD https://orbit-music.pages.dev/api/download": httpx.ConnectTimeout(""),
        },
    )

    mgr = UpdateManager(
        github_repo="jainnaitk/ORBIT",
        current_version="1.1.1",
        updates_dir=tmp_path / "updates",
        mirror_base_url="https://orbit-music.pages.dev",
    )
    state = await mgr.check_for_updates(force=True)
    assert state["status"] == "error"
    assert state["error"] == "Could not reach GitHub Releases: Connection timed out"


@pytest.mark.asyncio
async def test_download_update_falls_back_to_mirror_and_verifies_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"MZ" + (b"ORBIT-TEST-INSTALLER" * 64)
    digest = hashlib.sha256(payload).hexdigest()
    primary_url = "https://github.com/jainnaitk/ORBIT/releases/download/v1.2.0/ORBIT-Setup.exe"
    mirror_url = "https://orbit-music.pages.dev/api/download?asset=ORBIT-Setup.exe"

    _install_fake_httpx(
        monkeypatch,
        {
            f"GET {primary_url}": httpx.ConnectTimeout(""),
            f"GET {mirror_url}": _FakeResponse(
                200,
                body_bytes=payload,
                headers={
                    "content-type": "application/octet-stream",
                    "content-length": str(len(payload)),
                },
                url=mirror_url,
            ),
        },
    )

    mgr = UpdateManager(
        github_repo="jainnaitk/ORBIT",
        current_version="1.1.1",
        updates_dir=tmp_path / "updates",
        mirror_base_url="https://orbit-music.pages.dev",
    )
    mgr._state.update(
        {
            "status": "available",
            "latest_version": "1.2.0",
            "update_available": True,
            "asset_name": "ORBIT-Setup.exe",
            "asset_size": len(payload),
            "asset_sha256": digest,
            "download_url": primary_url,
            "fallback_download_url": mirror_url,
        }
    )

    state = await mgr.download_update(auto_install=False)
    assert state["status"] == "ready"
    assert state["error"] is None
    saved = Path(state["downloaded_path"])
    assert saved.is_file()
    assert saved.name == "ORBIT-v1.2.0-Setup.exe"
    assert saved.read_bytes() == payload


@pytest.mark.asyncio
async def test_download_update_rejects_sha_mismatch_and_cleans_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"MZ-corrupted-installer"
    mirror_url = "https://orbit-music.pages.dev/api/download?asset=ORBIT-Setup.exe"

    _install_fake_httpx(
        monkeypatch,
        {
            f"GET {mirror_url}": _FakeResponse(
                200,
                body_bytes=payload,
                headers={
                    "content-type": "application/octet-stream",
                    "content-length": str(len(payload)),
                },
                url=mirror_url,
            ),
        },
    )

    updates_dir = tmp_path / "updates"
    mgr = UpdateManager(
        github_repo="jainnaitk/ORBIT",
        current_version="1.1.1",
        updates_dir=updates_dir,
        mirror_base_url="https://orbit-music.pages.dev",
    )
    mgr._state.update(
        {
            "status": "available",
            "latest_version": "1.2.0",
            "update_available": True,
            "asset_name": "ORBIT-Setup.exe",
            "asset_size": len(payload),
            "asset_sha256": "0" * 64,
            "download_url": mirror_url,
            "fallback_download_url": None,
        }
    )

    state = await mgr.download_update(auto_install=False)
    assert state["status"] == "error"
    assert "SHA-256" in (state["error"] or "")
    assert list(updates_dir.glob("*.part")) == []
    assert not (updates_dir / "ORBIT-v1.2.0-Setup.exe").exists()


def test_launch_installer_passes_in_place_update_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    updates_dir = tmp_path / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    installer = updates_dir / "ORBIT_1.2.2_x64-setup.exe"
    installer.write_bytes(b"MZ-test")

    mgr = UpdateManager(
        github_repo="jainnaitk/ORBIT",
        current_version="1.2.1",
        updates_dir=updates_dir,
    )
    mgr._state["downloaded_path"] = str(installer)

    captured: list[list[str]] = []

    def _fake_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured.append(cmd)
        return MagicMock()

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(updates_mod.sys, "platform", "win32")
    monkeypatch.setattr(updates_mod.subprocess, "Popen", _fake_popen)

    state = mgr.launch_installer()
    assert state["status"] == "launched"
    assert captured == [[str(installer.resolve()), "/P", "/UPDATE", "/R"]]


def test_bridge_update_endpoints(tmp_path: Path) -> None:
    updater = UpdateManager(
        github_repo="jainnaitk/ORBIT",
        current_version="0.1.0",
        updates_dir=tmp_path / "updates",
    )
    updater.check_for_updates = AsyncMock(  # type: ignore[method-assign]
        return_value={
            **updater.get_state(),
            "status": "available",
            "latest_version": "1.0.0",
            "update_available": True,
            "asset_name": "ORBIT_1.0.0_x64-setup.exe",
        }
    )

    mock_player = MagicMock()
    mock_player.state = "stopped"
    mock_player.time_pos = 0.0
    mock_player.duration = 0.0
    mock_player.volume = 80
    mock_player.current_track = None
    mock_player.next_track = None

    ctrl = BridgeController(
        player=mock_player,
        db_path=tmp_path / "test.db",
        updater=updater,
    )
    app = create_app(controller=ctrl, token="test-tok")
    headers = {"X-Bridge-Token": "test-tok"}

    with TestClient(app) as client:
        res_status = client.get("/updates/status", headers=headers)
        assert res_status.status_code == 200
        assert res_status.json()["repo"] == "jainnaitk/ORBIT"

        res_check = client.post("/updates/check", headers=headers)
        assert res_check.status_code == 200
        body = res_check.json()
        assert body["update_available"] is True
        assert body["latest_version"] == "1.0.0"
