"""Design folders: a design is any directory holding ``design.json`` and its code.

    gearbox/
      design.json     structure, parameters, configurations, requirements, brief (format 2)
      interfaces.py   mating values, one function each: def lid_bolt_xy(p, i)
      parts/*.py      def build(p, i) -> {part: (shape, connectors)}, in each part's own frame
      lib/*.py        helpers shared by part files
      vendor/         vendor files (STEP), checksummed into each build
      references/     pictures requirement checks compare the design with, checksummed into each build
      assembly.py     def build(p, i) -> {part or component: [placement, …]}
      build/          outputs of the latest successful build; git-ignored

The folder is the copy on disk of the design's document (`document`), which hitlcad keeps: people and coding
agents edit its files with their own tools and commit them, and hitlcad merges what they write into the document.
What a build reads comes from a `Snapshot` of the document, not from the folder. build/ is regenerated; the full
revision history lives in hitlcad's data directory.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from hitlcad.core.design import DESIGN_FILE, DesignSpec
from hitlcad.core.report import BuildReport, BuildSpec
from hitlcad.core.structure import Tolerances
from hitlcad.kernel.errors import BUILD_COMPONENT, DESIGN_INVALID, DesignInvalid, HitlcadError, Location
from hitlcad.kernel.plugins import registry
from hitlcad.workspace.document import BUILD_DIR, Disk, Snapshot, sha

BUILD_MARKER = "# hitlcad build outputs: regenerated on every build, never committed\n"

#: The files of the design in a folder, by the folder's path; None for a folder that holds no design.
Files = Callable[[Path], Snapshot | None]


class DesignFolder:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    @property
    def design_file(self) -> Path:
        return self.path / DESIGN_FILE

    def exists(self) -> bool:
        return self.design_file.is_file()

    def load(self, text: str | None) -> DesignSpec:
        return load_design(text, str(self.design_file))

    @staticmethod
    def checked(design: DesignSpec, files: Snapshot) -> dict[str, str]:
        """The files its requirement checks read (`Check.files`), by content; '' for one not there (the check says so),
        so that saving it builds again. Part files never read them: the part cache does not depend on them."""
        return {rel: files.assets.get(rel, "") for rel in sorted({f for r in design.requirements if r.check
                                                                   for f in r.check.files()})}  # fmt: skip

    def spec(self, files: Files, defaults: Tolerances, configuration: str | None = None, *,
             _stack: tuple[Path, ...] = ()) -> BuildSpec:  # fmt: skip
        """What a build of the design's files depends on, with its components' specs."""
        mine = files(self.path)
        design = self.load(mine.design if mine else None)
        assert mine is not None
        components = {}
        for name, use in design.components.items():
            child = DesignFolder(self.path / use.design)
            where = Location(file=DESIGN_FILE, address=f"components.{name}")
            if child.path in (*_stack, self.path):
                chain = " -> ".join(str(p) for p in (*_stack, self.path, child.path))
                raise BUILD_COMPONENT(f"designs use each other in a cycle: {chain}", where)
            if files(child.path) is None:
                raise BUILD_COMPONENT(f"component {name}: no {DESIGN_FILE} in {child.path}", where)
            try:
                components[name] = child.spec(files, defaults, _stack=(*_stack, self.path))
            except HitlcadError as e:
                raise BUILD_COMPONENT(f"component {name} ({use.design}): {e.diagnostic.message}", where) from None
        return BuildSpec(design=design, sources=mine.sources(), assets=mine.vendor(), checked=self.checked(design, mine),
                         configuration=configuration,
                         tolerances=design.verification.resolve(defaults), components=components,
                         plugins=registry().versions())  # fmt: skip

    def components(self, files: Snapshot) -> list[Path]:
        """The folders of the designs this one uses."""
        return [(self.path / use.design).resolve() for use in self.load(files.design).components.values()]

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

    @staticmethod
    def scaffold(name: str, backend: str) -> Disk:
        """The files a new design starts with: design.json, interfaces.py, assembly.py and an empty parts/ (the agent
        writes the structure and the code)."""
        texts = {DESIGN_FILE: design_text(DesignSpec(name=name, backend=backend)),
                 "interfaces.py": INTERFACES_TEMPLATE, "assembly.py": ASSEMBLY_TEMPLATE}  # fmt: skip
        return Disk(texts, {"parts/.gitkeep": sha(b"")}, {sha(b""): b""})


def load_design(text: str | None, where: str = DESIGN_FILE) -> DesignSpec:
    """The design in design.json's text; `DesignInvalid` names each problem where it is (not pydantic's dump of the
    input). The plugins it names are checked first: without them its members do not validate, and that is not the
    file's to fix."""
    from pydantic import ValidationError

    if text is None:
        raise DesignInvalid(
            where, [DESIGN_INVALID.diagnostic(f"there is no {DESIGN_FILE}", Location(file=DESIGN_FILE))]
        )
    try:
        required = json.loads(text).get("plugins", {})
    except (ValueError, AttributeError):  # not JSON, or not an object: what validating it says
        required = {}
    if isinstance(required, dict) and (missing := registry().needs({str(k): str(v) for k, v in required.items()})):
        raise missing
    try:
        return DesignSpec.model_validate_json(text)
    except ValidationError as e:
        problems = [DESIGN_INVALID.diagnostic(err["msg"].removeprefix("Value error, "), Location(
            file=DESIGN_FILE, address=".".join(map(str, err["loc"])) or None)) for err in e.errors()]  # fmt: skip
        raise DesignInvalid(where, problems) from None


def design_text(design: DesignSpec) -> str:
    return json.dumps(design.model_dump(mode="json", exclude_defaults=True) | {"hitlcad": design.hitlcad},
                      indent=2, ensure_ascii=False) + "\n"  # fmt: skip


INTERFACES_TEMPLATE = '''"""Mating values: one function per value, def name(p, i). Parts and assembly.py read them as i.<name>."""
'''

ASSEMBLY_TEMPLATE = '''"""Where each part and component instance sits: def build(p, i) -> {name: [placement, …]}."""


def build(p, i):
    return {}
'''
