"""Design folders: a design is any directory holding ``design.json`` and its model source.

    my-hook/
      design.json   {"hitlcad": 1, "name", "backend", "source": "model.py", "parameters": [...], "brief": {...}}
      model.py      defines build(p), reading every tunable value from p
      build/        outputs of the latest successful build (model.step, mesh.stl, …); git-ignored

design.json and the model source are the source of truth, edited by people and coding agents
and committed to git. build/ is regenerated; the full revision history lives in hitlcad's data
directory.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from pydantic import Field

from hitlcad.core.types import BuildReport, BuildSpec, DesignBrief, Manufacturing, Model, ParamSpec, Requirement

DESIGN_FILE = "design.json"
BUILD_DIR = "build"
BUILD_MARKER = "# hitlcad build outputs: regenerated on every build, never committed\n"
EXPORTED_ROLES = frozenset({"exchange", "mesh", "native", "objects", "part_exchange", "part_mesh", "preview", "log"})


class DesignFile(Model):
    hitlcad: Literal[1] = 1
    name: str
    backend: str
    source: str = Field(default="model.py", description="Model source file, relative to the design folder")
    parameters: list[ParamSpec] = Field(default_factory=list)
    brief: DesignBrief | None = None
    manufacturing: Manufacturing | None = Field(
        default=None, description='How it will be made, e.g. {"process": "fdm"}'
    )
    requirements: list[Requirement] = Field(default_factory=list, description="What every build is verified against")


class DesignFolder:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    @property
    def design_file(self) -> Path:
        return self.path / DESIGN_FILE

    def exists(self) -> bool:
        return self.design_file.is_file()

    def load(self) -> DesignFile:
        return DesignFile.model_validate_json(self.design_file.read_text(encoding="utf-8"))

    def source_path(self, design: DesignFile | None = None) -> Path:
        design = design or self.load()
        src = (self.path / design.source).resolve()
        if self.path not in src.parents:
            raise ValueError(f"source {design.source!r} must be inside the design folder")
        return src

    def read_source(self, design: DesignFile | None = None) -> str:
        return self.source_path(design).read_text(encoding="utf-8")

    def save(self, design: DesignFile) -> None:
        text = json.dumps(design.model_dump(mode="json", exclude_none=True), indent=2, ensure_ascii=False) + "\n"
        tmp = self.design_file.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.design_file)  # atomic: watchers never see a half-written file

    def spec(self, design: DesignFile | None = None) -> BuildSpec:
        """What a build of the folder's current files depends on (not the brief)."""
        design = design or self.load()
        return BuildSpec(backend=design.backend, source=self.read_source(design), parameters=design.parameters,
                         manufacturing=design.manufacturing, requirements=design.requirements)  # fmt: skip

    @property
    def build_dir(self) -> Path:
        return self.path / BUILD_DIR

    def export_build(self, revision_dir: Path, report: BuildReport) -> list[str]:
        """Replace build/ with a build's outputs, so they can be opened from the workspace.
        Returns the files written. A build/ folder hitlcad did not create is left alone."""
        target, marker = self.build_dir, self.build_dir / ".gitignore"
        if target.exists() and not (marker.is_file() and marker.read_text(encoding="utf-8").startswith(BUILD_MARKER)):
            raise FileExistsError(f"{target} exists and was not created by hitlcad; not overwriting it")
        staging, old = self.path / f".{BUILD_DIR}.tmp", self.path / f".{BUILD_DIR}.old"
        for leftover in (staging, old):
            shutil.rmtree(leftover, ignore_errors=True)
        staging.mkdir()
        written = []
        for artifact in report.artifacts:
            if artifact.role in EXPORTED_ROLES and (revision_dir / artifact.path).is_file():
                shutil.copy2(revision_dir / artifact.path, staging / artifact.name)
                written.append(artifact.name)
        (staging / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (staging / ".gitignore").write_text(BUILD_MARKER + "*\n", encoding="utf-8")
        if target.exists():
            target.rename(old)
        staging.rename(target)  # swap, so build/ is never half-written
        shutil.rmtree(old, ignore_errors=True)
        return [*written, "report.json"]

    def scaffold(self, name: str, backend: str) -> list[str]:
        """Create design.json if missing; the model source comes from the agent. Returns the files written."""
        self.path.mkdir(parents=True, exist_ok=True)
        if self.exists():
            return []
        self.save(DesignFile(name=name, backend=backend))
        return [DESIGN_FILE]
