"""Agent skills hitlcad installs into design projects, for Claude Code and Codex.

Each folder here is one skill, copied whole into a project; `$connectors`, `$mates`, `$relations`,
`$fasteners`, `$checks` and `$processes` in its SKILL.md are filled in from the framework's unions. Every enabled
backend adds a `hitlcad-<backend>` skill holding its authoring guide. Skill folders named
`hitlcad*` in a project belong to hitlcad: installing replaces them with the current set.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from string import Template

from hitlcad import describe
from hitlcad.backends.base import CADBackend

SKILL_DIRS = (".claude/skills", ".agents/skills")  # Claude Code, Codex
PREFIX = "hitlcad"

Skill = dict[str, str]  # path inside the skill folder -> text


def _read(folder: Traversable, prefix: str = "") -> Skill:
    out: Skill = {}
    for item in folder.iterdir():
        if item.is_dir():
            out |= _read(item, f"{prefix}{item.name}/")
        else:
            out[prefix + item.name] = item.read_text(encoding="utf-8")
    return out


def _backend_skill(backend: CADBackend) -> Skill:
    description = (
        f'Code contract for hitlcad {backend.title} part files and assembly.py (backend "{backend.name}" in '
        f"design.json): what build(p, i) returns, connector frames, placements, catalog parts, imports and idioms. "
        f"Read it before writing or editing the code of a {backend.name} design."
    )
    body = f"**{backend.title}.** {backend.summary}\n\n{backend.authoring_guide()}"
    return {"SKILL.md": f"---\nname: {PREFIX}-{backend.name}\ndescription: {json.dumps(description)}\n---\n\n{body}"}


def render(backends: Iterable[CADBackend]) -> dict[str, Skill]:
    """Every skill by name."""
    values = describe.guide("")
    skills: dict[str, Skill] = {}
    for folder in files(__name__).iterdir():
        if folder.is_dir() and folder.name != "__pycache__":
            skill = _read(folder)
            skill["SKILL.md"] = Template(skill["SKILL.md"]).substitute(values)
            skills[folder.name] = skill
    for backend in backends:
        skills[f"{PREFIX}-{backend.name}"] = _backend_skill(backend)
    return skills


def install(root: Path, backends: Iterable[CADBackend]) -> list[str]:
    """Write the skills into the project's skill folders. Returns the skill folders changed."""
    skills = render(backends)
    changed: list[str] = []
    for base in SKILL_DIRS:
        folder = root / base
        for old in sorted(folder.glob(f"{PREFIX}*")):
            if old.is_dir() and old.name not in skills:
                shutil.rmtree(old)
                changed.append(f"{base}/{old.name}/")
        for name, skill in skills.items():
            target = folder / name
            current = (
                {p.relative_to(target).as_posix(): p.read_text() for p in target.rglob("*") if p.is_file()}
                if target.is_dir()
                else {}
            )
            if current == skill:
                continue
            shutil.rmtree(target, ignore_errors=True)
            for rel, text in skill.items():
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                (target / rel).write_text(text)
            changed.append(f"{base}/{name}/")
    return changed
