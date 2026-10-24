"""Real kernels: build123d and Blender builds plus Workbench previews. Needs Blender on PATH."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest
import trimesh
from build123d import Vector

from hitlcad.backends.registry import BackendRegistry
from hitlcad.config import Settings
from hitlcad.core.types import BuildSpec, ParamSpec
from hitlcad.pipeline import BuildPipeline
from hitlcad.render.base import BlenderWorkbenchRenderer

pytestmark = [
    pytest.mark.geometry,
    pytest.mark.skipif(shutil.which("blender") is None, reason="Blender not on PATH"),
]


@pytest.fixture(scope="module")
def registry() -> BackendRegistry:
    return BackendRegistry.from_settings(Settings(backends=["build123d", "blender"]))


def guide_example(registry: BackendRegistry, backend: str) -> str:
    return re.search(r"```python\n(.*?)```", registry.get(backend).authoring_guide(), re.S).group(1)  # type: ignore[union-attr]


def spec(backend: str, source: str, **values: float) -> BuildSpec:
    return BuildSpec(backend=backend, source=source, parameters=[ParamSpec(name=k, value=v) for k, v in values.items()])


def view_size(path: Path) -> list[float]:
    bb = json.loads(path.read_text())["shapes"]["bb"]
    return [bb[f"{a}max"] - bb[f"{a}min"] for a in "xyz"]


async def test_build123d_guide_example(registry: BackendRegistry, tmp_path: Path) -> None:
    pipeline = BuildPipeline(registry, BlenderWorkbenchRenderer(size=256))
    report = await pipeline.run(spec("build123d", guide_example(registry, "build123d"), width=60, depth=40, height=25,
                                     wall=2, hole_d=3.2, corner_r=1), tmp_path)  # fmt: skip
    assert report.status == "succeeded", report.error
    m = report.metrics
    assert m and m.size == pytest.approx((60, 40, 25), abs=1e-3) and m.watertight and m.bodies == 1
    assert {a.name for a in report.artifacts} >= {"mesh.stl", "view.json", "model.step", "preview.png"}
    assert view_size(tmp_path / "view.json") == pytest.approx([60, 40, 25], abs=1e-3)  # mm, Z up
    (solid,) = json.loads((tmp_path / "view.json").read_text())["instances"]
    assert solid["face_types"]["shape"][0] > 6  # the B-rep faces of a box with holes, not one triangle soup


async def test_blender_guide_example(registry: BackendRegistry, tmp_path: Path) -> None:
    pipeline = BuildPipeline(registry, BlenderWorkbenchRenderer(size=256))
    report = await pipeline.run(spec("blender", guide_example(registry, "blender"), height=120, radius=35, wall=2,
                                     twist_deg=45), tmp_path)  # fmt: skip
    assert report.status == "succeeded", report.error
    m = report.metrics
    assert m and m.size[2] == pytest.approx(120, abs=1e-3) and m.bbox_min[2] == pytest.approx(0, abs=1e-3)
    assert {a.name for a in report.artifacts} >= {"mesh.stl", "view.json", "model.blend", "preview.png"}
    assert view_size(tmp_path / "view.json")[2] == pytest.approx(120, abs=1e-3)  # tessellated from mesh.stl


async def test_model_errors_point_at_model_lines(registry: BackendRegistry, tmp_path: Path) -> None:
    source = (
        "from build123d import *\n\ndef build(p):\n    if p['wall'] > 1:\n        raise ValueError('wall too thick')\n"
    )
    report = await BuildPipeline(registry).run(spec("build123d", source, wall=2), tmp_path)
    assert report.status == "failed"
    assert "model.py line 5" in report.error and "ValueError: wall too thick" in report.error  # type: ignore[operator]


@pytest.mark.parametrize("backend", ["build123d", "blender"])
async def test_missing_build_function_is_explained(registry: BackendRegistry, backend: str, tmp_path: Path) -> None:
    report = await BuildPipeline(registry).run(spec(backend, "def make(p):\n    pass\n"), tmp_path)
    assert report.status == "failed" and "must define a top-level function build(p)" in report.error  # type: ignore[operator]


async def test_selection_describes_real_faces_and_files_convert(registry: BackendRegistry, tmp_path: Path) -> None:
    from hitlcad.analysis.cadview import cached_view, load_step
    from hitlcad.analysis.selection import describe_pick
    from hitlcad.core.types import ComponentRef

    source = (
        "from build123d import *\n\ndef build(p):\n    with BuildPart() as part:\n"
        "        Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
        "        with Locations((10, 0, 10)):\n            Hole(radius=p['r'])\n    return part\n"
    )
    out = tmp_path / "rev"
    report = await BuildPipeline(registry).run(spec("build123d", source, r=2.25), out)
    assert report.status == "succeeded", report.error

    on_mesh = describe_pick(out / "mesh.stl", (-10.0, 5.0, 10.0), None, None)
    assert on_mesh.kind == "point" and on_mesh.details["objects"] == 1 and "body 1 of 1" in on_mesh.label
    gltf = tmp_path / "part.glb"  # glTF is metres, Y up: read back in mm, Z up
    mesh = trimesh.load(out / "mesh.stl")
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    mesh.apply_scale(0.001)
    mesh.export(gltf)
    on_gltf = describe_pick(gltf, (-10.0, 5.0, 10.0), None, None)
    assert on_gltf.details["size"] == pytest.approx([40, 30, 10], abs=0.05)

    views = {}
    for path in (out / "model.step", out / "mesh.stl", gltf):
        views[path.name] = view = json.loads(cached_view(path, tmp_path / "cache").read_text())
        bb = view["shapes"]["bb"]
        assert [bb[f"{a}max"] - bb[f"{a}min"] for a in "xyz"] == pytest.approx([40, 30, 10], abs=0.05), path.name
    assert json.loads((out / "view.json").read_text()) == views["model.step"]  # builds and loose files agree

    # STEP keeps its topology: faces_i in the viewer is faces()[i] in build123d.
    (part,) = views["model.step"]["shapes"]["parts"]
    shape = load_step(out / "model.step")
    assert part["id"] == "/Group/model" and len(shape.faces()) == 7
    cyl = next(i for i, f in enumerate(shape.faces()) if f.geom_type.name == "CYLINDER")
    step, at = out / "model.step", (12.25, 0.0, 5.0)
    face = describe_pick(step, at, None, ComponentRef(part=part["id"], topo="face", index=cyl))
    assert face.kind == "face" and face.details["radius_mm"] == pytest.approx(2.25)
    assert face.component and face.component.index == cyl and f"(face {cyl} of model)" in face.label
    circle = next(i for i, e in enumerate(shape.edges()) if e.geom_type.name == "CIRCLE")
    edge = describe_pick(step, (12.25, 0.0, 10.0), None, ComponentRef(part=part["id"], topo="edge", index=circle))
    assert edge.kind == "edge" and edge.label.startswith("Circle edge r=2.25 mm") and edge.point == (12.25, 0.0, 10.0)
    with pytest.raises(LookupError, match="has 7 faces"):
        describe_pick(step, at, None, ComponentRef(part=part["id"], topo="face", index=7))
    with pytest.raises(LookupError, match="no part"):
        describe_pick(step, at, None, ComponentRef(part="/nope", topo="vertex", index=0))
    with pytest.raises(ValueError, match="must name"):
        describe_pick(step, at, None, None)
    with pytest.raises(ValueError, match="meshes have no"):
        describe_pick(out / "mesh.stl", at, None, ComponentRef(part=part["id"], topo="face", index=0))

    # Meshes: one face per mesh, crease lines as edges (a box with a hole: its outline rings).
    (stl,) = views["mesh.stl"]["instances"]
    assert stl["triangles_per_face"]["shape"] == [1] and stl["segments_per_edge"]["shape"][0] >= 3


async def test_geometry_listing_and_measuring(registry: BackendRegistry, tmp_path: Path) -> None:
    from hitlcad.analysis.geometry import StepModel, list_geometry, measure

    source = (
        "from build123d import *\n\ndef build(p):\n    with BuildPart() as part:\n"
        "        Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
        "        with Locations((10, 0, 10)):\n            Hole(radius=2.25)\n    return part\n"
    )
    report = await BuildPipeline(registry).run(spec("build123d", source), tmp_path)
    assert report.status == "succeeded", report.error
    step = tmp_path / "model.step"

    listing = list_geometry(step)
    assert listing.startswith("Part model: 7 faces, ") and "Cylinder face r=2.25 mm, axis (0.00, 0.00" in listing
    assert "edge 0: " in list_geometry(step, "edges") and "Vertex at (" in list_geometry(step, "vertices")

    faces = StepModel(step).part(None)[1].faces()
    top, bottom = (
        next(i for i, f in enumerate(faces) if f.is_planar and abs(f.normal_at().Z - z) < 1e-9) for z in (1, -1)
    )
    cylinder = next(i for i, f in enumerate(faces) if f.geom_type.name == "CYLINDER")
    walls = measure(step, f"face {top}", f"face {bottom} of model")
    assert "minimum distance 10.000 mm" in walls and "Angle between the face normals: 180.00°" in walls
    assert "gap between their planes is 10.000 mm" in walls
    assert "minimum distance 2.250 mm" in measure(step, f"face {cylinder}", "(10, 0, 5)")  # a point on the hole's axis

    edges = StepModel(step).part(None)[1].edges()
    x_line, y_line = (next(i for i, e in enumerate(edges) if e.geom_type.name == "LINE" and abs(e.tangent_at(0).dot(axis)) > 0.99)
                      for axis in (Vector(1, 0, 0), Vector(0, 1, 0)))  # fmt: skip
    assert "Angle between the edges: 90.00°" in measure(step, f"edge {x_line}", f"edge {y_line}")

    with pytest.raises(ValueError, match=r"has 7 faces \(0–6\)"):
        measure(step, "face 7", "face 0")
    with pytest.raises(ValueError, match="not a reference"):
        measure(step, "the hole", "face 0")
    with pytest.raises(ValueError, match="no single part named 'block'"):
        measure(step, "face 0 of block", "face 1")
    with pytest.raises(ValueError, match="kind must be one of"):
        list_geometry(step, "solids")


async def test_picks_follow_their_geometry_across_rebuilds(registry: BackendRegistry, tmp_path: Path) -> None:
    from hitlcad.analysis.anchors import carry
    from hitlcad.analysis.geometry import StepModel
    from hitlcad.core.types import ComponentRef, Pick

    async def block(name: str, height: float, holes: list[float]) -> Path:
        cut = "".join(f"        with Locations(({x}, 0, {height})):\n            Hole(radius=2.25)\n" for x in holes)
        source = ("from build123d import *\n\ndef build(p):\n    with BuildPart() as part:\n"
                  f"        Box(40, 30, {height}, align=(Align.CENTER, Align.CENTER, Align.MIN))\n{cut}    return part\n")  # fmt: skip
        report = await BuildPipeline(registry).run(spec("build123d", source), tmp_path / name)
        assert report.status == "succeeded", report.error
        return tmp_path / name / "model.step"

    def face(step: Path, kind: str, z: float | None = None) -> int:
        faces = StepModel(step).part(None)[1].faces()
        return next(
            i
            for i, f in enumerate(faces)
            if f.geom_type.name == kind and (z is None or abs(f.normal_at().Z - z) < 1e-9)
        )

    before = await block("a", 10, [10])
    top = Pick(
        point=(-10, 5, 10),
        normal=None,
        component=ComponentRef(part="/Group/model", topo="face", index=face(before, "PLANE", 1)),
    )
    hole = Pick(
        point=(12.25, 0, 5),
        normal=None,
        component=ComponentRef(part="/Group/model", topo="face", index=face(before, "CYLINDER")),
    )

    taller = await block("b", 14, [12])  # the top face moves up 4 mm, the hole 2 mm along X
    moved_top, moved_hole = carry(before, taller, [top, hole])
    assert moved_top and moved_top.component and moved_top.component.index == face(taller, "PLANE", 1)
    assert moved_top.point == pytest.approx((-10, 5, 14))
    assert moved_hole and moved_hole.component and moved_hole.component.index == face(taller, "CYLINDER")
    assert moved_hole.point == pytest.approx((14.25, 0, 5))

    no_hole = await block("c", 10, [])
    assert carry(before, no_hole, [hole]) == [None]  # nothing round left: stays on its revision
    two_holes = await block("d", 10, [8, 16])
    assert carry(before, two_holes, [hole]) == [None]  # 2.0 mm and 1.5 mm away: too close to call

    meshes = carry(
        tmp_path / "a" / "mesh.stl",
        tmp_path / "b" / "mesh.stl",
        [Pick(point=(-10, 5, 10), normal=None, component=None)],
    )
    assert meshes[0] and meshes[0].point == pytest.approx((-10, 5, 14), abs=1e-6) and meshes[0].component is None


async def test_requirements_are_verified_on_the_build(registry: BackendRegistry, tmp_path: Path) -> None:
    from hitlcad.analysis.assembly import Build, Part
    from hitlcad.analysis.geometry import find_holes
    from hitlcad.analysis.mesh import load_mesh, measure
    from hitlcad.core.types import Requirement

    source = (
        "from build123d import *\n\ndef build(p):\n    with BuildPart() as part:\n"
        "        Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
        "        fillet(part.edges().filter_by(Axis.Z), radius=3)\n"  # rounded outside corners are not holes
        "        with Locations((-10, 0, 10), (10, 0, 10)):\n            Hole(radius=3)\n"
        "        with Locations((-15, -10, 10), (15, -10, 10), (-15, 10, 10), (15, 10, 10)):\n            Hole(radius=1.6)\n"
        "    return part\n"
    )
    report = await BuildPipeline(registry).run(spec("build123d", source), tmp_path)
    assert report.status == "succeeded", report.error
    holes = find_holes(tmp_path / "model.step")
    assert sorted(round(h.diameter, 2) for h in holes) == [3.2, 3.2, 3.2, 3.2, 6.0, 6.0]
    assert all(h.depth == pytest.approx(10) for h in holes)

    mesh = load_mesh(tmp_path / "mesh.stl")
    build = Build(Part("model", mesh, measure(mesh), tmp_path / "model.step"), {})
    reqs = [
        Requirement.model_validate(r)
        for r in [
            {"text": "10 mm thick", "check": {"kind": "size", "axis": "z", "min": 9.9, "max": 10.1}},
            {"text": "fits a 35 mm slot", "check": {"kind": "fits_within", "box": [35, 50, 20]}},
            {"text": "two M6 clearance holes", "check": {"kind": "hole", "diameter": 6, "tol": 0.1, "count": 2}},
            {"text": "three M6 holes", "check": {"kind": "hole", "diameter": 6, "count": 3}},
            {"text": "light in PLA", "check": {"kind": "mass", "density_g_cm3": 1.24, "max": 20}},
            {"text": "under 8000 mm³", "check": {"kind": "volume", "max": 8000}},
            {"text": "3 mm walls", "check": {"kind": "wall", "min": 3}},
            {"text": "3.5 mm walls", "check": {"kind": "wall", "min": 3.5}},
            {"text": "one piece", "check": {"kind": "bodies", "count": 1}},
            {"text": "looks friendly"},
        ]
    ]
    results = {r.text: r for r in (req.verify(build) for req in reqs)}
    assert {t: r.status for t, r in results.items()} == {
        "10 mm thick": "pass", "fits a 35 mm slot": "fail", "two M6 clearance holes": "pass",
        "three M6 holes": "fail", "light in PLA": "pass", "under 8000 mm³": "fail", "3 mm walls": "pass", "3.5 mm walls": "fail",
        "one piece": "pass", "looks friendly": "unverified",
    }  # fmt: skip
    assert results["fits a 35 mm slot"].detail.endswith(": X 40.00 > 35")
    assert "2 of Ø6 ±0.1 mm (needs 3); holes: Ø3.20 ×4, Ø6.00 ×2" in results["three M6 holes"].detail
    assert "at 1.24 g/cm³" in results["light in PLA"].detail
    # The thinnest wall is between each corner hole and the sides: 5 mm from its centre, minus 1.6 mm.
    assert results["3.5 mm walls"].detail.startswith("thinnest wall 3.4")


async def test_assemblies_are_built_checked_and_verified_per_part(registry: BackendRegistry, tmp_path: Path) -> None:
    from hitlcad.analysis.geometry import list_geometry
    from hitlcad.core.types import FdmProcess, Requirement

    source = (
        "from build123d import *\n\ndef build(p):\n"
        "    with BuildPart() as base:\n"
        "        Box(40, 30, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
        "        with Locations((10, 0, 6)):\n            Hole(radius=3)\n"
        "    lid = Pos(0, 0, 6.3) * Box(40, 30, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"  # 0.3 mm above
        "    peg = Pos(-10, 0, 2) * Cylinder(2, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"  # sunk into the base only
        "    return {'base': base.part, 'lid': lid, 'peg': peg}\n"
    )
    reqs = [
        Requirement.model_validate(r)
        for r in [
            {"text": "lid clears base", "check": {"kind": "clearance", "between": ["lid", "base"], "min": 0.25}},
            {"text": "lid gap 0.5", "check": {"kind": "clearance", "between": ["lid", "base"], "min": 0.5}},
            {"text": "peg clear of base", "check": {"kind": "clearance", "between": ["peg", "base"], "min": 0}},
            {"text": "thin lid", "check": {"kind": "size", "axis": "z", "max": 2.1, "part": "lid"}},
            {"text": "base hole", "check": {"kind": "hole", "diameter": 6, "part": "base"}},
            {"text": "a handle", "check": {"kind": "bodies", "count": 1, "part": "handle"}},
        ]
    ]
    out = tmp_path / "asm"
    asm = spec("build123d", source).model_copy(
        update={"manufacturing": FdmProcess(process="fdm"), "requirements": reqs}
    )
    report = await BuildPipeline(registry).run(asm, out)
    assert report.status == "succeeded", report.error
    assert [(p.name, [round(v, 2) for v in p.size]) for p in report.parts] == [
        ("base", [40, 30, 6]), ("lid", [40, 30, 2]), ("peg", [4, 4, 4])
    ]  # fmt: skip
    files = {a.name: a.role for a in report.artifacts}
    assert files["part-lid.step"] == "part_exchange" and files["part-peg.stl"] == "part_mesh"

    checks = {c.name: c for c in report.checks}
    assert checks["interference: base / peg"].detail.startswith("base and peg overlap by ")
    assert not any("lid" in name for name in checks if name.startswith("interference"))
    assert {"fdm_overhangs: lid", "fdm_wall_thickness: base", "fdm_bed_contact: peg"} <= set(checks)

    results = {r.text: r for r in report.requirements}
    assert {t: r.status for t, r in results.items()} == {
        "lid clears base": "pass", "lid gap 0.5": "fail", "peg clear of base": "fail", "thin lid": "pass",
        "base hole": "pass", "a handle": "fail",
    }  # fmt: skip
    assert results["lid clears base"].detail.startswith("lid to base: 0.30 mm")
    assert results["peg clear of base"].detail.startswith("peg and base overlap by ")
    assert results["a handle"].detail == "no part named 'handle' (parts: base, lid, peg)"

    view = json.loads((out / "view.json").read_text())
    assert sorted(p["name"] for p in view["shapes"]["parts"]) == ["base", "lid", "peg"]
    assert list_geometry(out / "model.step", part="lid").startswith("Part lid: 6 faces")

    bad = await BuildPipeline(registry).run(spec("build123d", "from build123d import *\ndef build(p):\n    return {'my part': Box(1, 1, 1)}\n"),
                                            tmp_path / "bad")  # fmt: skip
    assert bad.status == "failed" and "part name 'my part'" in (bad.error or "")


async def test_whole_parts_are_picked_measured_and_carried_by_name(registry: BackendRegistry, tmp_path: Path) -> None:
    from hitlcad.analysis.anchors import carry
    from hitlcad.analysis.geometry import measure
    from hitlcad.analysis.selection import describe_pick
    from hitlcad.core.types import ComponentRef, Pick

    def pair(lid_height: float, lid: bool = True) -> str:
        parts = "{'base': base, 'lid': lid}" if lid else "{'base': base}"
        return (
            "from build123d import *\n\ndef build(p):\n"
            "    base = Box(40, 30, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
            f"    lid = Pos(0, 0, 6.3) * Box(40, 30, {lid_height}, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
            f"    return {parts}\n"
        )

    steps = {}
    for name, source in {"thin": pair(2), "thick": pair(5), "no_lid": pair(2, lid=False)}.items():
        report = await BuildPipeline(registry).run(spec("build123d", source), tmp_path / name)
        assert report.status == "succeeded", report.error
        steps[name] = tmp_path / name / "model.step"
    view = json.loads((tmp_path / "thin" / "view.json").read_text())
    lid = next(p["id"] for p in view["shapes"]["parts"] if p["name"] == "lid")

    top = (0.0, 0.0, 8.3)  # a click on the lid's top face, with the filter on Solid
    item = describe_pick(steps["thin"], top, None, ComponentRef(part=lid, topo="solid"))
    assert item.kind == "solid" and item.component == ComponentRef(part=lid, topo="solid", index=0)
    assert (
        item.label
        == "Whole part, volume 2400.0 mm³, size 40.00 × 30.00 × 2.00 mm, center (0.00, 0.00, 7.30) (part lid) at (0.00, 0.00, 8.30)"
    )
    assert item.details["bbox"] == [[-20, -15, 6.3], [20, 15, 8.3]] and item.details["faces"] == 6
    with pytest.raises(ValueError, match="whole part"):
        ComponentRef(part=lid, topo="solid", index=1)

    assert "part lid to part base: minimum distance 0.300 mm" in measure(steps["thin"], "part lid", "part base")
    with pytest.raises(ValueError, match="no single part named 'cap'"):
        measure(steps["thin"], "part cap", "part base")

    pick = Pick(point=top, component=ComponentRef(part=lid, topo="solid"))
    [thicker] = carry(steps["thin"], steps["thick"], [pick])
    assert thicker is not None and thicker.component == pick.component
    assert thicker.point[2] == pytest.approx(11.3)  # inside the thicker lid: moved onto its surface, its top
    assert carry(steps["thin"], steps["no_lid"], [pick]) == [None]  # the part is gone: stays on its revision


async def test_planetary_gearbox_is_measured_as_built(registry: BackendRegistry, tmp_path: Path) -> None:
    """Seven parts: involute gears meshing with 0.07 mm of backlash, a lid seated on the housing, holes of
    every kind. Fitted parts, sharp tooth tips and mating faces are where measurement goes wrong."""
    from hitlcad.analysis.anchors import carry
    from hitlcad.analysis.geometry import StepModel
    from hitlcad.core.types import ComponentRef, Pick
    from hitlcad.designs.folder import DesignFolder

    report = await BuildPipeline(registry).run(DesignFolder(Path(__file__).parent / "designs/planetary_gearbox").spec(),
                                               tmp_path)  # fmt: skip
    assert report.status == "succeeded", report.error
    assert [r.text for r in report.requirements if r.status != "pass"] == []
    results = {r.text: r.detail for r in report.requirements}
    assert results["Sun and planet mesh with backlash, no interference"].startswith("sun to planet_0: 0.07 mm")
    assert results["Lid seats on the housing"].startswith("housing to lid: 0.00 mm")

    # The lid touches the housing, so the combined mesh shares faces; each part is closed.
    assert report.metrics and report.metrics.watertight
    assert report.metrics.volume == pytest.approx(sum(p.volume or 0 for p in report.parts))
    checks = {c.name: c for c in report.checks}
    walls = [checks[f"fdm_wall_thickness: {p}"] for p in ("sun", "planet_0", "planet_1", "planet_2")]
    assert all(c.passed for c in walls) and len({c.detail.split(" mm")[0] for c in walls}) == 1, walls

    # Picks on faces that touch another part's stay on their own part.
    step = tmp_path / "model.step"
    model = StepModel(step)

    def face(part: str, kind: str) -> int:
        return next(i for i, f in enumerate(model.parts[part].faces()) if f.geom_type.name == kind)

    flank = model.parts["/model/sun"].faces()[face("/model/sun", "EXTRUSION")].position_at(0.5, 0.5)
    bore = next(f for f in model.parts["/model/planet_1"].faces() if f.geom_type.name == "CYLINDER" and f.radius < 2)
    picks = [
        Pick(point=tuple(flank), component=ComponentRef(part="/model/sun", topo="face", index=face("/model/sun", "EXTRUSION"))),
        Pick(point=tuple(bore.position_at(0.5, 0.5)),
             component=ComponentRef(part="/model/planet_1", topo="face", index=model.parts["/model/planet_1"].faces().index(bore))),
    ]  # fmt: skip
    assert [p.component if p else None for p in carry(step, step, picks)] == [p.component for p in picks]


async def test_blender_assemblies_have_named_parts(registry: BackendRegistry, tmp_path: Path) -> None:
    source = (
        "import bpy\n\ndef build(p):\n"
        "    bpy.ops.mesh.primitive_cube_add(size=10, location=(0, 0, 5))\n    body = bpy.context.object\n"
        "    bpy.ops.mesh.primitive_cylinder_add(radius=2, depth=6, location=(20, 0, 3))\n    knob = bpy.context.object\n"
        "    return {'body': body, 'knob': knob}\n"
    )
    report = await BuildPipeline(registry).run(spec("blender", source), tmp_path)
    assert report.status == "succeeded", report.error
    assert [p.name for p in report.parts] == ["body", "knob"] and report.parts[0].size == pytest.approx((10, 10, 10))
    assert {"part-body.stl", "part-knob.stl"} <= {a.name for a in report.artifacts}
    assert {c.name: c.passed for c in report.checks}["interference"]
    view = json.loads((tmp_path / "view.json").read_text())
    assert sorted(p["name"] for p in view["shapes"]["parts"]) == ["body", "knob"]


async def test_blender_picks_name_the_object(registry: BackendRegistry, tmp_path: Path) -> None:
    from hitlcad.analysis.selection import describe_pick

    source = (
        "import bpy\n\ndef build(p):\n"
        "    bpy.ops.mesh.primitive_cube_add(size=20, location=(0, 0, 10))\n    bpy.context.object.name = 'head'\n"
        "    bpy.ops.mesh.primitive_cube_add(size=4, location=(-8, 0, 22))\n    bpy.context.object.name = 'ear_left'\n"
    )
    report = await BuildPipeline(registry).run(spec("blender", source), tmp_path)
    assert report.status == "succeeded", report.error
    objects = report.artifact("objects")
    assert objects is not None and objects.name == "objects.glb"
    ear = describe_pick(tmp_path / "objects.glb", (-8.0, 0.0, 24.0), None, None)  # the ear's top, in mm
    assert "Point on ear_left (size 4.0 × 4.0 × 4.0 mm)" in ear.label and ear.details["objects"] == 2
    assert "Point on head" in describe_pick(tmp_path / "objects.glb", (0.0, -10.0, 10.0), None, None).label
