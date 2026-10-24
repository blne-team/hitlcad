from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from hitlcad.app import HitlCad
from hitlcad.backends.registry import BackendRegistry
from hitlcad.config import Settings
from hitlcad.render.base import NullRenderer

DESIGNS = Path(__file__).parent / "designs"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", backends=["build123d", "blender"])


@pytest.fixture
async def core(settings: Settings) -> AsyncIterator[HitlCad]:
    app = HitlCad.create(settings, registry=BackendRegistry.from_settings(settings), renderer=NullRenderer())
    yield app
    await app.shutdown()


def write_design(root: Path, files: dict[str, Any]) -> Path:
    """A design folder from {relative path: text, or a dict for design.json}."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(content, indent=2) if isinstance(content, dict) else content, encoding="utf-8")
    return root


def copy_design(name: str, target: Path) -> Path:
    shutil.copytree(DESIGNS / name, target)
    return target


def results(report: Any, analysis: str | None = None, status: str | None = None) -> list[Any]:
    return [
        r
        for r in report.results
        if (analysis is None or r.analysis == analysis) and (status is None or r.status == status)
    ]
