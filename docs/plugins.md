# Plugins and diagnostics

How hitlcad's vocabulary and outputs are plugins, how every error has one shape the agent can act
on, and the store and build processes that go with them. [architecture.md](architecture.md) places
them in the whole system.

## Core and plugins

A **plugin** contributes members of a family of named alternatives a design chooses among in
design.json (a process, a mate type), or something a build is made with (a backend, a catalog, an
exporter). Each member brings its own model, verification, documentation and kinds of result, and
adding one changes nothing else. **Core** is the language every plugin speaks, anything there is
exactly one of, and whatever makes the results of different plugins comparable.

**Core never names a member.** Where core needs something of one, it asks for a capability:

| Capability | Declared by | Asked by |
| --- | --- | --- |
| `Connector.round`: `"hole"`, `"shaft"` or none | a connector kind | fits, interference, fastener sets |
| `Connector.owns(face, frame, tol)` | a connector kind | naming a pick after the connector it is on |
| `Connector.fixed_nominals()` | a connector kind (a gear's `internal`) | the relations' nominal values |
| `Check.keeps_apart()`: two things and a gap | a requirement check (clearance) | motion, which watches them as it sweeps |
| `Process.allowance("hole" or "shaft")` | a process | fits, fastener clearance |
| `Mate.holds`: what of b's frame stays as assembled in a's | a mate type | a loop's solver and the check of its every position |
| `Relation.coordinates(fa0, fb0, fa, fb)`: how far each end moved, against what holds the coupling | a relation type | motion: the rate a driven gear turns at, and each position of a loop |

### What a plugin contributes

A plugin is a package, the unit people install; a contribution is one thing it adds, through one hook.
A package may contribute any number of each.

| Hook | Returns | hitlcad's own |
| --- | --- | --- |
| `hitlcad_connectors` | `Connector` subclasses with a `kind` literal | `joints`: frame, face, hole, shaft; `gears`: gear |
| `hitlcad_mates` | `Mate` subclasses with a `type` literal | `joints`: rigid, revolute, slider, cylindrical, planar, ball |
| `hitlcad_relations` | `Relation` subclasses with a `type` literal | `gears`: gear, rack, screw |
| `hitlcad_checks` | `Check` subclasses with a `kind` literal and a `hint` | `checks`: size, fits within, volume, mass, hole, wall, bodies, clearance, stack |
| `hitlcad_processes` | `Process` subclasses with a `process` literal and `findings` | `processes`: fdm, resin, cnc_milling, sheet_metal, injection_molding |
| `hitlcad_backends` | factories by name, each taking the settings | `build123d`, `blender` |
| `hitlcad_catalogs` | `CatalogProvider`s | `catalogs`: bd_warehouse, step |
| `hitlcad_exporters` | `Exporter`s | `exports`: bom, 3mf; `robot`: urdf, sdf |
| `hitlcad_solvers` | `Solver`s by name: positions of a loop of mates, which motion checks (`Settings.solver`) | `solvers`: least_squares |
| `hitlcad_analyses` | `Analysis` objects, each at a `stage` | none: hitlcad's analyses are core |
| `hitlcad_materials` | `materials.json` paths | `materials` |
| `hitlcad_model_packages` | `ModelPackage`s: code part files import, by backend | `catalogs` |
| `hitlcad_error_kinds` | `ErrorKind`s of the plugin's own diagnostics | none |
| `hitlcad_skill_references` | reference files for the agents' `hitlcad` skill, by name | none |

A building block is a function in a model package, with a skill reference describing it; it needs no
hook of its own.

The analyses hitlcad runs (structure, parts, connectors, mates, relations, fasteners, coverage,
interference, motion, manufacturing, requirements) are core: each loops over what a design declares
and asks each member to verify itself. A plugin's analysis is for checks of a new kind (stress, cost,
a print simulation); it runs at its stage, `structure`, `parts`, `connectors`, `joints`, `assembly`,
`motion`, `process` or `requirements`, so cause still comes before effect.

### hitlcad's own plugins

`backend/hitlcad/builtin/` holds hitlcad's own, grouped as a separate package would group them, and
registered in hitlcad's `pyproject.toml` under the same entry-point group as any other:

| Plugin | Contributes |
| --- | --- |
| `joints` | frame, face, hole and shaft connectors; the six mate types |
| `gears` | the gear connector; gear, rack and screw relations |
| `checks` | the requirement checks |
| `processes` | the five processes and their checks |
| `build123d`, `blender` | a backend, its entry script, its authoring guide |
| `catalogs` | bd_warehouse and step, and their model package (`hitlcad.builtin.catalogs`) |
| `exports` | bom, 3mf |
| `robot` | urdf, sdf |
| `solvers` | least_squares: a loop's positions by scipy's least squares, each step from the last |
| `materials` | the built-in materials |

### What stays core

- **Kernel** (`kernel/`): the plugin registry, diagnostics and their catalog.
- **The design's skeleton**: `DesignSpec`, `PartSpec`, components, configurations, parameters,
  requirements, fastener sets and `plugins`, with slots the installed members fill; the bases members
  derive from (`Connector`, `Mate`, `Relation`, `Check`, `Process`).
- **Execution**: the model runtime (`p`, `i`, what code read), the executor and its warm build
  processes, the pipeline, the part and result caches.
- **The measuring toolkit**: features on B-rep and mesh, wall thickness, interference and minimum
  gap, motion sweeps, sections. Plugins call it; none replaces it.
- **The analyses** above, and **the result contract**: `Result`, `Cause`, problems, the tally, the
  build summary.
- Services, the store, the design graph, picks, pins and anchors, the REST API, the MCP tools, the
  prompt hook, the skill installer, the preview renderer.

**Not plugins, on purpose**: MCP tools, API routes and the panel (plugins add vocabulary, not
interface: agents do better with a small fixed set of tools, and a plugin reaches the agent through
build results, `explore_design`, `search_catalog` and the skills); the store, executor and renderer
(one implementation each).

## Writing a plugin

[examples/plugins/hitlcad-lasercut](../examples/plugins/hitlcad-lasercut) is a complete one, kept
outside hitlcad: a laser cutting process, a building block part files import, and a skill reference.
hitlcad's development environment installs it (a uv workspace member), and its tests check it shows up
in validation, the skills, the playbook and the build report.

```toml
[project.entry-points."hitlcad.plugins"]
lasercut = "hitlcad_lasercut"
```

```python
from hitlcad.kernel.plugins import ModelPackage, hookimpl

@hookimpl
def hitlcad_processes() -> list[type]:
    from hitlcad_lasercut.process import LaserCutProcess  # inside the hook: see below
    return [LaserCutProcess]
```

- **Imports.** A plugin imports `hookimpl` and `ModelPackage` from `hitlcad.kernel.plugins`, the
  bases from `hitlcad.core.structure` and `hitlcad.core.checks`, diagnostics from
  `hitlcad.kernel.errors`, the built design and its bodies (`hitlcad.analysis.model`) and the measuring
  toolkit (`hitlcad.geometry`, `hitlcad.core.frames`) as it needs. It does not import the services or the
  surfaces; for hitlcad's own, `uv run lint-imports` holds that, and holds that nothing else in hitlcad
  imports `hitlcad.builtin`.
- **When hooks run.** The design model's members (connectors, mates, relations, checks, processes)
  are gathered while `hitlcad.core.design` is imported, to build design.json's unions. So a plugin's
  module, and the member classes it returns, import nothing that imports `hitlcad.core.design`
  (`hitlcad.core.report`, the backends, the pipeline); what else a hook returns, it imports inside
  the hook. Everything else is gathered the first time it is used.
- **Kinds of result.** A requirement check's results are `requirement.<kind>`, titled by its
  docstring, with its `hint` as the usual fix; a process's are `manufacturing.<finding>` for each
  of its `findings`, `{name: (title, usual fix)}`. They enter the error catalog when the member is
  gathered.
- **Code in the build process.** A model package names the directory its package is in and the
  backends whose part files may import it. The job hands those directories to the build process,
  which puts them on `sys.path`. Blender's Python sees nothing else of hitlcad's environment, so a
  model package for Blender uses the standard library and `bpy` only. A backend's fingerprint
  includes its model packages' files, so changing one rebuilds the parts.
- **Versions.** Each build records the versions of the installed plugins that are not hitlcad's own
  (`BuildSpec.plugins`), so a build after an upgrade is a new build, and a cached component's results
  are kept only for the same plugins and hitlcad code.

### Designs name the plugins they need

```json
{
  "hitlcad": 2,
  "plugins": { "hitlcad-lasercut": ">=0.1" },
  "manufacturing": { "process": "laser_cut", "thickness_mm": 3 }
}
```

Distribution names with version specifiers; hitlcad's own are never listed. They are checked before
the rest of design.json, so where a plugin is missing the design says `plugin.missing` ("design.json
needs hitlcad-lasercut >=0.1, which is not installed") rather than that `laser_cut` is no process.

### When a plugin fails

- A plugin that does not load is left out (`plugin.load_failed`).
- Two plugins giving one name (a process both call `"sls"`) both lose it (`plugin.conflict`).
- A backend enabled in `HITLCAD_BACKENDS` that no plugin gives is left out (`plugin.missing`).
- A member whose own code raises while it verifies or measures makes its result unverified with
  `plugin.error`, naming the plugin and its version, and the rest of the build goes on. When the
  member is hitlcad's own, that is `hitlcad.bug`.

`list_backends`, `hitlcad doctor`, `/api/capabilities` and the panel (above the Design view's tabs)
list what failed.

## Diagnostics

### One shape

```python
Owner = Literal["design", "request", "environment", "plugin", "hitlcad"]

class Location(Model):
    file: str | None    # relative to the design folder
    line: int | None
    address: str | None # in the address grammar, or a field of design.json

class Diagnostic(Model):
    code: str           # "model.connectors_missing"
    owner: Owner        # whose to fix, which says what the agent should do
    message: str        # in the design's own terms
    where: Location | None

class HitlcadError(Exception):
    diagnostic: Diagnostic
```

Every error hitlcad raises is a `HitlcadError` carrying a diagnostic of a kind in the catalog, raised
as `raise NOT_FOUND_PIN("pin p_1 not found")`. Three subclasses exist because code catches them by
what they are: `StructureError` (a name in design.json that does not resolve), `Unmeasurable` (what a
part's representation cannot answer) and `DesignInvalid` (design.json does not validate: a diagnostic
for each field, where it is). The build process is standard library only (Blender runs it), so its
`ModelError` carries a code as a string and the process writes it as a plain dictionary; the server
reads it back with its owner from the catalog. Any exception that is not a `HitlcadError` is a bug in
hitlcad (`diagnose`).

**The owner decides where a diagnostic goes:**

| Owner | Means | The agent should | Becomes |
| --- | --- | --- | --- |
| `design` | the design's files: design.json, its code, what they build | change the files | a failing `Result` of a revision |
| `request` | the call: no such revision, a relative path, an unknown parameter | change the call | a tool error; HTTP 404 or 422 |
| `environment` | the machine: a backend missing, a folder that cannot be written | tell the human | a tool error; HTTP 503; the design's error in the panel |
| `plugin` | an installed plugin: missing, broken, in conflict | tell the human | a tool error or an unverified result; HTTP 503; the panel |
| `hitlcad` | a bug | stop, and tell the human | a tool error saying not to change the design; HTTP 500 |

Each surface presents a diagnostic one way (`kernel/errors.py`): `agent_text` for the MCP tools and
the CLI, `http_status` and an `ErrorBody` (`detail` and the diagnostic) for REST, an `error` event
carrying the diagnostic for the panel.

### The catalog

```python
class ErrorKind(Model):
    code: str
    owner: Owner
    title: str        # "A part's code returns fewer connectors than design.json declares"
    hint: str         # the usual fix, imperative
    skill: str = ""   # where the skills explain it: "hitlcad: design.json reference, Mates"

MATE_FIT = define(code="mate.fit", owner="design", title=..., hint=...)
```

`define` enters a kind in the catalog, each code once. Every result names its kind too (`Result.code`),
passing or not: the kind of check it is, whose hint the agent reads when it fails. The skills'
`playbook.md` is written from the catalog, by owner, so a hint is written once and read in both places.
hitlcad's own codes, by area:

| Codes | Owner |
| --- | --- |
| `design.invalid`, `structure.*` (`reference`, `relation_kind`, `unplaced`, `ground`, `fastener_made`, `material_*`, `output_unknown`, `backend_unknown`) | design |
| `model.*` (`exception`, `no_build_function`, `return_type`, `connectors_missing`, `parts_mismatch`, `frame_type`, `shape`, `interface_cycle`, `unknown_interface`, `unknown_parameter`, `interface_value`) | design |
| `build.*` (`part_file_missing`, `placed_unknown`, `component`, `binding`, `time_limit`, `memory_limit`, `crashed`) | design |
| `part.*`, `connector.*`, `mate.*`, `relation.*`, `fastener.*`, `coverage.*`, `interference.*`, `motion.*`, `measure.unmeasurable`, `requirement.*`, `manufacturing.*` | design |
| `not_found.*`, `request.invalid`, `parameter.invalid`, `build.sources_changed`, `file.unreadable`, `project.config` | request |
| `backend.unavailable`, `build.outputs`, `export.write_failed`, `store.unreadable`, `design.unreadable` | environment |
| `plugin.missing`, `plugin.load_failed`, `plugin.conflict`, `plugin.error` | plugin |
| `hitlcad.bug` | hitlcad |

A build process that runs out of time or memory, or ends without a result, is the design's to fix:
it almost always does because of what the model code asks for. Its hint says so, and what to look for.

### Design errors are findings

What is wrong with a design reaches the agent through one channel, whether or not it built:

- **A failed build is a revision whose results say why**, under the `build` analysis, each with its
  file and line: an exception in the code, a contract broken, a part file missing, a component that
  does not build, a build process stopped. A build's log stays in its `build.log`.
- **An invalid design.json makes a failed revision too**, with no spec and a result for each problem
  in the file, at its field. The design is opened all the same, so the tools and the panel can say so.

### What the agent reads

A problem line gives its code, and where in the files when the subject is not already that; the usual
fix follows the first problem of each code; a problem the configuration's previous revision had too
says so:

```
Revision 7 — FAILED in 2.3s
  ✗ build parts/lid.py [model.connectors_missing]: parts/lid.py: part 'lid' declares connectors it does not return: screws
    → Return a frame (a list of frames for a pattern) for every connector design.json declares on the
      part, under the same name. (skill hitlcad-<backend>)
    Same as revision 6: the change since did not reach it.
  1 problem (1 failing check); 0 checks pass
```

A bug in hitlcad:

```
[hitlcad.bug] IndexError: list index out of range
This is a bug in hitlcad, not in the design: do not change the design to avoid it. Tell the human.
```

## The store

The design folder stays the source of truth: design.json is what the agent edits with its own tools,
what git diffs and what the panel writes parameters to. What hitlcad keeps on the machine is one
SQLite database, `<data_dir>/hitlcad.db` (`store/sqlite.py`, the standard library's `sqlite3`, WAL),
beside each revision's files in `<data_dir>/designs/<id>/revisions/<n>/`:

| Table | Holds |
| --- | --- |
| `folders` | folder path → design id |
| `designs` | the design ids |
| `events` | each design's events, in order: its history, folded into `DesignState` |
| `revisions` | each revision in full (what it built from and its report), with its configuration and status |
| `results` | each revision's results: code, analysis, subject, status, severity, file, line |

A revision's event lists it (`RevisionInfo`: id, origin, configuration, status, tally, what a failed
build failed on, its spec's key); the revision itself is read when asked for, and the panel fetches it
by id (`GET /api/designs/{id}/revisions/{n}`). Opening a design reads no revision's spec or report.
What a revision failed is a query (`DesignStore.failing`), which is how a build tells the agent it
repeats a problem.

A database of another layout (`meta` names its `SCHEMA`, raised with every change to the tables) is
set aside whole, as `hitlcad.<time>.db`, and a new one started; a design whose folder had a history
there says so when it is opened again. A design's history that does not read (its events or a revision
were written by another version) is set aside on its own, into `designs/<id>/unreadable-<time>/` as
JSON lines. No migrations.

One server keeps a data folder: it holds `<data_dir>/hitlcad.lock` while it runs, and a second server
on the same folder refuses to start (`store.in_use`), since each keeps its designs' state in memory.

## Warm build processes

Starting a build process costs about 1.5 s before model code runs, nearly all of it `import
build123d`, and every build starts at least two (the plan, then the parts). So every build process
is started before its job is known: it loads what its backend needs, then waits for its job's path
on stdin, and takes the job's folder as its working and temporary folder when the job comes.

The server keeps `warm_builds` (`HITLCAD_WARM_BUILDS`, default 1) such processes started for each
backend from when it starts. A build takes one; the next starts as it does. Each runs one job and
ends, which keeps a build's isolation: no design's modules, `sys.path` or library state carries into
the next build, and memory does not grow across jobs. Its deadline and memory limit count from the
hand-off. A process kept started holds about 400 MB for build123d; one that dies while it waits is
replaced. They end with the server, and on their own if the server is killed. The CLI's builds keep
none.

A pool that reuses its workers was not chosen: state would carry between designs, memory would grow
past the per-build limit, and a job stopped at its deadline takes its worker with it anyway.
`ProcessPoolExecutor` cannot stop a hung task or hold a process tree's memory, and Blender is not a
Python it can run; a fork server exists on POSIX only.
