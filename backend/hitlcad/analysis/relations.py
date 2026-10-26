"""Relations: each relation type verifies its pair of connectors from their placed frames and nominal values, once
both connectors' features measure as declared (a gear cut to another module or tooth count meshes with nothing)."""

from __future__ import annotations

from hitlcad.analysis.base import Context, label, member_failed, pair, result, structural
from hitlcad.analysis.model import BuiltDesign, PlacedFrame
from hitlcad.core.report import Result
from hitlcad.core.structure import Relation
from hitlcad.kernel.errors import RELATION_CHECK, RELATION_UNMEASURED, STRUCTURE_RELATION_KIND, StructureError


def relation_pairs(built: BuiltDesign, rel: Relation) -> list[tuple[PlacedFrame, PlacedFrame]]:
    """One end with a single frame meets every frame of the other (a sun and its planets); two patterns pair
    one-to-one."""
    a, b = built.frames(rel.a), built.frames(rel.b)
    for end, frames, kind in ((rel.a, a, rel.joins[0]), (rel.b, b, rel.joins[1])):
        wrong = {f.spec.kind for f in frames} - {kind}
        if wrong:
            raise StructureError(f"a {rel.type} relation joins a {rel.joins[0]} and a {rel.joins[1]}; {end} is a "
                                 f"{', '.join(sorted(wrong))}", kind=STRUCTURE_RELATION_KIND)  # fmt: skip
    if len(a) == 1 or len(b) == 1:
        return [(fa, fb) for fa in a for fb in b]
    return pair(a, b)


class Relations:
    name = "relation"
    stage = "joints"
    title = "Relations"
    summary = "Gears and other relations hold between their placed, measured features"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for rel in ctx.built.spec.relations:
            try:
                pairs = relation_pairs(ctx.built, rel)
            except StructureError as e:
                out.append(structural(rel.address, e, [rel.a, rel.b]))
                continue
            for fa, fb in pairs:
                about = [fa.instance, fb.instance, fa.address, fb.address, rel.a, rel.b]
                if wrong := [f for f in (fa, fb) if f.measured.status == "fail"]:
                    why = " and ".join(label(f) for f in wrong)
                    out.append(result(self.name, RELATION_UNMEASURED, rel.address, None, f"{label(fa)} ↔ {label(fb)}: not verified until "
                                      f"{why} measures as declared", about))  # fmt: skip
                    continue
                na, nb = fa.owner.nominals(fa.spec), fb.owner.nominals(fb.spec)
                try:
                    check = rel.verify(fa.frame, fb.frame, na, nb, ctx.tol)
                except Exception as e:  # the relation type's own code
                    out.append(member_failed(self.name, rel, f"the {rel.type} relation's check", e, about, rel.address))
                    continue
                out.append(result(self.name, RELATION_CHECK, rel.address, check.passed, f"{label(fa)} ↔ {label(fb)}: {check.detail}",
                                  about, check.values))  # fmt: skip
        return out
