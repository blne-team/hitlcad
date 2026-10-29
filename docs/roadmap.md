# Roadmap

Where hitlcad is, and the order of what comes next. Each milestone has a goal, its scope, and what
has to be true before it is done. Items move from here into the code; this page is updated when a
milestone closes or the order changes.

## Where hitlcad is

v0.1 is the first release. The assembly model is built (see [assembly-model.md](assembly-model.md)):
design folders, connectors, mates, fits, fastener sets, components, configurations, motion,
catalogs, materials, five manufacturing processes, exports. Every check gives a verdict the agent
can act on: a failure is a real problem, says where, and appears once per root cause. The VS Code
extension shows builds in 3D, takes picks and pins, and hands them to Claude Code and Codex through
their hook; an explode pulls an assembly apart, and a STEP, STL or glTF file opens in the same
view, its picks measured on the mesh a build would make. A part is built again only when something
it depends on changes, wherever its design folder is. It runs on Linux, macOS and Windows, which CI
checks, release installer included. Four reference designs build and verify in every configuration.

A design's vocabulary and hitlcad's outputs are plugins (see [plugins.md](plugins.md)): connector
kinds, mate and relation types, requirement checks, processes, backends, catalogs, exporters and
materials, hitlcad's own registered the same way as an installed package's, which a design names in
design.json; a laser cutting plugin outside hitlcad is the worked example. Every error has one shape:
a code, whose it is to fix and the usual fix, which the build summary, the tools and the panel show,
and which the skills' playbook lists. A failed build is a revision whose results say why, at the
file and line.

Motion follows a mechanism's inputs: each input's sweep places what it moves stage by stage, about
fixed axes by laws (swept exactly), or in blocks a solver plugin settles at each position (a four-bar's
coupler and rocker, a planetary's planets), which hitlcad checks and the human watches in the panel to
pass what is clear at every position but unchecked between them. Relations couple motions by laws: a
cam lifts its follower by a table the design's code computes, and the two must stay in contact.

A picture of what a design should look like (a photo, a render, a sketch, saved under `references/`) is a
`silhouette` requirement whose text is what the human says about it: every build cuts its subject out,
compares the model's outline from that side with it in millimetres, and says what strays, how far and
which way; the agent sees both outlines over the picture on a millimetre grid, and reads its landmarks
from it.

A backend tessellates each part for the 3D view where it builds it, into a binary view file beside the
part's other files that the part cache keeps with them; a build's scene only places those files, and the
panel reads each one where it lies, keeping those it has read by digest (256 MB), so a part unchanged
between revisions is neither fetched nor read again.

A design's code runs in build workers, as a notebook's cells run in its kernel: each one design's, kept
between its builds with its backend's libraries loaded, each part file a job of its own; what a worker
exported is kept by the shape's content (256 MB), so a part that comes out of a part file as before is
not exported or tessellated again. A backend says only what is its own: how its worker starts and how its
part values read and export (see [plugins.md](plugins.md#build-workers)). A save of other files
supersedes the build running for them: it stops at its next stage, and what its running part files
built reaches the part cache for the newer one. hitlcad tessellates parts itself, every face and edge in
the order picks number them, so a face the mesher cannot mesh no longer shifts the faces after it.
Motion measures gaps with FCL (python-fcl) on a tree of each part's triangles built once for all its instances, a turning
solid's round part is found from its triangles' distinct rings, a block is one compiled CasADi graph
solved with exact derivatives, and a traced sweep's pair already within a step of contact, with no clearance asked
of it (gears in mesh), is only tested for meeting: the gearbox's motion analysis takes 5 s, from 73 s.
The panel draws a build as soon as its parts are placed, before anything is checked, says which
analysis is running, and shows each analysis's results as it finishes; picks and motion come with its
revision. The report says how long each step took. A part's manufacturing findings are kept by what they
read of it, so an edit checks only the parts it changed; interference compares only instances whose boxes
meet along x and keeps each overlap by where one part sits from the other, so a subassembly moved whole is
not measured again. Each part's loaded geometry (its mesh, solid, B-rep features, metrics and colliders) is
kept in the server by its files' content, so a build, or a revision loaded back, loads and measures only
the parts an edit changed: on the V8 engine, a part file touched without changing its shape builds in 6 s.
A build writes what review needs (each part's own files, the 3D view, the report);
the assembly's STEP and mesh, the Blender scene, the rendered views and the exporters' files only when
design.json `outputs` names them, which the agent edits or the human ticks in the panel, and the agent's
images render the views of any revision without them.

A design's files are a CRDT document in hitlcad's database (pycrdt), of which its folder is the copy on disk, so
the human and the agents edit the same files at once without undoing each other: every edit is made on a version
and merged into the current one. The panel's parameter and outputs edits change only their value's text in
design.json, made on the revision on screen; the agents' hook runs around their file reads, edits and shell
commands as well as their prompts, so what a tool writes merges from the version it started on (a file written
whole, from the version it was read at), and the agent is told in one line, with the tool's result, what the human
changed meanwhile; any other save merges from the version each file was last written or read at. Two edits that
change the same text at once are both kept, by rule, and shown to the human (and the agents involved) to look at.

## Next: motion that scales

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

## Next: editing in the panel

The human edits a design in the panel as in a CAD tool, and only what needs code written goes to the agent:
editing a value never costs tokens. Real CAD tools let the human select geometry, see the dimensions that drive
it there, and drag or type them; their sketches stay editable. A Python part file has no feature tree to edit, so
hitlcad records one as the file runs, and anything structural is a request the agent turns into code, which the
human judges against what they drew.

- **The feature tree of a part file.** The build worker records each build123d operation as a part file runs
  (primitives, booleans, placements, sketches, extrudes, fillets, in builder and algebra code): its file, line and
  columns, its arguments' values, and which parameters and which literals of the source each value came from, with
  its rate (numbers that carry their sources and derivatives). Each face of the result maps to the operation that
  made it (OpenCascade's history of booleans, else the surface it lies on). A prototype on the enclosure mapped all
  57 faces to their lines and parameters. A value that loses its sources (numpy, `int()`) is found by building the
  part file once more with each parameter nudged.
- **Dimensions on the model.** Selecting a face shows the dimensions that drive it, on the model: a height arrow
  on the lid's top face tied to `lid_t`, a thickness between two faces tied to `wall`. Dragging or typing writes
  the parameter, merged from the revision on screen. A value fixed in code is changed where it is (its literal, by
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
  standoff) on a face: requests too, from what the plugins give, so v0.4's building blocks appear here without
  panel code.
- **Suggestions.** An edit tried before it is written: built from the document with it, beside the current build,
  then kept or dropped; and the same for an agent's proposal (as JupyterCAD's forks).

Done when, on the enclosure, the human drags the lid's thickness and changes a boss radius fixed in code, draws a
vent the agent turns into a pocket, and tries a wider box as a suggestion, all while the agent edits the same
files, with no edit lost and no agent turn for a value.

## v0.2: a build seen as it is built

A V8 engine of 53 parts and 258 instances took 939 s from save to anything in the panel. A cold
build of it now takes 120 s, its parts drawn once its part files are built (6 s), but its results
still wait for every analysis (105 s: manufacturing 53 s, motion 40 s, interference 11 s) and every
output, and every build reloads every part for its analyses. Parts' geometry, manufacturing and
interference now keep what an edit did not touch, results come as each analysis finishes, and outputs are written only when
asked; the rest is below. A viewer such as ocp-viewer-core shows the same parts at once because it
tessellates in a process that already holds the shapes, keeps what
it tessellated, and measures only when the human picks. hitlcad keeps verifying every build, after
showing it, and keeps what an edit did not touch.

- **Parts as they come.** Each part file's view reaches the build on screen as it builds or comes
  from the cache, so a part that takes long does not hold back the others.
- **Meshes the size of the part.** The analysis mesh's deflection scales with the part, as the view's
  does (0.01 mm on a 224 mm camshaft is 644 000 triangles where 50 000 show it); what needs precision
  keeps reading the B-rep, and a fit's facet allowance follows the mesh it is measured on.

Done when, on the V8 engine, a full rebuild shows every part before any analysis runs, an edit to one
part file redraws within two seconds of saving and checks the manufacturing of that part alone, and
the report shows the whole verification within a minute.

## v0.3: hitlcad's own agent, skills that teach design, and designing from a picture

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
- **Skills that teach design.** The skills (read by Claude Code, Codex and the built-in agent alike)
  now include `hitlcad-design`, `hitlcad-form`, `hitlcad-dfm` and `hitlcad-style`: general CAD intent,
  concept and style exploration, composition, manufacturing and assembly, with six initial visual
  directions. Their entrypoints teach human collaboration and tool decisions; optional references
  add architectural patterns, vector/organic form methods, process guidance, hardware and fits.
  Complete teaching code demonstrates a parametric assembly and a section-driven sculptural form.
  Content is packaged and examples are checked, but behavioral improvement remains to be measured.
  Compare operating skills alone against the design skills on held-out briefs and later edits:
  requirements met, preservation of intent, repair builds, tokens, meaningful variety and human
  preference from equivalent views. Count exploratory builds separately from repair builds. Record
  supported benefits and tradeoffs rather than equating fewer builds with better design. The usual
  fixes in the error catalog (which generates the playbook) are also measured and rewritten where
  they do not help; the benchmark counts mistakes by their codes.

Done when someone without Claude Code or Codex builds a reference design with the built-in agent,
one command runs the benchmark and prints its comparison by agent and model, with the first results
recorded here, the skills have measurably raised them, and a figurine modelled from a photo passes
its silhouette check from that photo's view.

## v0.4: building blocks

Today an agent writes every feature as raw geometry; v0.4 gives it tested pieces to compose instead.

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

- Printer build volume: the NAS case's front shell (249 × 251 mm) does not fit common
  250 × 210 mm beds.
- Sheet metal: flange length, hole-to-bend distance, bend relief.
- CNC: walls too thin to mill without chatter or breaking.

**Platforms and distribution**

- Linux ARM in CI (devcontainers on Apple Silicon run it; GitHub has ARM runners).
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
- **Manufacturing orientation.** A part's own frame is how it is made; a part made in several
  setups, or printed in another orientation than it is modelled, needs a declared orientation per
  process.
- **Very large assemblies.** Instanced tessellation covers repeated parts; simplified
  representations per component (as SolidWorks' SpeedPak) may be needed beyond a few thousand
  instances.
