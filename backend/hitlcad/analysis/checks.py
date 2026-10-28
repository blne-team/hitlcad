"""Analyses of single parts and declared requirements: part geometry, connector features, manufacturing
processes, requirement checks. Each covers this design's own parts; a component's parts are covered by
the component's own build."""

from __future__ import annotations

from hitlcad.analysis.base import Context, member_failed, result, structural
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
    """Each made part, in its own frame, against its process."""

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
                findings = process.checks(ctx.bodies.part(name))
            except Exception as e:  # the process's own code: its other parts, and the build, go on
                out.append(member_failed(self.name, process, f"the {process.process} checks of {name}", e, [name]))
                continue
            for finding in findings:
                out.append(result(self.name, of(f"manufacturing.{finding.name}"), f"{name}: {finding.name}",
                                  finding.passed, finding.detail, [name], severity="warning"))  # fmt: skip
        return out


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
