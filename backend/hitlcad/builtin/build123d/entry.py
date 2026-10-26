"""build123d entry point, run as `python -I -B -u -X utf8 entry.py` in hitlcad's environment: it loads build123d,
then waits for its job's path on stdin (`runtime.run`).

Modes: `plan` (interface values and placements) and `parts` (build part files, export each part in its
own frame as STEP and STL, with its connector frames).
"""

from pathlib import Path
from typing import Any

from hitlcad.execution import runtime
from hitlcad.execution.runtime import ModelError, Runtime


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
        raise ModelError(
            runtime.RETURN, f"{what}: return (shape, {{connector: location or [locations]}}) or a catalog part"
        )
    shape = _shape(shape, what)
    missing = [c for c in declared if c not in connectors]
    if missing:
        raise ModelError(
            runtime.CONNECTORS, f"{what} declares connectors it does not return: {', '.join(missing)}", file
        )
    frames = {c: [matrix(v) for v in runtime.as_list(connectors[c])] for c in declared}
    (out / "parts").mkdir(exist_ok=True)
    step, stl, glb = f"parts/{name}.step", f"parts/{name}.stl", f"parts/{name}.glb"
    shape.label = name
    build123d.export_step(shape, str(out / step))
    build123d.export_stl(shape, str(out / stl), tolerance=tolerance["linear"], angular_tolerance=tolerance["angular"])
    # After the STL, with its tolerances: the glTF reuses that triangulation, adding names and colours. Not the
    # shapes' own materials (hitlcad's come from design.json): their PBR texture maps would be most of the file.
    for node in build123d.PreOrderIter(shape):
        node.material = None
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
                raise ModelError(
                    runtime.RETURN,
                    f"{file}: build(p, i) returned {type(result).__name__}; return {{part name: …}}",
                    file,
                )
            if set(result) != set(expected):
                raise ModelError(runtime.PARTS, f"{file} returned parts {', '.join(map(str, result)) or 'none'}; design.json "
                                 f"declares {', '.join(expected)} in it")  # fmt: skip
            built = [_part(file, name, result[name], expected[name], tolerance, Path(job["out"])) for name in expected]
            files.append({"file": file, "reads": reads.as_json(), "parts": built})
        except Exception as e:  # each file on its own: the others still build
            files.append({"file": file, "error": rt.failure(e, file)})
    return {"files": files}


if __name__ == "__main__":
    import build123d  # noqa: F401  # loaded before the job comes: what a build would otherwise wait for

    runtime.run({"plan": lambda rt, job: runtime.plan(rt, job, matrix), "parts": parts})
