# Roadmap

Where hitlcad is, and what comes next. One milestone is open at a time, and its version is released
when its "done when" holds. The others are kept below, not yet ordered. This page changes when a
milestone closes or the plan does.

## Where hitlcad is

v0.1.2 is the latest release, for Linux, macOS and Windows, with its installer; CI checks all three. Four
reference designs build and verify in every configuration. What there is, and where it is described:

- **The assembly model** ([assembly-model.md](assembly-model.md)): design folders, connectors, mates, fits,
  fastener sets, components, configurations, motion, catalogs, materials, five manufacturing processes, exports.
  Every check gives a verdict the agent can act on: a failure is a real problem, says where, and appears once per
  root cause.
- **Motion** follows a mechanism's inputs: about fixed axes by laws, swept exactly, or in blocks a solver settles
  at each position, which the human watches in the panel where only sampled positions are checked.
- **Plugins** ([plugins.md](plugins.md)): a design's vocabulary and hitlcad's outputs, hitlcad's own registered as
  an installed package's. Every error has one shape: a code, whose it is to fix, and the usual fix.
- **Builds** ([architecture.md](architecture.md)): build workers kept per design, a part built again only when
  what it depends on changes, results as each analysis finishes, outputs only when asked.
- **The panel**: builds in 3D, model files in the same view, picks, pins, explode, the motion watched, parameters
  and outputs edited in place, Undo and Redo. A design's files are a CRDT document, so the human, the panel and
  the agents edit them at once without undoing each other.
- **The agents**: Claude Code and Codex, through the MCP tools, the generated skills and their hook.
- **Pictures**: a `silhouette` requirement compares the model's outline with a reference picture.

Since 0.1.2: a project names the plugins its designs use in `hitlcad.toml`, which its agent keeps, and a
design gets only those; a printed part says which way up it prints (`up`), apart from the frame it is modelled
in, and once the human has entered their printer in the panel's **Print** tab (kept in the project's
`hitlcad.toml`), each printed part is checked against its bed; the part under the cursor is named in the panel,
and a click selects a part first; **hitlcad: Restart Server** restarts the server, whoever started it.

## Now: v0.2, designed to be printed

hitlcad designs, assembles and checks parts, but stops short of the printer: a part is checked as it is modelled
(+Z up in its own frame), so the agent models each part lying the way it prints rather than the way it fits;
nothing knows the printer's bed (the NAS case's 249 × 251 mm front shell is on no bed's measure); and nothing
tells the human how to print what was designed. v0.2 makes the design aware of its printing, and ends where
the slicer begins: hitlcad says how to print each part so it works as designed (strong where it is loaded, true
where it fits, clean where it shows), and the human slices and prints it. How long a print takes, the filament
it uses and what it costs are the slicer's to say: hitlcad neither slices nor drives the printer.

- **Your printer, and slicers as adapters.** hitlcad defines no printer: the panel's **Print** asks for the human's
  once (build volume, nozzle, slicer, the filament and AMS slot of each material) and the project keeps it in its
  `hitlcad.toml`; a plugin gives slicers (`hitlcad_slicers`), Bambu Studio first. PrusaSlicer, OrcaSlicer or Cura
  are another adapter each.
- **Print orientation.** A printed part's process says which way up it prints (`"up": [0, -1, 0]`), so parts
  are modelled for the assembly; its process checks, its 3MF and its fit on the printer's bed (`printing.bed`,
  turned about Z as a slicer would) are of it lying that way. The 3D view's **Print** tab is the print view: each
  printed part once, lying as it prints, on a bed of the printer's size, with its plan; a click on a part frames it
  and shows its whole plan. Each part's candidates are its own way and the stable poses of its hull, each judged by
  its process as the way it prints is: whether it fits the printer, whether it stays put on the bed, and the support
  it needs, in that order; the build names the best when it beats the part's own (`printing.orientation`), and
  **Print this way** in the plan writes the part's `up` (a panel edit, no agent turn). The print view shades, seen
  through each part, what prints on nothing but support (not what is bridged or held at an edge) and what lies on
  the bed, as its process checks found them; pointing at a way in a part's plan shows the part lying that way,
  shaded so, and judged by looking before **Print this way**.
- **Design for printing.** Findings with their usual fix, which the agent acts on as on any other: a round hole
  lying on its side from `teardrop_from_mm` across, whose top prints as an arc bridged over nothing and sags
  (`manufacturing.fdm_teardrops`: a teardrop, or the part turned so it prints upright; on support where its top
  spans more than a bridge), its top the teardrop's finding, not the overhangs'; a part larger than the bed
  (`printing.bed`: a split, with a joint, dowels or screws). Supports are recommended where any finding shades
  what prints on nothing.
- **Printed to work.** A part's ways weigh what fits on it: each round feature with a mate's fit prints as a stack
  of its sections, whose layers step it out of round tilted from upright (2t·sin θ across), past its fit's band
  (`manufacturing.fdm_round`: turn it upright, or split the part so it can); upright, where its wall meets the bed
  at its own size, the first layer spreads into it past the fit's least clearance (`manufacturing.fdm_first_layer`:
  a chamfer there). A way's merit is then: it fits the printer, it stays put, it keeps its fits round, the support
  it needs; a way that costs a fit says so in the plan. A chamfer or countersink at a connector's feature leads to
  it from the frame's origin, so the fix the finding asks for keeps its connector.

Left, in this order:

1. **Printed for what it does.** What the design declares a part does weighs its ways too: a part that flexes (a
   clip, a snap, declared with its material's needs, below) bends along its layers, not across them; a face the
   design shows prints on top or on the bed, not over support. Loads in general, declared by the design, stay
   under "Speeds and loads" (Later).
2. **The slicer's file.** One `print.3mf` of every printed part, as many of each as the design has, each lying as
   it prints, with its settings as its own (Bambu Studio's per-object settings, written by the slicer adapter), so
   the human opens it, arranges it and slices with nothing to turn or set. Plates are the slicer's: its Arrange knows
   the plate, groups by filament and turns parts only about Z, which keeps the way each prints. It replaces the `3mf`
   output's file per part. The check is the human's: the file opened in Bambu Studio, each part as the plan says.
3. **Materials for what a part must do.** The agent declares a part's needs from the brief (`{"kind":
   "service", "target": "clip", "max_temperature_c": 70, "flexes": true}`); hitlcad recommends the materials that
   meet them and checks the one chosen. A material says its service temperature and whether it takes flexing.

Done when, for a printer the human enters (a Bambu Lab P2S with a 0.4 mm nozzle, in Bambu Studio), every printed
part of the NAS case lies the way its best orientation says, needing no support it could avoid, with each way's
cost to its strength or fit named in the plan; the front shell's size is a finding whose fix (a split) the agent
makes; and the human prints the case from its `print.3mf` in Bambu Studio without turning a part or guessing a setting.

## Not yet ordered

Each becomes a milestone when it is next.

### Editing in the panel

The human edits a design in the panel as in a CAD tool, and only what needs code written goes to the agent:
editing a value never costs tokens. Real CAD tools let the human select geometry, see the dimensions that drive
it there, and drag or type them; their sketches stay editable. A Python part file has no feature tree to edit, so
hitlcad records one as the file runs, and anything structural is a request the agent turns into code, which the
human judges against what they drew.

- **Pointing at the model.** The part under the cursor is lit and named, and a click selects a part, a click on a
  selected part what is under the cursor on it. Left: one click still selects, places a pin or measures by modes
  the human cannot see (**Place pins**, the viewer's tools), and inspection keeps targets of its own. Instead,
  everything acts on the one selection, from a bar shown with it: fit, isolate and section; measure, with two;
  the parameters that drive it; and a note for the agent, which is a pin. No click mode is left. The structure tree, the results' addresses and the 3D view set the same selection, by the same
  names. What the agent hears is what the human sends it, the notes; the selection is for looking.
- **The feature tree of a part file.** The build worker records each build123d operation as a part file runs, as
  it records each `BuildSketch` already (its profile, frames and line, drawn in the panel's 2D and 3D views):
  primitives, booleans, placements, extrudes, fillets, and sketches in algebra code: its file, line and
  columns, its arguments' values, and which parameters and which literals of the source each value came from, with
  its rate (numbers that carry their sources and derivatives). Each face of the result maps to the operation that
  made it (OpenCascade's history of booleans, else the surface it lies on). A prototype on the enclosure mapped all
  57 faces to their lines and parameters. A value that loses its sources (numpy, `int()`) is found by building the
  part file once more with each parameter nudged.
- **Dimensions on the model.** Selecting a face shows the dimensions that drive it, on the model: a height arrow
  on the lid's top face tied to `lid_t`, a thickness between two faces tied to `wall`. Dragging or typing previews
  the value in the panel and writes the parameter once, when the drag is released or the value entered (one edit,
  one Undo), merged from the revision on screen. A value fixed in code is changed where it is (its literal, by
  its columns) or made a parameter (design.json and the code, together) by the panel itself where `p` is in reach,
  by the agent where it is not. Then the deferred drag of a whole part in a locked view, by the parameters that
  place it, is the same edit.
- **The editor in the document.** Open editors join the design's document (Yjs in the extension, pycrdt-websocket
  in the server), so the human's unsaved typing merges with the panel's and the agent's edits as they happen.
- **Sketches as requests.** On a face or a part's datum plane the human draws lines, arcs, circles, rectangles and
  slots, with typed dimensions (a parameter's name or a value) and snaps recorded as intent (centred, on an edge),
  and gives each closed profile an operation: pocket, boss, cut through, hole, with a pattern. It reaches the agent
  as a pin carrying the sketch in the part's own frame; the agent writes the code, and the panel draws the sketch
  over the new build for the human to accept or send back.
- **Predefined objects.** Catalog parts (the providers `search_catalog` searches) dropped on a picked connector or
  hole pattern, sketch templates (bolt circle, slot, hex pocket, vent grid) and simple parts (box, cylinder, plate,
  standoff) on a face: requests too, from what the plugins give, so the building blocks (below) appear here
  without panel code.
- **Suggestions.** An edit tried before it is written: built from the document with it, beside the current build,
  then kept or dropped; and the same for an agent's proposal (as JupyterCAD's forks).

Done when, on the NAS case and the V8 engine, the human names any part by pointing at it, and selects, inspects,
measures and annotates it without wondering what a click will do; and, on the enclosure, the human drags the lid's
thickness and changes a boss radius fixed in code, draws a vent the agent turns into a pocket, and tries a wider box
as a suggestion, all while the agent edits the same files, with no edit lost and no agent turn for a value.

### Builds seen as they are built

Most of this shipped in 0.1.x: the panel draws a build once its parts are placed, results come as each
analysis finishes, outputs are written only when asked, and parts' geometry, manufacturing findings and
interference keep what an edit did not touch. What is left:

- **Parts as they come.** Each part file's view reaches the build on screen as it builds or comes
  from the cache, so a part that takes long does not hold back the others; today the scene comes once
  every part is placed.
- **Meshes the size of the part.** The analysis mesh's deflection scales with the part, as the view's
  does (0.01 mm on a 224 mm camshaft is 644 000 triangles where 50 000 show it); what needs precision
  keeps reading the B-rep, and a fit's facet allowance follows the mesh it is measured on.

Done when, on the V8 engine, a full rebuild shows every part before any analysis runs, an edit to one
part file redraws within two seconds of saving and checks the manufacturing of that part alone, and
the report shows the whole verification within a minute.

### Motion that scales

Motion follows a mechanism's inputs, placing what each moves stage by stage: about fixed axes by laws
where it can, else in small blocks settled in order, each a CasADi graph solved by CasADi's SQP method
with exact derivatives. Relations couple motions by laws (gears at a ratio, a cam by its lift table, its
follower kept in contact).

- **Close pairs that slide past each other.** On the V8 engine, half of a cold sweep (8 s of 16 s) goes to
  two kinds of pair that move along a 0.3 mm running gap: a piston along its bore, a pushrod's ball in its
  rocker's cup. A step is bounded by how far any point travels, not by how fast the gap closes, so each
  0.3 mm of travel costs an exact distance between two closely fitting meshes (7 ms each). Collision
  libraries bound a step by the approach along the direction that separates the two, inside their trees of
  triangles, in compiled code; none does it for meshes from Python. python-fcl's continuous collision does
  not work on meshes (a box moved straight through another reads clear; a box shape against a mesh
  crashes the process), and coal, hpp-fcl's successor, has none, and measures these pairs no faster than
  FCL (the same distances, 7 ms each; its lower bound reads 0.001 mm at the 0.3 mm gap). What remains is
  that bound written in compiled code over FCL's or coal's trees. Tried and set aside: checking a slide's solid
  dragged along its stroke, and what a ball joint keeps in place, once per pair (their Minkowski sums cost
  more than the distances they saved: 75 s from 41 s). Each block solve already settles in two SQP
  iterations, so a better starting guess saves nothing; tracing's time is CasADi's per call.
- **The V8 engine on the new motion.** Its valve train declared as cams (lobe to lifter, rocker to
  valve) and its springs as `span` requirements, so one input, the crankshaft, moves the whole engine.

Done when the V8 engine's whole cycle is one input's sweep, every valve lifting by its cam, in under
5 s of motion analysis.

### hitlcad's own agent, measured, and designing from a picture

Anyone can design with hitlcad, not only those with Claude Code or Codex (they need an account
with Anthropic or OpenAI, and are out of reach in some regions), and every agent gets a design
right in fewer builds.

- **The agent.** [pi](https://github.com/badlogic/pi-mono) runs inside the extension, with its chat in
  the panel: picks and pins attach to a message directly, and it builds, reads the results and
  fixes on its own. It reaches hitlcad the way Claude Code and Codex do (the MCP tools, the
  generated skills, the build report), through a thin pi extension: one more client of the same
  interface. Claude Code and Codex keep working as they do.
- **Any model.** The agent talks to a model endpoint the user sets, so it is not tied to one
  provider. Builds and design folders stay on the user's machine.
- **Measure the agent.** The reference designs become benchmark tasks, run headless against fixed
  requirements with Claude Code, Codex and the built-in agent on each model, small and local ones
  included: requirements passed on the first build and at the end, builds needed, mistakes caught
  and missed, tokens, and what kind each mistake was (3D placement, API code, design knowledge,
  losing track of the task). It is run deliberately, not on every push, because each run costs real
  tokens; it tells users which models work well for which tasks, shows whether a change moved the
  numbers it was meant to, and says where the next effort pays: skills, building blocks or messages.
- **Designing from a picture, in the panel.** The human attaches a photo or a sketch to a design in
  the panel, with what matters in it, which view it shows and one dimension (the figurine is 175 mm
  tall), and the panel writes the `silhouette` requirement; the panel shows the reference over the
  model from that view, and the outline the cut-out found, so a wrong cut-out is seen before it misleads.
- **Measure the design skills.** The skills now include `hitlcad-design`, `hitlcad-form`, `hitlcad-dfm` and
  `hitlcad-style` (general CAD intent, concept and style exploration, composition, manufacturing and assembly);
  whether they help is not yet measured. Compare operating skills alone against the design skills on held-out
  briefs and later edits: requirements met, preservation of intent, repair builds, tokens, meaningful variety and
  human preference from equivalent views. Count exploratory builds separately from repair builds. Record
  supported benefits and tradeoffs rather than equating fewer builds with better design. The usual fixes in the
  error catalog (which generates the playbook) are also measured and rewritten where they do not help; the
  benchmark counts mistakes by their codes.

Done when someone without Claude Code or Codex builds a reference design with the built-in agent,
one command runs the benchmark and prints its comparison by agent and model, with the first results
recorded here, the skills have measurably raised them, and a figurine modelled from a photo passes
its silhouette check from that photo's view.

### Building blocks

Today an agent writes every feature as raw geometry; building blocks give it tested pieces to compose instead.

- **Building blocks.** A library of tested, parametric features a part file calls instead of
  modelling them: a heat-set insert boss, a counterbored or countersunk screw hole, a snap fit, a
  gear pair, a bearing seat, a shelled enclosure with its lid. Each comes with its connectors
  declared and made for its process, so a block that builds is already a block that fits and
  prints. Composing pieces asks far less of a model than inventing geometry, which matters most for
  small ones. Blocks are model packages of plugins (hitlcad's own among them), as the laser cutting
  example's `plate` is.

Done when the reference designs use building blocks where one fits, with the benchmark showing what
that changed for small models.

## Later

**Checks no one has yet**

- Sheet metal: flange length, hole-to-bend distance, bend relief.
- CNC: walls too thin to mill without chatter or breaking.

**Cheaper manufacturing checks** (measured in [manufacturing-performance.md](manufacturing-performance.md))

- Wall sampling with an explicit accuracy and coverage policy: genuine wall evidence told apart from tapers
  before the refinement budget is spent, judged on a corpus of analytic walls, rims, fins and necks.
- CNC visibility and molding undercuts refine the whole surface (`_split`); resin drainage traces a dense grid
  of the bounding box. Each profiled on representative parts before its algorithm changes.
- Ray and nearest-point backends (an Embree-based one against libigl's) benchmarked on the same workloads,
  with one level of parallelism chosen deliberately.

**Platforms and distribution**

- Linux ARM in CI (devcontainers on Apple Silicon run it; GitHub has ARM runners).
- Optional plugins (laser cutting, and those that follow) in a repository of their own, each still its
  own package, once there are several of them and the plugin API has stopped changing; hitlcad's tests
  then keep a small plugin of their own.
- The extension on the VS Code Marketplace, under a real publisher.
- Measure the server's memory between builds; check whether Blender's meshes are byte-identical
  from build to build (a head mesh differed once and was not followed up).

**Builds**

- **Revisions that share their files.** Each revision holds a copy of every part's files, changed or
  not; stored once by content, with each revision naming what it holds, the data folder would stop
  growing by a whole assembly with every build.

**Motion**

- **Speeds and loads.** Each part's velocity per unit of the input at every position: an exact
  sweep's from its laws' slopes, a traced one's from its positions. With the parts' masses, that gives requirement checks:
  each part's speed at a driver speed, and the torque or force the driver needs against gravity and
  inertia over the range ("the motor's rated torque covers the worst torque, ×2"). Joint forces and
  friction only as labelled estimates: an overconstrained mechanism's joint forces are indeterminate
  without its compliance, and friction coefficients are unknown. Simulating how a mechanism moves
  under forces is left to the URDF and SDF exports and the tools that read them.
- **Gear-only blocks, exactly.** A block closed only by gears on parallel axes (a planetary's carrier
  and planets) moves each part by turns at fixed rates, so it could be placed by laws and keep the exact
  sweep's guarantee, needing no watch.

**Open questions**

- **When a build becomes a graph.** A build is a fixed chain with two fan-outs, its part files and its
  checks, so a scheduler of typed tasks with guards and transforms between them, or a chain of
  handlers each calling the next, would add concepts to every plugin and surface without making a
  build faster. It pays once plugins make what other plugins read (a mesh for FEA that several checks
  use), checks read each other's results, or builds run off the machine.

- **Proving what sampling leaves open.** A traced sweep is checked at the positions solved. An interval
  or SMT solver (dReal, Z3) could prove small claims about a block itself: that it locks where it
  says, that only one branch of its closing lies between two positions, how many ways it can move.
  Collisions between positions stay the human's to watch.
- **Change points.** Where two branches of a linkage's closing meet (a parallelogram folding over),
  the real mechanism may take either; a sweep follows one and does not say which.
- **Catalog breadth.** bd_warehouse covers fasteners and bearings; motors, connectors and boards
  come from vendor STEP files. Whether `search_catalog` also searches an online catalogue is a
  policy question as much as a technical one.
- **Orientation beyond printing.** v0.2 gives printed parts an orientation of their own; a part machined in
  several setups needs one per setup.
- **Very large assemblies.** Instanced tessellation covers repeated parts; simplified
  representations per component (as SolidWorks' SpeedPak) may be needed beyond a few thousand
  instances.
