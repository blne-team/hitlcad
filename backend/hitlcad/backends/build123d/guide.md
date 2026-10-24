# build123d authoring contract

Write one Python module that defines `build(p)`. `p` is a dict of parameter values keyed by
parameter name. Return a build123d `Part`, `Solid`, `Compound`, a `BuildPart` context, or a list
of solids (they become one compound). Units are millimetres, Z is up; put the part on the XY
plane (bottom at Z=0) unless the brief says otherwise.

Rules:
- Keep `model.py` self-contained: build123d plus the standard library and numpy. It runs in a
  fresh process with the output folder as its working directory.
- Every dimension that a human may want to tune must come from `p[...]`, never a literal.
  Derived values (e.g. inner = outer - 2 * wall) are fine as expressions.
- Guard against invalid combinations with clear `ValueError`s, e.g. a wall thicker than half
  the width. These messages are shown to the user.
- Keep it deterministic: no randomness unless seeded from a parameter.

Builder-mode skeleton:

```python
from build123d import *


def build(p):
    w, d, h, wall = p["width"], p["depth"], p["height"], p["wall"]
    with BuildPart() as part:
        Box(w, d, h, align=(Align.CENTER, Align.CENTER, Align.MIN))
        # hollow it, leaving the top open
        top = part.faces().sort_by(Axis.Z)[-1]
        offset(amount=-wall, openings=top)
        # four corner holes through the floor
        with Locations(part.faces().sort_by(Axis.Z)[0]):
            with GridLocations(w - 4 * wall, d - 4 * wall, 2, 2):
                Hole(radius=p["hole_d"] / 2)
        fillet(part.edges().filter_by(Axis.Z), radius=p["corner_r"])
    return part
```

Idioms that avoid common failures:
- `align=(Align.CENTER, Align.CENTER, Align.MIN)` puts a primitive's bottom at Z=0.
- Select topology with `.faces()`, `.edges()`, `.vertices()` then `.sort_by(Axis.Z)`,
  `.filter_by(Axis.Z)` (edges parallel to Z), `.group_by(Axis.Z)[-1]` (top group), `> Axis.Z`.
- Fillet/chamfer last, and with radius smaller than the thinnest adjacent wall; a failing
  fillet usually means the radius is too large or the edge selection is wrong.
- Sketch then extrude: `with BuildSketch(Plane.XY.offset(z)): Circle(r)` then
  `extrude(amount=h)`; subtract with `extrude(amount=-h, mode=Mode.SUBTRACT)` or `Mode.SUBTRACT`
  on any primitive.
- Positioning: `with Locations((x, y, z)):`, `PolarLocations(radius, count)`,
  `GridLocations(x_spacing, y_spacing, x_count, y_count)`, or `Pos(x, y, z) * shape`,
  `Rot(0, 0, 45) * shape` in algebra mode.
- Algebra mode is also fine: `part = Box(10, 10, 5) - Cylinder(2, 5)`.
- Threads, text and lofts are slow or fragile; prefer simplified geometry unless required.
- Assemblies: when the design is several parts (a base and a lid, a case and its insert),
  return a dict `{"base": base, "lid": lid}`, each part placed where it sits in the assembly.
  Names use letters, digits, `.`, `_` or `-`. Every part gets its own STEP and STL, overlaps
  between parts are reported, and requirements can name a part or the clearance between two.
  Share dimensions through `p` so the parts stay consistent.

The build produces `mesh.stl` (mm) and `model.step` (plus `part-<name>.step/.stl` per part). You receive
measured bounding box, volume, watertightness and body count after each build, plus preview
renders when your model supports images.
