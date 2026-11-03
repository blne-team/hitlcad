"""Analyses of single parts and declared requirements: part geometry, connector features, manufacturing
processes, requirement checks. Each covers this design's own parts; a component's parts are covered by
the component's own build."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

from hitlcad.analysis.base import Context, member_code, member_failed, result, structural
from hitlcad.core import frames as fr
from hitlcad.core.checks import PrintedProcess, PrintWay, Process, ProcessFinding, RoundInterface
from hitlcad.core.report import Result
from hitlcad.kernel.errors import (
    CONNECTOR_COUNT,
    CONNECTOR_FEATURE,
    PART_BREP,
    PART_EMPTY,
    PART_OPEN,
    REQUIREMENT_UNCHECKED,
    Location,
    StructureError,
    of,
)

if TYPE_CHECKING:
    from hitlcad.analysis.model import Body


class Parts:
    """Each part's mesh closes a solid (`mesh.closed`, the test every measurement shares), and its B-rep
    (where it has one) passes the kernel's check."""

    name = "part"
    stage = "parts"
    title = "Parts"
    summary = "Every part is a valid solid with a closed mesh"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for name, part in ctx.built.parts.items():
            m = part.metrics
            if m.triangles == 0:
                out.append(result(self.name, PART_EMPTY, name, False, "the part is empty", [name]))
                continue
            detail = "closed mesh"
            values = {}
            if not m.watertight:
                issues = part.mesh_issues
                evidence = "; ".join(issue.detail for issue in issues)
                detail = "the mesh does not close a solid: volume, mass, walls and overlaps cannot be measured"
                if evidence:
                    detail += f"; source mesh before cleanup: {evidence}; locations are part-local mm"
                else:
                    detail += "; no edge-pairing defect found: inspect shell orientation and self-intersections"
                values = {issue.kind: issue.count for issue in issues}
            finding = result(self.name, PART_OPEN, name, m.watertight, detail, [name], values, severity="warning")
            if not m.watertight:
                finding.where = Location(file=part.file, address=name)
            out.append(finding)
            if part.valid is not None:
                out.append(result(self.name, PART_BREP, name, part.valid, "B-rep valid" if part.valid else
                                  "the kernel's shape check fails: booleans and exports may be wrong", [name]))  # fmt: skip
        return out


class Connectors:
    """Each declared connector has the frames its count says, and each frame its feature (a datum has none to
    check, so no result of its own)."""

    name = "connector"
    stage = "connectors"
    title = "Connectors"
    summary = "Each declared feature is found in the geometry and measured"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for name, part in ctx.built.parts.items():
            for cname, spec in part.spec.connectors.items():
                address = f"{name}.{cname}"
                frames = part.frames.get(cname, [])
                try:
                    expected = ctx.built.count(spec)
                except StructureError as e:
                    out.append(structural(address, e, [name]))
                    continue
                if len(frames) != expected:
                    out.append(result(self.name, CONNECTOR_COUNT, address, False, f"{len(frames)} frames, but the connector declares "
                                      f"{expected}", [name, address]))  # fmt: skip
                if not spec.measures:
                    continue
                for k, measured in enumerate(ctx.built.measured.get((name, cname), [])):
                    subject = f"{address}[{k}]" if len(frames) > 1 else address
                    out.append(result(self.name, CONNECTOR_FEATURE, subject, measured.status, measured.detail, [name, address],
                                      measured.values))  # fmt: skip
        return out


class Manufacturing:
    """Each made part, as it lies when it is made, against its process."""

    name = "manufacturing"
    stage = "process"
    title = "Manufacturing"
    summary = "Each made part against its process, in the orientation it is made"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for name, part in ctx.built.parts.items():
            process = ctx.built.spec.process(name)
            if process is None or part.mesh.is_empty:  # an empty part fails as a part
                continue
            try:
                findings = process_findings(ctx, name, process)
            except Exception as e:  # the process's own code: its other parts, and the build, go on
                out.append(member_failed(self.name, process, f"the {process.process} checks of {name}", e, [name]))
                continue
            for finding in findings:
                out.append(result(self.name, of(f"manufacturing.{finding.name}"), f"{name}: {finding.name}",
                                  finding.passed, finding.detail, [name], severity="warning"))  # fmt: skip
        return out


def process_findings(ctx: Context, name: str, process: Process) -> list[ProcessFinding]:
    """A part's findings for its process, lying as it is made: its checks, and a printed part's way's (`print_way`).
    A printed part's checks do not depend on which way up it prints: they see it as modelled, so turning it checks
    only its way again, and say where in the frame its part file models it in."""
    own = process.model_copy(update={"up": (0.0, 0.0, 1.0)}) if isinstance(process, PrintedProcess) else process
    found = _kept(ctx, "process", name, own, lambda body: [f.model_dump(mode="json") for f in own.checks(body)])
    findings = [ProcessFinding.model_validate(f) for f in found]
    if isinstance(process, PrintedProcess):
        findings += print_way(ctx, name, process).findings
    return findings


def print_way(ctx: Context, name: str, process: PrintedProcess) -> PrintWay:
    """What a printed part's process finds of it lying the way `process.up` says, its round interfaces lying so too:
    the way it prints, or any other the best orientation weighs (`analysis.printing.orientations`), judged alike."""
    interfaces = _lying(ctx, name, process)
    return PrintWay.model_validate(
        _kept(
            ctx,
            "print way",
            name,
            process,
            lambda body: process.way(body, interfaces).model_dump(mode="json"),
            [it.model_dump(mode="json") for it in interfaces],
        )
    )


def print_way_key(ctx: Context, name: str, process: PrintedProcess) -> str:
    """The key `print_way` keeps a way by."""
    return process_key(
        ctx, "print way", name, process, [it.model_dump(mode="json") for it in _lying(ctx, name, process)]
    )


def _lying(ctx: Context, name: str, process: PrintedProcess) -> list[RoundInterface]:
    """A part's round interfaces where it lies as `process` makes it."""
    made = process.made()
    return [it.model_copy(update={"origin": tuple(float(v) for v in fr.transform_points(np.array([it.origin]), made)[0]),
                                  "axis": tuple(float(v) for v in made[:3, :3] @ np.asarray(it.axis))})
            for it in round_interfaces(ctx, name)]  # fmt: skip


def round_interfaces(ctx: Context, name: str) -> list[RoundInterface]:
    """A part's round features that another part fits on or in (a mate's fit), in its own frame, each once, with its
    fit's clearance."""
    found: dict[str, RoundInterface] = {}
    for link in ctx.links:
        fit = getattr(link.source, "fit", None)
        if fit is None:
            continue
        for end in (link.a, link.b):
            diameter = end.measured.values.get("diameter")
            if end.owner is not ctx.built or end.part.name != name or end.spec.round is None or not diameter:
                continue
            try:
                clearance = fit.limits(diameter)
            except ValueError:  # ISO 286 has no such fit at this size: the mate's fit check says so
                continue
            frame = end.part.frames[end.connector][end.index]
            key = f"{end.connector}[{end.index}]"
            found[key] = RoundInterface(name=key, round=end.spec.round, diameter=diameter, clearance_mm=clearance,
                                        origin=tuple(float(v) for v in fr.origin(frame)),
                                        axis=tuple(float(v) for v in fr.z_axis(frame)))  # fmt: skip
    return [found[k] for k in sorted(found)]


def process_key(ctx: Context, what: str, name: str, process: Process, *more: Any) -> str:
    """The key of what a process finds of a part as it is made: all it reads of the part on its own (its mesh, its
    material, the tolerances and sampling), the process with its code, and what else it is given (`more`)."""
    part = ctx.built.parts[name]
    material = ctx.built.materials.get(part.material or "")
    kind = type(process)
    return ctx.cache.key(what, part.digest, material.model_dump(mode="json") if material else part.material,
                         ctx.tol.model_dump(mode="json"), ctx.settings.mesh_tolerance_mm, ctx.settings.wall_samples,
                         ctx.settings.overlap_mm3, f"{kind.__module__}.{kind.__qualname__}", member_code(kind.__module__),
                         process.model_dump(mode="json"), *more)  # fmt: skip


def _kept(ctx: Context, what: str, name: str, process: Process, compute: Callable[[Body], Any], *more: Any) -> Any:
    """What a process finds of a part as it is made, kept (`process_key`): a part no edit touched is not checked
    again."""
    return ctx.cache.get_or(
        process_key(ctx, what, name, process, *more), lambda: compute(ctx.bodies.made(name, process))
    )


class Requirements:
    name = "requirement"
    stage = "requirements"
    title = "Requirements"
    summary = "The brief's requirements, measured; the human judges those without a check"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for req in ctx.built.spec.requirements:
            if req.check is None:
                out.append(result(self.name, REQUIREMENT_UNCHECKED, req.text, None, "no check: the human judges it"))
                continue
            try:
                passed, detail = req.check.verify(ctx.bodies)
                cause = req.check.cause(ctx.bodies) if passed is False else ""
            except StructureError as e:
                passed, detail, cause = False, f"{e}", ""
            except Exception as e:  # the check's own code
                out.append(member_failed(self.name, req.check, f"the {req.check.kind} check", e, req.check.targets(),
                                         req.text))  # fmt: skip
                continue
            out.append(result(self.name, of(f"requirement.{req.check.kind}"), req.text, passed, detail,
                              req.check.targets(), cause=cause))  # fmt: skip
        return out
