"""Backend-agnostic build pipeline.

    sources → plan (interface values, placements) → components (each a build of its own, cached by its
    spec) → part files (in parallel worker processes, cached by what each read) → the built design → its
    3D view (the parts' views placed: seen from here on) → connectors measured → every analysis → outputs
    (assembly STEP, mesh, preview, exporters, and the assembly in each backend's own format) → report

A build's directory is self-contained: its sources, its parts in their own frames, its components'
build directories, its outputs and its report. Loading it back (`load_built`) gives the model the
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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from hitlcad import materials
from hitlcad.analysis import motion
from hitlcad.analysis.base import Analysis, Context, ResultCache, analysis_code, reported, result
from hitlcad.analysis.model import AnalysisSettings, BuildBodies, BuiltDesign, PartGeometry
from hitlcad.backends.base import BuiltPart, FileOutcome, PlanOutcome
from hitlcad.backends.registry import BackendRegistry
from hitlcad.backends.workers import Superseded, Ticket
from hitlcad.build import assemble
from hitlcad.build.cache import Inputs, PartCache
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
    HITLCAD_BUG,
    SOURCES_CHANGED,
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


class Progress(Protocol):
    """What a build says as it goes, from whichever thread it is in."""

    def shown(self, scene: str) -> None:
        """Its parts are placed in its scene (a path in its directory): they can be seen before anything is checked."""

    def doing(self, what: str) -> None:
        """What it does now: "checking motion (5 of 12)"."""


class _Quiet:
    def shown(self, scene: str) -> None:
        pass

    def doing(self, what: str) -> None:
        pass


class _Failed(Exception):
    """The design's files cannot produce geometry: the results that say why."""

    def __init__(self, results: list[Result]) -> None:
        super().__init__(results[0].detail)
        self.results = results


#: What the panel's file list says of the files every build writes; the sources, the 3D view and the preview it
#: shows its own way. An exporter's files say its title.
LABELS = {
    "exchange": "STEP of the assembly, for CAD tools",
    "native": "The assembly in its modelling tool's own file (a Blender scene)",
    "mesh": "STL of the assembly",
    "part_exchange": "STEP of one part, in its own frame",
    "part_mesh": "STL of one part, in its own frame (as it is made)",
    "objects": "The part's named objects (glTF)",
    "log": "Build log",
}


def _artifact(out_dir: Path, rel: str, media_type: str, role: str, label: str = "") -> Artifact:
    data = (out_dir / rel).read_bytes()
    return Artifact(name=Path(rel).name, path=rel, media_type=media_type, role=role, label=label or LABELS.get(role, ""),
                    sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))  # fmt: skip


def write_sources(spec: BuildSpec, folder: Path, target: Path) -> None:
    """The design's code from the spec, and its vendor files from the folder (checked against the spec)."""
    shutil.rmtree(target, ignore_errors=True)
    for rel, text in spec.sources.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    for rel, digest in spec.assets.items():
        data = (folder / rel).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SOURCES_CHANGED(f"{rel} changed since the build started")
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_bytes(data)


class BuildPipeline:
    def __init__(self, registry: BackendRegistry, settings: Settings, analyses: Sequence[Analysis],
                 exporters: dict[str, Exporter], renderer: Renderer | None = None) -> None:  # fmt: skip
        self.registry = registry
        self.settings = settings
        self.analyses = list(analyses)
        self.exporters = exporters
        self.renderer: Renderer = renderer or NullRenderer()
        self.parts_cache = PartCache(settings.cache_dir / "parts")
        self.results_cache = ResultCache(settings.cache_dir / "results")
        self.components_dir = settings.cache_dir / "components"
        self._component_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.analysis_settings = AnalysisSettings(overlap_mm3=settings.overlap_mm3,
                                                  mesh_tolerance_mm=settings.mesh_tolerance_mm,
                                                  wall_samples=settings.wall_samples, solver=settings.solver,
                                                  loop_solver=plugins.registry().solvers.get(settings.solver) if settings.solver else None)  # fmt: skip

    # ------------------------------------------------------------------ entry points

    async def run(self, spec: BuildSpec, folder: Path, out_dir: Path, watched: Mapping[str, int] | None = None,
                  ticket: Ticket | None = None, progress: Progress | None = None) -> BuildReport:  # fmt: skip
        """Build a design into `out_dir`, with every output; the report says how it went. `watched`: the loop
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

        def finish(status: BuildStatus, **fields: Any) -> BuildReport:
            (out_dir / "build.log").write_text("\n".join(log), encoding="utf-8")
            report = BuildReport(status=status, configuration=spec.configuration, values=values,
                                 duration_s=round(time.monotonic() - started, 3), **fields)  # fmt: skip
            report.artifacts.append(_artifact(out_dir, "build.log", "text/plain", "log"))
            (out_dir / REPORT_FILE).write_text(report.model_dump_json(indent=2), encoding="utf-8")
            return report

        try:
            built, fields = await self._assemble(spec, folder, out_dir, ticket, values, log)
            if outputs:
                await asyncio.to_thread(assemble.write_scene, built, out_dir)
                progress.shown(cadview.SCENE_FILE)
            progress.doing("measuring connectors")
            await asyncio.to_thread(built.measure_connectors)
        except _Failed as e:
            return finish(BuildStatus.FAILED, results=e.results), None
        except HitlcadError as e:
            if e.owner != "design":
                raise
            return finish(BuildStatus.FAILED, results=[reported("build", e.diagnostic)]), None
        results = self._unregistered(spec, backends=False) + await asyncio.to_thread(
            self._analyse, built, watched or {}, ticket, progress
        )
        report_fields = {**fields, **await asyncio.to_thread(self._describe, built), "results": results}
        if outputs:
            ticket.check()
            progress.doing("writing outputs")
            try:
                report_fields["artifacts"] = await self._outputs(built, out_dir, report_fields, ticket)
            except ExceptionGroup as group:
                if said := next((e for e in group.exceptions if isinstance(e, HitlcadError)), None):
                    raise said from group  # a backend's own file, its build process said why
                unwritten, others = group.split(OSError)
                if others is not None:  # a bug: as itself, not as the group it surfaced in
                    raise others.exceptions[0] from None
                why = "; ".join(str(e) for e in unwritten.exceptions)
                raise BUILD_OUTPUTS(f"could not write the build's outputs: {why}") from group
        return finish(BuildStatus.SUCCEEDED, **report_fields), built

    async def check_structure(
        self, spec: BuildSpec, folder: Path, work: Path
    ) -> tuple[list[Result], PlanOutcome | None]:
        """The plan check: sources, the plan and every reference design.json makes, without building geometry."""
        from hitlcad.analysis.structure import structure_results

        shutil.rmtree(work, ignore_errors=True)
        try:
            write_sources(spec, folder, work / SOURCES)
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
                        log: list[str]) -> tuple[BuiltDesign, dict[str, Any]]:  # fmt: skip
        design = spec.design
        src = out_dir / SOURCES
        write_sources(spec, folder, src)
        if missing := self._missing(spec, "build"):
            raise _Failed(missing)
        plan = await self._plan(spec, folder, src, values, out_dir / "work" / "plan", ticket)
        ticket.check()
        log.append(plan.log)
        if plan.error:
            raise _Failed([reported("build", plan.error)])
        interfaces = {k: v.value for k, v in plan.interfaces.items()}
        placements = {k: [fr.as_frame(m) for m in v] for k, v in plan.placements.items()}
        if unknown := _unknown(spec, plan, "build"):
            raise _Failed(unknown)
        components = await self._components(spec, folder, out_dir, ticket, values, interfaces, placements)
        ticket.check()
        outcomes = await self._parts(spec, folder, src, ticket, values, interfaces, out_dir, log)
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
            "interfaces": [InterfaceValue(name=k, value=v.value, reads=v.reads) for k, v in plan.interfaces.items()],
            "assembly_reads": plan.assembly_reads,
            "components": {n: c[0] for n, c in components.items()},
        }
        shutil.rmtree(out_dir / "work", ignore_errors=True)
        return built, fields

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
        out = [result("structure", STRUCTURE_OUTPUT, f"outputs: {name}", False, f"no exporter {name!r}; exporters: "
                      f"{', '.join(self.exporters)}") for name in spec.design.outputs if name not in self.exporters]  # fmt: skip
        if backends:
            out += [result("structure", STRUCTURE_BACKEND, f"backend: {name}", False, f"backend {name!r} is not "
                           f"enabled; enabled: {', '.join(self.registry.names())}")
                    for name in sorted({spec.design.part_backend(p) for p in spec.design.parts} | {spec.design.backend})
                    if name not in self.registry.names()]  # fmt: skip
        return out

    async def _components(self, spec: BuildSpec, folder: Path, out_dir: Path, ticket: Ticket, values: dict[str, Value],
                          interfaces: dict[str, Any], placements: dict[str, list[fr.Frame]]
                          ) -> dict[str, tuple[BuildReport, BuiltDesign]]:  # fmt: skip
        out = {}
        for name, use in spec.design.components.items():
            if name not in placements:
                continue
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
                    raise BUILD_BINDING(
                        f"component {name}: binding {param} = {ref}: {e}", Location(address=name)
                    ) from None
            bound = child.model_copy(update={"configuration": use.configuration, "overrides": overrides})
            # A cached component is reused while everything its report came from is as it was: its spec, its
            # backends, the materials it sees and the analyses that judged it.
            backends = sorted({child.design.part_backend(p) for p in child.design.parts} | {child.design.backend})
            registry = {n: m.model_dump() for n, m in materials.for_design(child_folder).items()}
            key = json.dumps([bound.key, [self._backend(b).fingerprint() for b in backends], registry, analysis_code()],
                             sort_keys=True)  # fmt: skip
            target = self.components_dir / hashlib.sha256(key.encode()).hexdigest()
            report_file = target / REPORT_FILE
            async with self._component_locks[target.name]:  # two designs may build the same component at once
                if (
                    report_file.is_file()
                    and (cached := BuildReport.model_validate_json(report_file.read_text(encoding="utf-8"))).status
                    == "succeeded"
                ):
                    report = cached
                else:
                    report, _ = await self._build(bound, child_folder, target, ticket, _Quiet(), outputs=False)
            if report.status != "succeeded":
                lead = report.lead()
                why = f": {lead.code} {lead.subject}: {lead.detail}" if lead else ""
                raise BUILD_COMPONENT(f"component {name} ({use.design}) did not build{why}", Location(address=name))
            shutil.copytree(target, out_dir / "components" / name)
            out[name] = (report, load_built(report, bound, child_folder, out_dir / "components" / name))
        return out

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
                        (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(work / rel, out_dir / rel)
                self.parts_cache.store(outcome, declared[file], work, inputs[backend.name])
            return outcome, printed

        # Every file to its end, the ones already running included, before saying a newer build superseded this one:
        # what they built reaches the part cache for it, and nothing writes here once this build stops.
        built = await asyncio.gather(*(one(n, file) for n, file in enumerate(files)), return_exceptions=True)
        for raised in sorted(
            (b for b in built if isinstance(b, BaseException)), key=lambda e: isinstance(e, Superseded)
        ):
            raise raised
        log.extend(printed for _, printed in built if printed)  # type: ignore[misc]
        return [outcome for outcome, _ in built]  # type: ignore[misc]

    def _geometry(self, spec: BuildSpec, out_dir: Path, outcome: FileOutcome, bp: BuiltPart) -> PartGeometry:
        return PartGeometry(
            name=bp.name, spec=spec.design.parts[bp.name], file=outcome.file, backend=spec.design.part_backend(bp.name),
            mesh_file=out_dir / bp.mesh, step_file=out_dir / bp.step if bp.step else None,
            objects_file=out_dir / bp.objects, view_file=out_dir / bp.view,
            frames={k: [fr.as_frame(m) for m in v] for k, v in bp.connectors.items()}, catalog=bp.catalog,
            reads=outcome.reads, cached=outcome.cached, valid=bp.valid,
        )  # fmt: skip

    # ------------------------------------------------------------------ analyses and outputs

    def _analyse(self, built: BuiltDesign, watched: Mapping[str, int], ticket: Ticket,
                 progress: Progress) -> list[Result]:  # fmt: skip
        ctx = Context(built=built, settings=self.analysis_settings, cache=self.results_cache, watched=watched,
                      bodies=BuildBodies(built, self.analysis_settings, self.results_cache))  # fmt: skip
        out: list[Result] = []
        for n, analysis in enumerate(self.analyses, 1):
            ticket.check()
            progress.doing(f"checking {analysis.name} ({n} of {len(self.analyses)})")
            try:
                out += analysis.run(ctx)
            except Exception as e:
                d = diagnose(e)
                if d.owner == "design":  # something the design declares does not fit together
                    status = None if isinstance(e, Unmeasurable) else False
                    out.append(reported("structure", d, analysis.name, status=status))
                    continue
                # a bug in one analysis: report it unverified, keep the others
                logger.exception("analysis %s failed", analysis.name)
                bug = HITLCAD_BUG.diagnostic(f"the {analysis.name} analysis failed: {d.message}")
                out.append(reported(analysis.name, bug, analysis.name, status=None))
        return out

    def _describe(self, built: BuiltDesign) -> dict[str, Any]:
        """The parts, instances, mass and bill of materials of a built design, for its report."""
        parts = [_part_result(built, p) for p in built.parts.values()]
        instances = [InstanceInfo(path=a, part=n if n in built.parts else None, component=n if n in built.components else None,
                                  transform=fr.as_matrix(t)) for a, n, t in built.instances()]  # fmt: skip
        whole = BuildBodies(built, self.analysis_settings, self.results_cache).whole()
        return {"motions": motion.reported(built), "parts": parts, "instances": instances,
                "metrics": whole.metrics if whole.leaves else None, "mass": _mass(built), "bom": bom(built)}  # fmt: skip

    async def _outputs(
        self, built: BuiltDesign, out_dir: Path, fields: dict[str, Any], ticket: Ticket
    ) -> list[Artifact]:
        # Backends write their own assembly files in their own processes, alongside the rest; either failing stops
        # the other.
        async with asyncio.TaskGroup() as group:
            files = group.create_task(self._files(built, out_dir, fields))
            native = group.create_task(self._native(built, out_dir, ticket))
        return files.result() + native.result()

    async def _native(self, built: BuiltDesign, out_dir: Path, ticket: Ticket) -> list[Artifact]:
        """The assembly in the format of each backend that built its parts, components' included."""
        tree = assemble.tree(built)
        names = sorted({part.backend for part in assemble.all_parts(built)})
        written = await asyncio.gather(*(self._backend(name).write_assembly(ticket, built.folder, tree, out_dir, out_dir / "work" / name)
                                         for name in names))  # fmt: skip
        shutil.rmtree(out_dir / "work", ignore_errors=True)
        return [_artifact(out_dir, rel, media_type, "native") for rel, media_type in filter(None, written)]

    async def _files(self, built: BuiltDesign, out_dir: Path, fields: dict[str, Any]) -> list[Artifact]:
        artifacts = [_artifact(out_dir, rel.relative_to(out_dir).as_posix(), "text/x-python", "source")
                     for rel in sorted((out_dir / SOURCES).rglob("*.py"))]  # fmt: skip
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
        await asyncio.to_thread(built.mesh.export, out_dir / MESH_FILE)
        artifacts.append(_artifact(out_dir, MESH_FILE, "model/stl", "mesh"))
        if await asyncio.to_thread(assemble.step_assembly, built, out_dir / STEP_FILE):
            artifacts.append(_artifact(out_dir, STEP_FILE, "model/step", "exchange"))
        artifacts.append(_artifact(out_dir, cadview.SCENE_FILE, "application/json", "view"))
        metrics = fields.get("metrics")
        preview = await self.renderer.render(out_dir / MESH_FILE, out_dir, metrics)
        if preview:
            artifacts.append(_artifact(out_dir, preview.name, "image/png", "preview"))
        for name in built.spec.outputs:
            if name not in self.exporters:  # the structure check says so
                continue
            exporter = self.exporters[name]
            for rel, media_type in await asyncio.to_thread(exporter.export, built, fields, out_dir):
                artifacts.append(_artifact(out_dir, rel, media_type, name, exporter.title))
        return artifacts


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


def load_built(report: BuildReport, spec: BuildSpec, folder: Path, out_dir: Path) -> BuiltDesign:
    """A built design back from its build directory and report: as the analyses saw it, but for each part's B-rep
    check (`PartGeometry.valid`), which the report keeps only as the part analysis's result."""
    design = spec.design
    parts = {}
    for pr in report.parts:
        stl = out_dir / "parts" / f"{pr.name}.stl"
        step = out_dir / "parts" / f"{pr.name}.step"
        parts[pr.name] = PartGeometry(
            name=pr.name, spec=design.parts[pr.name], file=pr.file, backend=pr.backend, mesh_file=stl,
            step_file=step if step.is_file() else None, objects_file=out_dir / "parts" / f"{pr.name}.glb",
            view_file=out_dir / "parts" / f"{pr.name}{cadview.VIEW_SUFFIX}",
            frames={k: [fr.as_frame(m) for m in c.frames] for k, c in pr.connectors.items()},
            catalog=pr.catalog, reads=pr.reads, cached=pr.cached,
        )  # fmt: skip
    placements: dict[str, list[fr.Frame]] = {}
    for inst in report.instances:
        placements.setdefault(inst.part or inst.component, []).append(fr.as_frame(inst.transform))  # type: ignore[arg-type]
    components = {name: load_built(child, spec.components[name], (folder / design.components[name].design).resolve(),
                                   out_dir / "components" / name)
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
