"""Design folders: a design is any directory holding ``design.json`` and its code.

    gearbox/
      design.json     structure, parameters, configurations, requirements, brief (format 2)
      interfaces.py   mating values, one function each: def lid_bolt_xy(p, i)
      parts/*.py      def build(p, i) -> {part: (shape, connectors)}, in each part's own frame
      lib/*.py        helpers shared by part files
      vendor/         vendor files (STEP), checksummed into each build
      assembly.py     def build(p, i) -> {part or component: [placement, …]}
      build/          outputs of the latest successful build; git-ignored

design.json and the code are the source of truth, edited by people and coding agents and committed
to git. build/ is regenerated; the full revision history lives in hitlcad's data directory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from hitlcad.core.design import DesignSpec
from hitlcad.core.report import BuildReport, BuildSpec
from hitlcad.core.structure import Tolerances

DESIGN_FILE = "design.json"
BUILD_DIR = "build"
BUILD_MARKER = "# hitlcad build outputs: regenerated on every build, never committed\n"
CODE = ("interfaces.py", "assembly.py")
CODE_DIRS = ("parts", "lib")
ASSET_DIRS = ("vendor",)


class DesignFolder:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    @property
    def design_file(self) -> Path:
        return self.path / DESIGN_FILE

    def exists(self) -> bool:
        return self.design_file.is_file()

    def load(self) -> DesignSpec:
        return DesignSpec.model_validate_json(self.design_file.read_text(encoding="utf-8"))

    def save(self, design: DesignSpec) -> None:
        text = json.dumps(design.model_dump(mode="json", exclude_defaults=True) | {"hitlcad": design.hitlcad},
                          indent=2, ensure_ascii=False) + "\n"  # fmt: skip
        tmp = self.design_file.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.design_file)  # atomic: watchers never see a half-written file

    def sources(self) -> dict[str, str]:
        """The design's code, by path relative to the folder."""
        out = {name: (self.path / name).read_text(encoding="utf-8") for name in CODE if (self.path / name).is_file()}
        for folder in CODE_DIRS:
            for file in sorted((self.path / folder).rglob("*.py")):
                if "__pycache__" not in file.parts:
                    out[file.relative_to(self.path).as_posix()] = file.read_text(encoding="utf-8")
        return out

    def assets(self) -> dict[str, str]:
        return {f.relative_to(self.path).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                for folder in ASSET_DIRS for f in sorted((self.path / folder).rglob("*")) if f.is_file()}  # fmt: skip

    def spec(
        self, defaults: Tolerances, configuration: str | None = None, *, _stack: tuple[Path, ...] = ()
    ) -> BuildSpec:
        """What a build of the folder's current files depends on, with its components' specs."""
        if self.path in _stack:
            chain = " -> ".join(str(p) for p in (*_stack, self.path))
            raise ValueError(f"designs use each other in a cycle: {chain}")
        design = self.load()
        components = {}
        for name, use in design.components.items():
            child = DesignFolder(self.path / use.design)
            if not child.exists():
                raise ValueError(f"component {name}: no {DESIGN_FILE} in {child.path}")
            components[name] = child.spec(defaults, _stack=(*_stack, self.path))
        return BuildSpec(design=design, sources=self.sources(), assets=self.assets(), configuration=configuration,
                         tolerances=design.verification.resolve(defaults), components=components)  # fmt: skip

    def components(self) -> list[Path]:
        """The folders of the designs this one uses."""
        return [(self.path / use.design).resolve() for use in self.load().components.values()]

    @property
    def build_dir(self) -> Path:
        return self.path / BUILD_DIR

    def export_build(self, revision_dir: Path, report: BuildReport) -> list[str]:
        """Replace build/ with a build's outputs, so they can be opened from the workspace. Returns the files
        written. A build/ folder hitlcad did not create is left alone."""
        target, marker = self.build_dir, self.build_dir / ".gitignore"
        if target.exists() and not (marker.is_file() and marker.read_text(encoding="utf-8").startswith(BUILD_MARKER)):
            raise FileExistsError(f"{target} exists and was not created by hitlcad; not overwriting it")
        staging, old = self.path / f".{BUILD_DIR}.tmp", self.path / f".{BUILD_DIR}.old"
        for leftover in (staging, old):
            shutil.rmtree(leftover, ignore_errors=True)
        staging.mkdir()
        written = []
        for artifact in report.artifacts:
            if artifact.role != "source" and (revision_dir / artifact.path).is_file():  # the sources are here already
                (staging / artifact.path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(revision_dir / artifact.path, staging / artifact.path)
                written.append(artifact.path)
        (staging / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (staging / ".gitignore").write_text(BUILD_MARKER + "*\n", encoding="utf-8")
        if target.exists():
            target.rename(old)
        staging.rename(target)  # swap, so build/ is never half-written
        shutil.rmtree(old, ignore_errors=True)
        return [*written, "report.json"]

    def scaffold(self, name: str, backend: str) -> list[str]:
        """A new design: design.json, interfaces.py, assembly.py and an empty parts/ (the agent writes the
        structure and the code). Returns the files written; an existing design is left as it is."""
        if self.exists():
            return []
        self.path.mkdir(parents=True, exist_ok=True)
        written = []
        files = {
            "interfaces.py": INTERFACES_TEMPLATE,
            "assembly.py": ASSEMBLY_TEMPLATE,
            "parts/.gitkeep": "",
        }
        for rel, text in files.items():
            target = self.path / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                written.append(rel)
        self.save(DesignSpec(name=name, backend=backend))
        return [DESIGN_FILE, *written]


INTERFACES_TEMPLATE = '''"""Mating values: one function per value, def name(p, i). Parts and assembly.py read them as i.<name>."""
'''

ASSEMBLY_TEMPLATE = '''"""Where each part and component instance sits: def build(p, i) -> {name: [placement, …]}."""


def build(p, i):
    return {}
'''
