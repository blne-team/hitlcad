"""Blender entry point. Runs inside Blender's bundled Python, which cannot import hitlcad: it loads the
model runtime from the backends folder next to it.

Usage: blender -b --factory-startup -P entry.py -- <job.json>   (cwd = work dir)

Scene convention: 1 Blender unit = 1 mm, Z up. Each part file runs in a fresh, empty scene; the assembly
(`model.blend`) is put together in one more, from the built parts' files.
"""

import sys
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runtime  # noqa: E402
from runtime import ModelError  # noqa: E402

GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
MM_ROOT = "hitlcad_mm"  # the empty that scales a part's millimetres to glTF's metres


def _reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _millimetres()


def _millimetres():
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
    """The part's objects under their names, so a click can say which object it hit, with their materials,
    whose base colours the view, 3MF and model.blend show. glTF is metres: a root scaled by 1/1000 makes the
    scene's millimetres come out right. Runs after the STL export, because it reparents the objects."""
    root = bpy.data.objects.new(MM_ROOT, None)
    bpy.context.scene.collection.objects.link(root)
    root.scale = (0.001, 0.001, 0.001)
    for o in objs:
        if o.parent is None:
            o.parent = root
    _select_only([root, *objs])
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=True)


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


def _collection(name, parent):
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def _import_part(part, collection):
    """A built part's objects into `collection`, in the part's frame: from its glTF, under their names and with
    their materials, or from its STL when its backend has no objects (one object, named by the file)."""
    before = set(bpy.data.objects)
    if part["objects"]:
        bpy.ops.import_scene.gltf(filepath=part["objects"], import_scene_as_collection=False)
    else:
        bpy.ops.wm.stl_import(filepath=part["mesh"])
    new = [o for o in bpy.data.objects if o not in before]
    for root in [o for o in new if o.parent is None and o.name.startswith(MM_ROOT)]:
        for child in root.children:
            child.parent = None  # keeps its transform within the root: the part's frame, in millimetres
        new.remove(root)
        bpy.data.objects.remove(root)
    for o in new:
        for c in o.users_collection:
            c.objects.unlink(o)
        collection.objects.link(o)


def _merge_materials():
    """One material per name and colour: each part's glTF brings its own copy of a material that parts share,
    which Blender would call `fur.001`."""
    for material in list(bpy.data.materials):
        stem, _, n = material.name.rpartition(".")
        first = bpy.data.materials.get(stem) if n.isdigit() else None
        if first is not None and tuple(first.diffuse_color) == tuple(material.diffuse_color):
            material.user_remap(first)
            bpy.data.materials.remove(material)


def assembly(rt, job):
    """The built assembly as one scene (`model.blend`). Each part's objects sit in a collection of their own
    (`lid`, `box/lid` in component box) under `<design> parts`, which the view layer excludes; each instance
    is a collection instance at its placement named by its path from the design (`<design>/box[1]`), so it
    never takes an object's name, under `<design> assembly`. A component is a collection of its own
    instances (`<design>/box/lid`), which the component's instances show."""
    bpy.ops.wm.read_factory_settings(use_empty=True)  # the importer scales glTF's metres to the scene's units
    tree = job["tree"]
    library = _collection(f"{tree['name']} parts", bpy.context.scene.collection)

    def design(node, path, placed):
        sources = {}
        for name, part in node["parts"].items():
            sources[name] = _collection(path + name, library)
            _import_part(part, sources[name])
        for name, child in node["components"].items():
            sources[name] = design(child, f"{path}{name}/", _collection(path + name, library))
        for instance in node["instances"]:
            empty = bpy.data.objects.new(f"{tree['name']}/{path}{instance['name']}", None)
            empty.instance_type = "COLLECTION"
            empty.instance_collection = sources[instance["of"]]
            empty.matrix_basis = mathutils.Matrix(instance["transform"])
            placed.objects.link(empty)
        return placed

    design(tree, "", _collection(f"{tree['name']} assembly", bpy.context.scene.collection))
    _merge_materials()
    bpy.context.view_layer.layer_collection.children[library.name].exclude = True
    _millimetres()
    bpy.ops.wm.save_as_mainfile(filepath=job["target"])
    return {}


runtime.run(Path(sys.argv[sys.argv.index("--") + 1]), {"plan": lambda rt, job: runtime.plan(rt, job, matrix),
                                                       "parts": parts, "assembly": assembly})  # fmt: skip
