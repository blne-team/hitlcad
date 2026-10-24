"""Blender entry point. Runs inside Blender's bundled Python, which cannot import hitlcad: it loads the
model runtime from the backends folder next to it.

Usage: blender -b --factory-startup -P entry.py -- <job.json>   (cwd = work dir)

Scene convention: 1 Blender unit = 1 mm, Z up. Each part file runs in a fresh, empty scene.
"""

import sys
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runtime  # noqa: E402
from runtime import ModelError  # noqa: E402

GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def _reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    units = bpy.context.scene.unit_settings
    units.system = "METRIC"
    units.scale_length = 0.001
    units.length_unit = "MILLIMETERS"


def matrix(value):
    """A placement or connector frame as a 4 × 4 matrix: a mathutils.Matrix, an empty (its world matrix),
    or a 4 × 4 nested list."""
    if isinstance(value, bpy.types.Object):
        value = value.matrix_world
    if isinstance(value, mathutils.Matrix):
        if len(value.row) != 4 or len(value.col) != 4:
            raise ModelError("a placement or connector frame is a 4 × 4 Matrix")
        return [[float(c) for c in row] for row in value]
    rows = [list(map(float, row)) for row in value] if isinstance(value, list | tuple) else None
    if rows is None or len(rows) != 4 or any(len(r) != 4 for r in rows):
        raise ModelError(f"a placement or connector frame is a mathutils.Matrix, got {type(value).__name__}")
    return rows


def _objects(value, what):
    objs = list(value) if isinstance(value, list | tuple) else [value]
    if not objs or not all(isinstance(o, bpy.types.Object) and o.type in GEOMETRY_TYPES for o in objs):
        raise ModelError(f"{what}: a part is a mesh or curve Object, or a list of them")
    return objs


def _select_only(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def _export_objects(objs, path):
    """The part's objects under their names, so a click can say which object it hit. glTF is metres:
    a root scaled by 1/1000 makes the scene's millimetres come out right. Runs after the STL export,
    because it reparents the objects."""
    root = bpy.data.objects.new("hitlcad_mm", None)
    bpy.context.scene.collection.objects.link(root)
    root.scale = (0.001, 0.001, 0.001)
    for o in objs:
        if o.parent is None:
            o.parent = root
    _select_only([root, *objs])
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=True,
                              export_materials="NONE")  # fmt: skip


def _part(file, name, value, declared, out):
    what = f"{file}: part {name!r}"
    if not (isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict)):
        raise ModelError(f"{what}: return (object or [objects], {{connector: Matrix or [Matrices]}})")
    objs = _objects(value[0], what)
    connectors = value[1]
    missing = [c for c in declared if c not in connectors]
    if missing:
        raise ModelError(f"{what} declares connectors it does not return: {', '.join(missing)}")
    frames = {c: [matrix(v) for v in runtime.as_list(connectors[c])] for c in declared}
    (out / "parts").mkdir(exist_ok=True)
    stl = f"parts/{name}.stl"
    _select_only(objs)
    bpy.ops.wm.stl_export(filepath=str(out / stl), export_selected_objects=True, apply_modifiers=True)
    return {"name": name, "mesh": stl, "connectors": frames}, objs


def parts(rt, job):
    files = []
    for n, (file, expected) in enumerate(job["files"].items()):
        try:
            _reset_scene()
            result, reads = rt.call_build(file, f"hitlcad_part_{n}")
            if not isinstance(result, dict):
                raise ModelError(f"{file}: build(p, i) returned {type(result).__name__}; return {{part name: …}}")
            if set(result) != set(expected):
                raise ModelError(f"{file} returned parts {', '.join(map(str, result)) or 'none'}; design.json "
                                 f"declares {', '.join(expected)} in it")  # fmt: skip
            out = Path(job["out"])
            built = [_part(file, name, result[name], expected[name], out) for name in expected]
            for part, objs in built:  # after every STL: the glTF export reparents objects
                part["objects"] = f"parts/{part['name']}.glb"
                _export_objects(objs, str(out / part["objects"]))
            files.append({"file": file, "reads": reads.as_json(), "parts": [p for p, _ in built]})
        except Exception as e:
            files.append({"file": file, "error": rt.describe(e)})
    return {"files": files}


runtime.run(Path(sys.argv[sys.argv.index("--") + 1]), {"plan": lambda rt, job: runtime.plan(rt, job, matrix),
                                                       "parts": parts})  # fmt: skip
