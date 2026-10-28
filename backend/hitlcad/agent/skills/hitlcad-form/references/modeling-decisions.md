# Choose geometry operations from intent

Use when translating a concept into part geometry, or when an operation obscures the intended form.
The installed backend guide remains authoritative for signatures, imports, units and return values.

## Choose a representation

| Intended relationship | Useful construction | Evidence to inspect |
| --- | --- | --- |
| A constant section through a thickness | Closed profile and extrusion | Profile closure, thickness and resulting body count |
| Rotational symmetry | Revolved section | Axis, inner/outer sections and axial interfaces |
| Shape changing along a path | Sweep or loft with deliberate sections | Twist, section correspondence, local walls and self-intersection |
| Machined recess or hollow housing | Stock/envelope with explicit removal, or a suitable shell operation | Floor, walls, open faces and tool/process access |
| Repeated members or cutouts | One motif placed by derived coordinates | End conditions, retained material and connector patterns |
| Free sculptural mass or section study | Parameterized mesh, curves or suitable modifiers | Actual evaluated shape, closed boundary and interface resolution |

Start with the simplest construction that expresses the relationship. Do not approximate an exact
shaft interface with a decorative shape or replace smooth organic form with a stack of unrelated
features solely because those operations are familiar.

## B-rep decisions

Use explicit imports and part-local coordinates. Construct the functional feature, then place the
part through assembly.py. Select edges and faces by meaningful geometry or orientation where useful;
an incidental list index can change after a feature is added. A blanket fillet can consume critical
material or fail at a small edge. Choose the intended edge set and a radius supported by local
geometry and process; there is no universal minimum cosmetic radius.

An explicit cavity cut makes a simple teaching shell's floor and wall dimensions visible. A shell
operation may be more concise for a suitable shape but still needs inspection at transitions. A
section is often the clearest evidence for a wall, floor or joint; bounding dimensions alone cannot
settle an internal feature.

## Mesh decisions

Build closed boundaries with consistent winding and meaningful connectivity. Prefer data-oriented
mesh construction when operator context is unnecessary. Use modifiers for a clear geometric purpose,
and inspect their evaluated result through the framework. Smooth shading does not add geometric
accuracy. Extra subdivision can change a mating feature and does not repair a self-intersection.

For a regular cylinder of radius `r` with `n` segments, the maximum radial difference between its
circle and an inscribed polygon is `r * (1 - cos(pi / n))`. Compare that faceting with the required
fit and the framework's reported uncertainty; diametral consequences can be larger than the radial
error. Protect interfaces from indiscriminate decimation. Use enough resolution where geometry
requires it and keep simpler regions economical.

## Adapt local skills without their execution paths

The reviewed local `build123d-cad`, `bpy` and `blender-skill` skills offer operation examples,
geometry/preview verification and inspection before transformation. This guide applies those ideas
using hitlcad's existing interfaces. Its implementations are original and do not require those
skills to be installed in a recipient project.

Keep 1 Blender unit = 1 mm in hitlcad, even when a standalone recipe uses metres. Keep
`build(p, i)` returns and declared connectors instead of standalone `result` or `show_object`
conventions. Use framework builds, renders and exports rather than their separate runners or GUI
bridges. Treat local version observations, fixed radii and delivery budgets as conditional evidence;
verify against the active backend instead of promoting them into universal rules.

Primary API references: [build123d operations](https://build123d.readthedocs.io/en/latest/operations.html)
and [Blender mesh data](https://docs.blender.org/api/current/bpy.types.Mesh.html). Consult the version
actually used by the framework when a recipe depends on a particular API.
