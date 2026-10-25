"""Backend-agnostic build pipeline.

    sources → plan (interface values, placements) → components (each a build of its own, cached by its
    spec) → part files (in parallel worker processes, cached by what each read) → the built design →
    connectors measured → every analysis → outputs (assembly STEP, mesh, 3D view, preview, exporters, and
    the assembly in each backend's own format) → report

A build's directory is self-contained: its sources, its parts in their own frames, its components'
build directories, its outputs and its report. Loading it back (`load_built`) gives the same model
the analyses saw, so components built earlier and revisions reopened later behave alike.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from hitlcad import materials
from hitlcad.analysis.base import Analysis, Context, ResultCache, result
from hitlcad.analysis.model import AnalysisSettings, BuildBodies, BuiltDesign, PartGeometry, StructureError
from hitlcad.backends.base import BuiltPart, FileOutcome, PlanOutcome
from hitlcad.backends.registry import BackendRegistry
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
    MotionRange,
    PartResult,
    Result,
)
from hitlcad.core.types import Value
from hitlcad.exporters import Exporter
from hitlcad.render.base import NullRenderer, Renderer

LOG_TAIL_LINES = 40
VIEW_FILE = "view.json"
MESH_FILE = "mesh.stl"
STEP_FILE = "model.step"
REPORT_FILE = "report.json"
SOURCES = "src"

logger = logging.getLogger(__name__)


class BuildFailed(Exception):
    def __init__(self, error: str, log: str = "", results: list[Result] | None = None) -> None:
        super().__init__(error)
        self.error, self.log, self.results = error, log, results or []


def _artifact(out_dir: Path, rel: str, media_type: str, role: str) -> Artifact:
    data = (out_dir / rel).read_bytes()
    return Artifact(name=Path(rel).name, path=rel, media_type=media_type, role=role,
                    sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))  # fmt: skip


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-LOG_TAIL_LINES:])


def write_sources(spec: BuildSpec, folder: Path, target: Path) -> None:
    """The design's code from the spec, and its vendor files from the folder (checked against the spec)."""
    shutil.rmtree(target, ignore_errors=True)
    for rel, text in spec.sources.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    for rel, digest in spec.assets.items():
        data = (folder / rel).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise BuildFailed(f"{rel} changed since the build started; build again")
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
        self.analysis_settings = AnalysisSettings(
            tolerances=settings.tolerances, overlap_mm3=settings.overlap_mm3,
            mesh_tolerance_mm=settings.mesh_tolerance_mm, wall_samples=settings.wall_samples,
        )  # fmt: skip

    # ------------------------------------------------------------------ entry points

    async def run(self, spec: BuildSpec, folder: Path, out_dir: Path) -> BuildReport:
        """Build a design into `out_dir`, with every output; the report says how it went."""
        report, _ = await self._build(spec, folder, out_dir, outputs=True)
        return report

    async def _build(self, spec: BuildSpec, folder: Path, out_dir: Path, *, outputs: bool
                     ) -> tuple[BuildReport, BuiltDesign | None]:  # fmt: skip
        started = time.monotonic()
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        values = spec.values()
        log: list[str] = []

        def finish(status: BuildStatus, error: str | None, **fields: Any) -> BuildReport:
            text = "\n".join(log)
            (out_dir / "build.log").write_text(text, encoding="utf-8")
            report = BuildReport(status=status, configuration=spec.configuration, values=values, error=error,
                                 log_tail=_tail(text) if error else "", duration_s=round(time.monotonic() - started, 3),
                                 **fields)  # fmt: skip
            report.artifacts.append(_artifact(out_dir, "build.log", "text/plain", "log"))
            (out_dir / REPORT_FILE).write_text(report.model_dump_json(indent=2), encoding="utf-8")
            return report

        try:
            built, fields = await self._assemble(spec, folder, out_dir, values, log)
        except BuildFailed as e:
            log.append(e.log)
            return finish(BuildStatus.FAILED, e.error, results=e.results), None
        results = await asyncio.to_thread(self._analyse, built)
        report_fields = {**fields, **await asyncio.to_thread(self._describe, built), "results": results}
        if outputs:
            try:
                report_fields["artifacts"] = await self._outputs(built, out_dir, report_fields)
            except Exception as e:  # an output that cannot be written fails the build visibly
                return finish(BuildStatus.FAILED, f"could not write the build's outputs: {e}", **report_fields), None
        return finish(BuildStatus.SUCCEEDED, None, **report_fields), built

    async def check_structure(
        self, spec: BuildSpec, folder: Path, work: Path
    ) -> tuple[list[Result], PlanOutcome | None]:
        """The plan check: sources, the plan and every reference design.json makes, without building geometry."""
        from hitlcad.analysis.structure import structure_results

        shutil.rmtree(work, ignore_errors=True)
        try:
            write_sources(spec, folder, work / SOURCES)
            missing = sorted({p.file for p in spec.design.parts.values()} - set(spec.sources))
            plan = await self._plan(spec, work / SOURCES, spec.values(), work / "plan")
        except BuildFailed as e:
            return [result("structure", "build", False, e.error)], None
        out = [result("structure", f, False, f"{f} does not exist", spec.design.files()[f]) for f in missing]
        if plan.error:
            return [*out, result("structure", "plan", False, plan.error)], plan
        out += [result("structure", n, False, f"assembly.py places {n}, which design.json does not declare")
                for n in plan.placements if n not in spec.design.parts and n not in spec.design.components]  # fmt: skip
        for name in spec.design.outputs:
            if name not in self.exporters:
                out.append(result("structure", f"outputs: {name}", False, f"no exporter {name!r}; exporters: "
                                  f"{', '.join(self.exporters)}"))  # fmt: skip
        for name in {spec.design.part_backend(p) for p in spec.design.parts}:
            if name not in self.registry.names():
                out.append(result("structure", f"backend: {name}", False, f"backend {name!r} is not enabled; "
                                  f"enabled: {', '.join(self.registry.names())}"))  # fmt: skip
        exports = {n: c.design.exports for n, c in spec.components.items()}
        out += structure_results(spec.design, plan.placements, exports, materials.for_design(folder))
        shutil.rmtree(work, ignore_errors=True)
        return out, plan

    # ------------------------------------------------------------------ building

    async def _assemble(self, spec: BuildSpec, folder: Path, out_dir: Path, values: dict[str, Value], log: list[str]
                        ) -> tuple[BuiltDesign, dict[str, Any]]:  # fmt: skip
        design = spec.design
        src = out_dir / SOURCES
        write_sources(spec, folder, src)
        missing = sorted({p.file for p in design.parts.values()} - set(spec.sources))
        if missing:
            raise BuildFailed("part files declared in design.json do not exist: " + ", ".join(missing),
                              results=[result("structure", f, False, f"{f} does not exist") for f in missing])  # fmt: skip
        plan = await self._plan(spec, src, values, out_dir / "work" / "plan")
        log.append(plan.log)
        if plan.error:
            raise BuildFailed(plan.error, plan.log)
        interfaces = {k: v.value for k, v in plan.interfaces.items()}
        placements = {k: [fr.as_frame(m) for m in v] for k, v in plan.placements.items()}
        unknown = [n for n in placements if n not in design.parts and n not in design.components]
        if unknown:
            raise BuildFailed(f"assembly.py places {', '.join(unknown)}, which design.json does not declare as parts "
                              "or components", results=[result("structure", n, False, f"{n} is not declared") for n in unknown])  # fmt: skip
        components = await self._components(spec, folder, out_dir, values, interfaces, placements)
        outcomes = await self._parts(spec, src, values, interfaces, out_dir, folder, log)
        errors = [f"{o.file}:\n{o.error}" for o in outcomes if o.error]
        if errors:
            raise BuildFailed("\n\n".join(errors), results=[
                result("part", o.file, False, o.error or "", design.files()[o.file]) for o in outcomes if o.error])  # fmt: skip
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
        await asyncio.to_thread(built.measure_connectors)
        fields = {
            "interfaces": [InterfaceValue(name=k, value=v.value, reads=v.reads) for k, v in plan.interfaces.items()],
            "assembly_reads": plan.assembly_reads,
            "components": {n: c[0] for n, c in components.items()},
        }
        shutil.rmtree(out_dir / "work", ignore_errors=True)
        return built, fields

    async def _plan(self, spec: BuildSpec, src: Path, values: dict[str, Value], work: Path) -> PlanOutcome:
        backend = self._backend(spec.design.backend)
        return await backend.plan(src, values, work)

    def _backend(self, name: str) -> Any:
        try:
            backend = self.registry.get(name)
        except KeyError as e:
            raise BuildFailed(str(e)) from None
        available, detail = backend.availability()
        if not available:
            raise BuildFailed(f"backend {name} unavailable: {detail}")
        return backend

    async def _components(self, spec: BuildSpec, folder: Path, out_dir: Path, values: dict[str, Value],
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
                    raise BuildFailed(f"component {name}: {use.design} has no parameter {param!r} to bind")
                try:
                    overrides[param] = by_name[param].coerce(_ref_value(ref, values, interfaces))
                except (KeyError, ValueError) as e:
                    raise BuildFailed(f"component {name}: binding {param} = {ref}: {e}") from None
            bound = child.model_copy(update={"configuration": use.configuration, "overrides": overrides})
            backends = sorted({child.design.part_backend(p) for p in child.design.parts} | {child.design.backend})
            fingerprints = ":".join(self._backend(b).fingerprint() for b in backends)
            target = self.components_dir / hashlib.sha256(f"{bound.key}:{fingerprints}".encode()).hexdigest()
            report_file = target / REPORT_FILE
            async with self._component_locks[target.name]:  # two designs may build the same component at once
                if (
                    report_file.is_file()
                    and (cached := BuildReport.model_validate_json(report_file.read_text(encoding="utf-8"))).status
                    == "succeeded"
                ):
                    report = cached
                else:
                    report, _ = await self._build(bound, child_folder, target, outputs=False)
            if report.status != "succeeded":
                raise BuildFailed(f"component {name} ({use.design}) did not build:\n{report.error}")
            shutil.copytree(target, out_dir / "components" / name)
            out[name] = (report, load_built(report, bound, child_folder, out_dir / "components" / name))
        return out

    async def _parts(self, spec: BuildSpec, src: Path, values: dict[str, Value], interfaces: dict[str, Any],
                     out_dir: Path, folder: Path, log: list[str]) -> list[FileOutcome]:  # fmt: skip
        design = spec.design
        scope = str(folder)
        files = design.files()
        declared = {file: {n: list(design.parts[n].connectors) for n in names} for file, names in files.items()}
        inputs: dict[str, Inputs] = {}  # by backend
        outcomes: dict[str, FileOutcome] = {}
        todo: dict[str, dict[str, dict[str, list[str]]]] = {}
        for file, names in files.items():
            backend = self._backend(design.part_backend(names[0]))
            inputs.setdefault(backend.name, Inputs(fingerprint=backend.fingerprint(), sources=spec.sources,
                                                   assets=spec.assets, values=values, interfaces=interfaces))  # fmt: skip
            hit = self.parts_cache.lookup(scope, file, declared[file], inputs[backend.name])
            if hit:
                key, outcome = hit
                self.parts_cache.restore(key, outcome, out_dir)
                outcomes[file] = outcome.model_copy(update={"cached": True})
            else:
                todo.setdefault(backend.name, {})[file] = declared[file]
        jobs = []
        for backend_name, wanted in todo.items():
            backend = self._backend(backend_name)
            chunks = [dict(list(wanted.items())[i:: self.settings.build_workers])
                      for i in range(min(self.settings.build_workers, len(wanted)))]  # fmt: skip
            for k, chunk in enumerate(chunks):
                jobs.append((backend, chunk, out_dir / "work" / f"{backend_name}-{k}"))
        built = await asyncio.gather(*(b.build_parts(src, values, chunk, work) for b, chunk, work in jobs))
        for (backend, chunk, work), outcome in zip(jobs, built, strict=True):
            log.append(outcome.log)
            if outcome.error:
                for file in chunk:
                    outcomes[file] = FileOutcome(file=file, error=outcome.error)
                continue
            for fo in outcome.files:
                outcomes[fo.file] = fo
                if fo.error:
                    continue
                for part in fo.parts:
                    for rel in filter(None, (part.mesh, part.step, part.objects)):
                        (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(work / rel, out_dir / rel)
                self.parts_cache.store(scope, fo, declared[fo.file], work, inputs[backend.name])
        return [outcomes[f] for f in files]

    def _geometry(self, spec: BuildSpec, out_dir: Path, outcome: FileOutcome, bp: BuiltPart) -> PartGeometry:
        return PartGeometry(
            name=bp.name, spec=spec.design.parts[bp.name], file=outcome.file, backend=spec.design.part_backend(bp.name),
            mesh_file=out_dir / bp.mesh, step_file=out_dir / bp.step if bp.step else None,
            objects_file=out_dir / bp.objects,
            frames={k: [fr.as_frame(m) for m in v] for k, v in bp.connectors.items()}, catalog=bp.catalog,
            reads=outcome.reads, cached=outcome.cached, valid=bp.valid,
        )  # fmt: skip

    # ------------------------------------------------------------------ analyses and outputs

    def _analyse(self, built: BuiltDesign) -> list[Result]:
        ctx = Context(built=built, settings=self.analysis_settings,
                      bodies=BuildBodies(built, self.analysis_settings, self.results_cache), cache=self.results_cache)  # fmt: skip
        out: list[Result] = []
        for analysis in self.analyses:
            try:
                out += analysis.run(ctx)
            except StructureError as e:
                out.append(result("structure", analysis.name, False, str(e)))
            except Exception as e:  # a bug in one analysis: report it unverified, keep the others
                logger.exception("analysis %s failed", analysis.name)
                out.append(result(analysis.name, analysis.name, None, f"the analysis failed: {e}"))
        return out

    def _describe(self, built: BuiltDesign) -> dict[str, Any]:
        """The parts, instances, mass and bill of materials of a built design, for its report."""
        parts = [_part_result(built, p) for p in built.parts.values()]
        instances = [InstanceInfo(path=a, part=n if n in built.parts else None, component=n if n in built.components else None,
                                  transform=fr.as_matrix(t)) for a, n, t in built.instances()]  # fmt: skip
        whole = BuildBodies(built, self.analysis_settings, self.results_cache).whole()
        return {"motions": _motions(built), "parts": parts, "instances": instances,
                "metrics": whole.metrics if whole.leaves else None, "mass": _mass(built), "bom": bom(built)}  # fmt: skip

    async def _outputs(self, built: BuiltDesign, out_dir: Path, fields: dict[str, Any]) -> list[Artifact]:
        # Backends write their own assembly files in their own processes, alongside the rest.
        native = asyncio.ensure_future(self._native(built, out_dir))
        try:
            artifacts = await self._files(built, out_dir, fields)
        except BaseException:
            native.cancel()
            raise
        return artifacts + await native

    async def _native(self, built: BuiltDesign, out_dir: Path) -> list[Artifact]:
        """The assembly in the format of each backend that built its parts, components' included."""
        tree = assemble.tree(built)
        names = sorted({part.backend for part in assemble.all_parts(built)})
        written = await asyncio.gather(*(self._backend(name).write_assembly(tree, out_dir, out_dir / "work" / name)
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
        await asyncio.to_thread(assemble.write_view, built, out_dir / VIEW_FILE)
        artifacts.append(_artifact(out_dir, VIEW_FILE, "application/json", "view"))
        metrics = fields.get("metrics")
        preview = await self.renderer.render(out_dir / MESH_FILE, out_dir, metrics)
        if preview:
            artifacts.append(_artifact(out_dir, preview.name, "image/png", "preview"))
        for name in built.spec.outputs:
            if name not in self.exporters:
                raise ValueError(f"design.json outputs names {name!r}; exporters: {', '.join(self.exporters)}")
            for rel, media_type in await asyncio.to_thread(self.exporters[name].export, built, fields, out_dir):
                artifacts.append(_artifact(out_dir, rel, media_type, name))
        return artifacts


def _motions(built: BuiltDesign) -> list[MotionRange]:
    from hitlcad.analysis.base import label
    from hitlcad.analysis.motion import Plan

    out = []
    for plan in built.motion_plans:
        if not isinstance(plan, Plan):
            continue
        frame = plan.link.a.frame
        for kind, start, end, continuous in plan.ranges:
            out.append(MotionRange(
                mate=plan.link.source.address, pair=f"{label(plan.link.a)} ↔ {label(plan.link.b)}", kind=kind,
                start=start, end=end, continuous=continuous, current=plan.free.get(kind, 0.0),
                origin=tuple(float(v) for v in fr.origin(frame)), axis=tuple(float(v) for v in fr.z_axis(frame)),
                sign=plan.sign, moving=plan.world.leaves_of(plan.moving),
            ))  # fmt: skip
    return out


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
    total = sum(m.mass_g for m in each if m)
    centre = sum(np.array(m.centre_mm) * m.mass_g for m in each if m) / total
    inertia = np.zeros((3, 3))
    for m in each:
        assert m is not None
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
    """A built design back from its build directory and report."""
    design = spec.design
    parts = {}
    for pr in report.parts:
        stl = out_dir / "parts" / f"{pr.name}.stl"
        step = out_dir / "parts" / f"{pr.name}.step"
        parts[pr.name] = PartGeometry(
            name=pr.name, spec=design.parts[pr.name], file=pr.file, backend=pr.backend, mesh_file=stl,
            step_file=step if step.is_file() else None, objects_file=out_dir / "parts" / f"{pr.name}.glb",
            frames={k: [fr.as_frame(m) for m in c.frames] for k, c in pr.connectors.items()},
            catalog=pr.catalog, reads=pr.reads, cached=pr.cached,
        )  # fmt: skip
    placements: dict[str, list[fr.Frame]] = {}
    for inst in report.instances:
        placements.setdefault(inst.part or inst.component or "", []).append(fr.as_frame(inst.transform))
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
