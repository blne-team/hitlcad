# Assembly model

The model of a hitlcad design: what design.json declares, what the code produces, and what every
build verifies. [architecture.md](architecture.md) describes the system around it.

## Why

A design used to be one `model.py` whose `build(p)` returned a shape or a dict of named shapes placed
in world coordinates. How the parts related existed only in that code. In the gearbox test model,
the bolt circle shared by housing and lid was a local variable; the motor, bearing, screws and
inserts it was built around were not in the model; its three planets were three unrelated parts;
and the sun's bore was Ø5.2 on a Ø5.0 shaft because a radial clearance parameter was read as a
diametral one. Nothing knew which features were meant to meet, so nothing checked them.

Real products have tens to thousands of parts in nested sub-assemblies, made with different
processes, mixed with purchased parts, produced in variants and handed to manufacturing as a bill
of materials and per-part files. In this model the agent declares the product's structure before it
writes geometry, the code produces geometry that honours it, and every build verifies the two
against each other.

## Principles

They come from how SolidWorks, Onshape, Fusion, FreeCAD, CadQuery, build123d and Blender
practitioners structure assemblies.

1. **Define once, place many.** A part is defined once and placed as instances.
2. **Skeleton down.** Mating values are computed once, in `interfaces.py`, and every part reads
   them from there. Parts never reference each other's geometry (SolidWorks' layout-sketch best
   practice, Onshape's Variable Studio, made enforceable).
3. **Connectors where partners meet.** A part carries named frames at the features other parts
   attach to (Onshape mate connectors, SolidWorks mate references, Fusion joint origins, build123d
   joints).
4. **Mates describe degrees of freedom.** `rigid`, `revolute`, `slider`, `cylindrical`, `planar`,
   `ball` mean what they mean in every CAD tool. Fits, gears, fasteners and clearance are separate
   concepts.
5. **Check, don't solve.** The agent places instances in code (as build123d's `connect_to` does);
   hitlcad verifies that the placement honours every mate.
6. **One design until there is a reason for two.** A product is one design, its related parts
   grouped by file. A sub-assembly becomes a separate design only for reuse, an independent life, a
   unit of supply or scale. It then exposes chosen connectors, and its parent mates to those and
   never reaches inside it.
7. **Library first.** Screws, nuts, washers, inserts and bearings come from a catalog provider with
   their connectors and data; the holes for them are made by hole operations sized to the fastener.
8. **Everything declared is verified.** Every build verifies every connector, mate, relation,
   fastener set and requirement, or reports it `unverified` with the reason. Nothing is estimated,
   guessed or tried a second way.
9. **The core knows no domain.** The pipeline, the address grammar, the design graph and the tools
   know parts, connectors, mates, relations, fastener sets and analyses as abstractions. What a gear,
   a bearing seat, an ISO fit, PETG or FDM is lives in union members, catalog providers and data
   tables. Tolerances and sampling are settings, not constants in checks.

Non-goals: constraint solving, release workflows and part-number allocation (PDM/ERP territory: git
and the user's own systems hold them), load simulation (FEA).

## Terms

| Term | Definition | Elsewhere |
| --- | --- | --- |
| **Project** | A folder of designs that may use each other (`hitlcad init`). | workspace |
| **Design** | A folder with `design.json`: an assembly of instances, with its own parameters, configurations, requirements and brief. Built on its own, and usable by other designs as a component. | assembly document |
| **Part** | One rigid body made or bought as one piece, built in its own frame, which is how it is made. **Made** parts have a material and a process; **catalog** parts are bought. | part, purchased part |
| **Component** | Another design used inside this one, its parameters bound by this one. | sub-assembly |
| **Instance** | One placement of a part or component: `lid`, `planet[2]`, `tilt/planet[2]` inside a component. | occurrence |
| **Interface value** | A mating value computed once from the parameters in `interfaces.py`. | layout sketch, skeleton |
| **Connector** | A named frame, or pattern of frames, on a part, at a feature where another part meets it. | mate connector, joint origin |
| **Mate** | Two connectors held together with a stated set of free degrees of freedom, an optional fit and optional limits. | mate, joint |
| **Relation** | A coupling between two connectors' motions: gears, rack and pinion, lead screw. | gear mate, relation |
| **Fastener set** | A catalog fastener through a stack of holes into a thread or insert. | Smart Fasteners |
| **Configuration** | A named set of parameter values: one product variant. | configuration |

## Design folders

```
designs/gearbox/
  design.json      the structure, parameters, configurations, requirements, outputs, brief
  interfaces.py    mating values, one function each: def lid_bolt_xy(p, i)
  parts/
    housing.py     def build(p, i) -> {"housing": (shape, connectors)}
    gears.py       sun and planet: related parts in one file
    hardware.py    catalog parts: bearing, insert, screws
    …
  lib/             helpers part files share, not parts (gear.py: involute outlines)
  vendor/          vendor STEP files
  references/      pictures `silhouette` requirements compare the design with
  assembly.py      def build(p, i) -> {part or component: [placement, …]}
  build/           outputs of the latest successful build; git-ignored
designs/pan_tilt/  a design using gearbox twice, as two components
```

A part file defines one or more parts. Closely related parts (a gear set, a box and its lid) share
one file and one parametric history, like an Onshape Part Studio; the part cache works per file.
Parts that only meet through interface values get their own files. A design may not contain
itself, directly or through its components.

## design.json

```jsonc
{
  "hitlcad": 2,
  "name": "Planetary gearbox",
  "number": "GBX-100",                       // optional, the user's own part number
  "backend": "build123d",                    // for parts that do not set their own
  "parameters": [ /* {name, label, type, value, min, max, step, unit, choices, description} */ ],
  "configurations": {"ratio_5": {"sun_teeth": 12, "planet_teeth": 18}},
  "ground": "housing",                       // the instance every mate chain leads back to
  "parts": { /* name -> part */ },
  "components": { /* name -> component */ },
  "exports": { /* connector name -> connector of a direct child, for designs using this one */ },
  "mates": [ /* … */ ],
  "relations": [ /* … */ ],
  "fasteners": [ /* … */ ],
  "requirements": [ /* {"text": "…", "check": {…}} */ ],
  "manufacturing": {"process": "fdm"},       // default process of made parts
  "verification": {"position_mm": 0.01, "angle_deg": 0.05, "motion_steps": 2000},
  "outputs": ["bom", "3mf", "urdf", "sdf"],  // exporters run on every successful build
  "brief": { /* title, summary, key_dimensions, features, assumptions, open_questions */ }
}
```

Names of parameters, parts, components, configurations and connectors are letters, digits and `_`,
and a name means one thing within a design. `verification`'s fields default to hitlcad's settings
(`HITLCAD_VERIFY_*`); no check carries a number of its own. The design's own validation (names,
configuration values within their parameters' ranges, one backend per part file) happens when the
file loads; how the names relate is checked by the structure analysis of each build.

### Parts

```jsonc
"lid": {
  "role": "closes the housing, holds the output bearing",
  "file": "parts/lid.py",
  "number": "GBX-104",
  "material": "PLA",                          // a name in the material registry
  "manufacturing": {"process": "fdm"},        // overrides the design's process
  "backend": "build123d",                     // overrides the design's backend
  "connectors": {
    "bottom": {"kind": "face", "symmetry": 4},
    "screws": {"kind": "hole", "count": 4},
    "bearing_pocket": {"kind": "hole"}
  }
},
"bearing": {
  "role": "supports the output shaft",
  "file": "parts/hardware.py",
  "catalog": {"designation": "608-2RS deep-groove ball bearing, 8 × 22 × 7", "fidelity": "exact",
              "supplier": "", "mpn": ""},
  "connectors": {"outer": {"kind": "shaft", "symmetry": "axial"}, "bore": {"kind": "hole", "symmetry": "axial"}}
}
```

- A **made** part has a `material` and inherits or overrides `manufacturing`.
- A **catalog** part has `catalog`: what to buy and its `fidelity` (`exact`: the maker's geometry;
  `simplified`: an envelope, so fits against it are approximate). It is never checked for
  manufacture. Its geometry is built in its part file, usually by a catalog provider, whose
  material and data the build records.
- Every part declares the connectors other parts mate to, catalog parts included. A provider must
  supply the declared ones.
- Parts in one file share a backend; a design may mix backends.
- A part that deforms (a spring) names its two end connectors, seat first: `"stretches": ["seat",
  "top"]`. It is modelled at its length as assembled, each end mated to what it sits on (a rigid mate
  to the head, another to the retainer). Motion moves it with neither end's part and leaves it out of
  collisions, its length the `span` requirement between its ends keeps; the panel stretches it between
  them as they move. Interference checks it where it sits.

### Connectors

| Field | Meaning |
| --- | --- |
| `kind` | A member of the `ConnectorKind` union: `frame` (a datum), `face`, `hole`, `shaft`, `gear` |
| `count` | Frames in a pattern: a number or a parameter name; default 1 |
| `symmetry` | Rotations about Z that look the same: n (every 360°/n) or `"axial"`; default none |
| the kind's own fields | Nominal values the kind cannot measure, naming parameters or interface values (`"interfaces.ring_teeth"`), never literals. `gear`: `teeth`, `module`, `width`, `internal` |

**Frame convention.** The origin is where the mating connector's origin will be; Z points out of
the part, so mated connectors face each other; X fixes the rotation about Z where it matters (a
bolt pattern's clocking), and `symmetry` says where it does not.

**Measurement.** Each kind measures its feature on the part's B-rep, in the part's frame:

- `hole`: the inward cylinder coaxial with the frame, below the origin (it opens along +Z): its
  diameter and depth. Of cylinders that start equally close, the innermost (a bearing's bore, not
  the gap between its races).
- `shaft`: the outward cylinder coaxial with the frame, above the origin (from its shoulder along
  +Z): its diameter and length. Of cylinders that start equally close, the outermost.
- `face`: the planar face with Z as its outward normal and the origin on its plane, inside its
  outline (the centre of a rim or an annulus is fine): its area.
- `gear`: in sections across the axis at 10, 50 and 90 % of the face width, its tip diameter (how
  far the teeth reach from the axis; how near, for an internal gear), which must be m(z + 2)
  (m(z − 2) internal) within a tenth of the module, and its teeth, counted where the section crosses
  the circle a module inside the tip (about the pitch circle), which must be the declared number in
  every section.
- `frame`: nothing; it is a datum, and has no result of its own (its count is still checked).

A hole or shaft is coaxial with the frame within `verification` (`position_mm`, `angle_deg`), its
wall goes at least 60 % round (a slot or a teardrop's point may open it; a half-round notch at an
edge is not a hole), and it starts at the origin, within `position_mm`, or farther only where
coaxial surfaces of revolution lead to it from the origin (a countersink's cone, a chamfer or a
fillet at the edge: `Ø3.400 × 8.00 deep, starting 2.00 mm from the origin`). At a counterbore's top
the hole is the counterbore; a fastener's clearance takes the hole under it. A frame with no matching feature
fails and names the nearest one found and what keeps it from being the frame's ("lid.screws[2]: no
hole on the frame's axis below its origin; nearest: Ø5.000 hole, axis 0.020 mm off (allowed 0.01),
0.00° tilt"), so a frame cannot drift from its geometry.

Every part is measured, whatever it is made of (`geometry/features.py`). Analyses ask a part's
`Features`, and one of two adapters answers: on a B-rep part, its faces; on a mesh part, its
triangles. Round features are probed in sections across the frame's axis, at stations along it: a
hole with rays from the axis outwards, which land on its wall facing the axis, a shaft with rays
from outside inwards, which land on its skin facing away, even when it is a tube. A station is on a
round surface where most rays land at one distance from the axis (the rest may leave through a slot
or a teardrop's point); a circle fitted through the vertices of the facets they land on gives its
diameter and centre, the same however the frame is turned, and the station is on a cylinder where
those vertices lie on the circle (a cone's do not). The feature runs while its stations stay on one
cylinder. A mesh measurement carries its facets' sagitta as its uncertainty (`Ø3.800 × 6.00 long
(on the mesh, ±0.018)`), and a fit allows for it; positions and angles are held to the same
`verification` as on a B-rep. Faces are trimesh's facets. On the build123d examples the two adapters
agree on every connector. What a mesh cannot measure at all (CNC inside corners, sheet bends, a
part's inventory of holes) is `unverified`, with the reason.

### Components

**One design is the default.** Make a separate design used as a component only for reuse (used in
more than one place), an independent life (built, versioned, tested or released on its own), a unit
of supply (bought or made elsewhere as one item) or scale.

```jsonc
// pan_tilt/design.json
"components": {
  "pan":  {"design": "../gearbox", "configuration": "ratio_5", "flexible": true},
  "tilt": {"design": "../gearbox", "bind": {"output_length": "interfaces.tilt_shaft_length"}, "flexible": true}
}
```

`configuration` picks one of the component's configurations; `bind` sets its parameters from this
design's parameters or interface values, over that configuration. Each component is built as its
own design with those values, cached by them (and by its backends' code, the materials it sees and the
analyses that judged it), and kept in the build's `components/<name>/`. It
exposes only what its `exports` name (`{"output": "carrier.output"}`); the parent mates to
`tilt.output`, never inside. An export of a connector of a part placed several times is a pattern.
A `flexible` component's internal mates take part in the parent's motion analysis; a rigid one
moves as one body.

### Mates

```jsonc
{"type": "cylindrical", "a": "motor.shaft", "b": "sun.bore", "fit": {"clearance": [0.0, 0.1]}}
```

Ends are `part.connector` (every instance of the part), `part[k].connector` (one), or
`component.export`; they name direct children only. Patterns pair one-to-one, nearest axis first.

| Type | Free | Verified after placement, within `verification` |
| --- | --- | --- |
| `rigid` | nothing | origins coincide (apart by `offset` along a's Z); Z axes face each other; X axes agree up to the features' shared symmetry |
| `revolute` | rotation about Z | origins coincide (apart by `offset`); Z axes face each other |
| `slider` | translation along Z | origins on one line along Z; Z axes face each other; X axes agree up to symmetry |
| `cylindrical` | rotation about and translation along Z | origins on one line along Z; Z axes face each other |
| `planar` | translation in the plane, rotation about Z | b's origin on a's plane (apart by `offset`); Z axes face each other |
| `ball` | rotation about the origin | origins coincide |

Optional fields:

- `fit`, between a `hole` and a `shaft`: `{"clearance": [min, max]}` in diametral mm (negative is
  interference), which the measured clearance must lie in, or `{"iso": "H7/g6"}`, an ISO 286 fit.
  The measured clearance is hole Ø − shaft Ø, adjusted by each part's process allowance (FDM's and
  resin's `hole_allowance_mm`, `shaft_allowance_mm`), within the measurements' uncertainty on mesh
  parts. An ISO fit's hole and shaft are modelled at one basic size, which the build checks, and its
  result gives the classes' limits there, for the drawing (`fit H7/g6 at Ø8: hole +0.015/0, shaft
  -0.005/-0.014, clearance 0.005 … 0.029 mm`). The limits come from ISO 286-1's tables, kept in
  `core/iso286.py` and checked against every value ISO 286-2 tabulates: holes A … ZC and shafts
  a … zc in grades 1 to 18, at sizes above 0 up to 3150 mm, as far as the standard defines each; a
  class it does not define at the size fails the mate, saying what it does define. A mate that moves
  (revolute, slider, cylindrical) fails with a fit whose greatest clearance is 0 or less. A fit that allows interference lets the parts
  overlap by the ring their measured diameters make (π/4·(D² − d²) over the engaged length, give or
  take the facets), no more; one that does not lets them touch.
- `limits`: the range of the free motion (`{"angle": [0, 110]}` degrees, `{"travel": [0, 40]}` mm).
  The placed position must lie within it, and motion analysis sweeps it.
- `offset`: a designed gap along a's Z, for `rigid`, `revolute` and `planar`.
- `flip`: b's Z runs with a's instead of facing it (a shaft entering a bore from the end opposite
  the bore's declared opening, as Onshape's flip).
- `overlap`: b's part is set into a hole of a's part and overlaps its wall (a heat-set insert's
  knurls, a modelled thread, a pressed pin). a's part has no material on the hole's axis where b's part
  sits (nor within the hole's radius, when a's end is a measured `hole`): a part set into solid
  material, where the hole was never cut or is too shallow, is reported as interference.

**Ground and coverage.** Mates, relations and fastener sets connect every instance to `ground`; an
instance nothing connects is floating, the real-CAD "under-defined component".

### Relations

| Type | Ends | Verified | Motion |
| --- | --- | --- | --- |
| `gear` | two `gear` connectors | same module; axes parallel; centre distance m(z₁ ± z₂)/2 (− for an internal gear); face widths overlap; not verified until both gears measure as declared | b turns −z_a/z_b (+ internal), both about a's Z |
| `rack` | a `gear` and a `frame` on the pitch line | pitch circle tangent to the pitch line; pinion axis square to the rack | π·m·z mm per turn, along the rack's X the way the pitch point moves |
| `screw` | two `frame`s | axes collinear | `lead` mm per turn, right-hand: back along a's Z as a turns positively about it |
| `cam` | two `frame`s: the cam (Z its axis, X where its law's angle is 0) and the follower (at the point its lift is given for, Z along its travel, away from the cam) | the follower's travel square to the cam's axis; it sits `base` + its law's lift for the cam's angle as assembled | the follower lifts by `lift`, an interface value `[[θ°, mm], …]` over one turn (the table the design's code cuts the profile from), smooth between its points (PCHIP); it follows the cam and does not drive it back; the two stay in contact |

A relation couples b's motion to a's by a law: a straight line for gears, a rack and a screw, the lift
table for a cam. Any part turning against a follower is a cam (a rocker arm pressing a valve stem).

Distances are verified within the relation's `tolerance`, or `verification.position_mm`. A single
frame meets every frame of the other end (a sun and its planets); two patterns pair one-to-one.

### Fastener sets

```jsonc
{"fastener": "lid_screw.seat", "through": ["lid.screws"], "into": "insert.thread", "engagement": 1.5}
```

`fastener` names the fastener's seat connector (under the head, Z along the shank); its part is a
catalog part whose provider gives its `thread` (`M3-0.5`), `thread_diameter`, `thread_pitch`,
`length` and ISO 273 clearance holes `clearance_fine` and `clearance_medium`. `through` lists
clearance-hole patterns, head side first; `into` is the thread or insert it ends in. The fastener's
instances are placed by `assembly.py`, one per stack. Each build verifies, per stack: the holes
share an axis; a fastener sits seated on the first through hole; its shank clears every through
hole: the narrowest of the coaxial holes that run on from its frame (the hole under a counterbore),
less the process's hole allowance, is at least ISO 273's fine series; its thread is the into
thread's (a catalog insert's or nut's size and pitch; a made hole lies between 0.7 and 1 × the
thread diameter, tapped or thread-forming); it engages `into` (the shorter of how far it reaches
past the into hole's opening and that hole's length) by at least the into part's material minimum
(`min_thread_engagement_d` × diameter, or the set's `engagement`); and it does not bottom out: along
its axis, nothing in the assembled design stops it before its tip (an open insert in a deeper pocket
is fine). A fastener set joins its parts rigidly.

### Requirements

The requirement checks are `size`, `fits_within`, `volume`, `mass`, `hole`, `wall`, `bodies` (each
on a `target`: a part in its own frame, an instance or component path placed, or the whole design),
`clearance` (between two placed things: the least gap where they sit and wherever a range of motion
moves one against the other, as motion analysis sweeps it, and where), `span` (the length between two
connector frames where they sit and at every position motion moves either through: a spring's ends) and
`stack`:

```jsonc
{"text": "The carrier keeps an axial gap under the lid",
 "check": {"kind": "stack", "between": ["carrier.output", "housing.top"],
           "tolerances": {"housing": 0.1, "carrier": 0.1}, "min": 0.2}}
```

A stack is the gap between two connector frames along the first one's Z: nominal as built, worst
case (± the sum of the contributors' tolerances, which must stay within min and max) and RSS.
`mass` uses each part's material (a catalog part's from its provider); a part without one makes it
`unverified`, naming the part.

`silhouette` (the `pictures` plugin) compares the model, seen from one side, with a picture of what it
should look like: a photo, render or sketch the human saves under `references/`, with the
requirement's text saying what matters in it:

```jsonc
{"text": "Looks like Max from the front: the floppy ears matter most",
 "check": {"kind": "silhouette", "image": "references/front.jpg", "view": "front", "height": "height",
           "min_overlap": 0.9, "max_deviation": 3}}
```

The subject is cut out of its background (a transparent PNG as it is cut; otherwise by the colour of
the picture's border, refined by GrabCut, or as the area a sketch's lines enclose), scaled to `height`
mm and stood on Z=0, centred across. The check measures how much of the two outlines' union they
share and how far either strays from the other; a failure names what strays, how far and which way
(`head (ear_left) stops 4.2 mm short of the reference towards +Z at X −18.0, Z 168.0`), and
`build_design` shows the agent both outlines over the picture on a millimetre grid, which it reads
landmarks from. A picture is checksummed into the build, so saving a new one builds again, from cached
parts.

A requirement's numbers (min and max, a box, a hole's diameter, a wall) may name a parameter or an
interface value instead (`"max": "interfaces.height_max"`), so the requirement follows the design in
every configuration; anything computed is computed in `interfaces.py`, like any shared value.

## Code contract

hitlcad runs every function with `p` (the parameter values of the configuration built, a
read-only mapping) and `i` (the interface values, as attributes), and records which keys each one
reads. Code must be deterministic. It runs in the design's sources folder, so relative paths such
as `vendor/…` resolve there.

**`interfaces.py`**: one top-level function per value, `def name(p, i)`, returning a number,
string, or list or dict of them. Values may read each other through `i` (a cycle is an error) and
hold the validation of what they compute (`ValueError`s reach the human). They are evaluated
lazily, once per build process; names starting with `_` are helpers.

**`parts/*.py`**: `def build(p, i)` returns `{part: (shape, connectors)}` for exactly the parts
design.json assigns to the file; a catalog part's value is the `CatalogPart` its provider returns.
The shape is in the part's own frame (how it is made). `connectors` maps each declared connector
to a location or a list of them (a build123d `Location`, a Blender `mathutils.Matrix` or empty).
Part files import `lib/`, the model SDK and catalog providers, never each other.

**`assembly.py`**: `def build(p, i)` returns `{part or component: [placement, …]}`, one per
instance in order (`planet[0]`, `planet[1]`, …). It places and nothing else; what a placement
needs comes from `i`, and catalog data from the providers.

**The model SDK** (`hitlcad.sdk`) knows no library and imports nothing, so every backend's Python
loads it: `mate_to(frame_on_part, frame_on_target)` returns the placement that makes a connector frame
face a target frame (build123d Locations, Blender Matrices or 4 × 4 lists in, the same out), and
`CatalogPart` is what providers return.

**Catalog providers** (plugins' `hitlcad_catalogs`; `search_catalog` searches them all; hitlcad's own):

- `hitlcad.builtin.catalogs.bd_warehouse`, over [bd_warehouse](https://bd-warehouse.readthedocs.io/):
  `screw(kind, size, length, standard)`, `nut(kind, size, standard)`, `washer(kind, size,
  standard)`, `heat_set_insert(size, standard)`, `bearing(kind, size, standard)`, and
  bd_warehouse's hole operations (`ClearanceHole`, `TapHole`, `InsertHole`, `ThreadedHole`,
  `PressFitHole`), sized to the fastener. Each hole operation records where the fastener's own
  origin goes; `frames(part.native)` returns those frames as a pattern connector, and every part of
  this provider has a `placement` connector (its origin, facing out) that mates `rigid` with them.
  Screws also have `seat`, `shank`, `tip`; nuts and washers `bottom`, `top`, and `thread` or `bore`;
  inserts and bearings `bottom`, `top`, `outer` and `thread` or `bore`. `ClearanceHole`
  counterbores a screw's head unless `counter_sunk=False`, so its frames sit on the seat.
- `hitlcad.builtin.catalogs.step`: `part(path, sha256=…, designation=…, material=…, connectors={…})`
  loads a vendor STEP and fails when its checksum differs; its connectors are the part file's.

The backend's guide (the `hitlcad-<backend>` skill) shows each in code.

## Configurations

`configurations` are named sets of parameter values over `parameters`. A build builds one
configuration; requirements and every other analysis are verified per configuration, and
`build_design(path, configuration="all")` builds them all. Builds of different configurations share
every cached part file whose recorded reads are equal. Parameter edits change `parameters` (the
default configuration).

## Motion

A mechanism has inputs, and everything else follows them. Each mate's free motion with a range (its
`limits`, or a full turn for a revolute or cylindrical mate without angle limits) is an input, but one
an input before it already moves (a gear's turn its relation drives, a follower its cam lifts: a
follower's own motion comes after the rest): that mate is `motion.driven`, checked along its input's
sweep, and fails (`motion.locked`) where its input drives it past its own limits. For each input,
motion analysis moves the side of the mate away from `ground` from where it sits towards each end of
the range (both ways round, for a full turn), and reports the first contact each way and the limit to
change ("base.hinge ↔ lid.pin: from 0.0°, lid hits base at
-99.0° on the way to -100°: limits.angle [-100, 0] run past it; make them [-98.9, 0], or clear base").

It sweeps by conservative advancement, so a collision between two positions is never missed: at each
position it measures the gap (FCL, from a tree of each solid's triangles built once, the solids moved
not remeshed) between each moving instance and every instance it can
reach, and moves on no further than lets any point travel that gap: the gap over the instance's
largest distance from the axis, for a turn, or the gap itself, along a slide. Where instances
touch, a step moves no point further than `verification.position_mm`, which is how closely a
contact is placed. Two shortcuts keep it fast without losing that certainty:

- what is round about the axis of a turn (a hub on its shaft, a knuckle in its hinge, a duct's
  wall), to the mesh's tolerance, turns within itself and keeps its gap, so only the rest of each
  part is moved against the rest of the other;
- an instance that meets nothing anywhere in a full turn (a blade inside its duct) is cleared at
  once: what it passes through in a full turn is the solid of revolution of its profile.

A pair that already overlaps where it sits, beyond what a mate allows (a clearance fit modelled at
its basic size touches, to its facets), is interference's to report: motion leaves it out and says so (`unverified`), as it does for a pair it cannot measure (an open mesh), and for a mate it
cannot sweep to the ends in `verification.motion_steps` positions (parts that stay within the
position tolerance of each other all the way). Each sweep is cached by the meshes, placements and
motions it depends on. A `clearance` requirement on a pair that moves takes the least gap over the
sweep, and says where.

Rigid mates, fastener sets and rigid components join instances into rigid clusters; the other
mates and the relations join clusters. A mate whose two ends are held rigidly anyway does not move
(an informational pass). Relations carry a motion on: a part driven by a moving one moves about or along
its own axis by the law its relation holds (`Relation.coordinates`: each gear's turn against the line of
their centres, so a gear on a moving arm turns by Willis's relation), and conservative advancement
bounds each step by the law's steepest slope over the range. A part hit while it is held on a motion of
its own is named, with the relation that would couple them. Parts a relation keeps in contact (a cam
and its follower) are checked at positions no point moves more than 0.5 mm apart: standing apart by
more than the relation's tolerance and both meshes' is `motion.parted` (the profile and its law
disagree), sharing volume a collision. Motion is kinematic: forces (whether a follower leaves its cam at
speed, a spring's preload) are not checked; a `span` requirement keeps a spring's ends between its
lengths wherever motion takes them.

### Stages and blocks

Every free motion of a moving mate is an input, held, or driven: in order (those with a range, a
follower's own motion after the rest, then those without a range), a motion the mates, relations and the
motions before it do not already determine, where they sit as assembled, is an input when it has a range
and is held where it sits when it has none; one they do determine is driven, by the input that moves it
most. An input's sweep holds the other inputs and the held motions where they sit, and places what it
moves stage by stage: about or along a fixed axis by a law of the input (the input's own side, a gear its
relation drives, a follower its cam lifts), carried where it sits on what an earlier stage places, or
settled in a block, given the poses of what moves before it (a four-bar's coupler and rocker given its
crank, a planetary's carrier and planets given its sun, each valve train given its lifter). A sweep whose
stages all move by laws is exact; one with blocks is traced.

A traced sweep steps the input from where it sits towards each end, and settles each block at each
position by the block solver (`hitlcad_solvers`; `HITLCAD_SOLVER`, hitlcad's own `sqp` by default:
CasADi's SQP method on the block's compiled graph; empty, and nothing with a block is swept: it is
unverified, "no solver is set", and checked as assembled only). The solver proposes; hitlcad checks every
position: each mate still holds what its type `holds` of b's frame in a's, each relation its law from as
assembled, within the verification tolerances, and no point has moved more than 0.5 mm since the position
before, so the positions show the motion and a jump to a block's other way of closing is refused. A part
that only slides along or turns about its own axis without moving anything else (a gear sliding on its
shaft) is held where it sits, and not counted as a freedom.

At each traced position every pair the input moves is measured as the exact sweep measures it, round
parts about a mate's axis left out where one of the pair turns against the other on it. A pair with no
`clearance` asked of it whose gap is less than a position moves it (gears in mesh, a pin in its bore) is
from then on only tested for meeting (touching, crossing, or one inside the other), far cheaper than
measuring how far apart. Every position is shown clear, but what happens between two of them is not:

- a pair that overlaps at a position is a collision (`motion.collision`), at that position;
- an input whose blocks go no further than its `limits` (they lock at a dead point) fails
  (`motion.locked`): "it goes no further than 124.5° on the way to 180°: limits.angle [0, 180] run past
  it; make them [0, 124.4]". Without limits a lock is just how far it goes;
- a traced sweep clear at every position is `motion.sampled` (unverified): "base.a ↔ crank.a moves a full
  turn: clear at 243 solved positions, no point moving more than 0.51 mm between them; between them
  unchecked". The human watches it move in the panel and says so; the build that follows passes it
  (`motion.watched`, "watched in the panel on revision 3"). The watch is of the sweep: any change to
  what it moves, or to what it moves past, leaves it unwatched;
- an input its blocks hold where it sits is `motion.held`; one the solver cannot move from where it sits,
  or a solver that cannot settle a block (no solver installed, a relation type it does not know), is
  `motion.loop` (unverified), saying why.

A driven motion with a range takes its result from its input's sweep, with the range it moves through
("base.d ↔ rocker.d moves 77.4 … 141.4°, driven by base.a ↔ crank.a: checked along its sweep").

The report lists each range and what it moves, each set of instances about or along its own axis by
samples of its law, or a traced sweep's positions (at most 400 of those solved), which the panel's motion slider
moves. A pin placed with the slider moved records where it was (`Pin.motion`), and selecting the pin
shows it there again.

## Analyses on every build

| Analysis | On | Reports |
| --- | --- | --- |
| Structure | design.json, the plan | unknown names and connectors, relations joining the wrong kinds, fasteners that are not catalog parts, unknown materials, materials their parts' process cannot make, parts declared but not placed, ground |
| Parts | each part | empty meshes, meshes that do not close a solid (manifold3d's test, the one volume, mass, overlaps and gaps all use), the kernel's validity check |
| Connectors | each part | frame counts, measured features (holes, shafts, faces, gears; not datums) |
| Mates | each pair of frames | residuals in what the type fixes, limits, fits |
| Relations | each pair | the type's geometry |
| Fastener sets | each stack | coaxial, seated, ISO 273 clearance, thread size, engagement, free space past the tip |
| Coverage | the instances | floating instances |
| Interference | each pair of placed instances | overlap volume, and where it is (pieces, box, depth), except where a mate (`overlap`, an interference fit) or a fastener set (its thread in `into`) expects it, and within what the mate allows; pairs inside a component are its own build's |
| Motion | each input, and each motion it drives | the first contact each way and the limit to change; a traced sweep's at the positions solved, which the human watches; or why it is not analysed |
| Manufacturing | each made part, in its own frame | the process's checks: FDM (walls; overhangs, each region bridged between two supports up to `max_bridge_mm` or held at its edge, reported, not failed, with `supports`; bed contact, the base's outline against the part's seen from above and its height against the base's narrowest width, unless `brim`), resin (walls, hollows without a drain at least `min_drain_mm` across), CNC milling (inside corners against the tool, pocket depth against the cutter that finishes each corner, faces with no clear path up), sheet metal (thickness, the inside radius of each bend), injection molding (draft, undercuts along the pull, thick sections against the median wall); each failure lists where |
| Requirements | as declared | pass, fail or unverified |

Wall thickness (the process checks and the `wall` requirement) is measured once per body, and only on
closed meshes: an open one has no inside to measure across, so its walls are not measured. At points
spread evenly over the surface (`wall_samples`) it is the chord straight through the part to a face
turned back within 60°, capped where that chord runs along a shell or meets a wall at a slant by the
largest ball that fits the wall at the chord's middle (or, beside an edge, the narrowest line across
it). The surface round the thinnest readings is sampled again at an eighth of their thickness; a
reading stands where at least 3 of the 7 samples nearest it agree, so a real thin wall, which covers
an area, is found, and one ray glancing off a crease is not. The taper of a knife edge, two faces
meeting at under 60° (a hole leaving a chamfer), is no wall: a slicer rounds it off at any size.
Rays and nearest points go through libigl's AABB tree. Inside corners and bends are read on the
B-rep: an edge is concave by the normals there, turning about it as it runs in its first face, and
faces meeting tangentially (a fillet's edges) make no corner; coaxial cylindrical faces of one radius are one
wall (a hole a CAD exporter split into half-faces is one hole, not two corners); a bend is a pair of coaxial cylinders,
inside r and outside r + the sheet, or a sharp concave edge with the sheet's other side behind both
faces, so a cut-out's corners are not bends.

Interference uses a bounding-box broad phase before exact manifold3d booleans, and caches each
pair's overlap by the parts' meshes and transforms, so a rebuild recomputes only what moved.

### Problems, not checks

One mistake can fail several checks: an impeller typed 2 mm too large overlaps its duct
(interference), misses the clearance a requirement asks (requirement) and leaves its motion
unchecked (motion). A result names its `cause` where its analysis can tell, as a key of what it is
about, and the report (`BuildReport.problems`) groups the results that do not pass by it:

| Cause | Means | Set by |
| --- | --- | --- |
| `overlap:A\|B` | instances A and B share volume where they sit | interference (an overlap nothing expects, or more than a joint allows); motion (a range left unchecked only because of it); a `clearance` requirement between A and B; a mate whose fit measures the shaft larger than its hole; a fastener that bottoms out against B |
| `contact:M:A\|B` | moving mate M brings A against B | motion (its first contacts, when all are A and B); a `clearance` requirement that comes to no gap through M |
| `mate:M:A\|B` | mate M's frames on A and B disagree | mates; interference, for an overlap of the one M puts elsewhere (the end further from ground) |

Instances are named by their top-level instance, so a component's leaves meet the requirements
that name the component. An overlap of a fastener in the host of the insert it is threaded into,
where the insert's hole is not cut, has the insert's cause. A result whose analysis cannot tell is
its own problem.

The analyses run cause before effect (structure, parts, connectors, mates, relations, fastener sets,
coverage, interference, motion, manufacturing, requirements), so the earliest one's result leads
its problem and the rest are named with it. Problems alike but for pattern indices and numbers (four
screws each too long) fold into one, with ×N and the indices. `build_design`, `hitlcad build` and
the panel's Results show the same lines and the same tally, problems rather than checks. The
blower with its impeller typed as 70 mm:

```
✗ interference duct+impeller: duct and impeller overlap by 91.11 mm³ in 7 pieces within x 4.00 … 22.00, y -34.20 … 35.00, z -35.00 … 35.00, about 0.25 mm deep (also: motion motor.shaft+impeller.bore, requirement 'Impeller blade tips clear the duct')
? requirement Intake guard is finger-safe: no opening wider than 7 mm: no check: the human judges it
1 problem (2 failing checks), 1 unverified; 130 checks pass
```

A warning (`!`) is a problem whose failing checks are all warnings (manufacturing's, a mesh that does
not close), counted apart. Info results (an overlap a
joint expects, a mate others hold still) are listed under their analysis but are not checks.

## Outputs

`build/` after a successful build of the default configuration:

| File | Contents |
| --- | --- |
| `model.step` | the assembly as a STEP product structure, instances named by path (only when every part is B-rep) |
| `model.blend` | the assembly as one Blender scene, 1 unit = 1 mm, Z up (when a part, a component's included, is a Blender part): each part's objects with their names and materials in a collection of their own (from its `.glb`, whichever backend built it), each instance a collection instance at its placement named `<design>/<instance path>`, nested for components |
| `mesh.stl` | the placed assembly as one mesh |
| `parts/<part>.stl`, `.glb` (its objects' names, materials and colours), `.step` (B-rep parts), `.view` (tessellated for the 3D view) | each part once, in its own frame; its backend writes them all where it builds it, and the part cache keeps them together |
| `scene.json` | the 3D view: each part's `.view` drawn per instance, named by its path and digest; a B-rep part one `exact` shape whose faces, edges and vertices picks name, a mesh part one shape per colour |
| `preview.png` | iso, front, right and top renders |
| the exporters' files | `bom`: `bom.csv`, `bom.json` · `3mf`: `parts/<made part>.3mf`, `model.3mf`, a part as one object per colour with its base material · `urdf`: `urdf/<design>.urdf` with meshes · `sdf`: `sdf/model.sdf` with meshes |
| `report.json`, `build.log` | the report, the backends' output |

URDF and SDF map placed instances to links (with inertia where the material is known) and mates to
joints along a tree from `ground`: rigid and fastened fixed, revolute revolute (continuous without
limits), slider prismatic, cylindrical continuous, planar planar, ball floating (URDF) or ball (SDF),
a relation of a straight-line law between two moving joints a URDF `<mimic>` (a cam has none). A mate that would close a loop is left
out; an instance no mate reaches is fixed where it stands.

## Picks and pins

Picks name an instance and a component of its part: "face 12 of `tilt/planet[2]`" is `faces()[12]`
of `build/parts/planet.step`, placed. When the picked face is a connector's feature, the label says
so (`lid.bearing_pocket: Cylinder face r=11.00 mm …`). Across rebuilds a pick keeps its instance; a
pick on a connector's feature keeps that connector; any other is carried by geometry within its
part; a mesh point moves to the nearest point of its instance. Without a match it stays on its
revision, and the panel and the agent say so.

## Addresses

Every tool argument and answer, requirement, mate and pin uses one grammar:

| Address | Means |
| --- | --- |
| `lid` | the part `lid`, or its instance when it is placed once |
| `planet[2]`, `tilt/planet[2]` | one instance; a path through components |
| `lid.bearing_pocket`, `lid.screws[2]` | a connector; one frame of a pattern |
| `tilt.output` | a component's export |
| `lid.bottom+housing.top` | a mate (its two ends) |
| `sun.teeth~planet.teeth` | a relation |
| `lid_screw.seat:lid.screws>insert.thread` | a fastener set |
| `face 12 of lid[0]`, `part lid` | a B-rep component of an instance; a part in its own frame |
| `screw_d`, `interfaces.lid_bolt_xy` | a parameter; an interface value |
| `ratio_5`, `rev 12` | a configuration; a revision |

## MCP tools

The tools follow the agent's workflow: plan the structure, find standard parts, build in steps,
inspect, measure, assess impact, answer the human. Structure and code are edited as files with the
agent's own tools (reviewed as diffs); no tool edits them.

| Tool | Purpose |
| --- | --- |
| `list_backends()` | backends, materials, catalog providers, exporters |
| `create_design(path, name, backend)` | scaffold the folder |
| `search_catalog(query, limit)` | standard parts: designation, call, sizes, connectors |
| `build_design(path, scope, configuration, summary)` | `scope="structure"`: the plan check, no geometry, in seconds. `scope="<address>"`: a build (unchanged part files come from the cache) answered for that address. `configuration`: one variant, or `"all"`. Its problems (below), what changed, a tally; images: the preview, and a section through the failing mate, relation or fastener set that leads each problem, along its axis |
| `explore_design(path, focus, configuration)` | the design graph (below) |
| `compare_revisions(path, base, target)` | parameters, file diffs, results that changed status, parts rebuilt, size, mass |
| `list_geometry(path, target, kind, revision)` | faces, edges or vertices of an instance (placed) or `part <name>` (its frame) |
| `measure_geometry(path, a, b, revision)` | distance, angle and parallel gap between two addresses |
| `resolve_pins(path, pin_ids, reply)` | mark pins addressed |

**The design graph** (`agent/graph.py`) is a projection of design.json, the latest build report of the
configuration explored and the event log. Without a focus, `explore_design` gives the overview:
counts, components used and designs using this one, configurations, staleness (files changed since
the build), its problems, open pins, the selection, the tally, the
parts. With a focus it gives that node and its neighbours: a part's role, file, process, size and
mass, what it read, its connectors as measured, its instances, joints, results and pins; a
parameter's or interface value's impact, following recorded reads backwards (interface values →
parts and placements → their joints → requirements → designs using this one), exact because the
reads were recorded as the code ran. Answers are bounded by `explore_limit`, the rest counted.

## The panel

- **3D editor**: the assembly, instances named in the tree; picks and pins by instance and
  connector; the configuration on screen; a motion slider per range of motion, which for a loop
  says what was checked and takes the human's watch.
- **Structure**: parts and components as nodes, mates, relations and fastener sets as edges,
  coloured by their results (laid out with dagre); a click selects the part's instance.
- **Parameters** with configurations; **Changes**; **Results**: the problems and the tally the agent
  reads, every check by analysis, parts, components, renders, files and sources; **BOM**; **Pins**; **Brief**.

## Reference designs

The test suite's designs (`backend/tests/designs/`) are worked examples and the fixtures every
change is measured on:

| Design | Exercises | What its build reports |
| --- | --- | --- |
| [gearbox](../backend/tests/designs/gearbox/) | gears in a set file, a ring gear, shaft fits, a bearing seat, catalog bearing, inserts and screws, a simplified vendor motor whose rotor turns in it, the sun keyed to its D-shaft, a stack requirement, a configuration | one failing mate: `rotor.shaft+sun.bore`, fit 0.200 mm on a 0 … 0.1 fit, because `shaft_clearance` is added to the bore's radius; FDM warnings on the housing's pilot recess and the carrier; the planetary solved as a loop, its carrier turning a quarter of the rotor's turn, clear at every position solved; the 4:1 and 5:1 configurations both mesh |
| [enclosure](../backend/tests/designs/enclosure/) | face mates, heat-set inserts by `InsertHole` (`overlap`), fastener sets, BOM, 3MF, URDF and SDF | everything verifies; the `wide` configuration misses the 50 mm envelope requirement |
| [hinge](../backend/tests/designs/hinge/) | a cylindrical mate with angle limits, motion | the lid hits the stop post at −99.0°, short of its −100° limit; without the post it sweeps clear |
| [linkage](../backend/tests/designs/linkage/) | a four-bar: a closed loop of revolute mates | the crank turns a full turn and the rocker swings 64.0°, clear at every position solved; a post in the rocker's way is hit; a shorter coupler locks the crank at 124.5°, past its exact 124.2° by what the joints' position tolerance lets it stretch |
| [shelf](../backend/tests/designs/shelf/) | a component placed twice, bindings, exports, a flexible component's motion at the parent's level, an indented BOM | each box's hinge collision found at the shelf's level |

Seeded faults in `backend/tests/test_build.py` (a screw too long, a lid placed 0.2 mm off, an
undeclared overlap, a floating instance, a broken reference, an error in a part file) are each
caught by the analysis that owns them.

## Extension points

Every member below is a plugin's contribution (hitlcad's own are in `hitlcad.builtin`); a design
names the plugins it needs beyond hitlcad's own in `plugins`. How to write one: [plugins.md](plugins.md).

| Extension point | Kind | A member provides |
| --- | --- | --- |
| CAD backend | `CADBackend` and its worker's `runtime.Adapter`, hook `hitlcad_backends` | how its worker starts, how its part values read and export, an authoring guide, `brep` |
| Connector kind | `Connector`, hook `hitlcad_connectors` | its fields, `measure(features, frame, tol, nominals)`, `round` and `owns` where they apply |
| Mate type | `Mate`, hook `hitlcad_mates` | its free motion, what it `holds`, and `verify(fa, fb, symmetry, tol)` |
| Relation type | `Relation`, hook `hitlcad_relations` | the kinds it joins, `verify`, `ratio`, `coordinates` |
| Requirement check | `Check`, hook `hitlcad_checks` | `verify(build)`, its `hint`, and `cause(build)` when another analysis sees its failure too |
| Manufacturing process | `Process`, hook `hitlcad_processes` | `checks(body)`, `allowance(feature)`, its `findings` |
| Analysis | `Analysis`, hook `hitlcad_analyses` | `stage`, `title`, `summary`, `run(ctx) -> [Result]` |
| Catalog provider | `CatalogProvider`, hooks `hitlcad_catalogs` and `hitlcad_model_packages` | helpers returning `CatalogPart`s, `search` |
| Exporter | `Exporter`, hook `hitlcad_exporters` | `title`, `summary`, `export(built, report, out_dir)` |
| Loop solver | `Solver`, hook `hitlcad_solvers` | `supports(problem)`, `trace(problem, start, end, budget)`: positions, which hitlcad checks |
| Material | `materials.json`, hook `hitlcad_materials`, or in a project | density, processes, minimum thread engagement |
| Executor, renderer, store | protocols | one implementation each |

## What's next

See [roadmap.md](roadmap.md): the milestones from here, and the open questions.
