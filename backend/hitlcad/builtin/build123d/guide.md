# build123d code contract

A design's code is `interfaces.py`, part files under `parts/`, `lib/` helpers and `assembly.py`.
hitlcad runs each with `p` (parameter values, a read-only dict) and `i` (interface values, as
attributes) and records what each reads. Units are millimetres, Z is up. Keep the code
deterministic: no randomness unless seeded from a parameter.

## interfaces.py

One top-level function per mating value, `def name(p, i)`, returning a number, a string, or a list
or dict of them. Validate there what you compute, with clear `ValueError`s (they reach the human).

```python
import math


def bolt_circle(p, i):
    return (p["housing_od"] + p["ring_od"]) / 4


def bolt_xy(p, i):
    r = i.bolt_circle
    return [[r * math.cos(a), r * math.sin(a)] for a in (math.pi / 4 + k * math.pi / 2 for k in range(4))]
```

## Part files

`def build(p, i)` returns `{part name: (shape, connectors)}` for exactly the parts design.json
assigns to the file. The shape is a build123d `Part`/`Solid`/`Compound` (or a `BuildPart`
context), in the part's own frame: the way it is made (a printed part stands as it prints, its
lowest point at Z=0). `connectors` maps each declared connector to a `Location`, or a list of
Locations for a pattern. A catalog part's value is the `CatalogPart` its provider returns.

Frames: the origin sits where the mating connector's origin will be, Z points out of the part, so
mated connectors face each other.

```python
from build123d import Align, Box, BuildPart, Cylinder, Location, Locations, Pos
from hitlcad.builtin.catalogs.bd_warehouse import ClearanceHole, frames, screw

ON = (Align.CENTER, Align.CENTER, Align.MIN)


def build(p, i):
    t = p["lid_thickness"]
    fastener = screw("SocketHeadCapScrew", "M3-0.5", i.lid_screw_length, "iso4762").native
    with BuildPart() as lid:
        Box(p["width"], p["depth"], t, align=ON)
        with Locations(*[Pos(x, y, t) for x, y in i.bolt_xy]):
            ClearanceHole(fastener, fit="Normal", counter_sunk=False)
    return {
        "lid": (
            lid.part,
            {
                "bottom": Location((0, 0, 0), (180, 0, 0)),  # a face pointing down: Z out of the part
                "screws": frames(fastener),  # where each screw's origin goes, from the hole operation
                "axis": Location((0, 0, t)),
            },
        )
    }
```

- `Location((x, y, z))` faces +Z; `Location((x, y, z), (180, 0, 0))` faces -Z; `Location((x, y, z),
  (0, 90, 0))` faces +X. A hole's frame sits at its opening with Z out of it; a shaft's at its shoulder
  with Z towards its tip.
- Part files import `lib/` and the catalog providers, never each other. Two parts that meet read
  the same interface values instead.

## Catalog parts

`search_catalog("M3 socket head screw")` gives the exact call and the connectors.
`hitlcad.builtin.catalogs.bd_warehouse` makes ISO/ASME screws, nuts, washers, heat-set inserts and bearings
(`screw`, `nut`, `washer`, `heat_set_insert`, `bearing`) and re-exports bd_warehouse's hole
operations, sized to the fastener: `ClearanceHole`, `TapHole`, `InsertHole`, `ThreadedHole`,
`PressFitHole`. Each records where the fastener's origin goes; `frames(part.native)` gives those
frames as a pattern connector that mates `rigid` with the fastener's `placement` connector.
`ClearanceHole` counterbores (or countersinks) a screw's head unless `counter_sunk=False`, so its
frames sit on the seat, not the top face.

```python
from hitlcad.builtin.catalogs.bd_warehouse import bearing, heat_set_insert, screw


def build(p, i):
    return {
        "bearing": bearing("SingleRowDeepGrooveBallBearing", "M8-22-7", "SKT"),
        "insert": heat_set_insert(i.insert_size, "McMaster-Carr"),
        "lid_screw": screw("SocketHeadCapScrew", "M3-0.5", 14, "iso4762"),
    }
```

A vendor STEP: `step.part("vendor/nema17.step", sha256="…", designation="…", material="steel",
connectors={...})` from `hitlcad.catalogs`; its connectors are yours to give.

## assembly.py

`def build(p, i)` returns `{part or component name: [placement, …]}`: one `Location` per instance,
in list order (`planet[0]`, `planet[1]`, …). It places and does nothing else.

```python
from build123d import Pos, Rot


def build(p, i):
    top = i.housing_height
    return {
        "housing": [Pos()],
        "lid": [Pos(0, 0, top)],
        "lid_screw": [Pos(x, y, top + p["lid_thickness"]) for x, y in i.bolt_xy],
        "planet": [Pos(x, y, i.z_gear) * Rot(0, 0, a) for x, y, a in i.planet_poses],
    }
```

`from hitlcad.sdk import mate_to`: `mate_to(frame_on_part, frame_on_target)` returns the placement
that makes a part's connector frame face a target frame, as a `rigid` mate checks.

## Modelling idioms

- Every dimension a person may tune comes from `p[...]` or `i.<value>`, never a literal. Derived
  values (inner = outer - 2 * wall) are fine as expressions.
- Guard invalid combinations with clear `ValueError`s (a wall thicker than half the width).
- `align=(Align.CENTER, Align.CENTER, Align.MIN)` puts a primitive's bottom at Z=0.
- Select topology with `.faces()`, `.edges()`, `.vertices()`, then `.sort_by(Axis.Z)`,
  `.filter_by(Axis.Z)`, `.group_by(Axis.Z)[-1]`, `> Axis.Z`.
- Fillet and chamfer last, with radii smaller than the thinnest adjacent wall.
- Sketch then extrude: `with BuildSketch(Plane.XY.offset(z)): Circle(r)`, then `extrude(amount=h)`;
  subtract with `mode=Mode.SUBTRACT`. Algebra mode is fine too: `Box(10, 10, 5) - Cylinder(2, 5)`.
- Threads, text and lofts are slow or fragile; prefer simplified geometry unless required.

A shape's `color` (`part.color = Color("orange")`) and the `label` of each solid in a compound carry
into the 3D view, the 3MF files and a mixed design's `model.blend`.

The build exports each part in its own frame (`build/parts/<part>.step`, `.stl`, and `.glb` with its
labels and colours), the assembly (`build/model.step`, `mesh.stl`) and the 3D view; connector features,
fits and holes are measured on the B-rep.
