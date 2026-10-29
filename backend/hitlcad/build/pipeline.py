"""Backend-agnostic build pipeline.

    sources → plan (interface values, placements) → components (each a build of its own, cached by its
    spec, all at once) beside part files (in parallel worker processes, cached by what each read), both reading
    only the plan → the built design → its 3D view (the parts' views placed: seen from here on) → connectors
    measured → every analysis, each one's results said as it finishes → the outputs design.json names (`OUTPUTS`
    and the exporters) → report

A build's directory is self-contained: its sources, its parts in their own frames, its components'
build directories, the outputs it was asked for and its report. Loading it back (`load_built`) gives the model the
analyses saw, so components built earlier and revisions reopened later behave alike.

A build fails when what the design's files say cannot produce geometry; its results say why, as any build's do.
Anything else that stops it (the machine's, or a bug) is raised.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol, TypeVar

import numpy as np

from hitlcad import materials
from hitlcad.analysis import motion
from hitlcad.analysis.base import Analysis, Context, ResultCache, analysis_code, reported, result
from hitlcad.analysis.model import AnalysisSettings, BuildBodies, BuiltDesign, PartGeometry, Shapes
from hitlcad.backends.base import BuiltPart, FileOutcome, PlanOutcome
from hitlcad.backends.registry import BackendRegistry
from hitlcad.backends.workers import Superseded, Ticket
from hitlcad.build import assemble
from hitlcad.build.cache import Inputs, PartCache, link
from hitlcad.config import Settings
from hitlcad.core import frames as fr
from hitlcad.core.report import (
    Artifact,
    BomLine,
    BuildReport,
    BuildSpec,
    BuildStatus,
    ConnectorInfo,
    InstanceInfo,
    InterfaceValue,
    MassProperties,
    PartResult,
    Result,
)
from hitlcad.core.structure import ComponentSpec
from hitlcad.core.types import Value
from hitlcad.exporters import Exporter
from hitlcad.geometry import cadview
from hitlcad.kernel import plugins
from hitlcad.kernel.errors import (
    BACKEND_UNAVAILABLE,
    BUILD_BINDING,
    BUILD_COMPONENT,
    BUILD_OUTPUTS,
    BUILD_PART_FILE,
    BUILD_PLACED,
    FILES_UNMADE,
    HITLCAD_BUG,
    OUTPUT_UNAVAILABLE,
    STRUCTURE_BACKEND,
    STRUCTURE_OUTPUT,
    HitlcadError,
    Location,
    Unmeasurable,
    diagnose,
)
from hitlcad.render.base import NullRenderer, Renderer

MESH_FILE = "mesh.stl"
STEP_FILE = "model.step"
REPORT_FILE = "report.json"
SOURCES = "src"

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Progress(Protocol):
    """What a build says as it goes, from whichever thread it is in."""

    def shown(self, scene: str) -> None:
        """Its parts are placed in its scene (a path in its directory): they can be seen before anything is checked."""

    def doing(self, what: str) -> None:
        """What it does now: "checking motion (5 of 12)"."""

    def found(self, results: list[Result]) -> None:
        """An analysis finished: what it found, before the build ends."""


class _Quiet:
    def shown(self, scene: str) -> None:
        pass

    def doing(self, what: str) -> None:
        pass

    def found(self, results: list[Result]) -> None:
        pass


class _Clock:
    """How long each step of a build took, in the order they started (`BuildReport.durations`): steps that run at
    once (components and part files) each count their own time."""

    def __init__(self) -> None:
        self.durations: dict[str, float] = {}

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        self.durations[name] = 0.0
        try:
            yield
        finally:
            self.durations[name] = round(time.monotonic() - started, 3)

    async def timed(self, name: str, step: Awaitable[T]) -> T:
        with self.step(name):
            return await step


class _Failed(Exception):
    """The design's files cannot produce geometry: the results that say why."""

    def __init__(self, results: list[Result]) -> None:
        super().__init__(results[0].detail)
        self.results = results


#: The outputs a build writes itself, beside the exporters plugins give, by the name design.json `outputs` gives
#: them: a title (the panel's label for their files) and what they are for.
OUTPUTS = {
    "step": (
        "STEP of the assembly, for CAD tools",
        "model.step: the assembly as a STEP product structure, each part "
        "shared by its instances; every part must be B-rep",
    ),
    "stl": ("STL of the assembly", "mesh.stl: the placed assembly as one mesh"),
    "native": (
        "The assembly in its modelling tool's own file (a Blender scene)",
        "the assembly in the own file of each backend that writes one (Blender's model.blend)",
    ),
    "preview": (
        "",
        "preview.png: iso, front, right and top views on one sheet, where Blender is installed; the "
        "panel shows it with the results",
    ),
}

#: What the panel's file list says of the files every build writes; the sources and the 3D view it shows its own
#: way. An output's files say its title.
LABELS = {
    "part_exchange": "STEP of one part, in its own frame",
    "part_mesh": "STL of one part, in its own frame (as it is made)",
    "objects": "The part's named objects (glTF)",
    "log": "Build log",
}


def _artifact(out_dir: Path, rel: str, media_type: str, role: str, label: str = "") -> Artifact:
    data = (out_dir / rel).read_bytes()
    return Artifact(name=Path(rel).name, path=rel, media_type=media_type, role=role, label=label or LABELS.get(role, ""),
                    sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))  # fmt: skip


#: The content of a file kept by its sum (sha256): the store's, or a folder's as it was read.
Blobs = Callable[[str], bytes | None]


def write_sources(spec: BuildSpec, blobs: Blobs, target: Path) -> None:
    """The design's files as its spec has them, where its code runs: the code, and the vendor files by their sums."""
    shutil.rmtree(target, ignore_errors=True)
    for rel, text in spec.sources.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8", newline="")  # as the file has it, its line endings too
    for rel, digest in spec.assets.items():
        data = blobs(digest)
        assert data is not None, f"no content for {rel}"  # every file a document holds, its blob is kept
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_bytes(data)


class BuildPipeline:
    def __init__(self, registry: BackendRegistry, settings: Settings, analyses: Sequence[Analysis],
                 exporters: dict[str, Exporter], blobs: Blobs, renderer: Renderer | None = None) -> None:  # fmt: skip
        self.registry = registry
        self.blobs = blobs  # the vendor files' contents, by the sums a spec names them by
        self.settings = settings
        self.analyses = list(analyses)
        self.exporters = exporters
        #: Every output design.json may name, by name: its title (the panel's label for its files) and summary.
        self.outputs = {**{n: (e.title, e.summary) for n, e in exporters.items()}, **OUTPUTS}
        self.renderer: Renderer = renderer or NullRenderer()
        self.parts_cache = PartCache(settings.cache_dir / "parts")
        self.shapes = Shapes(settings.loaded_parts)
        self.results_cache = ResultCache(settings.cache_dir / "results")
        self.components_dir = settings.cache_dir / "components"
        self._component_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.analysis_settings = AnalysisSettings(overlap_mm3=settings.overlap_mm3,
                                                  mesh_tolerance_mm=settings.mesh_tolerance_mm,
                                                  wall_samples=settings.wall_samples, solver=settings.solver,
                                                  motion_solver=plugins.registry().solvers.get(settings.solver) if settings.solver else None)  # fmt: skip

    # ------------------------------------------------------------------ entry points

    async def run(self, spec: BuildSpec, folder: Path, out_dir: Path, watched: Mapping[str, int] | None = None,
                  ticket: Ticket | None = None, progress: Progress | None = None) -> BuildReport:  # fmt: skip
        """Build a design into `out_dir`, with the outputs design.json names; the report says how it went. `watched`: the loop
        sweeps the human has watched move (`Context.watched`). A build whose `ticket` a newer one supersedes stops
        between its stages (`Superseded`), once what it is running is done. `progress` hears its scene as soon as
        its parts are in it, and each stage after."""
        report, _ = await self._build(spec, folder, out_dir, ticket or Ticket(), progress or _Quiet(), outputs=True,
                                      watched=watched)  # fmt: skip
        return report

    async def _build(self, spec: BuildSpec, folder: Path, out_dir: Path, ticket: Ticket, progress: Progress, *,
                     outputs: bool, watched: Mapping[str, int] | None = None) -> tuple[BuildReport, BuiltDesign | None]:  # fmt: skip
        started = time.monotonic()
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        values = spec.values()
        log: list[str] = []
        clock = _Clock()

        def finish(status: BuildStatus, **fields: Any) -> BuildReport:
            (out_dir / "build.log").write_text("\n".join(log), encoding="utf-8")
            report = BuildReport(status=status, configuration=spec.configuration, values=values,
                                 duration_s=round(time.monotonic() - started, 3), durations=clock.durations, **fields)  # fmt: skip
            report.artifacts.append(_artifact(out_dir, "build.log", "text/plain", "log"))
            return report

        try:
            built, fields = await self._assemble(spec, folder, out_dir, ticket, values, log, clock)
            if outputs:
                with clock.step("scene"):
                    await asyncio.to_thread(assemble.write_scene, built, out_dir)
                progress.shown(cadview.SCENE_FILE)
            progress.doing("measuring connectors")
            with clock.step("measuring connectors"):
                await asyncio.to_thread(built.measure_connectors)
        except _Failed as e:
            return finish(BuildStatus.FAILED, results=e.results), None
        except HitlcadError as e:
            if e.owner != "design":
                raise
            return finish(BuildStatus.FAILED, results=[reported("build", e.diagnostic)]), None
        results = self._unregistered(spec, backends=False) + await asyncio.to_thread(
            self._analyse, built, watched or {}, ticket, progress, clock
        )
        with clock.step("report"):
            report_fields = {**fields, **await asyncio.to_thread(self._describe, built), "results": results}
        if outputs:
            ticket.check()
            progress.doing("writing outputs")
            with clock.step("outputs"):
                written, unavailable = await self.export(spec.design.outputs, built, out_dir, report_fields, ticket)
            report_fields["artifacts"] = self._files(built, out_dir) + written
            report_fields["results"] += unavailable
        return finish(BuildStatus.SUCCEEDED, **report_fields), built

    async def restore(self, spec: BuildSpec, folder: Path, out_dir: Path, report: BuildReport) -> None:
        """A revision's files made again into `out_dir`, for one that no longer keeps them: its parts (from the part
        cache, where it found them), its scene and its outputs. Its analyses are its report's, and are not run."""
        out_dir.mkdir(parents=True)
        log: list[str] = []
        try:
            built, _ = await self._assemble(spec, folder, out_dir, Ticket(), spec.values(), log, _Clock())
        except _Failed as e:
            lead = e.results[0]
            raise FILES_UNMADE(f"its build now fails: {lead.code} {lead.subject}: {lead.detail}") from None
        (out_dir / "build.log").write_text("\n".join(log), encoding="utf-8")
        await asyncio.to_thread(assemble.write_scene, built, out_dir)
        await self.export(spec.design.outputs, built, out_dir, dict(report), Ticket())

    async def check_structure(
        self, spec: BuildSpec, folder: Path, work: Path
    ) -> tuple[list[Result], PlanOutcome | None]:
        """The plan check: sources, the plan and every reference design.json makes, without building geometry."""
        from hitlcad.analysis.structure import structure_results

        shutil.rmtree(work, ignore_errors=True)
        try:
            write_sources(spec, self.blobs, work / SOURCES)
            plan = await self._plan(spec, folder, work / SOURCES, spec.values(), work / "plan", Ticket())
        except HitlcadError as e:
            if e.owner != "design":
                raise
            return [reported("structure", e.diagnostic)], None
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out = [*self._missing(spec, "structure"), *self._unregistered(spec, backends=True)]
        if plan.error:
            return [*out, reported("structure", plan.error)], plan
        out += _unknown(spec, plan, "structure")
        exports = {n: c.design.exports for n, c in spec.components.items()}
        out += structure_results(spec.design, plan.placements, exports, materials.for_design(folder))
        return out, plan

    # ------------------------------------------------------------------ building

    async def _assemble(self, spec: BuildSpec, folder: Path, out_dir: Path, ticket: Ticket, values: dict[str, Value],
                        log: list[str], clock: _Clock) -> tuple[BuiltDesign, dict[str, Any]]:  # fmt: skip
        """What its code builds: the plan, its components and its parts, run from a copy of its code in `work/`,
        which goes once they are built."""
        try:
            design = spec.design
            src = out_dir / "work" / SOURCES
            write_sources(spec, self.blobs, src)
            if missing := self._missing(spec, "build"):
                raise _Failed(missing)
            with clock.step("plan"):
                plan = await self._plan(spec, folder, src, values, out_dir / "work" / "plan", ticket)
            ticket.check()
            log.append(plan.log)
            if plan.error:
                raise _Failed([reported("build", plan.error)])
            interfaces = {k: v.value for k, v in plan.interfaces.items()}
            placements = {k: [fr.as_frame(m) for m in v] for k, v in plan.placements.items()}
            if unknown := _unknown(spec, plan, "build"):
                raise _Failed(unknown)
            # Components and part files read only the plan, not each other: they build at once.
            components, outcomes = await _settled(
                clock.timed("components",
                            self._components(spec, folder, out_dir, ticket, values, interfaces, placements)),
                clock.timed("part files", self._parts(spec, folder, src, ticket, values, interfaces, out_dir, log)),
            )  # fmt: skip
            failed: dict[str, list[FileOutcome]] = {}  # files that failed alike (a worker that would not start)
            for o in outcomes:
                if o.error:
                    failed.setdefault(o.error.model_dump_json(), []).append(o)
            if failed:
                files = design.files()
                raise _Failed([reported("build", same[0].error, ", ".join(o.file for o in same),  # type: ignore[arg-type]
                                        [n for o in same for n in files[o.file]]) for same in failed.values()])  # fmt: skip
            parts = {}
            for outcome in outcomes:
                for bp in outcome.parts:
                    parts[bp.name] = self._geometry(spec, out_dir, outcome, bp)
            built = BuiltDesign(
                spec=design, folder=folder, values=values, interfaces=interfaces,
                interface_reads={k: v.reads for k, v in plan.interfaces.items()}, assembly_reads=plan.assembly_reads,
                parts=parts, placements=placements, components={n: c[1] for n, c in components.items()},
                materials=materials.for_design(folder), tolerances=spec.tolerances,
            )  # fmt: skip
            fields = {
                "interfaces": [
                    InterfaceValue(name=k, value=v.value, reads=v.reads) for k, v in plan.interfaces.items()
                ],
                "assembly_reads": plan.assembly_reads,
                "components": {n: c[0] for n, c in components.items()},
            }
            return built, fields
        finally:
            shutil.rmtree(out_dir / "work", ignore_errors=True)

    async def _plan(
        self, spec: BuildSpec, folder: Path, src: Path, values: dict[str, Value], work: Path, ticket: Ticket
    ) -> PlanOutcome:
        backend = self._backend(spec.design.backend)
        return await backend.plan(ticket, folder, src, values, work)

    def _backend(self, name: str) -> Any:
        if name not in self.registry.names():
            raise STRUCTURE_BACKEND(f"backend {name!r} is not enabled; enabled: {', '.join(self.registry.names())}")
        backend = self.registry.get(name)
        available, detail = backend.availability()
        if not available:
            raise BACKEND_UNAVAILABLE(f"backend {name} is not available: {detail}")
        return backend

    def _missing(self, spec: BuildSpec, analysis: str) -> list[Result]:
        """The part files design.json names that do not exist."""
        files = spec.design.files()
        return [result(analysis, BUILD_PART_FILE, f, False, f"{f} does not exist", files[f])
                for f in sorted(set(files) - set(spec.sources))]  # fmt: skip

    def _unregistered(self, spec: BuildSpec, *, backends: bool) -> list[Result]:
        """What design.json names that this hitlcad does not provide: exporters, and (before a build) backends."""
        out = [result("structure", STRUCTURE_OUTPUT, f"outputs: {name}", False, f"no output {name!r}; outputs: "
                      f"{', '.join(self.outputs)}") for name in spec.design.outputs if name not in self.outputs]  # fmt: skip
        if backends:
            out += [result("structure", STRUCTURE_BACKEND, f"backend: {name}", False, f"backend {name!r} is not "
                           f"enabled; enabled: {', '.join(self.registry.names())}")
                    for name in sorted({spec.design.part_backend(p) for p in spec.design.parts} | {spec.design.backend})
                    if name not in self.registry.names()]  # fmt: skip
        return out

    async def _components(self, spec: BuildSpec, folder: Path, out_dir: Path, ticket: Ticket, values: dict[str, Value],
                          interfaces: dict[str, Any], placements: dict[str, list[fr.Frame]]
                          ) -> dict[str, tuple[BuildReport, BuiltDesign]]:  # fmt: skip
        """Each component placed, all at once: their bindings read only this design's plan, never another component."""
        placed = [(name, use) for name, use in spec.design.components.items() if name in placements]
        built = await _settled(*(self._component(name, use, spec, folder, out_dir, ticket, values, interfaces)
                                 for name, use in placed))  # fmt: skip
        return {name: b for (name, _), b in zip(placed, built, strict=True)}

    async def _component(self, name: str, use: ComponentSpec, spec: BuildSpec, folder: Path, out_dir: Path,
                         ticket: Ticket, values: dict[str, Value], interfaces: dict[str, Any]
                         ) -> tuple[BuildReport, BuiltDesign]:  # fmt: skip
        """One component, built as its own design with the values this one binds, cached by its bound spec."""
        child = spec.components[name]
        child_folder = (folder / use.design).resolve()
        by_name = {p.name: p for p in child.design.parameters}
        overrides = {}
        for param, ref in use.bind.items():
            if param not in by_name:
                raise BUILD_BINDING(f"component {name}: {use.design} has no parameter {param!r} to bind",
                                    Location(address=name))  # fmt: skip
            try:
                overrides[param] = by_name[param].coerce(_ref_value(ref, values, interfaces))
            except (KeyError, ValueError) as e:
                raise BUILD_BINDING(f"component {name}: binding {param} = {ref}: {e}", Location(address=name)) from None
        bound = child.model_copy(update={"configuration": use.configuration, "overrides": overrides})
        # A cached component is reused while everything its report came from is as it was: its spec, its
        # backends, the materials it sees and the analyses that judged it.
        backends = sorted({child.design.part_backend(p) for p in child.design.parts} | {child.design.backend})
        registry = {n: m.model_dump() for n, m in materials.for_design(child_folder).items()}
        key = json.dumps([bound.key, [self._backend(b).fingerprint() for b in backends], registry, analysis_code()],
                         sort_keys=True)  # fmt: skip
        target = self.components_dir / hashlib.sha256(key.encode()).hexdigest()
        report_file = target / REPORT_FILE
        async with self._component_locks[target.name]:  # two components may be the same design bound alike
            if (
                report_file.is_file()
                and (cached := BuildReport.model_validate_json(report_file.read_text(encoding="utf-8"))).status
                == "succeeded"
            ):
                report = cached
            else:
                report, _ = await self._build(bound, child_folder, target, ticket, _Quiet(), outputs=False)
                report_file.write_text(report.model_dump_json(), encoding="utf-8")
        if report.status != "succeeded":
            lead = report.lead()
            why = f": {lead.code} {lead.subject}: {lead.detail}" if lead else ""
            raise BUILD_COMPONENT(f"component {name} ({use.design}) did not build{why}", Location(address=name))
        shutil.copytree(target, out_dir / "components" / name, copy_function=link)
        return report, load_built(report, bound, child_folder, out_dir / "components" / name, self.shapes)

    async def _parts(self, spec: BuildSpec, folder: Path, src: Path, ticket: Ticket, values: dict[str, Value],
                     interfaces: dict[str, Any], out_dir: Path, log: list[str]) -> list[FileOutcome]:  # fmt: skip
        """Each part file's parts: from the part cache, else built in one of its backend's workers, all at once."""
        design = spec.design
        files = design.files()
        declared = {file: {n: list(design.parts[n].connectors) for n in names} for file, names in files.items()}
        inputs: dict[str, Inputs] = {}  # by backend

        async def one(n: int, file: str) -> tuple[FileOutcome, str]:
            backend = self._backend(design.part_backend(files[file][0]))
            inputs.setdefault(backend.name, Inputs(fingerprint=backend.fingerprint(), sources=spec.sources,
                                                   assets=spec.assets, values=values, interfaces=interfaces))  # fmt: skip
            hit = self.parts_cache.lookup(file, declared[file], inputs[backend.name])
            if hit:
                key, cached = hit
                self.parts_cache.restore(key, cached, out_dir)
                return cached.model_copy(update={"cached": True}), ""
            work = out_dir / "work" / f"part-{n}"
            outcome, printed = await backend.build_file(ticket, folder, src, values, file, declared[file], work)
            if not outcome.error:
                for part in outcome.parts:
                    for rel in part.files():
                        link(work / rel, out_dir / rel)
                self.parts_cache.store(outcome, declared[file], work, inputs[backend.name])
            return outcome, printed

        built = await _settled(*(one(n, file) for n, file in enumerate(files)))
        log.extend(printed for _, printed in built if printed)
        return [outcome for outcome, _ in built]

    def _geometry(self, spec: BuildSpec, out_dir: Path, outcome: FileOutcome, bp: BuiltPart) -> PartGeometry:
        mesh, step, objects = out_dir / bp.mesh, out_dir / bp.step if bp.step else None, out_dir / bp.objects
        return PartGeometry(
            name=bp.name, spec=spec.design.parts[bp.name], file=outcome.file, backend=spec.design.part_backend(bp.name),
            mesh_file=mesh, step_file=step, objects_file=objects, view_file=out_dir / bp.view,
            shape=self.shapes.of(bp.name, mesh, step, objects),
            frames={k: [fr.as_frame(m) for m in v] for k, v in bp.connectors.items()}, catalog=bp.catalog,
            reads=outcome.reads, cached=outcome.cached, valid=bp.valid,
        )  # fmt: skip

    # ------------------------------------------------------------------ analyses and outputs

    def _analyse(self, built: BuiltDesign, watched: Mapping[str, int], ticket: Ticket,
                 progress: Progress, clock: _Clock) -> list[Result]:  # fmt: skip
        ctx = Context(built=built, settings=self.analysis_settings, cache=self.results_cache, watched=watched,
                      bodies=BuildBodies(built, self.analysis_settings, self.results_cache))  # fmt: skip
        out: list[Result] = []
        for n, analysis in enumerate(self.analyses, 1):
            ticket.check()
            progress.doing(f"checking {analysis.name} ({n} of {len(self.analyses)})")
            with clock.step(analysis.name):
                found = _run(analysis, ctx)
            out += found
            progress.found(found)
        return out

    def _describe(self, built: BuiltDesign) -> dict[str, Any]:
        """The parts, instances, mass and bill of materials of a built design, for its report."""
        parts = [_part_result(built, p) for p in built.parts.values()]
        instances = [InstanceInfo(path=a, part=n if n in built.parts else None, component=n if n in built.components else None,
                                  transform=fr.as_matrix(t)) for a, n, t in built.instances()]  # fmt: skip
        whole = BuildBodies(built, self.analysis_settings, self.results_cache).whole()
        return {"motions": motion.reported(built, self.analysis_settings, self.results_cache), "parts": parts,
                "instances": instances,
                "metrics": whole.metrics if whole.leaves else None, "mass": _mass(built), "bom": bom(built)}  # fmt: skip

    async def export(self, names: Sequence[str], built: BuiltDesign, out_dir: Path, report: dict[str, Any],
                     ticket: Ticket) -> tuple[list[Artifact], list[Result]]:  # fmt: skip
        """Write the outputs design.json names into a build's directory, all at once, each backend's own file in its
        own process: their files, and a failing result for each this design cannot have."""
        names = [n for n in dict.fromkeys(names) if n in self.outputs]  # an unknown one: the structure check says so
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(self._output(name, built, out_dir, report, ticket)) for name in names]
        except ExceptionGroup as group:
            if said := next((e for e in group.exceptions if isinstance(e, HitlcadError)), None):
                raise said from group  # a backend's own file: its build process said why
            unwritten, others = group.split(OSError)
            if others is not None:  # a bug: as itself, not as the group it surfaced in
                raise others.exceptions[0] from None
            why = "; ".join(str(e) for e in unwritten.exceptions)
            raise BUILD_OUTPUTS(f"could not write the build's outputs: {why}") from group
        finally:
            shutil.rmtree(out_dir / "work", ignore_errors=True)
        artifacts: list[Artifact] = []
        results: list[Result] = []
        for name, task in zip(names, tasks, strict=True):
            written, why = task.result()
            label = self.outputs[name][0]
            artifacts += [_artifact(out_dir, rel, media_type, name, label) for rel, media_type in written]
            if why:
                results.append(result("structure", OUTPUT_UNAVAILABLE, f"outputs: {name}", False, why))
        return artifacts, results

    async def _output(self, name: str, built: BuiltDesign, out_dir: Path, report: dict[str, Any],
                      ticket: Ticket) -> tuple[list[tuple[str, str]], str]:  # fmt: skip
        """One output's files (path, media type), or why this design cannot have it."""
        if name == "step":
            if not await asyncio.to_thread(assemble.step_assembly, built, out_dir / STEP_FILE):
                return [], "a part is a mesh, not B-rep: the assembly has no exact shape to write as STEP"
            return [(STEP_FILE, "model/step")], ""
        if name == "stl":
            await asyncio.to_thread(built.mesh.export, out_dir / MESH_FILE)
            return [(MESH_FILE, "model/stl")], ""
        if name == "native":
            tree = assemble.tree(built)
            backends = sorted({part.backend for part in assemble.all_parts(built)})
            written = await asyncio.gather(*(self._backend(b).write_assembly(ticket, built.folder, tree, out_dir,
                                                                             out_dir / "work" / b) for b in backends))  # fmt: skip
            return [w for w in written if w], ""
        if name == "preview":
            preview = await self.preview(built, out_dir, report.get("metrics"))
            return [(preview.name, "image/png")] if preview else [], ""
        return await asyncio.to_thread(self.exporters[name].export, built, report, out_dir), ""

    async def preview(self, built: BuiltDesign, out_dir: Path, metrics: Any) -> Path | None:
        """The assembly's rendered views in a build's directory (the agent's images ask for them too), or None
        where no renderer is installed."""
        work = out_dir / "work" / "preview"
        work.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(built.mesh.export, work / MESH_FILE)
            return await self.renderer.render(work / MESH_FILE, out_dir, metrics)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _files(self, built: BuiltDesign, out_dir: Path) -> list[Artifact]:
        """What every build writes, for review: each part's own files and the 3D view."""
        artifacts = []
        for part in built.parts.values():
            artifacts.append(
                _artifact(out_dir, part.mesh_file.relative_to(out_dir).as_posix(), "model/stl", "part_mesh")
            )
            if part.step_file:
                artifacts.append(
                    _artifact(out_dir, part.step_file.relative_to(out_dir).as_posix(), "model/step", "part_exchange")
                )
            artifacts.append(
                _artifact(out_dir, part.objects_file.relative_to(out_dir).as_posix(), "model/gltf-binary", "objects")
            )
        return [*artifacts, _artifact(out_dir, cadview.SCENE_FILE, "application/json", "view")]


async def _settled(*steps: Awaitable[Any]) -> list[Any]:
    """What each step returned, every one run to its end, the ones already running included, before saying a newer
    build superseded this one: what they built reaches the caches for it, and nothing writes here once this build
    stops. Else what the first of them raised, a newer build only when nothing else stopped this one."""
    done = await asyncio.gather(*steps, return_exceptions=True)
    for raised in sorted((d for d in done if isinstance(d, BaseException)), key=lambda e: isinstance(e, Superseded)):
        raise raised
    return done


def _run(analysis: Analysis, ctx: Context) -> list[Result]:
    """An analysis's results; what it raises is the design's problem, or a bug reported unverified, so the others
    still run."""
    try:
        return analysis.run(ctx)
    except Exception as e:
        d = diagnose(e)
        if d.owner == "design":  # something the design declares does not fit together
            status = None if isinstance(e, Unmeasurable) else False
            return [reported("structure", d, analysis.name, status=status)]
        logger.exception("analysis %s failed", analysis.name)
        bug = HITLCAD_BUG.diagnostic(f"the {analysis.name} analysis failed: {d.message}")
        return [reported(analysis.name, bug, analysis.name, status=None)]


def _unknown(spec: BuildSpec, plan: PlanOutcome, analysis: str) -> list[Result]:
    """What assembly.py places that design.json does not declare."""
    declared = {*spec.design.parts, *spec.design.components}
    return [result(analysis, BUILD_PLACED, n, False, f"assembly.py places {n}, which design.json does not declare as a "
                   "part or component", [n]) for n in plan.placements if n not in declared]  # fmt: skip


def _ref_value(ref: str, values: dict[str, Value], interfaces: dict[str, Any]) -> Any:
    if ref.startswith("interfaces."):
        return interfaces[ref.removeprefix("interfaces.")]
    return values[ref]


def _part_result(built: BuiltDesign, part: PartGeometry) -> PartResult:
    process = built.spec.process(part.name)
    connectors = {name: ConnectorInfo(kind=spec.kind, frames=[fr.as_matrix(f) for f in part.frames.get(name, [])],
                                      measured=built.measured.get((part.name, name), []))
                  for name, spec in part.spec.connectors.items()}  # fmt: skip
    return PartResult(name=part.name, file=part.file, backend=part.backend, made=part.spec.catalog is None,
                      material=part.material, process=getattr(process, "process", None), catalog=part.catalog,
                      cached=part.cached, reads=part.reads, metrics=part.metrics,
                      mass=built.mass(part), connectors=connectors)  # fmt: skip


def _mass(built: BuiltDesign) -> MassProperties | None:
    """The assembly's mass properties, when every part's are known."""
    each = [leaf.owner.mass(leaf.part, leaf.transform) for leaf in built.leaves()]
    if not each or any(m is None for m in each):
        return None
    total = sum(m.mass_g for m in each)
    centre = sum(np.array(m.centre_mm) * m.mass_g for m in each) / total
    inertia = np.zeros((3, 3))
    for m in each:
        d = np.array(m.centre_mm) - centre
        inertia += np.array(m.inertia_g_mm2) + m.mass_g * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
    return MassProperties(
        mass_g=float(total),
        centre_mm=tuple(float(v) for v in centre),
        inertia_g_mm2=tuple(tuple(float(v) for v in row) for row in inertia),
    )  # type: ignore[arg-type]


def bom(built: BuiltDesign, level: int = 0, prefix: str = "", times: int = 1) -> list[BomLine]:
    """Indented bill of materials: this design's parts, then each component's, quantities multiplied."""
    out = []
    for name, placed in built.placements.items():
        count = len(placed) * times
        if name in built.components:
            child = built.components[name]
            out.append(BomLine(level=level, path=f"{prefix}{name}", number=child.spec.number, name=child.spec.name,
                               quantity=count, kind="component"))  # fmt: skip
            out += bom(child, level + 1, f"{prefix}{name}/", count)
            continue
        part = built.parts[name]
        spec = part.spec
        process = built.spec.process(name)
        out.append(BomLine(
            level=level, path=f"{prefix}{name}", number=spec.number, name=name, quantity=count,
            kind="catalog" if spec.catalog else "made", material=part.material, process=getattr(process, "process", None),
            designation=spec.catalog.designation if spec.catalog else "", supplier=spec.catalog.supplier if spec.catalog else "",
            mpn=spec.catalog.mpn if spec.catalog else "",
        ))  # fmt: skip
    return out


def load_built(report: BuildReport, spec: BuildSpec, folder: Path, out_dir: Path, shapes: Shapes) -> BuiltDesign:
    """A built design back from its build directory and report: as the analyses saw it, but for each part's B-rep
    check (`PartGeometry.valid`), which the report keeps only as the part analysis's result. Its parts' shapes come
    from `shapes` where a build loaded the same files."""
    design = spec.design
    parts = {}
    for pr in report.parts:
        stl = out_dir / "parts" / f"{pr.name}.stl"
        step = out_dir / "parts" / f"{pr.name}.step"
        brep, objects = step if step.is_file() else None, out_dir / "parts" / f"{pr.name}.glb"
        parts[pr.name] = PartGeometry(
            name=pr.name, spec=design.parts[pr.name], file=pr.file, backend=pr.backend, mesh_file=stl,
            step_file=brep, objects_file=objects, view_file=out_dir / "parts" / f"{pr.name}{cadview.VIEW_SUFFIX}",
            shape=shapes.of(pr.name, stl, brep, objects),
            frames={k: [fr.as_frame(m) for m in c.frames] for k, c in pr.connectors.items()},
            catalog=pr.catalog, reads=pr.reads, cached=pr.cached,
        )  # fmt: skip
    placements: dict[str, list[fr.Frame]] = {}
    for inst in report.instances:
        placements.setdefault(inst.part or inst.component, []).append(fr.as_frame(inst.transform))  # type: ignore[arg-type]
    components = {name: load_built(child, spec.components[name], (folder / design.components[name].design).resolve(),
                                   out_dir / "components" / name, shapes)
                  for name, child in report.components.items()}  # fmt: skip
    built = BuiltDesign(
        spec=design, folder=folder, values=report.values, interfaces={i.name: i.value for i in report.interfaces},
        interface_reads={i.name: i.reads for i in report.interfaces}, assembly_reads=report.assembly_reads,
        parts=parts, placements=placements, components=components, materials=materials.for_design(folder),
        tolerances=spec.tolerances,
    )  # fmt: skip
    for pr in report.parts:
        for name, info in pr.connectors.items():
            built.measured[(pr.name, name)] = info.measured
    return built
