"""The sketches a part file draws are recorded as it runs, each shown with the part it lies on."""

from pathlib import Path

import numpy as np
import pytest

from hitlcad.builtin.build123d.entry import Build123d
from hitlcad.execution import runtime
from hitlcad.execution.runtime import Exports, Runtime
from hitlcad.geometry.cadview import PartView

PLATE = """from build123d import *


def build(p, i):
    with BuildPart() as plate:
        with BuildSketch(Plane.XZ.offset(7)):
            Rectangle(40, 20)
            Circle(3, mode=Mode.SUBTRACT)
        extrude(amount=5)
        with BuildSketch(Plane.XZ.offset(7), Plane.XZ.offset(12)):  # a pocket in each face
            SlotOverall(20, 6)
        extrude(amount=1, both=True, mode=Mode.SUBTRACT)
    with BuildSketch(Plane.XY.offset(100)):
        Circle(1)
    with BuildPart() as pin:
        with BuildSketch():
            Circle(p["pin"])
        extrude(amount=10)
    bare = Pos(0, 0, 50) * Box(1, 1, 1)
    return {"plate": (plate.part, {}), "pin": (pin.part, {}), "bare": (bare, {})}
"""


def built(src: Path, out: Path, adapter: Build123d, exports: Exports) -> dict[str, PartView]:
    rt = Runtime(src, {"pin": 2})
    job = {"files": {"parts/plate.py": {"plate": [], "pin": [], "bare": []}}, "out": str(out), "mesh_tolerance_mm": 0.1,
           "mesh_angular_tolerance": 0.2}  # fmt: skip
    try:
        (file,) = runtime.parts(rt, job, adapter, exports)["files"]
    finally:
        rt.close()
    assert "parts" in file, file["error"]
    return {part["name"]: PartView.read(out / part["view"]) for part in file["parts"]}


@pytest.mark.geometry
def test_each_part_shows_the_sketches_that_lie_on_it_with_their_lines(tmp_path: Path) -> None:
    src, out = tmp_path / "src", tmp_path / "out"
    (src / "parts").mkdir(parents=True)
    out.mkdir()
    (src / "parts/plate.py").write_text(PLATE)
    adapter, exports = Build123d(), Exports()
    exports.budget = 10 << 20
    views = built(src, out, adapter, exports)

    profile, slot = views["plate"].sketches  # not the circle far above it, nor the pin's
    assert (profile["file"], profile["line"], slot["line"]) == ("parts/plate.py", 6, 10)
    assert np.allclose(np.array(profile["planes"][0])[:3, 3], [0, -7, 0])
    ((outer, hole),) = profile["faces"]
    assert [s["kind"] for s in outer] == ["line"] * 4 and hole == [{"kind": "circle", "center": [0, 0], "radius": 3}]
    assert all(np.allclose(a["points"][-1], b["points"][0]) for a, b in zip(outer, outer[1:] + outer[:1], strict=True))
    assert len(slot["planes"]) == 2 and sorted(s["kind"] for s in slot["faces"][0][0]) == ["arc", "arc", "line", "line"]
    (pin,) = views["pin"].sketches
    assert pin["line"] == 16 and pin["faces"][0][0][0]["radius"] == 2
    assert views["bare"].sketches == [] and "sketches" not in PartView.header((out / "parts/bare.view").read_bytes())

    assert built(src, out, adapter, exports)["plate"].sketches == views["plate"].sketches  # kept, and written again
    (src / "parts/plate.py").write_text("\n" + PLATE)
    assert [s["line"] for s in built(src, out, adapter, exports)["plate"].sketches] == [7, 11]
