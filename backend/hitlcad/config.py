"""Runtime settings, read from ``HITLCAD_*`` environment variables or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from hitlcad.core.structure import Tolerances


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HITLCAD_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".hitlcad")

    # CAD backends
    backends: list[str] = Field(default_factory=lambda: ["build123d", "blender"])
    blender_binary: str = "blender"
    build_timeout_s: float = 180.0
    build_memory_mb: int = 4096
    build_workers: int = Field(default=4, ge=1, description="Part files built in parallel child processes")
    mesh_tolerance_mm: float = Field(default=0.01, gt=0, description="Chordal deviation of exported meshes")
    mesh_angular_tolerance: float = Field(default=0.1, gt=0, description="Angular deviation of exported meshes, rad")

    # Verification defaults; a design's `verification` overrides them
    verify_position_mm: float = Field(default=0.01, gt=0)
    verify_angle_deg: float = Field(default=0.05, gt=0)
    verify_motion_samples: int = Field(default=24, ge=2)
    overlap_mm3: float = Field(
        default=1e-3, gt=0, description="Bodies sharing less than this touch; they do not overlap"
    )
    wall_samples: int = Field(default=4000, ge=100, description="Surface points probed for wall thickness")

    # What agents are told
    explore_limit: int = Field(default=25, ge=5, description="Results listed in full per answer; the rest are counted")
    section_size: int = Field(default=640, ge=200, description="Pixels of a section view")
    loaded_builds: int = Field(default=8, ge=1, description="Built revisions kept in memory for picks and pins")

    # Carrying picks and pins to a new build by geometry
    anchor_reach: float = Field(
        default=0.1, gt=0, description="How far a face may move, × its part's bounding-box diagonal"
    )
    anchor_tie: float = Field(
        default=0.1, gt=0, description="A runner-up this close, × the reach, makes a match ambiguous"
    )
    anchor_aligned_deg: float = Field(default=1.0, gt=0, description="How far a face may turn and still be the same")

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
    def tolerances(self) -> Tolerances:
        return Tolerances(position_mm=self.verify_position_mm, angle_deg=self.verify_angle_deg,
                          motion_samples=self.verify_motion_samples)  # fmt: skip

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def designs_dir(self) -> Path:
        return self.data_dir / "designs"

    @property
    def folders_file(self) -> Path:
        return self.data_dir / "folders.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
