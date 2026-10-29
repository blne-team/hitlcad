"""Runtime settings, read from ``HITLCAD_*`` environment variables or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from hitlcad.core.structure import Tolerances

DEFAULT_BACKEND = "build123d"
MCP_PATH = "/mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HITLCAD_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".hitlcad")

    # CAD backends
    backends: list[str] = Field(default_factory=lambda: [DEFAULT_BACKEND, "blender"])
    blender_binary: str = "blender"
    build_timeout_s: float = Field(default=180.0, gt=0, description="Deadline of each job a build worker runs")
    build_memory_mb: int = Field(default=4096, gt=0, description="Memory a build worker may use, with everything it "
                                 "starts; one that ends a job holding more than half of it is replaced")  # fmt: skip
    workers: int = Field(default=4, ge=1, description="Build workers each backend keeps, each one design's, its part "
                         "files built in them in parallel: about 400 MB each for build123d, and what they export")  # fmt: skip
    worker_cache_mb: int = Field(default=256, ge=0, description="What a build worker keeps of what it exported, by "
                                 "the shape's content, so a part that comes out the same is not exported again")  # fmt: skip
    mesh_tolerance_mm: float = Field(default=0.01, gt=0, description="Chordal deviation of exported meshes")
    mesh_angular_tolerance: float = Field(default=0.1, gt=0, description="Angular deviation of exported meshes, rad")

    # Verification defaults; a design's `verification` overrides them
    verify_position_mm: float = Field(default=0.01, gt=0)
    verify_angle_deg: float = Field(default=0.05, gt=0)
    verify_motion_steps: int = Field(default=2000, ge=10)
    solver: str = Field(default="sqp", description="The block solver (`hitlcad_solvers`) motion settles what "
                        "an input moves in blocks with; empty: such motion is not swept, only checked as assembled")  # fmt: skip
    overlap_mm3: float = Field(
        default=1e-3, gt=0, description="Bodies sharing less than this touch; they do not overlap"
    )
    wall_samples: int = Field(default=4000, ge=100, description="Surface points probed for wall thickness")

    # What agents are told
    explore_limit: int = Field(default=25, ge=5, description="Results listed in full per answer; the rest are counted")
    section_size: int = Field(default=640, ge=200, description="Pixels of a section view")
    loaded_builds: int = Field(default=8, ge=1, description="Built revisions kept in memory for picks and pins")
    loaded_revisions: int = Field(default=32, ge=1, description="Revisions (spec and report) kept in memory")
    loaded_parts: int = Field(default=1024, ge=1, description="Part shapes (mesh, solid, B-rep features) kept in "
                              "memory across builds, by their files' content")  # fmt: skip

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
    render_timeout_s: float = Field(default=120.0, gt=0)

    public_url: str | None = Field(default=None, description="Base URL agents and hooks use to reach this server")
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=list, description="Origins besides VS Code webviews that may call the API"
    )

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host  # an IPv6 address
        return (self.public_url or f"http://{host}:{self.port}").rstrip("/")

    @property
    def mcp_url(self) -> str:
        return self.base_url + MCP_PATH

    @property
    def hosts(self) -> list[str]:
        """What requests may be addressed to (their Host header, IPv6 in brackets): this machine, and the URL
        agents are given."""
        public = urlsplit(self.base_url).hostname or ""
        return ["127.0.0.1", "localhost", "[::1]", f"[{public}]" if ":" in public else public]

    @property
    def tolerances(self) -> Tolerances:
        return Tolerances(position_mm=self.verify_position_mm, angle_deg=self.verify_angle_deg,
                          motion_steps=self.verify_motion_steps)  # fmt: skip

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


@lru_cache
def get_settings() -> Settings:
    return Settings()
