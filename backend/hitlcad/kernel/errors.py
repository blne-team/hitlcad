"""Diagnostics: every error hitlcad raises, in one shape, with whose it is to fix and what usually fixes it.

An `ErrorKind` is one kind of mistake or failure: a stable code (`area.what`), its owner, a title and the usual
fix. Each is defined once, into one catalog (`define`); the skills' playbook is written from it. A `Diagnostic`
is one occurrence: a kind's code and owner, a message in the design's own terms, and where. `HitlcadError`
carries one. The owner decides where a diagnostic goes:

    design       the design's files: a failing `Result` of a revision; the agent changes the files
    request      the call (no such revision, a relative path): a tool error, HTTP 404 or 422
    environment  the machine (Blender missing, a folder that cannot be written): tell the human, HTTP 503
    plugin       an installed plugin, missing or broken: tell the human, HTTP 503
    hitlcad      a bug: the agent must not change the design to avoid it, HTTP 500

Any exception that is not a `HitlcadError` is a bug in hitlcad (`diagnose`).

A result's code (`Result.code`) comes from the same catalog: the kind of check, whose `hint` the agent reads
when it fails.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict

from hitlcad.kernel.model import Model

Owner = Literal["design", "request", "environment", "plugin", "hitlcad"]


class Location(Model):
    file: str | None = None
    line: int | None = None
    address: str | None = None

    def __str__(self) -> str:
        at = " line ".join(str(v) for v in (self.file, self.line) if v is not None)
        return ", ".join(v for v in (at, self.address or "") if v)


class Diagnostic(Model):
    code: str
    owner: Owner
    message: str
    where: Location | None = None


class ErrorKind(Model):
    model_config = ConfigDict(frozen=True)

    code: str
    owner: Owner
    title: str
    hint: str = ""
    skill: str = ""  # the skill and section that explain it: "hitlcad: Mates"

    def diagnostic(self, message: str, where: Location | None = None) -> Diagnostic:
        return Diagnostic(code=self.code, owner=self.owner, message=message, where=where)

    def __call__(self, message: str, where: Location | None = None) -> HitlcadError:
        """The error to raise: ``raise NOT_FOUND_PIN("pin p_1 not found")``."""
        return HitlcadError(self.diagnostic(message, where))


_CATALOG: dict[str, ErrorKind] = {}


def define(code: str, owner: Owner, title: str, hint: str = "", skill: str = "") -> ErrorKind:
    """A kind, entered in the catalog: each code once."""
    return register(ErrorKind(code=code, owner=owner, title=title, hint=hint, skill=skill))


def register(kind: ErrorKind) -> ErrorKind:
    """Enter a kind in the catalog: a plugin's own, for instance. A code is one kind: the same again is nothing new."""
    if _CATALOG.get(kind.code, kind) != kind:
        raise ValueError(f"error kind {kind.code} is defined twice, differently")
    _CATALOG[kind.code] = kind
    return kind


def catalog() -> dict[str, ErrorKind]:
    return dict(_CATALOG)


def of(code: str) -> ErrorKind:
    """The kind of a code that must have one: a member's result's."""
    if code not in _CATALOG:
        raise LookupError(f"no error kind {code}")
    return _CATALOG[code]


class HitlcadError(Exception):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic

    @property
    def code(self) -> str:
        return self.diagnostic.code

    @property
    def owner(self) -> Owner:
        return self.diagnostic.owner


def diagnose(e: BaseException) -> Diagnostic:
    """What an exception says: a HitlcadError its diagnostic, anything else a bug in hitlcad."""
    if isinstance(e, HitlcadError):
        return e.diagnostic
    return HITLCAD_BUG.diagnostic(f"{type(e).__name__}: {e}")


def reported(data: dict[str, Any]) -> Diagnostic:
    """A diagnostic a build process wrote, {"code", "message", "where"}, with the owner its kind gives it."""
    k = _CATALOG.get(data["code"])
    if k is None:
        return HITLCAD_BUG.diagnostic(f"a build reported an unknown error code {data['code']}: {data['message']}")
    return k.diagnostic(data["message"], Location.model_validate(data["where"]) if data.get("where") else None)


BUG_ADVICE = "This is a bug in hitlcad, not in the design: do not change the design to avoid it. Tell the human."


def agent_text(d: Diagnostic) -> str:
    """A diagnostic as the agent reads it: its code, message and place, then what to do."""
    where = f" ({d.where})" if d.where else ""
    lines = [f"[{d.code}] {d.message}{where}"]
    if d.owner == "hitlcad":
        lines.append(BUG_ADVICE)
    elif hint := advice(d.code):
        lines.append(f"→ {hint}")
    return "\n".join(lines)


def advice(code: str) -> str:
    """The usual fix for a code, with the skill section that explains it: what follows `→` in the agent's text."""
    k = _CATALOG.get(code)
    if k is None or not k.hint:
        return ""
    return k.hint + (f" (skill {k.skill})" if k.skill else "")


def http_status(d: Diagnostic) -> int:
    match d.owner:
        case "request":
            return 404 if d.code.startswith("not_found.") else 422
        case "design":
            return 422
        case "environment" | "plugin":
            return 503
        case _:
            return 500


# --------------------------------------------------------------------------- hitlcad's own kinds

HITLCAD_BUG = define(code="hitlcad.bug", owner="hitlcad", title="A bug in hitlcad")

# The design's files: design.json and the code that builds it. The build process names the `model.*` codes (and
# `build.part_file_missing`) as strings: `backends.runtime.CODES`.

DESIGN_INVALID = define(
    code="design.invalid", owner="design", title="design.json does not validate",
    hint="Fix each field where the message puts it (the path before the colon is where in design.json).",
    skill="hitlcad: design.json reference",
)  # fmt: skip
DESIGN_UNREADABLE = define(
    code="design.unreadable", owner="environment", title="The design's files could not be read",
    hint="Tell the human: a file of the design may have been removed while it was read, or is not readable.",
)  # fmt: skip
STRUCTURE_REFERENCE = define(
    code="structure.reference", owner="design", title="A name in design.json or assembly.py does not resolve",
    hint="Use a name the message lists, or declare it in design.json; build_design(path, scope=\"structure\") checks "
    "every reference in seconds.",
)  # fmt: skip

define(
    code="model.exception", owner="design", title="The design's code raised an exception",
    hint="Fix the line it names; the lines above it show how the code got there.", skill="hitlcad-<backend>",
)  # fmt: skip
define(
    code="model.no_build_function", owner="design", title="A design file defines no build(p, i)",
    hint="Define a top-level function build(p, i) in the file.",
)  # fmt: skip
define(
    code="model.return_type", owner="design", title="build(p, i) returns something else than its file must",
    hint="Return what the message asks: a part file {part: (shape, {connector: frame or [frames]})} or a catalog "
    "part, assembly.py {part or component: [placement, …]}.", skill="hitlcad-<backend>",
)  # fmt: skip
define(
    code="model.connectors_missing", owner="design",
    title="A part's code returns fewer connectors than design.json declares",
    hint="Return a frame (a list of frames for a pattern) for every connector design.json declares on the part, "
    "under the same name.", skill="hitlcad-<backend>",
)  # fmt: skip
define(
    code="model.parts_mismatch", owner="design", title="A part file returns other parts than design.json gives it",
    hint="Return one entry for each part whose `file` in design.json is this file, under the part's name.",
)  # fmt: skip
define(
    code="model.frame_type", owner="design", title="A placement or connector frame is not a frame",
    hint="Give the backend's own location or a 4 × 4 matrix (build123d: Location; Blender: mathutils.Matrix).",
    skill="hitlcad-<backend>",
)  # fmt: skip
define(
    code="model.shape", owner="design", title="A part is not a solid the backend can export",
    hint="Return a 3D solid (build123d: a Part, Solid or Compound of solids; Blender: mesh or curve objects).",
    skill="hitlcad-<backend>",
)  # fmt: skip
define(
    code="model.interface_cycle", owner="design", title="Interface values depend on each other in a cycle",
    hint="Compute one value of the cycle from parameters alone, and the others from it.",
)  # fmt: skip
define(
    code="model.unknown_interface", owner="design", title="Code reads an interface value interfaces.py lacks",
    hint="Define it as a function in interfaces.py, or read one of the values listed.",
)  # fmt: skip
define(
    code="model.unknown_parameter", owner="design", title="Code reads a parameter design.json does not declare",
    hint="Declare it in design.json `parameters`, or read one of those listed.",
)  # fmt: skip
define(
    code="model.interface_value", owner="design", title="An interface value is not plain data",
    hint="Return numbers, strings, booleans, or lists and dicts of them from interfaces.py; geometry belongs in "
    "part files.",
)  # fmt: skip

BUILD_PART_FILE = define(
    code="build.part_file_missing", owner="design", title="A part file design.json names does not exist",
    hint="Create the file under parts/, or correct the part's `file` in design.json.",
)  # fmt: skip
BUILD_PLACED = define(
    code="build.placed_unknown", owner="design", title="assembly.py places a name design.json does not declare",
    hint="Declare it in design.json `parts` or `components`, or place one of the names declared.",
)  # fmt: skip
BUILD_COMPONENT = define(
    code="build.component", owner="design", title="A component's own design does not build",
    hint="Build the component's design folder on its own and fix what it reports.",
)  # fmt: skip
BUILD_BINDING = define(
    code="build.binding", owner="design", title="A component's binding does not resolve",
    hint="Bind only parameters the component declares, each to a parameter or interfaces.<value> of this design.",
)  # fmt: skip
BUILD_TIME = define(
    code="build.time_limit", owner="design", title="A build process ran past its time limit",
    hint="Look for loops that never end and for geometry far finer than it needs to be (thousands of features, "
    "dense patterns, text); build one part with scope=\"<part>\" to find which.",
)  # fmt: skip
BUILD_MEMORY = define(
    code="build.memory_limit", owner="design", title="A build process used more memory than it may",
    hint="Simplify the heaviest geometry (fewer, larger booleans, coarser patterns); build one part with "
    "scope=\"<part>\" to find which.",
)  # fmt: skip
BUILD_CRASHED = define(
    code="build.crashed", owner="design", title="A build process ended without a result",
    hint="Usually the geometry kernel failing on degenerate geometry (zero-thickness walls, coincident faces in a "
    "boolean, self-intersecting sweeps): change the operation the log ends at.",
)  # fmt: skip

# What design.json declares, checked against what exists.

STRUCTURE_UNPLACED = define(
    code="structure.unplaced", owner="design", title="A part or component is declared but not placed",
    hint="Place it in assembly.py, or remove it from design.json.",
)  # fmt: skip
STRUCTURE_GROUND = define(
    code="structure.ground", owner="design", title="No ground, or a ground assembly.py does not place",
    hint="Set `ground` in design.json to the instance every mate chain leads back to, and place it.",
)  # fmt: skip
STRUCTURE_RELATION_KIND = define(
    code="structure.relation_kind", owner="design", title="A relation joins connectors of the wrong kinds",
    hint="Join the connector kinds the relation type names (a gear relation joins two gear connectors).",
    skill="hitlcad: Relations",
)  # fmt: skip
STRUCTURE_FASTENER = define(
    code="structure.fastener_made", owner="design", title="A fastener set's fastener is a made part",
    hint="Make the fastener a catalog part: search_catalog finds screws by size.",
)  # fmt: skip
STRUCTURE_MATERIAL = define(
    code="structure.material_unknown", owner="design", title="A part's material is not in the material registry",
    hint="Use a material list_backends lists, or add it to the project's materials.json.",
)  # fmt: skip
STRUCTURE_MATERIAL_PROCESS = define(
    code="structure.material_process", owner="design", title="A part's process cannot make its material",
    hint="Change the part's material or its process to one that suits the other.",
)  # fmt: skip
STRUCTURE_NO_MATERIAL = define(
    code="structure.material_missing", owner="design", title="A made part has no material",
    hint="Give the part a `material`, so its mass and its threads' engagement can be judged.",
)  # fmt: skip
STRUCTURE_OUTPUT = define(
    code="structure.output_unknown", owner="design", title="design.json `outputs` names an unknown output",
    hint="Name outputs list_backends lists.",
)  # fmt: skip
OUTPUT_UNAVAILABLE = define(
    code="output.unavailable", owner="design", title="design.json `outputs` names an output this design cannot have",
    hint="A STEP of the assembly needs every part as B-rep: build mesh parts with a B-rep backend, or ask for `stl`.",
)  # fmt: skip
STRUCTURE_BACKEND = define(
    code="structure.backend_unknown", owner="design", title="design.json names a backend that is not enabled",
    hint="Name a backend list_backends lists.",
)  # fmt: skip

# What each build measures.

MEASURE_UNMEASURABLE = define(
    code="measure.unmeasurable", owner="design", title="This build cannot measure it",
    hint="Usually a mesh that does not close a solid, or a B-rep feature asked of a mesh part: see the part's "
    "results.",
)  # fmt: skip
PART_EMPTY = define(
    code="part.empty", owner="design", title="A part is empty",
    hint="Return the part's solid; an empty part is usually a boolean that removed everything.",
)  # fmt: skip
PART_OPEN = define(
    code="part.mesh_open", owner="design", title="A part's mesh does not close a solid",
    hint="Repair the reported region in the part file: bridge boundary edges, remove duplicate/internal faces, "
    "or reverse incorrectly wound faces. Locations are in the part's own frame, in mm. "
    "Thicken surfaces meant to enclose material; then rebuild before trusting volume or wall checks.",
)  # fmt: skip
PART_BREP = define(
    code="part.brep_invalid", owner="design", title="A part fails the kernel's shape check",
    hint="Rebuild the operation that broke it: self-intersecting sweeps, booleans of coincident faces, fillets "
    "larger than their faces.",
)  # fmt: skip
CONNECTOR_COUNT = define(
    code="connector.count", owner="design", title="A connector has another number of frames than it declares",
    hint="Return as many frames as its `count` says, or change `count`.",
)  # fmt: skip
CONNECTOR_FEATURE = define(
    code="connector.feature", owner="design", title="A connector's feature is missing, or other than declared",
    hint="Put the frame on its feature with Z pointing out of the part, and model the feature at its declared "
    "size; the message says what was measured.", skill="hitlcad: design.json reference, Connectors",
)  # fmt: skip
MATE_FRAMES = define(
    code="mate.frames", owner="design", title="A mate's two frames do not agree",
    hint="Place the instance from the same interface values as its geometry (or with the SDK's mate_to) so both "
    "frames meet where the mate says; check `flip` and `offset`.", skill="hitlcad: design.json reference, Mates",
)  # fmt: skip
MATE_LIMITS = define(
    code="mate.limits", owner="design", title="A mate sits outside its limits",
    hint="Assemble the moving side within its limits, or widen the limits.",
)  # fmt: skip
MATE_FIT = define(
    code="mate.fit", owner="design", title="A mate's fit is not what it asks for",
    hint="Size the hole and the shaft from one interface value and the fit's clearance, after the process "
    "allowance; a fit is between a hole and a shaft connector.", skill="hitlcad: design.json reference, Mates",
)  # fmt: skip
RELATION_CHECK = define(
    code="relation.check", owner="design", title="A relation does not hold between its connectors",
    hint="Place the pair where the relation needs it (gears: centre distance = module × (z1 + z2) / 2), from "
    "interface values.", skill="hitlcad: design.json reference, Relations",
)  # fmt: skip
RELATION_UNMEASURED = define(
    code="relation.unmeasured", owner="design", title="A relation waits for its connectors to measure as declared",
    hint="Fix the connector results it names first.",
)  # fmt: skip
FASTENER_STACK = define(
    code="fastener.stack", owner="design",
    title="A fastener does not seat, clear its holes, take the thread or engage enough",
    hint="Size the clearance holes (ISO 273), the thread or insert and the fastener's length from one catalog size "
    "in interfaces.py, so it engages without bottoming out.", skill="hitlcad: design.json reference, Fastener sets",
)  # fmt: skip
FASTENER_NO_DATA = define(
    code="fastener.data", owner="design", title="A fastener's catalog provider gives no thread data",
    hint="Use a fastener from a provider that gives its thread (search_catalog).",
)  # fmt: skip
COVERAGE_FLOATING = define(
    code="coverage.floating", owner="design", title="An instance is connected to nothing",
    hint="Join it with a mate, relation or fastener set to something connected to ground.",
)  # fmt: skip
COVERAGE_GROUNDED = define(code="coverage.grounded", owner="design", title="Every instance connects to ground")
INTERFERENCE_OVERLAP = define(
    code="interference.overlap", owner="design", title="Two instances overlap",
    hint="Move or resize one of them; for parts meant to overlap (an insert set into a hole) declare it on their "
    "mate (`overlap: true`, or an interference fit).",
)  # fmt: skip
INTERFERENCE_EXPECTED = define(
    code="interference.expected", owner="design", title="An overlap a joint declares"
)  # fmt: skip
INTERFERENCE_CLEAR = define(code="interference.clear", owner="design", title="No unexpected overlaps")
MOTION_HELD = define(code="motion.held", owner="design", title="A moving mate held still by rigid ones")
MOTION_LOOP = define(
    code="motion.loop", owner="design", title="A motion its solver could not trace",
    hint="The message says why: no solver, one that cannot settle a block of it, or a motion that does not move from "
    "where it sits; nothing to fix unless the mechanism is a mistake.",
)  # fmt: skip
MOTION_LOCKED = define(
    code="motion.locked", owner="design", title="Motion that runs a mate past its limits, or that locks within them",
    hint="Change the limits to where the motion goes (the message gives them), or what drives it, so that they agree.",
)  # fmt: skip
MOTION_PARTED = define(
    code="motion.parted", owner="design", title="Parts a relation keeps in contact stand apart within the motion",
    hint="The profile and the relation's law disagree where it says: cut the profile from the same table the law "
    "reads, or correct the table.",
)  # fmt: skip
MOTION_DRIVEN = define(
    code="motion.driven", owner="design", title="A mate another motion drives, checked along that motion's sweep",
)  # fmt: skip
MOTION_SAMPLED = define(
    code="motion.sampled", owner="design", title="A traced motion, clear at each position solved but not between",
    hint="Ask the human to watch it move in the panel's motion control; watched, it passes.",
)  # fmt: skip
MOTION_COLLISION = define(
    code="motion.collision", owner="design", title="A moving part hits something within its range",
    hint="Change the limit to where it last moved clear (the message gives it), or give the parts room along the "
    "sweep.",
)  # fmt: skip
MOTION_PARTIAL = define(
    code="motion.partial", owner="design", title="A range of motion checked only in part",
    hint="Give the parts the clearance the message names, or fix what kept them from being measured.",
)  # fmt: skip
MOTION_WATCHED = define(
    code="motion.watched", owner="design", title="A traced motion, clear at each position solved and watched by the human",
)  # fmt: skip
MOTION_CLEAR = define(code="motion.clear", owner="design", title="Moves through its range without collision")
REQUIREMENT_UNCHECKED = define(
    code="requirement.unchecked", owner="design", title="A requirement without a check: the human judges it",
    hint="Add a check where one fits.", skill="hitlcad: design.json reference, Requirements",
)  # fmt: skip

# The call itself.

NOT_FOUND_DESIGN = define(
    code="not_found.design", owner="request", title="No such design",
    hint="Pass the absolute path of a folder holding design.json, or create one with create_design.",
)  # fmt: skip
NOT_FOUND_REVISION = define(
    code="not_found.revision", owner="request", title="No such revision, or it did not build",
    hint="Use a revision explore_design lists; build_design makes one.",
)  # fmt: skip
NOT_FOUND_PIN = define(
    code="not_found.pin", owner="request", title="No such pin", hint="Use the pin ids explore_design lists."
)  # fmt: skip
NOT_FOUND_FILE = define(code="not_found.file", owner="request", title="No such file")
NOT_FOUND_GEOMETRY = define(
    code="not_found.geometry", owner="request", title="No such part, instance, face, edge, vertex or frame",
    hint="Use the names explore_design and list_geometry give.",
)  # fmt: skip
REQUEST_INVALID = define(code="request.invalid", owner="request", title="The request cannot be done as asked")
PARAMETER_INVALID = define(
    code="parameter.invalid", owner="request", title="An unknown parameter, or a value outside its range",
    hint="Use parameters design.json declares, within their min and max.",
)  # fmt: skip
OUTPUT_UNKNOWN = define(
    code="output.unknown", owner="request", title="An output hitlcad does not have",
    hint="Name outputs list_backends lists.",
)  # fmt: skip
BASE_UNKNOWN = define(
    code="request.base_unknown", owner="request", title="An edit made on a version of the design it never had",
    hint="Make the edit on a version the design's state lists (its revisions' versions).",
)  # fmt: skip
NOT_UNDOABLE = define(
    code="request.not_undoable", owner="request", title="An Undo or Redo of a step that is not the latest",
    hint="Undo the latest panel edit the design's state lists (`undo`), or redo its latest undo (`redo`).",
)  # fmt: skip
FILES_UNMADE = define(
    code="build.files_unmade", owner="environment", title="An earlier revision's files could not be made again",
    hint="Build the design again: its latest build keeps its files.",
)  # fmt: skip
FILE_UNREADABLE = define(code="file.unreadable", owner="request", title="A model file that does not read")

# Installed plugins.

PLUGIN_MISSING = define(
    code="plugin.missing", owner="plugin", title="design.json needs a plugin that is not installed",
    hint="Tell the human which plugin to install (the message names it and its version).",
)  # fmt: skip
PLUGIN_LOAD_FAILED = define(
    code="plugin.load_failed", owner="plugin", title="An installed plugin did not load",
    hint="Tell the human: the plugin is broken or does not fit this version of hitlcad.",
)  # fmt: skip
PLUGIN_CONFLICT = define(
    code="plugin.conflict", owner="plugin", title="Two plugins give one name, so neither is used",
    hint="Tell the human which plugins clash: one of them has to go.",
)  # fmt: skip
PLUGIN_ERROR = define(
    code="plugin.error", owner="plugin", title="A plugin's code failed",
    hint="Tell the human: the plugin has a bug. Its other results, and the rest of the build, still stand.",
)  # fmt: skip

# The machine.

BACKEND_UNAVAILABLE = define(
    code="backend.unavailable", owner="environment", title="A CAD backend is not available on this machine",
    hint="Tell the human: `hitlcad doctor` says what is missing.",
)  # fmt: skip
BUILD_OUTPUTS = define(
    code="build.outputs", owner="environment", title="A build's files could not be written",
    hint="Tell the human: the disk may be full, or hitlcad's data folder not writable.",
)  # fmt: skip
EXPORT_FAILED = define(
    code="export.write_failed", owner="environment", title="A build could not be copied to the design's build/",
    hint="Tell the human: the folder's build/ is not writable.",
)  # fmt: skip
STORE_IN_USE = define(
    code="store.in_use", owner="environment", title="Another hitlcad server keeps this data folder",
    hint="Tell the human: stop the other server, or give this one a data folder of its own (HITLCAD_DATA_DIR).",
)  # fmt: skip
STORE_UNREADABLE = define(
    code="store.unreadable", owner="environment", title="A design's history was written by another version",
)  # fmt: skip
PROJECT_CONFIG = define(
    code="project.config", owner="request", title="A configuration file of the human's does not read",
)  # fmt: skip


class StructureError(HitlcadError):
    """What design.json or assembly.py declares does not fit together (a name that does not resolve, by default):
    what the structure analysis reports."""

    def __init__(self, message: str, where: Location | None = None, kind: ErrorKind = STRUCTURE_REFERENCE) -> None:
        super().__init__(kind.diagnostic(message, where))


class DesignInvalid(HitlcadError):
    """design.json does not validate: a diagnostic for each problem, where it is in the file."""

    def __init__(self, path: str, problems: list[Diagnostic]) -> None:
        lines = [(f"{p.where.address}: " if p.where and p.where.address else "") + p.message for p in problems]
        super().__init__(DESIGN_INVALID.diagnostic(f"{path} is not valid:\n" + "\n".join(lines)))
        self.problems = problems


class Unmeasurable(HitlcadError):
    """What a part's representation cannot answer; the message says why."""

    def __init__(self, message: str, where: Location | None = None) -> None:
        super().__init__(MEASURE_UNMEASURABLE.diagnostic(message, where))
