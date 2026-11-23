"""Unit tests for GitHub auto-updater and bridge update routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from metrolite.bridge.server import BridgeController, create_app
from metrolite.updates import (
    UpdateManager,
    _is_trusted_download_url,
    _pick_windows_installer,
    is_newer_version,
    parse_version,
)


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
    assert not _is_trusted_download_url("http://github.com/jainnaitk/ORBIT/foo.exe")
    assert not _is_trusted_download_url("https://evil.example.com/ORBIT-Setup.exe")


def test_pick_windows_installer_prefers_setup_exe() -> None:
    assets = [
        {"name": "ORBIT_1.0.0.AppImage", "browser_download_url": "https://github.com/a.AppImage"},
        {"name": "ORBIT_1.0.0_x64_en-US.msi", "browser_download_url": "https://github.com/a.msi"},
        {"name": "ORBIT_1.0.0_x64-setup.exe", "browser_download_url": "https://github.com/a.exe"},
    ]
    picked = _pick_windows_installer(assets)
    assert picked is not None
    assert picked["name"] == "ORBIT_1.0.0_x64-setup.exe"


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
