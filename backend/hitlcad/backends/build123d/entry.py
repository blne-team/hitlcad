"""build123d entry point, run as `python -I entry.py <job.json>` in hitlcad's environment (cwd = work dir).

Modes: `plan` (interface values and placements) and `parts` (build part files, export each part in its
own frame as STEP and STL, with its connector frames).
"""

import sys
from pathlib import Path
from typing import Any

from hitlcad.backends import runtime
from hitlcad.backends.runtime import ModelError, Runtime


def matrix(value: Any) -> list[list[float]]:
    """A placement or connector frame as a 4 × 4 matrix: a build123d Location, or a 4 × 4 nested list."""
    from build123d import Location

    if isinstance(value, Location):
        t = value.wrapped.Transformation()
        return [[t.Value(r, c) for c in range(1, 5)] for r in range(1, 4)] + [[0.0, 0.0, 0.0, 1.0]]
    rows = [list(map(float, row)) for row in value] if isinstance(value, list | tuple) else None
    if rows is None or len(rows) != 4 or any(len(r) != 4 for r in rows):
        raise ModelError(f"a placement or connector frame is a build123d Location, got {type(value).__name__}")
    return rows


def _shape(value: Any, what: str) -> Any:
    import build123d

    if hasattr(value, "part") and not isinstance(value, build123d.Shape):  # a BuildPart context
        value = value.part
    if isinstance(value, list | tuple):
        value = build123d.Compound(children=[_shape(v, what) for v in value])
    if not isinstance(value, build123d.Shape):
        raise ModelError(f"{what} is a {type(value).__name__}; expected a build123d Shape")
    if not value.solids():
        raise ModelError(f"{what} has no solids; parts are 3D")
    return value


def _part(
    file: str, name: str, value: Any, declared: list[str], tolerance: dict[str, float], out: Path
) -> dict[str, Any]:
    import build123d

    from hitlcad.sdk import CatalogPart

    what = f"{file}: part {name!r}"
    catalog = None
    if isinstance(value, CatalogPart):
        shape, connectors = value.shape, value.connectors
        catalog = {"provider": value.provider, "designation": value.designation, "material": value.material,
                   "metadata": value.metadata}  # fmt: skip
    elif isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
        shape, connectors = value
    else:
        raise ModelError(f"{what}: return (shape, {{connector: location or [locations]}}) or a catalog part")
    shape = _shape(shape, what)
    missing = [c for c in declared if c not in connectors]
    if missing:
        raise ModelError(f"{what} declares connectors it does not return: {', '.join(missing)}")
    frames = {c: [matrix(v) for v in runtime.as_list(connectors[c])] for c in declared}
    (out / "parts").mkdir(exist_ok=True)
    step, stl, glb = f"parts/{name}.step", f"parts/{name}.stl", f"parts/{name}.glb"
    shape.label = name
    build123d.export_step(shape, str(out / step))
    build123d.export_stl(shape, str(out / stl), tolerance=tolerance["linear"], angular_tolerance=tolerance["angular"])
    # After the STL, with its tolerances: the glTF reuses that triangulation, adding names and colours
    build123d.export_gltf(shape, str(out / glb), binary=True, linear_deflection=tolerance["linear"],
                          angular_deflection=tolerance["angular"])  # fmt: skip
    return {"name": name, "mesh": stl, "step": step, "objects": glb, "connectors": frames, "catalog": catalog,
            "valid": bool(shape.is_valid)}  # fmt: skip


def parts(rt: Runtime, job: dict[str, Any]) -> dict[str, Any]:
    tolerance = {"linear": job["mesh_tolerance_mm"], "angular": job["mesh_angular_tolerance"]}
    files = []
    for n, (file, expected) in enumerate(job["files"].items()):
        try:
            result, reads = rt.call_build(file, f"hitlcad_part_{n}")
            if not isinstance(result, dict):
                raise ModelError(f"{file}: build(p, i) returned {type(result).__name__}; return {{part name: …}}")
            if set(result) != set(expected):
                raise ModelError(f"{file} returned parts {', '.join(map(str, result)) or 'none'}; design.json "
                                 f"declares {', '.join(expected)} in it")  # fmt: skip
            built = [_part(file, name, result[name], expected[name], tolerance, Path(job["out"])) for name in expected]
            files.append({"file": file, "reads": reads.as_json(), "parts": built})
        except Exception as e:  # each file on its own: the others still build
            files.append({"file": file, "error": rt.describe(e)})
    return {"files": files}


if __name__ == "__main__":
    runtime.run(Path(sys.argv[1]), {"plan": lambda rt, job: runtime.plan(rt, job, matrix), "parts": parts})
