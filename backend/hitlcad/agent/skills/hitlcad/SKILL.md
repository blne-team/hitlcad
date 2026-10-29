---
name: hitlcad
description: "Create, edit, build, verify and review the parametric CAD assemblies of this hitlcad project (designs/<name>/: design.json, interfaces.py, parts/, assembly.py), reviewed by the human in the hitlcad panel in VS Code. Use for any request to make or change a part, assembly, enclosure, mechanism, bracket, figurine or other 3D model here, to check fits, fasteners, motion, requirements or manufacturability, to find standard parts, and to act on what the human selected or pinned. Takes precedence over generic CAD, build123d, Blender, bpy or mesh skills for building, exporting, rendering and checking; use those only as API reference for part files."
---

# Working on hitlcad designs

The `hitlcad` MCP server builds, verifies and renders the designs of this project; the human reviews
each build in the hitlcad panel in VS Code and talks to you in your own chat. Tools that address a
design take its folder's absolute path; every name a tool answers with can be passed back to a tool.

## Design decisions

Use the companion skills when the task needs their judgment. They supplement this operating
contract and preserve the human's requirements, selections, pins and accepted choices:

- `hitlcad-design`: interpret intent, brainstorm concepts, compare tradeoffs and review an edit.
- `hitlcad-form`: silhouette, proportions, composition, profile-driven and organic geometry.
- `hitlcad-dfm`: process choice, part divisions, assembly, hardware and fits.
- `hitlcad-style`: choose and apply a visual language grounded in use and construction.

Choose references as needed; a local dimension edit does not require a concept or style exercise.

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
- **Relation**: coupled motion by a law (gears, rack and pinion, lead screw, a cam and its follower).
- **Fastener set**: a catalog fastener through clearance holes into a thread or insert.
- **Component**: another design used as a sub-assembly, its parameters bound by this one.
- **Configuration**: a named set of parameter values: a product variant.

**One design per product.** Group related parts by file (a gear set in `parts/gears.py`). Make a
separate design used as a component only for reuse (used in more than one place), an independent
life (built, versioned or tested on its own), a unit of supply (bought or made elsewhere as one
item), or scale (too large for one context).

## Workflow

For an existing design, begin at the relevant checkpoint: inspect its current state and human
feedback, then choose the action that resolves the uncertainty. The structural, interface and
verification rules below still apply to changes; already completed setup need not be repeated.

1. `create_design(path, name, backend)` (backends: `list_backends`). For a new or substantially
   changed design, write the `brief` and tell the human your assumptions.
2. **Structure first.** In design.json declare every part with its `role`, `file`, `material` or
   `catalog`, its connectors, then the mates, relations, fastener sets, `ground` and the
   requirements the human gave (with a check where one fits). Use `search_catalog` for screws, nuts,
   washers, inserts and bearings before modelling any. Run `build_design(path, scope="structure")`
   until every reference resolves.
3. **Interfaces.** Put every value two parts share in interfaces.py: bolt circles, centre
   distances, bore sizes, catalog sizes, the heights where parts sit. Never type a mating value twice.
4. **Parts.** Read the `hitlcad-<backend>` skill and its construction helpers below. Start from a
   matching complete recipe when one fits; change its parameters and brief before changing its topology.
   Build a skeleton first (envelopes with the
   connector features), then detail one part file at a time and check it with
   `build_design(path, scope="<part>")`. Unchanged part files come from the cache.
5. **Assembly.** assembly.py places every instance from interface values (or with the SDK's
   `mate_to`), so placements follow the same numbers as the geometry.
6. **Verify.** `build_design(path)` answers one line per problem: frames that do not agree, fits out
   of range, fasteners that do not clear or engage, collisions (also through each mate's range of
   motion), floating instances, process problems, requirements. A line leads with the check closest
   to the mistake and names the others it fails, `(also: requirement '…')`: fix that one cause and
   they pass with it. `×4 at [0, 1, 2, 3]` is one problem at each of a pattern's copies. `✗` fails,
   `!` is a warning, `?` no check could settle (and a requirement with no check is the human's to
   judge). `[mate.fit]` is the problem's code; the usual fix for it follows (`→ …`) the first time it
   appears, and `Same as revision 6` says your last change did not reach it: look again at the cause
   rather than repeating the fix. [playbook.md](playbook.md) lists every code. The last line counts
   problems, not checks: `1 problem (2 failing checks), 1 unverified; 130 checks pass`.
   The default response lists up to five problems. Focus `explore_design` on an address or `rev N`
   for more detail. Request `include_images=True` on `build_design` or `explore_design` when geometry
   diagnosis or appearance review needs the preview, joint sections or requirement figures;
   `explore_design` uses an existing build.
   An input that moves a closed loop of mates (a four-bar's crank, a planetary's sun) is swept at the
   positions the solver settles the loop in: `[motion.sampled]` says it is clear at each of them and
   unchecked between them. Do not call it clear: ask the human to watch it move in the panel's motion
   control. Once they have, it passes (`[motion.watched]`) until what it moves, or anything it moves
   past, changes. A mate an input moves is `[motion.driven]`, checked along that input's sweep.
   A build that fails (the code raised, design.json does not validate) reports why the same way, at
   the file and line, and a tool error names its code too: an error whose code is `hitlcad.bug` is
   hitlcad's, not the design's; tell the human and do not work around it.
7. Before changing a parameter or interface value, `explore_design(path, "<name>")` shows what reads
   it and what it affects. After a change, `compare_revisions` shows what moved.
8. Configurations: `build_design(path, configuration="all")` verifies every variant.

## Construction helpers

These packages are supplied to part files by the build process, for the backends listed. Read the
backend skill for exact keyword arguments, connector names and a complete recipe before calling one.
Return a helper's `(shape, connectors)` value under your declared part name; preserve the documented
connector names or explicitly rename them in both code and design.json.

$builders

Use a helper where its construction matches the requested shape. Compose additional geometry with
the backend API when needed. When a build fails, repair the named input or reported region, rebuild,
and inspect the preview. Keep the brief and requirement bounds intact unless the human changes them.
A passing build verifies only its reported checks; judge appearance from the actual views.

## Reading the human

When the human says "this", "here", "the selected …" or mentions pins, hitlcad's hook has added
what they point at to their message ("The human is pointing at these in the hitlcad panel"),
named by instance and connector. `explore_design(path, focus="review")` repeats it. After addressing pins, call
`resolve_pins` with a short reply. Parameter values the human set in the panel are deliberate.

The human edits the same files while you work: parameters and outputs in the panel, and the files themselves.
Your edits and theirs merge; neither undoes the other. After your tools, the hook says what they changed
("Meanwhile, in the hitlcad designs you work in"): read a file again before you edit it. Where it says your edit
and theirs changed the same line at once, the merge kept both: make that line what it should be.

When the human gives a picture of what to make (a photo, a render, a sketch) with what matters in it,
it becomes a `silhouette` requirement that every build compares the model with:
[reference/pictures.md](reference/pictures.md) says how to declare it and model against it.

## design.json reference

Read [reference/design-json.md](reference/design-json.md) when declaring or changing connectors,
mates, relations, fastener sets, requirements, manufacturing processes, components or plugins.
It lists the installed kinds with examples; load the relevant section for the current edit.

## Rules

- Millimetres, Z up. A part's own frame is how it is made; assembly.py says where it sits.
- Never type a mating value twice: interfaces.py. Part files read `p` and `i`, import `lib/` and the
  catalog providers, and never import each other.
- Catalog parts come from a provider (`search_catalog`) or a vendor STEP (`hitlcad.builtin.catalogs.step`);
  model one only when neither exists, with `"fidelity": "simplified"`.
- Never claim a fit, clearance, printability or strength that no result reports.
- Builds, exports, renders and checks run only through the hitlcad tools. Do not run build123d,
  Blender or export scripts yourself, and never edit `build/`.
- Other CAD, build123d, Blender, bpy or mesh skills are API reference for part files only.
