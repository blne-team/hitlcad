"""Blender entry point. Runs inside Blender's bundled Python; imports nothing from hitlcad.

Usage: blender -b --factory-startup -P entry.py -- <job.json>   (cwd = output directory)

Scene convention: 1 Blender unit = 1 mm, Z up.
"""

import json
import os
import re
import sys
import traceback

import bpy

MODEL_FILENAME = "model.py"
GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
PART_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _model_traceback(exc):
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == MODEL_FILENAME]
    lines = [f"  model.py line {f.lineno}, in {f.name}: {f.line or ''}" for f in frames]
    return "\n".join(lines + [f"{type(exc).__name__}: {exc}"])


def _reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    units = bpy.context.scene.unit_settings
    units.system = "METRIC"
    units.scale_length = 0.001
    units.length_unit = "MILLIMETERS"


def _collect(result):
    if result is None:
        objs = list(bpy.context.scene.objects)
    elif isinstance(result, bpy.types.Object):
        objs = [result]
    elif isinstance(result, list | tuple):
        objs = list(result)
    else:
        raise TypeError(f"build(p) returned {type(result).__name__}; return None, an Object, or a list of Objects")
    objs = [o for o in objs if isinstance(o, bpy.types.Object) and o.type in GEOMETRY_TYPES]
    if not objs:
        raise ValueError("build(p) produced no mesh/curve objects")
    return objs


def _parts(result):
    """build(p) returns objects (one part), or a dict {part name: object or list of objects} (an assembly)."""
    if not isinstance(result, dict):
        return None
    if not result:
        raise ValueError("build(p) returned an empty dict; an assembly needs at least one part")
    parts = {}
    for name, value in result.items():
        if not isinstance(name, str) or not PART_NAME.match(name):
            raise ValueError(f"part name {name!r}: use letters, digits, '.', '_' or '-'")
        parts[name] = _collect(value if isinstance(value, list | tuple) else [value])
    return parts


def _select_only(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def _stats(objs):
    deps = bpy.context.evaluated_depsgraph_get()
    verts = tris = 0
    for o in objs:
        mesh = o.evaluated_get(deps).to_mesh()
        mesh.calc_loop_triangles()
        verts += len(mesh.vertices)
        tris += len(mesh.loop_triangles)
        o.evaluated_get(deps).to_mesh_clear()
    return {"objects": len(objs), "vertices": verts, "triangles": tris, "names": [o.name for o in objs][:50]}


def _export_objects(objs, path):
    """The result's objects under their names, so a click on the mesh can say which object it hit.
    glTF is metres: a root scaled by 1/1000 makes the scene's millimetres come out right. Runs last,
    after the other exports, because it reparents the objects."""
    root = bpy.data.objects.new("hitlcad_mm", None)
    bpy.context.scene.collection.objects.link(root)
    root.scale = (0.001, 0.001, 0.001)
    for o in objs:
        if o.parent is None:
            o.parent = root
    _select_only([root, *objs])
    bpy.ops.export_scene.gltf(
        filepath=path, export_format="GLB", use_selection=True, export_apply=True, export_materials="NONE"
    )


def main():
    with open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8") as f:
        job = json.load(f)
    out = os.getcwd()
    result = {"ok": False, "files": [], "info": {}}
    try:
        _reset_scene()
        with open(MODEL_FILENAME, encoding="utf-8") as f:
            source = f.read()
        namespace = {"__name__": "hitlcad_model"}
        try:
            exec(compile(source, MODEL_FILENAME, "exec"), namespace)
            if not callable(namespace.get("build")):
                raise TypeError("model.py must define a top-level function build(p)")
            built = namespace["build"](dict(job["params"]))
            parts = _parts(built)
            objs = [o for part in parts.values() for o in part] if parts else _collect(built)
        except Exception as e:
            result["error"] = _model_traceback(e)
            return

        # Drop everything that is not part of the result (cameras, lights, helpers).
        keep = set(objs)
        for o in list(bpy.context.scene.objects):
            if o not in keep and o.type not in GEOMETRY_TYPES:
                bpy.data.objects.remove(o, do_unlink=True)

        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "model.blend"), copy=True)
        result["info"] = _stats(objs)

        _select_only(objs)
        bpy.ops.wm.stl_export(
            filepath=os.path.join(out, "mesh.stl"), export_selected_objects=True, apply_modifiers=True
        )
        result["files"] = [
            {"name": "mesh.stl", "media_type": "model/stl", "role": "mesh"},
            {"name": "model.blend", "media_type": "application/x-blender", "role": "native"},
        ]
        for name, part in (parts or {}).items():
            _select_only(part)
            bpy.ops.wm.stl_export(
                filepath=os.path.join(out, f"part-{name}.stl"), export_selected_objects=True, apply_modifiers=True
            )
            result["files"].append({"name": f"part-{name}.stl", "media_type": "model/stl", "role": "part_mesh"})
        _export_objects(objs, os.path.join(out, "objects.glb"))
        result["files"].append({"name": "objects.glb", "media_type": "model/gltf-binary", "role": "objects"})
        result["ok"] = True
    except Exception:
        result["error"] = "internal build error:\n" + traceback.format_exc(limit=8)
    finally:
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)


main()
