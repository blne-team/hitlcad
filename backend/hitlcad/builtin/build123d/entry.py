"""build123d's build worker, run as `python -I -B -u -X utf8 entry.py` in hitlcad's environment: it loads build123d,
then serves jobs (`runtime.serve`) with the adapter below.

Modes: `plan` (interface values and placements) and `parts` (build part files, export each part in its
own frame as STEP, STL and GLB, with its connector frames, and tessellate it for the 3D view).
"""

import hashlib
import io
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from hitlcad.execution import runtime
from hitlcad.execution.runtime import ModelError


def matrix(value: Any) -> list[list[float]]:
    """A placement or connector frame as a 4 × 4 matrix: a build123d Location, or a 4 × 4 nested list."""
    from build123d import Location

    from hitlcad.sdk import rows

    out = rows(value) if isinstance(value, Location | list | tuple) else None
    if out is None or len(out) != 4 or any(len(r) != 4 for r in out):
        raise ModelError(
            runtime.FRAME, f"a placement or connector frame is a build123d Location, got {type(value).__name__}"
        )
    return out


def _shape(value: Any, what: str) -> Any:
    import build123d

    if hasattr(value, "part") and not isinstance(value, build123d.Shape):  # a BuildPart context
        value = value.part
    if isinstance(value, list | tuple):
        value = build123d.Compound(children=[_shape(v, what) for v in value])
    if not isinstance(value, build123d.Shape):
        raise ModelError(runtime.SHAPE, f"{what} is a {type(value).__name__}; expected a build123d Shape")
    if not value.solids():
        raise ModelError(runtime.SHAPE, f"{what} has no solids; parts are 3D")
    return value


#: How near a part a sketch lies to be one of its sketches, in mm: a profile that made a feature lies on its boundary.
ON_PART = 1e-4


@dataclass
class Drawn:
    """A BuildSketch the design's code ran: its profile in its own XY, the frames it was placed at, and the line of
    the design's code it ran from."""

    file: str
    line: int
    local: Any
    planes: list[Any]

    def on(self, shape: Any) -> bool:
        return any(shape.distance(self.local.moved(plane)) < ON_PART for plane in self.planes)

    def view(self, deflection: float) -> dict[str, Any]:
        """As the part view carries it: each face's loops, outer first, each a chain of segments in its own XY."""
        faces = [[[_segment(edge, deflection) for edge in wire.order_edges()]
                  for wire in (face.outer_wire(), *face.inner_wires())] for face in self.local.faces()]  # fmt: skip
        return {"file": self.file, "line": self.line, "planes": [matrix(p) for p in self.planes], "faces": faces}


def _segment(edge: Any, deflection: float) -> dict[str, Any]:
    """A line, an arc (start, middle, end), a circle, or any other curve as points `deflection` from it."""
    from build123d import GeomType
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopAbs import TopAbs_REVERSED

    if edge.geom_type == GeomType.LINE:
        return {"kind": "line", "points": [_xy(edge @ 0), _xy(edge @ 1)]}
    if edge.geom_type == GeomType.CIRCLE:
        circle = {"center": _xy(edge.arc_center), "radius": edge.radius}
        if edge.is_closed:
            return {"kind": "circle", **circle}
        return {"kind": "arc", **circle, "points": [_xy(edge @ t) for t in (0, 0.5, 1)]}
    sampled = GCPnts_QuasiUniformDeflection(BRepAdaptor_Curve(edge.wrapped), deflection)
    points = [[sampled.Value(n).X(), sampled.Value(n).Y()] for n in range(1, sampled.NbPoints() + 1)]
    return {"kind": "curve", "points": points[::-1] if edge.wrapped.Orientation() == TopAbs_REVERSED else points}


def _xy(vector: Any) -> list[float]:
    return [vector.X, vector.Y]


def _brep(shape: Any) -> bytes:
    """Its B-rep, without the triangles an export leaves on it."""
    from OCP.BinTools import BinTools, BinTools_FormatVersion

    brep = io.BytesIO()
    BinTools.Write_s(shape.wrapped, brep, False, False, BinTools_FormatVersion.BinTools_FormatVersion_CURRENT)
    return brep.getvalue()


@dataclass
class ViewedPart:
    shape: Any
    sketches: list[Drawn]


class Build123d:
    """Records each BuildSketch the design's code runs, as it leaves its `with`: a part's sketches are those of its
    file that lie on it."""

    modes: dict[str, Any] = {}

    def __init__(self) -> None:
        from build123d import BuildSketch
        from build123d.build_common import Builder, _get_build_scope

        self.src = Path()
        self.drawn: list[Drawn] = []

        # Only its exit: build123d finds a builder's parent from the frame that enters it. The frame that made it
        # (build123d's `_python_frame`) is still at its `with`, or at the call to the code that made it.
        def leave(builder: Any, *exc: Any) -> None:
            scope = _get_build_scope()
            planes = [publication * output for publication in scope.publication_locations
                      for output in scope.output_placements]  # fmt: skip
            at = next(((f.f_code.co_filename, line) for f, line in traceback.walk_stack(builder._python_frame)
                       if runtime._in(f.f_code.co_filename, self.src)), None)  # fmt: skip
            Builder.__exit__(builder, *exc)
            if at and builder.sketch_local is not None:
                file = Path(at[0]).resolve().relative_to(self.src).as_posix()
                self.drawn.append(Drawn(file, at[1], builder.sketch_local, planes))

        BuildSketch.__exit__ = leave

    def placement(self, value: Any) -> list[list[float]]:
        return matrix(value)

    def begin(self, src: Path) -> None:
        self.src, self.drawn = src, []

    def unpack(self, what: str, value: Any) -> tuple[ViewedPart, dict[str, Any], dict[str, Any] | None]:
        from hitlcad.sdk import CatalogPart

        if isinstance(value, CatalogPart):
            catalog = {"provider": value.provider, "designation": value.designation, "material": value.material,
                       "metadata": value.metadata}  # fmt: skip
            return ViewedPart(_shape(value.shape, what), []), value.connectors, catalog
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
            shape = _shape(value[0], what)
            return ViewedPart(shape, [d for d in self.drawn if d.on(shape)]), value[1], None
        raise ModelError(
            runtime.RETURN, f"{what}: return (shape, {{connector: location or [locations]}}) or a catalog part"
        )

    def digest(self, part: ViewedPart) -> str:
        """Its B-rep, each node's name and colour, and its sketches' profiles, frames and lines."""
        import build123d

        looks = [(node.label, str(node.color)) for node in build123d.PreOrderIter(part.shape)]
        sketches = [(d.file, d.line, [matrix(p) for p in d.planes]) for d in part.sketches]
        profiles = b"".join(_brep(d.local) for d in part.sketches)
        return hashlib.sha256(_brep(part.shape) + repr(looks).encode() + profiles + repr(sketches).encode()).hexdigest()

    def export(self, part: ViewedPart, name: str, out: Path, tolerance: dict[str, float]) -> dict[str, Any]:
        import build123d

        from hitlcad.geometry import cadview

        shape = part.shape
        step, stl, glb = f"parts/{name}.step", f"parts/{name}.stl", f"parts/{name}.glb"
        view = f"parts/{name}{cadview.VIEW_SUFFIX}"
        shape.label = name
        build123d.export_step(shape, str(out / step))
        build123d.export_stl(shape, str(out / stl), tolerance=tolerance["linear"],
                             angular_tolerance=tolerance["angular"])  # fmt: skip
        # After the STL, with its tolerances: the glTF reuses that triangulation, adding names and colours. Not the
        # shapes' own materials (hitlcad's come from design.json): their PBR texture maps would be most of the file.
        for node in build123d.PreOrderIter(shape):
            node.material = None
        build123d.export_gltf(shape, str(out / glb), binary=True, linear_deflection=tolerance["linear"],
                              angular_deflection=tolerance["angular"])  # fmt: skip
        # From its STEP read back, as the build reads the part: picks number its faces, edges and vertices so.
        objects = cadview.object_meshes(cadview.load_mesh(out / glb))
        drawn = cadview.brep_view(cadview.load_step(out / step), name, objects)
        replace(drawn, sketches=[d.view(tolerance["linear"]) for d in part.sketches]).write(out / view)
        return {"mesh": stl, "step": step, "objects": glb, "view": view, "valid": bool(shape.is_valid)}


if __name__ == "__main__":
    # Loaded before the first job comes: what a build would otherwise wait for.
    import build123d  # noqa: F401
    import trimesh  # noqa: F401

    runtime.serve(Build123d())
