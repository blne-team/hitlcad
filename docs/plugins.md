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
| `Check.files()`: design files it reads | a requirement check (silhouette: its picture) | the build spec, which checksums them, so a changed one builds again |
| `Check.figure(build)`: a PNG of what it measured | a requirement check (silhouette: both outlines on a grid) | `build_design`, which shows it to the agent with a failing result |
| `Process.allowance("hole" or "shaft")` | a process | fits, fastener clearance |
| `Mate.holds`: what of b's frame stays as assembled in a's | a mate type | the block solver and the check of its every position |
| `Relation.coordinates(fa0, fb0, fa, fb)`: how far each end moved, against what holds the coupling, written with `frames.heading`, `coordinate` and `wrapped` so a solver compiles it; `Relation.law(ends)`: b's coordinate as a function of a's | a relation type | motion: what a part a relation drives does, and each position of a block |

### What a plugin contributes

A plugin is a package, the unit people install; a contribution is one thing it adds, through one hook.
A package may contribute any number of each.

| Hook | Returns | hitlcad's own |
| --- | --- | --- |
| `hitlcad_connectors` | `Connector` subclasses with a `kind` literal | `joints`: frame, face, hole, shaft; `gears`: gear |
| `hitlcad_mates` | `Mate` subclasses with a `type` literal | `joints`: rigid, revolute, slider, cylindrical, planar, ball |
| `hitlcad_relations` | `Relation` subclasses with a `type` literal | `gears`: gear, rack, screw; `cams`: cam |
| `hitlcad_checks` | `Check` subclasses with a `kind` literal and a `hint` | `checks`: size, fits within, volume, mass, hole, wall, bodies, clearance, stack; `pictures`: silhouette |
| `hitlcad_processes` | `Process` subclasses with a `process` literal and `findings` | `processes`: fdm, resin, cnc_milling, sheet_metal, injection_molding |
| `hitlcad_backends` | factories by name, each taking the settings | `build123d`, `blender` |
| `hitlcad_catalogs` | `CatalogProvider`s | `catalogs`: bd_warehouse, step |
| `hitlcad_exporters` | `Exporter`s | `exports`: bom, 3mf; `robot`: urdf, sdf |
| `hitlcad_solvers` | `Solver`s by name: where a block of an input's motion settles at each position, which motion checks (`Settings.solver`) | `solvers`: sqp |
| `hitlcad_analyses` | `Analysis` objects, each at a `stage` | none: hitlcad's analyses are core |
| `hitlcad_materials` | `materials.json` paths | `materials` |
| `hitlcad_model_packages` | `ModelPackage`s: code part files import, by backend | `catalogs` |
| `hitlcad_error_kinds` | `ErrorKind`s of the plugin's own diagnostics | none |
| `hitlcad_skill_references` | reference files for the agents' `hitlcad` skill, by name | the backends' construction helpers; `pictures` |

A building block is a function in a model package, with a skill reference describing it; it needs no
hook of its own.

Both geometry backends ship construction helpers this way: `hitlcad_build123d.mounting_plate`
creates through holes and optional bosses, and `hitlcad_blender.section_form` creates a closed
material boundary around an open cavity. Their packages live under each backend's `models/`
directory, so Blender can import its helpers without importing the server's package dependencies.
Their reference files document keyword arguments, units, connectors, limitations and complete recipes.
`ModelPackage.summary` describes helpers without importing geometry libraries; `list_backends` and
the generated operating skill list these packages. They use the same normal builds and checks as hand-written parts.

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
| `cams` | the cam relation: a follower lifted by a table the design's code computes, kept in contact |
| `checks` | the requirement checks |
| `processes` | the five processes and their checks |
| `build123d`, `blender` | a backend, its entry script, its authoring guide |
| `catalogs` | bd_warehouse and step, and their model package (`hitlcad.builtin.catalogs`) |
| `exports` | bom, 3mf |
| `robot` | urdf, sdf |
| `solvers` | sqp: where a block settles, by CasADi's SQP method on the block's compiled graph (`Block.nlp`), with exact derivatives |
| `materials` | the built-in materials |
| `pictures` | the silhouette check (a model against a reference picture, cut out with OpenCV), and its skill reference |

### What stays core

- **Worker** (`worker/`): the plugin registry, diagnostics and their catalog.
- **The design's skeleton**: `DesignSpec`, `PartSpec`, components, configurations, parameters,
  requirements, fastener sets and `plugins`, with slots the installed members fill; the bases members
  derive from (`Connector`, `Mate`, `Relation`, `Check`, `Process`).
- **Execution**: the model runtime (`p`, `i`, what code read), the executor and the build workers,
  the pipeline, the part and result caches.
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
- **Construction contracts.** Prefer keyword-only arguments and explicit dimension relationships.
  Return the backend's normal `(shape, connectors)` value. Reject invalid inputs with the argument or
  section/hole index and a repair direction, before creating geometry. Include a complete recipe with
  requirements and configurations so an agent can change dimensions without inventing topology.
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

Each surface presents a diagnostic one way (`worker/errors.py`): `agent_text` for the MCP tools and
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

- An open part's `part.mesh_open` result includes source-mesh boundary/non-manifold edge counts,
  winding defects, duplicate/degenerate triangles where present, and up to three locations per kind
  in part-local millimetres. It inspects the source STL before measurement cleanup, welding STL's
  repeated vertex positions but preserving faces. These are diagnostic locations, not stable face
  addresses; this check does not detect geometric self-intersections. Healthy parts avoid this extra scan.

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

## Build workers

A design's code runs in build workers, as a notebook's cells run in its kernel. Starting a backend's
Python costs about 1.5 s before model code runs, nearly all of it `import build123d`; a worker pays it
once and then runs job after job (`runtime.serve`): the plan, then each part file as a job of its own,
in parallel over the backend's workers. Each job's folder is its working and temporary folder, and what
it prints goes to its own log.

A worker is one design's, from its first job to its end, so what it keeps is never another design's:
its libraries loaded, and what it exported, by the content of each part's shape (`Adapter.digest`, up
to `worker_cache_mb`, default 256), so a part that comes out of a part file as it did before is not
exported or tessellated again. A part file goes back to the worker that last ran it, where its exports
are kept. What is the design's own (its sources on `sys.path`, the modules its files ran as and
imported from `lib/`) is dropped after every job, so the next runs from the files as they are; what its
code changes in a library stays, as it would in a notebook. Part files the cache has are not run at all.

A backend keeps at most `workers` (`HITLCAD_WORKERS`, default 4), about 400 MB each for build123d. One
of them is a spare, a worker of no design yet with its libraries loaded: started with the server, and
again whenever a design takes it or starts a worker of its own, while a place is free, so a design opened
next builds without the wait. A job takes an idle worker of its design, else the spare, else starts one,
else waits for a spare on its way, else replaces the idle worker of another design used least recently,
else waits. Each job has the
deadline `build_timeout_s`; the memory limit `build_memory_mb` holds the worker and everything it
started. A worker that overruns either, ends, or ends a job holding more than half its memory limit is
replaced, and its successor starts empty: the part cache on disk brings back what it had built. Workers
end with the server (the CLI's with its build), and on their own if the server is killed.

`ProcessPoolExecutor` was not used: it cannot stop a hung task or hold a process tree's memory, Blender
is not a Python it can run, and its workers are no design's; a fork server exists on POSIX only.

### What a backend gives

A backend is a `CADBackend` subclass (`backends/base.py`) and its worker's entry script. hitlcad runs
the jobs, its workers and their limits, the part cache and what each file read; the backend says only
what is its own:

| Member | What it says |
| --- | --- |
| `entry`, `command()` | Its worker: the script, which loads its libraries and calls `runtime.serve(adapter)`, and the argv that starts it |
| `availability()`, `authoring_guide()`, `brep`, `summary` | Whether it runs here, and what the agent is told of it |
| `libraries()`, `code()` | What its parts are built with besides the design's code, for the part cache |
| `complete_parts(parts, work)` | What of its parts it writes in hitlcad's process: Blender's Python has no trimesh, so Blender's part views are tessellated there |
| `write_assembly(...)` | The assembly in its own format, in a job of its own (Blender's `model.blend`) |

The entry script's `runtime.Adapter` is the worker's side of it: `placement(value)` (a placement or
connector frame as a 4 × 4 matrix), `begin()` before each part file (Blender: an empty scene),
`unpack(what, value)` (a part's shape, connectors and catalog entry, or a `ModelError` saying what the
part file must return), `digest(shape)` (its content, or None: never reused), `export(shape, name, out,
tolerance)` (its files, as `BuiltPart` names them) and `modes`, jobs of its own that run no model code.
The model runtime and the worker are standard library only, so any Python runs them, Blender's included.
