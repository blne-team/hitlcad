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
the prompt hook. It runs on Linux, macOS and Windows, which CI checks, release installer included.
Four reference designs build and verify in every configuration.

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
  fasteners and bearings and their fits, a playbook from each kind of failure to its usual fix, and
  the reference designs as worked patterns. Each change is kept only if the benchmark improves:
  requirements met on the first build, builds needed, tokens.
- **Faster saves.** A build123d build starts fresh Python processes, and each spends about 1.7 s
  importing build123d before it runs the design: about 3.5 s of every save goes to startup. The
  server keeps a few build processes warm, started and imported, waiting for a job; a build takes
  one, runs in it once, and it exits as today, while a replacement warms up. Each build still has
  a fresh process of its own, with its own limits, and nothing it starts outlives it; the same on
  every platform.

Done when someone without Claude Code or Codex builds a reference design with the built-in agent,
one command runs the benchmark and prints its comparison by agent and model, with the first results
recorded here, the skills have measurably raised them, a figurine modelled from a photo passes its
silhouette check from that photo's view, and a save of a build123d design no longer waits for
Python to start.

## v0.3: plugins and building blocks

Today backends, catalogs and exporters are plugins (entry points); the vocabulary of a design is
not. Supporting SLS, laser cutting or a shop's own CNC rules means editing hitlcad. v0.3 makes that
vocabulary pluggable, and keeps what every check depends on shared. And today an agent writes every
feature as raw geometry; v0.3 gives it tested pieces to compose instead.

- **Pluggable:** manufacturing processes, requirement kinds, analyses, connector kinds and
  relations. A plugin contributes its `design.json` model (its `process` or `kind`), its
  verification and its documentation, through entry points.
- **Shared, not pluggable:** the measuring toolkit (holes and shafts, wall thickness, minimum gap,
  visibility, motion sweeps) that plugins call, and the result contract (status, root cause,
  location) that makes every plugin's output read the same to the agent.
- **One path:** a registry builds the design model's unions from what is installed; validation,
  the agents' generated skills and the panel's types all come from it. The built-in processes and
  checks register the same way as any plugin.
- A design that names a process no installed plugin provides fails its structure check by saying
  which plugin it needs.
- **Building blocks.** A library of tested, parametric features a part file calls instead of
  modelling them: a heat-set insert boss, a counterbored or countersunk screw hole, a snap fit, a
  gear pair, a bearing seat, a shelled enclosure with its lid. Each comes with its connectors
  declared and made for its process, so a block that builds is already a block that fits and
  prints. Composing pieces asks far less of a model than inventing geometry, which matters most for
  small ones. Plugins contribute blocks the same way.

Done when a separate package adds a manufacturing process (one shipped as a worked example) that
shows up in validation, the skills, the panel and the build report without a change to hitlcad, and
the reference designs use building blocks where one fits, with the benchmark showing what that
changed for small models.

## Later

**Checks no one has yet**

- Printer build volume: the NAS case's front shell (249 × 251 mm) does not fit common
  250 × 210 mm beds.
- Sheet metal: flange length, hole-to-bend distance, bend relief.
- Resin: drain hole size; CNC: thin walls and the smallest feature a tool can cut.

**Platforms and distribution**

- Linux ARM in CI (devcontainers on Apple Silicon run it; GitHub has ARM runners).
- The extension on the VS Code Marketplace, under a real publisher.
- A faster test suite: tests in parallel, and the every-configuration example builds only where
  they add coverage.
- Measure the server's memory between builds; check whether Blender's meshes are byte-identical
  from build to build (a head mesh differed once and was not followed up).

**Open questions**

- **Coupled mechanisms.** Motion does not sweep closed loops (a planetary, a four-bar linkage). A
  loop-closing kinematic solver would be the first exception to "check, don't solve".
- **Catalog breadth.** bd_warehouse covers fasteners and bearings; motors, connectors and boards
  come from vendor STEP files. Whether `search_catalog` also searches an online catalogue is a
  policy question as much as a technical one.
- **Manufacturing orientation.** A part's own frame is how it is made; a part made in several
  setups, or printed in another orientation than it is modelled, needs a declared orientation per
  process.
- **Very large assemblies.** Instanced tessellation covers repeated parts; simplified
  representations per component (as SolidWorks' SpeedPak) may be needed beyond a few thousand
  instances.
