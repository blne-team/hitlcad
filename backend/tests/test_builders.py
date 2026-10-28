"""Construction contracts, actual solids and repair feedback available to coding agents."""

from __future__ import annotations

import importlib
import math
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import trimesh

from hitlcad.agent import describe
from hitlcad.agent.skills import install
from hitlcad.app import HitlCad
from hitlcad.geometry.mesh import load_mesh, topology
from hitlcad.kernel.plugins import registry
from hitlcad.workspace.folder import DesignFolder

from .conftest import write_design


@pytest.fixture
def builders(monkeypatch: pytest.MonkeyPatch) -> dict[str, ModuleType]:
    """Use the same import roots supplied to backend processes, without importing plugin internals."""
    for package in registry().model_packages:
        monkeypatch.syspath_prepend(str(package.root))
    return {backend: importlib.import_module(f"hitlcad_{backend}") for backend in ("build123d", "blender")}


def test_construction_helpers_are_discoverable_and_installed(core: HitlCad, tmp_path: Path) -> None:
    guide = describe.guide()["builders"]
    assert "`hitlcad_build123d` (build123d): mounting_plate" in guide
    assert "`hitlcad_blender` (blender): section_form" in guide
    packages = {p.package: p for p in registry().model_packages}
    assert packages["hitlcad_blender"].backends == ("blender",)
    assert packages["hitlcad_build123d"].files()
    install(tmp_path, core.registry.all())
    for client in (".agents", ".claude"):
        skill = tmp_path / client / "skills" / "hitlcad"
        assert "hitlcad_blender" in (skill / "SKILL.md").read_text(encoding="utf-8")
        assert sorted(p.name for p in (skill / "reference").glob("builders-*.md")) == [
            "builders-blender.md", "builders-build123d.md"]  # fmt: skip


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"width": 0}, "width must be finite and > 0"),
        ({"thickness": float("nan")}, "thickness must be finite and > 0"),
        ({"hole_diameter": -1}, "hole_diameter must be finite and > 0"),
        ({"boss_height": -1}, "boss_height must be finite and >= 0"),
        ({"boss_height": 2}, "boss_diameter is required"),
        ({"boss_height": 2, "boss_diameter": 2}, "boss_diameter must exceed hole_diameter"),
        ({"holes": []}, "holes must contain at least one"),
        ({"holes": [(29, 0)]}, "holes[0]"),
        ({"holes": [(0, float("inf"))]}, "holes[0]"),
        ({"holes": [(0, 0, 1)]}, "holes[0]"),
        ({"holes": [(0, 0), (3, 0)]}, "holes[1] touches or overlaps"),
    ],
)
def test_cad_inputs_name_the_dimension_or_hole_to_repair(
    builders: dict[str, ModuleType], changes: dict[str, Any], message: str
) -> None:
    arguments = {"width": 60, "depth": 40, "thickness": 3, "holes": [(-20, 0), (20, 0)], "hole_diameter": 3}
    with pytest.raises(ValueError) as error:
        builders["build123d"].mounting_plate(**(arguments | changes))
    assert message in str(error.value)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sections": []}, "at least two"),
        ({"sections": [(1, 10), (20, 10)]}, "sections[0].z"),
        ({"sections": [(0, 10), (0, 12)]}, "sections[1].z"),
        ({"sections": [(0, 10), (20, 2)]}, "sections[1].outer_radius"),
        ({"sections": [(0, 10), (20, float("nan"))]}, "sections[1]"),
        ({"wall": 0}, "wall must be finite and > 0"),
        ({"floor": 20}, "floor (20 mm) must be below"),
        ({"chord_tolerance": 0}, "chord_tolerance must be finite and > 0"),
        ({"chord_tolerance": 1e-20}, "more than 4096 ring segments"),
    ],
)
def test_mesh_inputs_are_rejected_before_loading_bpy(
    builders: dict[str, ModuleType], changes: dict[str, Any], message: str
) -> None:
    # This runs in the server's Python, where bpy is unavailable. Valid construction is tested in Blender below.
    with pytest.raises(ValueError) as error:
        builders["blender"].section_form(**({"sections": [(0, 10), (20, 10)], "wall": 2} | changes))
    assert message in str(error.value)


@pytest.mark.geometry
@pytest.mark.parametrize("boss_height", [0, 6])
def test_plate_volume_connectors_and_builder_isolation(builders: dict[str, ModuleType], boss_height: float) -> None:
    from build123d import Box, BuildPart

    # Helpers must not accidentally add their stock or cutter shapes to the caller's active builder.
    with BuildPart() as unrelated:
        Box(1, 1, 1)
        shape, connectors = builders["build123d"].mounting_plate(
            width=60,
            depth=40,
            thickness=3,
            holes=[(-20, 0), (20, 0)],
            hole_diameter=3.4,
            boss_height=boss_height,
            boss_diameter=9,
        )
    assert unrelated.part.volume == pytest.approx(1)
    expected = 60 * 40 * 3 + 2 * math.pi * (4.5**2 * boss_height - 1.7**2 * (3 + boss_height))
    assert shape.volume == pytest.approx(expected)
    assert shape.is_valid and len(shape.solids()) == 1
    assert tuple(shape.bounding_box().size) == pytest.approx((60, 40, 3 + boss_height))
    assert tuple(connectors["bottom"].z_axis.direction) == pytest.approx((0, 0, -1))
    assert [tuple(frame.position) for frame in connectors["holes"]] == [
        (-20, 0, 3 + boss_height),
        (20, 0, 3 + boss_height),
    ]


def test_source_topology_preserves_defects_hidden_by_cleanup(tmp_path: Path) -> None:
    original = trimesh.creation.box(extents=(10, 10, 10))
    # Remove one triangle and add a degenerate triangle. STL is a triangle soup: welding is required to
    # distinguish actual boundary edges from the per-triangle vertices repeated by its serialization.
    faulty = trimesh.Trimesh(original.vertices, [*original.faces[:-1], [0, 0, 1]], process=False)
    source = tmp_path / "fault.stl"
    faulty.export(source)
    issues = {issue.kind: issue for issue in topology(source)}
    assert issues["degenerate_faces"].count == 1
    assert issues["boundary_edges"].count == 3
    assert len(load_mesh(source).faces) == len(original.faces) - 1
    assert "boundary edges" in issues["boundary_edges"].detail
    assert len(issues["boundary_edges"].places) == 3


def test_source_topology_distinguishes_winding_duplicate_and_nonmanifold_edges(tmp_path: Path) -> None:
    box = trimesh.creation.box()
    good = tmp_path / "good.stl"
    box.export(good)
    assert topology(good) == []
    box.faces[0] = box.faces[0, ::-1]
    reversed_face = tmp_path / "winding.stl"
    box.export(reversed_face)
    issues = {issue.kind: issue for issue in topology(reversed_face)}
    assert issues["winding_edges"].count == 3
    assert "boundary_edges" not in issues
    duplicate = trimesh.Trimesh(box.vertices, [*box.faces, box.faces[1]], process=False)
    duplicate.export(tmp_path / "duplicate.stl")
    issues = {issue.kind: issue for issue in topology(tmp_path / "duplicate.stl")}
    assert issues["duplicate_faces"].count == 1
    assert issues["nonmanifold_edges"].count == 3


def mesh_design(part_code: str) -> dict[str, Any]:
    return {
        "design.json": {
            "hitlcad": 2,
            "name": "section helper",
            "backend": "blender",
            "ground": "form",
            "parts": {
                "form": {
                    "file": "parts/form.py",
                    "role": "hollow form",
                    "material": "PLA",
                    "connectors": {"bottom": {"kind": "frame"}, "rim": {"kind": "frame"}},
                }
            },
            "parameters": [{"name": "radius", "value": 10, "min": 5, "max": 20}],
        },
        "parts/form.py": part_code,
        "assembly.py": "from mathutils import Matrix\n\ndef build(p, i):\n    return {'form': [Matrix.Identity(4)]}\n",
    }


@pytest.mark.geometry
async def test_blender_form_bounds_volume_resolution_and_frames(core: HitlCad, tmp_path: Path) -> None:
    radius = 10
    folder = write_design(
        tmp_path / "form",
        mesh_design(
            "from hitlcad_blender import section_form\n\ndef build(p, i):\n"
            "    return {'form': section_form(sections=[(0, p['radius']), (20, p['radius'])], wall=2, floor=3)}\n"
        ),
    )
    spec = DesignFolder(folder).spec(core.settings.tolerances, None)
    out = tmp_path / "out"
    report = await core.pipeline.run(spec, folder, out)
    assert report.status == "succeeded" and not report.failing(), report.lead()
    (part,) = report.parts
    assert part.metrics.watertight and part.metrics.winding_consistent and part.metrics.bodies == 1
    assert part.metrics.size == pytest.approx((2 * radius, 2 * radius, 20))
    expected = math.pi * (radius**2 * 20 - (radius - 2) ** 2 * 17)
    assert part.metrics.volume == pytest.approx(expected, rel=0.01)
    assert topology(out / "parts/form.stl") == []
    # Check the actual exported outer ring's chordal error rather than just repeating the segment formula.
    mesh = load_mesh(out / "parts/form.stl")
    outer = mesh.triangles_center
    side = (outer[:, 2] > 0) & (outer[:, 2] < 20)
    radial = np.linalg.norm(outer[side, :2], axis=1)
    assert radius - np.min(radial[radial > radius - 1]) <= 0.05
    assert np.asarray(part.connectors["bottom"].frames[0])[:3, 2] == pytest.approx((0, 0, -1), abs=1e-6)
    assert np.asarray(part.connectors["rim"].frames[0])[:3, 3] == pytest.approx((0, 0, 20))


@pytest.mark.geometry
async def test_open_mesh_feedback_names_file_counts_and_local_coordinates(core: HitlCad, tmp_path: Path) -> None:
    folder = write_design(
        tmp_path / "open",
        mesh_design(
            "from hitlcad_blender import section_form\n\ndef build(p, i):\n"
            "    obj, connectors = section_form(sections=[(0, 10), (20, 10)], wall=2)\n"
            "    mesh = obj.data\n"
            "    vertices = [tuple(v.co) for v in mesh.vertices]\n"
            "    faces = [tuple(f.vertices) for f in mesh.polygons if f.center.z != 0]\n"
            "    mesh.clear_geometry()\n    mesh.from_pydata(vertices, [], faces)\n    mesh.update()\n"
            "    return {'form': (obj, connectors)}\n"
        ),
    )
    report = await core.pipeline.run(DesignFolder(folder).spec(core.settings.tolerances), folder, tmp_path / "out")
    assert report.status == "succeeded", report.lead()
    (finding,) = [r for r in report.results if r.code == "part.mesh_open"]
    assert finding.status == "fail"
    assert finding.where and finding.where.file == "parts/form.py"
    assert finding.values["boundary_edges"] >= 12
    assert "boundary edges" in finding.detail and "part-local mm" in finding.detail and "0.000)" in finding.detail
    assert not any(r.code == "hitlcad.bug" for r in report.results)
