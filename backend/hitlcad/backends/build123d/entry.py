"""Subprocess entry point for build123d models. Self-contained: imports nothing from hitlcad.

Usage: python -I entry.py <job.json>   (cwd = output directory)
"""

import json
import re
import sys
import traceback

MODEL_FILENAME = "model.py"
PART_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _model_traceback(exc: BaseException) -> str:
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == MODEL_FILENAME]
    lines = [f"  model.py line {f.lineno}, in {f.name}: {f.line or ''}" for f in frames]
    return "\n".join(lines + [f"{type(exc).__name__}: {exc}"])


def _as_shape(result, build123d):
    if result is None:
        raise TypeError("build(p) returned None; return a Part/Solid/Compound")
    if hasattr(result, "part") and not isinstance(result, build123d.Shape):  # BuildPart context
        result = result.part
    if isinstance(result, list | tuple):
        shapes = [_as_shape(r, build123d) for r in result]
        result = build123d.Compound(children=shapes)
    if not isinstance(result, build123d.Shape):
        raise TypeError(f"build(p) returned {type(result).__name__}; expected a build123d Shape")
    if not result.solids():
        raise ValueError("build(p) returned a shape without solids; 3D output is required")
    return result


def _parts(result, build123d):
    """build(p) returns one shape (a part), or a dict {part name: shape} (an assembly)."""
    if not isinstance(result, dict):
        return None
    if not result:
        raise ValueError("build(p) returned an empty dict; an assembly needs at least one part")
    parts = {}
    for name, value in result.items():
        if not isinstance(name, str) or not PART_NAME.match(name):
            raise ValueError(f"part name {name!r}: use letters, digits, '.', '_' or '-'")
        parts[name] = _as_shape(value, build123d)
        parts[name].label = name
    return parts


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as f:
        job = json.load(f)
    result: dict = {"ok": False, "files": [], "info": {}}
    try:
        import build123d

        with open(MODEL_FILENAME, encoding="utf-8") as f:
            source = f.read()
        namespace = {"__name__": "hitlcad_model"}
        try:
            exec(compile(source, MODEL_FILENAME, "exec"), namespace)
            if not callable(namespace.get("build")):
                raise TypeError("model.py must define a top-level function build(p)")
            built = namespace["build"](dict(job["params"]))
            parts = _parts(built, build123d)
            shape = None if parts else _as_shape(built, build123d)
        except Exception as e:  # model error: report in model terms
            result["error"] = _model_traceback(e)
            return 0

        part_files = []
        for name, part in (parts or {}).items():  # before grouping them: a grouped part no longer exports alone
            build123d.export_stl(part, f"part-{name}.stl", tolerance=job.get("tolerance", 0.01), angular_tolerance=0.1)
            build123d.export_step(part, f"part-{name}.step")
            part_files += [
                {"name": f"part-{name}.stl", "media_type": "model/stl", "role": "part_mesh"},
                {"name": f"part-{name}.step", "media_type": "model/step", "role": "part_exchange"},
            ]
        if parts:
            shape = build123d.Compound(children=list(parts.values()))  # the assembly, its parts named by label
        build123d.export_stl(shape, "mesh.stl", tolerance=job.get("tolerance", 0.01), angular_tolerance=0.1)
        build123d.export_step(shape, "model.step")
        bb = shape.bounding_box()
        result["info"] = {
            "brep_volume_mm3": shape.volume,
            "brep_valid": bool(shape.is_valid),
            "solids": len(shape.solids()),
            "faces": len(shape.faces()),
            "brep_bbox": [[bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]],
        }
        result["files"] = [
            {"name": "mesh.stl", "media_type": "model/stl", "role": "mesh"},
            {"name": "model.step", "media_type": "model/step", "role": "exchange"},
        ]
        result["files"] += part_files
        result["ok"] = True
    except Exception:
        result["error"] = "internal build error:\n" + traceback.format_exc(limit=8)
    finally:
        with open("result.json", "w", encoding="utf-8") as f:
            json.dump(result, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
