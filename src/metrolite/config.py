"""Configuration loader for MetroLite using pydantic-settings."""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

_config_yaml_var: ContextVar[Path | None] = ContextVar("_config_yaml_var", default=None)


def expand_path(val: str | Path) -> Path:
    """Expand environment variables (e.g. %LOCALAPPDATA%) and resolve path."""
    raw = str(val)
    expanded = os.path.expandvars(raw)
    if "%LOCALAPPDATA%" in expanded:
        fallback = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        expanded = expanded.replace("%LOCALAPPDATA%", fallback)
    return Path(expanded).resolve()


class AppConfig(BaseModel):
    name: str = "MetroLite"
    data_dir: Path = Field(default_factory=lambda: expand_path("%LOCALAPPDATA%\\metrolite"))
    downloads_dir: Path = Field(
        default_factory=lambda: expand_path("%LOCALAPPDATA%\\metrolite\\downloads")
    )
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None
    ram_budget_active_mb: int = 185
    ram_budget_tray_mb: int = 60

    @field_validator("data_dir", "downloads_dir", mode="before")
    @classmethod
    def _resolve_paths(cls, v: Any) -> Path:
        return expand_path(v)

    @field_validator("cookies_file", mode="before")
    @classmethod
    def _resolve_cookies_path(cls, v: Any) -> Path | None:
        return expand_path(v) if v else None


class AudioConfig(BaseModel):
    quality: str = "high"
    format_selector: str = "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio*/best"
    gapless: bool = True
    normalize_volume: bool = False
    default_volume: int = 80
    mpv_demuxer_max_mb: int = 6
    mpv_demuxer_back_mb: int = 2
    stream_url_ttl_s: int = 18000
    cookies_from_browser: str | None = None
    cookies_file: Path | None = None

    @field_validator("cookies_file", mode="before")
    @classmethod
    def _resolve_cookies_path(cls, v: Any) -> Path | None:
        return expand_path(v) if v else None


class DownloadsConfig(BaseModel):
    preferred_ext: str = "opus"
    embed_metadata: bool = True
    embed_thumbnail: bool = True
    max_concurrent: int = 2
    cookies_from_browser: str | None = None
    cookies_file: Path | None = None

    @field_validator("cookies_file", mode="before")
    @classmethod
    def _resolve_cookies_path(cls, v: Any) -> Path | None:
        return expand_path(v) if v else None


class QueueConfig(BaseModel):
    autoplay_radio: bool = True
    history_limit: int = 500


class HotkeysConfig(BaseModel):
    enabled: bool = True
    play_pause: str = "media_play_pause"
    next_track: str = "media_next"
    prev_track: str = "media_previous"


class BridgeConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8766


class UIConfig(BaseModel):
    theme: str = "dark"
    close_to_tray: bool = True
    destroy_webview_on_tray: bool = True
    thumbnail_cache_mb: int = 40


class YTMConfig(BaseModel):
    cookies_file: Path | None = None

    @field_validator("cookies_file", mode="before")
    @classmethod
    def _resolve_cookies(cls, v: Any) -> Path | None:
        return expand_path(v) if v else None


class UpdatesConfig(BaseModel):
    enabled: bool = True
    github_repo: str = "jainnaitk/ORBIT"
    current_version: str = "1.2.0"
    check_on_startup: bool = True
    mirror_base_url: str | None = "https://orbit-music.pages.dev"


def find_default_config_path() -> Path:
    """Find the project config.yaml file if it exists."""
    cwd_config = Path.cwd() / "config.yaml"
    if cwd_config.is_file():
        return cwd_config.resolve()
    repo_config = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if repo_config.is_file():
        return repo_config.resolve()
    return cwd_config


class Settings(BaseSettings):
    """Application settings loaded from config.yaml."""

    app: AppConfig = Field(default_factory=AppConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    downloads: DownloadsConfig = Field(default_factory=DownloadsConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    hotkeys: HotkeysConfig = Field(default_factory=HotkeysConfig)
    bridge: BridgeConfig = Field(default_factory=BridgeConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    ytm: YTMConfig = Field(default_factory=YTMConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)

    model_config = SettingsConfigDict(
        extra="ignore",
        env_prefix="METROLITE_",
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        y_path = _config_yaml_var.get() or find_default_config_path()
        if y_path and Path(y_path).is_file():
            return (
                init_settings,
                YamlConfigSettingsSource(settings_cls, yaml_file=Path(y_path)),
                env_settings,
            )
        return (init_settings, env_settings)


def load_config(config_path: Path | str | None = None) -> Settings:
    """Load configuration from config_path or default config.yaml."""
    if config_path is not None:
        token = _config_yaml_var.set(Path(config_path).resolve())
        try:
            return Settings()
        finally:
            _config_yaml_var.reset(token)
    return Settings()


_config_instance: Settings | None = None


def get_config(config_path: Path | str | None = None, reload: bool = False) -> Settings:
    """Get or reload the global configuration instance."""
    global _config_instance
    if _config_instance is None or reload or config_path is not None:
        _config_instance = load_config(config_path)
    return _config_instance
