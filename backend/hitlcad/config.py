"""Runtime settings, read from ``HITLCAD_*`` environment variables or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HITLCAD_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".hitlcad")

    # CAD backends
    backends: list[str] = Field(default_factory=lambda: ["build123d", "blender"])
    blender_binary: str = "blender"
    build_timeout_s: float = 180.0
    build_memory_mb: int = 4096

    # Preview rendering
    render_size: int = 512

    public_url: str | None = Field(default=None, description="Base URL agents and hooks use to reach this server")
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=list, description="Origins besides VS Code webviews that may call the API"
    )

    @property
    def base_url(self) -> str:
        return (self.public_url or f"http://{self.host}:{self.port}").rstrip("/")

    @property
    def mcp_url(self) -> str:
        return self.base_url + "/mcp"

    @property
    def designs_dir(self) -> Path:
        return self.data_dir / "designs"

    @property
    def folders_file(self) -> Path:
        return self.data_dir / "folders.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
