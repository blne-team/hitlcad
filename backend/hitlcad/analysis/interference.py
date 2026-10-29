"""Interference: no two placed part instances overlap, except where a mate (a fit, or `overlap`) or a
fastener set (a thread in its into hole) declares the overlap it expects. Pairs inside one component
instance are the component's own build's to check.

What a mate declares bounds the overlap. An interference fit makes at most the ring its measured
diameters predict, π/4·(D² − d²) over the engaged length, give or take their facets; a clearance fit
modelled without a gap (an ISO fit at its basic size) only what those facets make. An `overlap` mate
sets b's part into a hole of a's part (an insert, a thread, a pressed pin): the overlap is where b
presses into the hole's wall, and a has no material on the hole's axis where b sits (nor within the
hole's radius, when a's end is a measured hole). A part set into solid material, where its hole was
never cut or is too shallow, overlaps it there too, and that overlap is reported.

An overlap's cause (`Cause`) is the pair's overlap, unless a mate whose frames disagree puts one of the two
elsewhere (that mate's), or the overlap is a fastener's in the host of the part it is threaded into, set where
its hole is not cut (that part's in its host)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from hitlcad.analysis.base import Context, ResultCache, result
from hitlcad.analysis.coverage import Link, links, misplaced, top
from hitlcad.analysis.model import AnalysisSettings, Leaf
from hitlcad.core import frames as fr
from hitlcad.core.report import Cause, Result
from hitlcad.core.structure import Tolerances
from hitlcad.kernel.errors import (
    INTERFERENCE_CLEAR,
    INTERFERENCE_EXPECTED,
    INTERFERENCE_OVERLAP,
    MEASURE_UNMEASURABLE,
    Unmeasurable,
)

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
    kind: str  # "fit", "touch" (a clearance fit), "set" (an `overlap` mate) or "thread" (a fastener's into hole)


def allowed(found: list[Link]) -> dict[frozenset[str], Allowance]:
    """Instance pairs that may overlap, and why."""
    out: dict[frozenset[str], Allowance] = {}
    for link in found:
        pair = frozenset((link.a.instance, link.b.instance))
        if getattr(link.source, "overlap", False):
            out[pair] = Allowance(f"the {link.source.address} mate declares it", link, "set")
        fit = getattr(link.source, "fit", None)
        if fit is not None and {link.a.spec.round, link.b.spec.round} == {"hole", "shaft"}:
            measured = [f.measured.values.get("diameter") for f in (link.a, link.b)]
            if all(measured) and fit.allows_interference(sum(measured) / 2):  # type: ignore[arg-type]
                out[pair] = Allowance(f"the {link.source.address} fit is an interference fit", link, "fit")
            elif all(measured):
                out[pair] = Allowance(f"the {link.source.address} clearance fit's parts touch, to their facets", link,
                                      "touch")  # fmt: skip
        if link.role == "into":
            out[pair] = Allowance(f"{link.a.instance} is threaded into {link.b.instance}", link, "thread")
    return out


def boxes_touch(a: np.ndarray, b: np.ndarray, pad: float) -> bool:
    return bool(np.all(a[0] - pad <= b[1]) and np.all(b[0] - pad <= a[1]))


def touching(boxes: list[np.ndarray], pad: float) -> list[list[int]]:
    """For each box, the later ones (by index, in order) it touches within `pad`. The boxes are swept along x, so
    only those whose x extents meet are compared."""
    near: list[list[int]] = [[] for _ in boxes]
    active: list[int] = []  # boxes whose x extent may still reach the next one's
    for k in sorted(range(len(boxes)), key=lambda k: float(boxes[k][0][0])):
        active = [m for m in active if boxes[m][1][0] >= boxes[k][0][0] - pad]
        for m in active:
            if boxes_touch(boxes[m], boxes[k], pad):
                near[min(m, k)].append(max(m, k))
        active.append(k)
    return [sorted(js) for js in near]


def overlap(cache: ResultCache, a: Leaf, b: Leaf) -> float:
    """Volume (mm³) two leaves share where they sit; cached by their parts and where b sits from a, so a pair that
    moved together (a subassembly placed elsewhere) is not measured again."""
    relative = fr.rigid_inverse(a.transform) @ b.transform
    key = cache.key("overlap", a.part.digest, b.part.digest, np.round(relative, 6).tolist())

    def compute() -> float:
        return float((a.part.manifold ^ b.part.manifold.transform(relative[:3, :])).volume())

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

    from hitlcad.geometry.walls import wall_thickness

    mesh = piece.to_mesh()
    points = np.asarray(mesh.vert_properties[:, :3], dtype=float)
    spread = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
    if spread[-1] <= 1e-9 * max(float(spread[0]), 1.0):  # flat: a sliver where two faces meet, no box fits it
        return 0.0
    solid = trimesh.Trimesh(points, mesh.tri_verts)
    across = float(trimesh.bounds.oriented_bounds(solid)[1].min())
    try:
        walls = wall_thickness(solid, DEPTH_SAMPLES)
    except Unmeasurable:
        return across
    return min(across, float(np.median(walls.thin[: walls.even][walls.measured])))


class Interference:
    name = "interference"
    stage = "assembly"
    title = "Interference"
    summary = "No two parts overlap unless a joint says they should"

    def run(self, ctx: Context) -> list[Result]:
        leaves = ctx.built.leaves()
        found, _ = links(ctx.built)
        ok_pairs = allowed(found)
        astray = misplaced(ctx.built, found)
        out: list[Result] = []
        failed: list[tuple[int, str, str]] = []
        unset: dict[frozenset[str], str] = {}  # a part set into a hole that is not cut, and its host: their cause
        checked = 0
        pad = ctx.tol.position_mm
        near = touching([leaf.bounds for leaf in leaves], pad)
        for i, a in enumerate(leaves):
            for b in (leaves[j] for j in near[i]):
                if top(a.path) == top(b.path):
                    continue
                checked += 1
                try:
                    shared = overlap(ctx.cache, a, b)
                except Unmeasurable as e:  # an open mesh
                    out.append(
                        result(
                            self.name,
                            MEASURE_UNMEASURABLE,
                            f"{a.path}+{b.path}",
                            None,
                            f"not measurable: {e}",
                            [a.path, b.path],
                        )
                    )
                    continue
                if shared <= ctx.settings.overlap_mm3:
                    continue
                allowance = ok_pairs.get(frozenset((a.path, b.path)))
                excess = allowance and beyond(allowance, a, b, shared, ctx.settings, ctx.tol)
                if allowance and not excess:
                    out.append(result(self.name, INTERFERENCE_EXPECTED, f"{a.path}+{b.path}", True, f"overlap {shared:.2f} mm³, expected: "
                                      f"{allowance.why}", [a.path, b.path], {"overlap_mm3": shared}, severity="info"))  # fmt: skip
                    continue
                text, values = where(a.manifold ^ b.manifold)
                cause = astray.get(top(a.path)) or astray.get(top(b.path)) or Cause.overlap(a.path, b.path)
                if allowance and allowance.kind == "set":
                    unset[frozenset((a.path, b.path))] = cause
                failed.append((len(out), a.path, b.path))
                out.append(result(self.name, INTERFERENCE_OVERLAP, f"{a.path}+{b.path}", False, f"{a.path} and {b.path} overlap by "
                                  f"{shared:.2f} mm³{text}" + (f"; {excess}" if excess else ""), [a.path, b.path],
                                  {"overlap_mm3": shared, **values}, cause=cause))  # fmt: skip
        # A fastener threaded into a part set where its hole is not cut overlaps the host for the same reason.
        threaded = {k.a.instance: k.b.instance for k in found if k.role == "into"}
        for n, a, b in failed:
            for x, y in ((a, b), (b, a)):
                if cause := unset.get(frozenset((threaded.get(x, ""), y))):
                    out[n] = out[n].model_copy(update={"cause": cause})
        if not any(r.status == "fail" for r in out):
            out.append(result(self.name, INTERFERENCE_CLEAR, "interference", True, f"no unexpected overlaps among {len(leaves)} instances "
                              f"({checked} pairs close enough to check)", [], {"pairs_checked": checked}))  # fmt: skip
        return out


def beyond(allowance: Allowance, a: Leaf, b: Leaf, shared: float, settings: AnalysisSettings, tol: Tolerances) -> str:
    """How an expected overlap of two leaves goes beyond what declares it, or '' when it does not."""
    link = allowance.link
    if allowance.kind in ("fit", "touch"):
        hole, shaft = (link.a, link.b) if link.a.spec.round == "hole" else (link.b, link.a)
        d, big = hole.measured.values["diameter"], shaft.measured.values["diameter"]
        length = min(hole.measured.values["length"], shaft.measured.values["length"])
        facets = hole.measured.uncertainty_mm + shaft.measured.uncertainty_mm + settings.mesh_tolerance_mm
        if allowance.kind == "fit":
            ring, what = (
                math.pi / 4 * max((big + facets) ** 2 - (d - facets) ** 2, 0.0) * length,
                "interference fit makes",
            )
        else:
            ring, what = math.pi * (d + big) / 2 * facets * length, "clearance fit's facets make where its parts touch"
        if shared <= ring + settings.overlap_mm3:
            return ""
        return (f"more than the {ring:.2f} mm³ the {link.source.address} {what} (Ø{big:.3f} shaft in a Ø{d:.3f} "
                f"hole over {length:.2f} mm)")  # fmt: skip
    if allowance.kind != "set":
        return ""
    host, inserted = (a, b) if a.path == link.a.instance else (b, a)
    radius = AXIS_MM
    if link.a.spec.round == "hole" and "diameter" in link.a.measured.values:
        radius = max(link.a.measured.values["diameter"] / 2 - link.a.measured.uncertainty_mm - tol.position_mm, AXIS_MM)
    if (host.manifold ^ _axis_core(link.a.frame, inserted, radius)).volume() <= settings.overlap_mm3:
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
    placed = fr.on_axis(fr.origin(frame) + lo * z, z)
    return manifold3d.Manifold.cylinder(hi - lo, radius, radius, 16).transform(placed[:3])
