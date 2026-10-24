---
name: hitlcad
description: "Create, edit, build, measure and review the parametric CAD designs of this hitlcad project (designs/<name>/ with design.json and model.py), reviewed by the human in the hitlcad panel in VS Code. Use for any request to make or change a part, enclosure, bracket, figurine or other 3D model here, to check requirements, fit or printability, and to act on what the human selected or pinned in the panel. Takes precedence over generic CAD, build123d, Blender, bpy or mesh skills for building, exporting, rendering and checking; use those only as API reference for model.py."
---

# Working on hitlcad designs

The `hitlcad` MCP server builds, measures and renders the designs in this project; the human
reviews each build in the hitlcad panel in VS Code (3D view, checks, parameters, selections and
pins) and talks to you in your own chat. All hitlcad tools take the design folder's absolute path.

## Layout

Each design is a folder in `designs/` containing:
- `design.json`: name, backend (see `list_backends`), parameters (name, value, min, max, unit,
  …), an optional `brief` (title, summary, key dimensions, features, assumptions, open questions)
  that the panel shows, `requirements`, and an optional `manufacturing` process.
  Write the human's requirements as `{"text": "…", "check": {…}}`; every build verifies each
  check and reports pass, fail or unverified (no check: the human judges it). Add `"part": "lid"`
  to any check but `clearance` to verify one part of an assembly. Checks:
$checks
  Set `manufacturing` when the part will be made; every build is then checked for that process:
$processes
- `model.py`: defines `build(p)`; `p` maps parameter names to values. A design of several parts
  (an assembly) returns `{"base": base, "lid": lid}`: each part gets its own files and checks,
  and the human sees and selects them by name.
- `build/`: outputs of the latest successful build (`model.step` or `model.blend`, `mesh.stl`,
  `preview.png`, `build.log`, `report.json`). Regenerated on every build and git-ignored; point
  the human here when they want the files.

## Working on a design

1. New designs go in `designs/<name>/`; create one with `create_design`. Pick the backend from
   `list_backends`.
2. Read the `hitlcad-<backend>` skill (e.g. `hitlcad-build123d`) before writing model code. It
   is the code contract: what `build(p)` returns, allowed imports and idioms.
3. For a new or substantially changed part, write the `brief` in `design.json` first and tell
   the human your assumptions in chat.
4. Edit `model.py` and `design.json` with your normal file tools. Keep `parameters` in
   `design.json` in sync with what `model.py` reads. Saving rebuilds the panel's view.
5. Call `build_design(path)`. It returns measurements, checks and preview renders: look at the
   images and compare the numbers with what was asked before you report back. For build123d
   designs, `list_geometry(path)` lists every face (or edge) with the numbers selections use, and
   `measure_geometry(path, "face 3", "face 7")` measures distances, angles and wall gaps exactly.
6. The human may tune parameters in the panel; those values are written to `design.json` and
   are deliberate.
7. When the human says "this", "here", "the selected …" or mentions pins: hitlcad's prompt hook
   adds what they point at to their message ("The human is pointing at these in the hitlcad
   panel"). It gives a design in full when its selection or pins changed since you were last
   told, and a one-line reminder while they stay unchanged; call `get_feedback(path)` for the
   full details again. The human may also paste the same text. After addressing pins, call
   `resolve_pins` with a short reply.

## Modelling principles

- Every dimension a person might tune is a parameter in `design.json` (snake_case, with a range,
  unit and label) and is read from `p[...]` in the model.
- Millimetres, Z up, the part resting on the XY plane at Z=0 unless told otherwise.
- Prefer robust, simple geometry over fragile cleverness.
- After each build, compare the measured size and volume with what was asked and look at every
  render view. Never claim printability, fit or strength that no check reports.
- Pins and selections are model-space points in mm, Z up: the same frame as the build report.
  On build123d designs they also name the exact face, edge or vertex: "face 9 of model" is
  `import_step("build/model.step").faces()[9]` (`edges()` and `vertices()` likewise). With the
  panel's filter on Solid they name a whole part instead, "part lid": the human means that part
  as a whole, e.g. to resize, move or remove it. The geometry tools take both forms.

## What hitlcad owns

- Builds, exports, renders and checks run only through the hitlcad tools. Do not run
  build123d, Blender or export scripts yourself, and do not save models, meshes or renders
  outside `build/`: the panel, the revision history and the checks would not see them.
- Never edit `build/`; it is regenerated from `model.py` and `design.json`.
- Other CAD, build123d, Blender, bpy or mesh skills are API reference here: put what they teach
  into `build(p)` in `model.py`, not into scripts of your own, and do not run their tools on
  `build/` outputs.
