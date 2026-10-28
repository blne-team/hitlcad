"""build123d's build worker, run as `python -I -B -u -X utf8 entry.py` in hitlcad's environment: it loads build123d,
then serves jobs (`runtime.serve`) with the adapter below.

Modes: `plan` (interface values and placements) and `parts` (build part files, export each part in its
own frame as STEP, STL and GLB, with its connector frames, and tessellate it for the 3D view).
"""

import hashlib
import io
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


class Build123d:
    modes: dict[str, Any] = {}

    def placement(self, value: Any) -> list[list[float]]:
        return matrix(value)

    def begin(self) -> None:
        pass

    def unpack(self, what: str, value: Any) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
        from hitlcad.sdk import CatalogPart

        if isinstance(value, CatalogPart):
            catalog = {"provider": value.provider, "designation": value.designation, "material": value.material,
                       "metadata": value.metadata}  # fmt: skip
            return _shape(value.shape, what), value.connectors, catalog
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
            return _shape(value[0], what), value[1], None
        raise ModelError(
            runtime.RETURN, f"{what}: return (shape, {{connector: location or [locations]}}) or a catalog part"
        )

    def digest(self, shape: Any) -> str:
        """Its B-rep, without the triangles an export leaves on it, and each node's name and colour."""
        import build123d
        from OCP.BinTools import BinTools, BinTools_FormatVersion

        brep = io.BytesIO()
        BinTools.Write_s(shape.wrapped, brep, False, False, BinTools_FormatVersion.BinTools_FormatVersion_CURRENT)
        looks = [(node.label, str(node.color)) for node in build123d.PreOrderIter(shape)]
        return hashlib.sha256(brep.getvalue() + repr(looks).encode()).hexdigest()

    def export(self, shape: Any, name: str, out: Path, tolerance: dict[str, float]) -> dict[str, Any]:
        import build123d

        from hitlcad.geometry import cadview

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
        cadview.brep_view(cadview.load_step(out / step), name, objects).write(out / view)
        return {"mesh": stl, "step": step, "objects": glb, "view": view, "valid": bool(shape.is_valid)}


if __name__ == "__main__":
    # Loaded before the first job comes: what a build would otherwise wait for.
    import build123d  # noqa: F401
    import trimesh  # noqa: F401

    runtime.serve(Build123d())
