from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import trimesh

from hitlcad.app import HitlCad
from hitlcad.backends.base import BackendBuild, CADBackend, OutputFile
from hitlcad.backends.registry import BackendRegistry
from hitlcad.config import Settings
from hitlcad.core.types import DesignBrief, Dimension, ParamSpec
from hitlcad.render.base import NullRenderer

BOX_SOURCE = "def build(p):\n    return p\n"


class FakeBackend(CADBackend):
    """Builds an axis-aligned box sized by the width/depth/height parameters."""

    name = "fake"
    title = "Fake"
    summary = "Test backend producing boxes."

    def __init__(self) -> None:
        self.builds: list[dict[str, Any]] = []

    def availability(self) -> tuple[bool, str]:
        return True, "always"

    def authoring_guide(self) -> str:
        return "# fake backend\nDefine build(p)."

    async def build(self, source: str, params: dict[str, Any], out_dir: Path) -> BackendBuild:
        self.builds.append(params)
        if "FAIL" in source:
            return BackendBuild(error="model.py line 2: ValueError: requested failure", log="boom")
        box = trimesh.creation.box((params.get("width", 10), params.get("depth", 10), params.get("height", 10)))
        box.apply_translation((0, 0, params.get("height", 10) / 2))
        box.export(out_dir / "mesh.stl")
        return BackendBuild(files=[OutputFile("mesh.stl", "model/stl", "mesh")], log="ok")


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", backends=[])


def make_core(settings: Settings, backend: FakeBackend) -> HitlCad:
    return HitlCad.create(settings, registry=BackendRegistry({"fake": backend}), renderer=NullRenderer())


@pytest.fixture
def core(settings: Settings, fake_backend: FakeBackend) -> HitlCad:
    return make_core(settings, fake_backend)


def brief(**overrides: Any) -> DesignBrief:
    data: dict[str, Any] = {
        "title": "Test box",
        "summary": "A box",
        "backend": "fake",
        "key_dimensions": [Dimension(name="width", value=20)],
        "features": ["box"],
    }
    data.update(overrides)
    return DesignBrief(**data)


def box_params(width: float = 20, depth: float = 10, height: float = 5) -> list[ParamSpec]:
    return [
        ParamSpec(name="width", value=width, min=1, max=200),
        ParamSpec(name="depth", value=depth, min=1, max=200),
        ParamSpec(name="height", value=height, min=1, max=200),
    ]
