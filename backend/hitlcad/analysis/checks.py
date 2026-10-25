"""Analyses of single parts and declared requirements: part geometry, connector features, manufacturing
processes, requirement checks. Each covers this design's own parts; a component's parts are covered by
the component's own build."""

from __future__ import annotations

from hitlcad.analysis.base import Context, result, structural
from hitlcad.analysis.model import StructureError
from hitlcad.core.report import Result


class Parts:
    """Each part's mesh closes a solid (`mesh.closed`, the test every measurement shares), and its B-rep
    (where it has one) passes the kernel's check."""

    name = "part"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for name, part in ctx.built.parts.items():
            m = part.metrics
            if m.triangles == 0:
                out.append(result(self.name, name, False, "the part is empty", [name]))
                continue
            out.append(result(self.name, name, m.watertight,
                              "closed mesh" if m.watertight else "the mesh does not close a solid: volume, mass, walls "
                              "and overlaps cannot be measured", [name], severity="warning"))  # fmt: skip
            if part.valid is not None:
                out.append(result(self.name, name, part.valid, "B-rep valid" if part.valid else
                                  "the kernel's shape check fails: booleans and exports may be wrong", [name]))  # fmt: skip
        return out


class Connectors:
    """Each declared connector has the frames its count says, and each frame its feature (a datum has none to
    check, so no result of its own)."""

    name = "connector"

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
                    out.append(result(self.name, address, False, f"{len(frames)} frames, but the connector declares "
                                      f"{expected}", [name, address]))  # fmt: skip
                if not spec.measures:
                    continue
                for k, measured in enumerate(ctx.built.measured.get((name, cname), [])):
                    subject = f"{address}[{k}]" if len(frames) > 1 else address
                    out.append(result(self.name, subject, measured.status, measured.detail, [name, address],
                                      measured.values))  # fmt: skip
        return out


class Manufacturing:
    """Each made part, in its own frame, against its process."""

    name = "manufacturing"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for name, part in ctx.built.parts.items():
            process = ctx.built.spec.process(name)
            if process is None or part.mesh.is_empty:  # an empty part fails as a part
                continue
            for finding in process.checks(ctx.bodies.part(name)):
                out.append(result(self.name, f"{name}: {finding.name}", finding.passed, finding.detail, [name],
                                  severity="warning"))  # fmt: skip
        return out


class Requirements:
    name = "requirement"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for req in ctx.built.spec.requirements:
            if req.check is None:
                out.append(result(self.name, req.text, None, "no check: the human judges it"))
                continue
            try:
                passed, detail = req.check.verify(ctx.bodies)
            except StructureError as e:
                passed, detail = False, f"{e}"
            out.append(result(self.name, req.text, passed, detail, req.check.targets()))
        return out
