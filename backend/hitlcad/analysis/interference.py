"""Interference: no two placed part instances overlap, except where a mate (a fit that allows interference,
or `overlap`) or a fastener set (a thread in its into hole) declares the overlap it expects. Pairs inside
one component instance are the component's own build's to check.

What a mate declares bounds the overlap. An interference fit makes at most the ring its measured
diameters predict, π/4·(D² − d²) over the engaged length, give or take their facets. An `overlap` mate sets
b's part into a hole of a's part (an insert, a thread, a pressed pin): the overlap is where b presses
into the hole's wall, and a has no material on the hole's axis where b sits (nor within the hole's
radius, when a's end is a measured hole). A part set into solid material, where its hole was never cut or
is too shallow, overlaps it there too, and that overlap is reported."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from hitlcad.analysis.base import Context, ResultCache, result
from hitlcad.analysis.coverage import Link, links, top
from hitlcad.analysis.features import Unmeasurable
from hitlcad.analysis.model import Leaf
from hitlcad.core import frames as fr
from hitlcad.core.report import Result

if TYPE_CHECKING:
    import manifold3d

AXIS_MM = 0.1  # the radius about an `overlap` mate's axis a's part keeps clear when its end measures no hole
DEPTH_SAMPLES = 400  # surface points a piece of an overlap is measured through
DEPTH_PIECES = 8  # the largest pieces of an overlap whose depth is measured


@dataclass(frozen=True)
class Allowance:
    """Why two instances may overlap, and the link that bounds how."""

    why: str
    link: Link
    kind: str  # "fit": an interference fit; "set": an `overlap` mate; "thread": a fastener in its into hole


def allowed(found: list[Link]) -> dict[frozenset[str], Allowance]:
    """Instance pairs that may overlap, and why."""
    out: dict[frozenset[str], Allowance] = {}
    for link in found:
        pair = frozenset((link.a.instance, link.b.instance))
        if getattr(link.source, "overlap", False):
            out[pair] = Allowance(f"the {link.source.address} mate declares it", link, "set")
        fit = getattr(link.source, "fit", None)
        if fit is not None and {link.a.spec.kind, link.b.spec.kind} == {"hole", "shaft"}:
            measured = [f.measured.values.get("diameter") for f in (link.a, link.b)]
            if all(measured) and fit.allows_interference(sum(measured) / 2):  # type: ignore[arg-type]
                out[pair] = Allowance(f"the {link.source.address} fit is an interference fit", link, "fit")
        if link.role == "into":
            out[pair] = Allowance(f"{link.a.instance} is threaded into {link.b.instance}", link, "thread")
    return out


def boxes_touch(a: np.ndarray, b: np.ndarray, pad: float) -> bool:
    return bool(np.all(a[0] - pad <= b[1]) and np.all(b[0] - pad <= a[1]))


def overlap(cache: ResultCache, a: Leaf, ta: fr.Frame, b: Leaf, tb: fr.Frame) -> float:
    """Volume (mm³) two leaves share after moving them by ta and tb; cached by what it depends on."""
    ma, mb = ta @ a.transform, tb @ b.transform
    key = cache.key("overlap", a.part.digest, b.part.digest, np.round(ma, 6).tolist(), np.round(mb, 6).tolist())

    def compute() -> float:
        sa = a.part.manifold.transform(ma[:3, :])
        sb = b.part.manifold.transform(mb[:3, :])
        return float((sa ^ sb).volume())

    return float(cache.get_or(key, compute))


def where(shared: manifold3d.Manifold) -> tuple[str, dict[str, float]]:
    """Where an overlap is: its pieces, the box around them, and how deep the deepest of its largest pieces
    is (`_depth`)."""
    pieces = sorted(shared.decompose(), key=lambda p: -p.volume())
    depth = max(_depth(piece) for piece in pieces[:DEPTH_PIECES])
    x0, y0, z0, x1, y1, z1 = shared.bounding_box()
    box = f"x {x0:.2f} … {x1:.2f}, y {y0:.2f} … {y1:.2f}, z {z0:.2f} … {z1:.2f}"
    return (f" in {len(pieces)} piece{'s' if len(pieces) > 1 else ''} within {box}, about {depth:.2f} mm deep",
            {"pieces": len(pieces), "depth_mm": depth})  # fmt: skip


def _depth(piece: manifold3d.Manifold) -> float:
    """A piece's thinnest extent: across the box that fits it closest, or for a ring or skin that no box fits
    closely, the median thickness straight through it."""
    import trimesh

    from hitlcad.analysis.processes import wall_thickness

    mesh = piece.to_mesh()
    solid = trimesh.Trimesh(mesh.vert_properties[:, :3], mesh.tri_verts)
    across = float(trimesh.bounds.oriented_bounds(solid)[1].min())
    _, thickness = wall_thickness(solid, DEPTH_SAMPLES)
    measured = thickness[np.isfinite(thickness)]
    return min(across, float(np.median(measured))) if len(measured) else across


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
                    shared = overlap(ctx.cache, a, fr.IDENTITY, b, fr.IDENTITY)
                except Unmeasurable as e:  # an open mesh
                    out.append(result(self.name, f"{a.path}+{b.path}", None, f"not measurable: {e}", [a.path, b.path]))
                    continue
                if shared <= ctx.settings.overlap_mm3:
                    continue
                allowance = ok_pairs.get(frozenset((a.path, b.path)))
                beyond = allowance and self._beyond(ctx, allowance, a, b, shared)
                if allowance and not beyond:
                    out.append(result(self.name, f"{a.path}+{b.path}", True, f"overlap {shared:.2f} mm³, expected: "
                                      f"{allowance.why}", [a.path, b.path], {"overlap_mm3": shared}, severity="info"))  # fmt: skip
                    continue
                text, values = where(a.manifold ^ b.manifold)
                out.append(result(self.name, f"{a.path}+{b.path}", False, f"{a.path} and {b.path} overlap by "
                                  f"{shared:.2f} mm³{text}" + (f"; {beyond}" if beyond else ""), [a.path, b.path],
                                  {"overlap_mm3": shared, **values}))  # fmt: skip
        if not any(r.status == "fail" for r in out):
            out.append(result(self.name, "interference", True, f"no unexpected overlaps among {len(leaves)} instances "
                              f"({checked} pairs close enough to check)", [], {"pairs_checked": checked}))  # fmt: skip
        return out

    @staticmethod
    def _beyond(ctx: Context, allowance: Allowance, a: Leaf, b: Leaf, shared: float) -> str:
        """How an expected overlap goes beyond what declares it, or '' when it does not."""
        link = allowance.link
        if allowance.kind == "fit":
            hole, shaft = (link.a, link.b) if link.a.spec.kind == "hole" else (link.b, link.a)
            d, big = hole.measured.values["diameter"], shaft.measured.values["diameter"]
            length = min(hole.measured.values["length"], shaft.measured.values["length"])
            facets = hole.measured.uncertainty_mm + shaft.measured.uncertainty_mm
            ring = math.pi / 4 * max((big + facets) ** 2 - (d - facets) ** 2, 0.0) * length
            if shared <= ring + ctx.settings.overlap_mm3:
                return ""
            return (f"more than the {ring:.2f} mm³ the {link.source.address} interference fit makes (Ø{big:.3f} shaft "
                    f"in a Ø{d:.3f} hole over {length:.2f} mm)")  # fmt: skip
        if allowance.kind != "set":
            return ""
        host, inserted = (a, b) if a.path == link.a.instance else (b, a)
        radius = AXIS_MM
        if link.a.spec.kind == "hole" and "diameter" in link.a.measured.values:
            radius = max(link.a.measured.values["diameter"] / 2 - link.a.measured.uncertainty_mm - ctx.tol.position_mm,
                         AXIS_MM)  # fmt: skip
        if (host.manifold ^ _axis_core(link.a.frame, inserted, radius)).volume() <= ctx.settings.overlap_mm3:
            return ""
        return (f"the {link.source.address} mate sets {inserted.path} into a hole of {host.path}, but {host.path} has "
                f"material within {radius:.2f} mm of that hole's axis where {inserted.path} sits: the hole is not cut "
                "there, or not deep enough")  # fmt: skip


def _axis_core(frame: fr.Frame, inserted: Leaf, radius: float) -> manifold3d.Manifold:
    """A cylinder of `radius` about the frame's Z, as long as the inserted leaf reaches along it."""
    import manifold3d

    z = fr.z_axis(frame)
    along = (inserted.vertices - fr.origin(frame)) @ z
    lo, hi = float(along.min()), float(along.max())
    u = np.cross(z, [1.0, 0.0, 0.0] if abs(z[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    placed = np.column_stack([u, np.cross(z, u), z, fr.origin(frame) + lo * z])
    return manifold3d.Manifold.cylinder(hi - lo, radius, radius, 16).transform(placed)
