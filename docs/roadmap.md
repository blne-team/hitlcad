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
the prompt hook; an explode pulls an assembly apart, and a STEP, STL or glTF file opens in the same
view, its picks measured on the mesh a build would make. A part is built again only when something
it depends on changes, wherever its design folder is. It runs on Linux, macOS and Windows, which CI
checks, release installer included. Four reference designs build and verify in every configuration.

A design's vocabulary and hitlcad's outputs are plugins (see [plugins.md](plugins.md)): connector
kinds, mate and relation types, requirement checks, processes, backends, catalogs, exporters and
materials, hitlcad's own registered the same way as an installed package's, which a design names in
design.json; a laser cutting plugin outside hitlcad is the worked example. Every error has one shape:
a code, whose it is to fix and the usual fix, which the build summary, the tools and the panel show,
and which the skills' playbook lists. A failed build is a revision whose results say why, at the
file and line. Builds start in processes already started, their libraries loaded.

A closed loop of mates (a four-bar, a planetary) moves with the rest of its loop: a loop solver
plugin gives its positions, hitlcad checks each, and the human watches the motion in the panel to
pass what is clear at every position but unchecked between them.

## v0.2: hitlcad's own agent, skills that teach design, and designing from a picture

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
- **Designing from a picture.** The human attaches a photo or a sketch to a design, saying which
  view it shows and one dimension in it (the figurine is 175 mm tall). hitlcad extracts its
  silhouette and, where it shows a body, its pose (named keypoints: nose, ears, paws, joints), in
  millimetres in the design's frame, and hands them to the agent as outlines and points it can model
  against: a profile to extrude or loft, where a sculpted mass goes. A requirement check projects
  the built model from the same view and compares it with the reference outline (how much they
  overlap, and the largest deviation in mm), and a failure says where they part ("the left ear is
  4 mm lower than the reference"). The panel shows the reference over the model from that view.
- **Skills that teach design.** The skills (read by Claude Code, Codex and the built-in agent alike)
  explain how hitlcad works but little about designing well, so agents learn it from failed builds.
  `SKILL.md` keeps the workflow and rules; reference files the agent reads when a task needs them
  add design for manufacture (walls, bridges, holes and inserts, tool reach, bends), choosing
  fasteners and bearings and their fits, and the reference designs as worked patterns; the usual
  fixes in the error catalog (which the playbook is generated from) are measured and rewritten
  where they do not help. Each change is kept only if the benchmark improves: requirements met on
  the first build, builds needed, tokens. The benchmark counts mistakes by their codes.

Done when someone without Claude Code or Codex builds a reference design with the built-in agent,
one command runs the benchmark and prints its comparison by agent and model, with the first results
recorded here, the skills have measurably raised them, and a figurine modelled from a photo passes
its silhouette check from that photo's view.

## v0.3: building blocks

Today an agent writes every feature as raw geometry; v0.3 gives it tested pieces to compose instead.

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

**Motion**

- **Speeds and loads.** The loop solver keeps each part's velocity per unit of the driver at every
  position (exact sweeps have their rates). With the parts' masses, that gives requirement checks:
  each part's speed at a driver speed, and the torque or force the driver needs against gravity and
  inertia over the range ("the motor's rated torque covers the worst torque, ×2"). Joint forces and
  friction only as labelled estimates: an overconstrained mechanism's joint forces are indeterminate
  without its compliance, and friction coefficients are unknown. Simulating how a mechanism moves
  under forces is left to the URDF and SDF exports and the tools that read them.
- **Gear-only loops, exactly.** A loop closed only by gears on parallel axes (a planetary) moves each
  part by turns at fixed rates, so it could keep the exact sweep's guarantee, needing no watch.

**Open questions**

- **Proving what sampling leaves open.** A loop is checked at the positions solved. An interval or
  SMT solver (dReal, Z3) could prove small claims about the loop itself: that it locks where it
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
