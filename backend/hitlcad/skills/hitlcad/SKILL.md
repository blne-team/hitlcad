---
name: hitlcad
description: "Create, edit, build, verify and review the parametric CAD assemblies of this hitlcad project (designs/<name>/: design.json, interfaces.py, parts/, assembly.py), reviewed by the human in the hitlcad panel in VS Code. Use for any request to make or change a part, assembly, enclosure, mechanism, bracket, figurine or other 3D model here, to check fits, fasteners, motion, requirements or manufacturability, to find standard parts, and to act on what the human selected or pinned. Takes precedence over generic CAD, build123d, Blender, bpy or mesh skills for building, exporting, rendering and checking; use those only as API reference for part files."
---

# Working on hitlcad designs

The `hitlcad` MCP server builds, verifies and renders the designs of this project; the human reviews
each build in the hitlcad panel in VS Code and talks to you in your own chat. All hitlcad tools take
the design folder's absolute path, and every name a tool answers with can be passed back to a tool.

## What a design is

A design is an assembly: instances of parts (and of other designs, as components), held together by
mates, relations and fastener sets that every build verifies.

```
designs/<name>/
  design.json     the structure: parts, connectors, mates, relations, fastener sets, parameters,
                  configurations, requirements, brief
  interfaces.py   mating values, one function each: def bolt_circle(p, i)
  parts/*.py      def build(p, i) -> {part: (shape, connectors)}: each part in its own frame
  lib/*.py        helpers part files share (not parts)
  vendor/         vendor STEP files
  assembly.py     def build(p, i) -> {part or component: [placement, …]}
  build/          the latest successful build's outputs: never edit, point the human here
```

- **Part**: one rigid body, made (a `material`, a `manufacturing` process) or bought (a `catalog`
  entry). Its own frame is how it is made: a printed part stands as it prints.
- **Instance**: one placement of a part: `lid`, or `planet[2]` when placed several times.
- **Interface value**: a mating dimension computed once in interfaces.py; parts and assembly.py read
  it as `i.<name>`, so two parts that meet read the same number.
- **Connector**: a named frame (or pattern of frames) on a part, at a feature another part meets.
- **Mate**: two connectors held together, with the motion they allow, a fit and limits.
- **Relation**: coupled motion (gears, rack and pinion, lead screw).
- **Fastener set**: a catalog fastener through clearance holes into a thread or insert.
- **Component**: another design used as a sub-assembly, its parameters bound by this one.
- **Configuration**: a named set of parameter values: a product variant.

**One design per product.** Group related parts by file (a gear set in `parts/gears.py`). Make a
separate design used as a component only for reuse (used in more than one place), an independent
life (built, versioned or tested on its own), a unit of supply (bought or made elsewhere as one
item), or scale (too large for one context).

## Workflow

1. `create_design(path, name, backend)` (backends: `list_backends`). For a new or substantially
   changed design, write the `brief` and tell the human your assumptions.
2. **Structure first.** In design.json declare every part with its `role`, `file`, `material` or
   `catalog`, its connectors, then the mates, relations, fastener sets, `ground` and the
   requirements the human gave (with a check where one fits). Use `search_catalog` for screws, nuts,
   washers, inserts and bearings before modelling any. Run `build_design(path, scope="structure")`
   until every reference resolves.
3. **Interfaces.** Put every value two parts share in interfaces.py: bolt circles, centre
   distances, bore sizes, catalog sizes, the heights where parts sit. Never type a mating value twice.
4. **Parts.** Read the `hitlcad-<backend>` skill. Build a skeleton first (envelopes with the
   connector features), then detail one part file at a time and check it with
   `build_design(path, scope="<part>")`. Unchanged part files come from the cache.
5. **Assembly.** assembly.py places every instance from interface values (or with the SDK's
   `mate_to`), so placements follow the same numbers as the geometry.
6. **Verify.** `build_design(path)` lists failures first: frames that do not agree, fits out of
   range, fasteners that do not clear or engage, collisions (also through each mate's range of
   motion), floating instances, process problems, requirements. Fix the cause, not the check. Look
   at the preview and the section views.
7. Before changing a parameter or interface value, `explore_design(path, "<name>")` shows what reads
   it and what it affects. After a change, `compare_revisions` shows what moved.
8. Configurations: `build_design(path, configuration="all")` verifies every variant.

## Reading the human

When the human says "this", "here", "the selected …" or mentions pins, hitlcad's prompt hook has added
what they point at to their message ("The human is pointing at these in the hitlcad panel"),
named by instance and connector. `explore_design(path)` repeats it. After addressing pins, call
`resolve_pins` with a short reply. Parameter values the human set in the panel are deliberate.

## design.json reference

**Connectors** (`"connectors": {"name": {...}}` on a part). A frame's origin is where the mating
connector's origin will be, and its Z points out of the part, so mated connectors face each other;
X fixes the rotation about Z where it matters (a bolt pattern's clocking), `symmetry` says where it
does not. `count` (a number or a parameter name) is how many frames a pattern has. Kinds:
$connectors

**Mates** (`"mates": [...]`). Ends are `part.connector` (every instance of the part),
`part[k].connector` or `component.export`; patterns pair one-to-one by position. After placement the
frames must agree in what the type fixes (within `verification`). Optional: `fit`
(`{"clearance": [min, max]}` diametral mm, negative is interference, or `{"iso": "H7/g6"}`, with hole
and shaft modelled at one basic size: the result gives the limits for the drawing) between a hole and a
shaft; `limits` (`{"angle": [lo, hi]}` degrees, `{"travel": [lo, hi]}` mm), swept for
collisions; `offset` (mm along a's Z); `flip: true` when b's Z runs with a's (a shaft entering a bore
from the end opposite its declared opening); `overlap: true` when b's part is set into a hole of a's
part and overlaps its wall (a heat-set insert's knurls, a modelled thread): a has no material on the
hole's axis where b sits. An interference fit allows the ring its diameters make, no more. Types:
$mates

**Relations** (`"relations": [...]`), verified from the frames and the connectors' nominal values:
$relations

**Fastener sets** (`"fasteners": [...]`): seated, the shank clears every through hole, the thread
engages at least the into part's material minimum (or `engagement` × diameter) without bottoming out:
$fasteners

**Requirements** (`"requirements": [{"text": "…", "check": {…}}]`). `target` is a part (its own
frame), an instance or component path (placed), or nothing (the whole design). Checks:
$checks

**Manufacturing** (`"manufacturing"` on the design, or on a made part). Each made part is checked in
its own frame:
$processes

**Components** (`"components": {"name": {"design": "../other", "configuration": …, "bind":
{"param": "my_param" or "interfaces.value"}, "flexible": false}}`). A component exposes connectors
through its own `exports` (`{"output": "carrier.output"}`); mate to those, never inside it.

**Configurations** (`"configurations": {"ratio_5": {"sun_teeth": 12}}`), **verification**
(`{"position_mm", "angle_deg", "motion_steps"}`, defaults from hitlcad's settings) and **outputs**
(exporters run on every build: see `list_backends`) complete the file.

## Rules

- Millimetres, Z up. A part's own frame is how it is made; assembly.py says where it sits.
- Never type a mating value twice: interfaces.py. Part files read `p` and `i`, import `lib/` and the
  catalog providers, and never import each other.
- Catalog parts come from a provider (`search_catalog`) or a vendor STEP (`hitlcad.catalogs.step`);
  model one only when neither exists, with `"fidelity": "simplified"`.
- Never claim a fit, clearance, printability or strength that no result reports.
- Builds, exports, renders and checks run only through the hitlcad tools. Do not run build123d,
  Blender or export scripts yourself, and never edit `build/`.
- Other CAD, build123d, Blender, bpy or mesh skills are API reference for part files only.
