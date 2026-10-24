"""Interference: no two placed part instances overlap, except where a mate (a fit that allows interference)
or a fastener set (a thread in its into hole) declares the overlap it expects. Pairs inside one
component instance are the component's own build's to check."""

from __future__ import annotations

import numpy as np

from hitlcad.analysis.base import Context, result
from hitlcad.analysis.coverage import Link, links, top
from hitlcad.analysis.model import Leaf
from hitlcad.core import frames as fr
from hitlcad.core.report import Result


def allowed(found: list[Link]) -> dict[frozenset[str], str]:
    """Instance pairs that may overlap, and why."""
    out: dict[frozenset[str], str] = {}
    for link in found:
        if getattr(link.source, "overlap", False):
            out[frozenset((link.a.instance, link.b.instance))] = f"the {link.source.address} mate declares it"
        fit = getattr(link.source, "fit", None)
        if fit is not None and {link.a.spec.kind, link.b.spec.kind} == {"hole", "shaft"}:
            measured = [f.measured.values.get("diameter") for f in (link.a, link.b)]
            if all(measured) and fit.allows_interference(sum(measured) / 2):  # type: ignore[arg-type]
                out[frozenset((link.a.instance, link.b.instance))] = (
                    f"the {link.source.address} fit is an interference fit"
                )
        if link.role == "into":
            out[frozenset((link.a.instance, link.b.instance))] = f"{link.a.instance} is threaded into {link.b.instance}"
    return out


def boxes_touch(a: np.ndarray, b: np.ndarray, pad: float) -> bool:
    return bool(np.all(a[0] - pad <= b[1]) and np.all(b[0] - pad <= a[1]))


def placed_bounds(leaf: Leaf, motion: fr.Frame) -> np.ndarray:
    corners = np.array([[x, y, z, 1.0] for x in leaf.part.mesh.bounds[:, 0] for y in leaf.part.mesh.bounds[:, 1]
                        for z in leaf.part.mesh.bounds[:, 2]])  # fmt: skip
    pts = (corners @ (motion @ leaf.transform).T)[:, :3]
    return np.array([pts.min(axis=0), pts.max(axis=0)])


def overlap(ctx: Context, a: Leaf, ta: fr.Frame, b: Leaf, tb: fr.Frame) -> float:
    """Volume (mm³) two leaves share after moving them by ta and tb; cached by what it depends on."""
    ma, mb = ta @ a.transform, tb @ b.transform
    key = ctx.cache.key("overlap", a.part.digest, b.part.digest, np.round(ma, 6).tolist(), np.round(mb, 6).tolist())

    def compute() -> float:
        sa = a.part.manifold.transform(ma[:3, :])
        sb = b.part.manifold.transform(mb[:3, :])
        return float((sa ^ sb).volume())

    return float(ctx.cache.get_or(key, compute))


class Interference:
    name = "interference"

    def run(self, ctx: Context) -> list[Result]:
        leaves = ctx.built.leaves()
        found, _ = links(ctx.built)
        ok_pairs = allowed(found)
        out: list[Result] = []
        checked = 0
        pad = ctx.tol.position_mm
        for i, a in enumerate(leaves):
            for b in leaves[i + 1 :]:
                if top(a.path) == top(b.path) or not boxes_touch(a.bounds, b.bounds, pad):
                    continue
                checked += 1
                try:
                    shared = overlap(ctx, a, fr.IDENTITY, b, fr.IDENTITY)
                except ValueError as e:  # an open mesh
                    out.append(result(self.name, f"{a.path}+{b.path}", None, f"not measurable: {e}", [a.path, b.path]))
                    continue
                if shared <= ctx.settings.overlap_mm3:
                    continue
                why = ok_pairs.get(frozenset((a.path, b.path)))
                if why:
                    out.append(result(self.name, f"{a.path}+{b.path}", True, f"overlap {shared:.2f} mm³, expected: {why}",
                                      [a.path, b.path], {"overlap_mm3": shared}, severity="info"))  # fmt: skip
                else:
                    out.append(result(self.name, f"{a.path}+{b.path}", False, f"{a.path} and {b.path} overlap by "
                                      f"{shared:.2f} mm³", [a.path, b.path], {"overlap_mm3": shared}))  # fmt: skip
        if not any(r.status == "fail" for r in out):
            out.append(result(self.name, "interference", True, f"no unexpected overlaps among {len(leaves)} instances "
                              f"({checked} pairs close enough to check)", [], {"pairs_checked": checked}))  # fmt: skip
        return out
