"""Relations: each relation type verifies its pair of connectors from their placed frames and nominal values, once
both connectors' features measure as declared (a gear cut to another module or tooth count meshes with nothing)."""

from __future__ import annotations

from hitlcad.analysis.base import Context, label, pair, result, structural
from hitlcad.analysis.model import BuiltDesign, PlacedFrame, StructureError
from hitlcad.core.report import Result
from hitlcad.core.structure import _Relation


def relation_pairs(built: BuiltDesign, rel: _Relation) -> list[tuple[PlacedFrame, PlacedFrame]]:
    """One end with a single frame meets every frame of the other (a sun and its planets); two patterns pair
    one-to-one."""
    a, b = built.frames(rel.a), built.frames(rel.b)
    for end, frames, kind in ((rel.a, a, rel.joins[0]), (rel.b, b, rel.joins[1])):
        wrong = {f.spec.kind for f in frames} - {kind}
        if wrong:
            raise StructureError(f"a {rel.type} relation joins a {rel.joins[0]} and a {rel.joins[1]}; {end} is a "
                                 f"{', '.join(sorted(wrong))}")  # fmt: skip
    if len(a) == 1 or len(b) == 1:
        return [(fa, fb) for fa in a for fb in b]
    return pair(a, b)


class Relations:
    name = "relation"

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
                    out.append(result(self.name, rel.address, None, f"{label(fa)} ↔ {label(fb)}: not verified until "
                                      f"{why} measures as declared", about))  # fmt: skip
                    continue
                na, nb = fa.owner.nominals(fa.spec), fb.owner.nominals(fb.spec)
                check = rel.verify(fa.frame, fb.frame, na, nb, ctx.tol)
                out.append(result(self.name, rel.address, check.passed, f"{label(fa)} ↔ {label(fb)}: {check.detail}",
                                  about, check.values))  # fmt: skip
        return out
