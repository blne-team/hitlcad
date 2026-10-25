"""Manufacturability checks of the manufacturing processes, on one made part in its own frame (the way it
is made). Each process's thresholds are fields of its model in `core.checks`; nothing here is a
tolerance of its own. Findings are warnings: a part that is hard to make still builds, and the agent
and the human see why, and where.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import igl
import numpy as np
import trimesh
from scipy.spatial import ConvexHull, cKDTree

from hitlcad.analysis.features import Features, InsideCorner, Unmeasurable
from hitlcad.analysis.mesh import Probe
from hitlcad.core import frames as fr
from hitlcad.core.checks import (
    CncMillingProcess,
    FdmProcess,
    InjectionMoldingProcess,
    ProcessFinding,
    ResinProcess,
    SheetMetalProcess,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from hitlcad.analysis.model import Body

#: A wall's far side faces back within this angle; a ray that leaves through anything else crossed a
#: corner, not a wall, and so does a ball held by faces turned less far apart. This defines what wall
#: thickness means, not a tolerance.
WALL_EXIT_DEG = 60
#: A thickness reading stands where at least AGREE of the NEIGHBOURS samples nearest it (itself among them)
#: read as thin: a real wall covers an area, one ray glancing off a crease does not.
NEIGHBOURS, AGREE = 7, 3
#: The REFINED thinnest readings in separate places each have the surface within a sample spacing of them
#: sampled again, 1/STEP of their thickness apart (at most PATCH points): a thin spot between the even samples
#: is found, and a narrow one is measured at its thinnest.
REFINED, PATCH, STEP = 16, 8000, 8
#: The largest ball in a wall is found to this share of its radius (a chord this near the ball's diameter is
#: taken as it).
BALL_PRECISION = 0.01
#: Lines across a chord, evenly turned about it, that measure a wall where the ball cannot: a wall met at this
#: many lines is read at most 1 / cos(90° / ACROSS) too thick (2%).
ACROSS = 8
#: Knife edges are traced at points this share of the part's size apart.
KNIFE_STEP = 1e-4
#: A reading up to this many times a knife edge's wedge thickness near it is the wedge's taper: rays and balls
#: meet a wedge's faces at a slant, so they read it somewhat thicker than straight across.
KNIFE_SLACK = 1.25
#: Samples within this many sample spacings of each other are one place: on an even sampling, neighbours lie
#: about a spacing apart, and diagonal ones √2 spacings.
LINK = 1.5
#: A failure names this many places, the worst first.
PLACES = 3


def _fmt(p: Any) -> str:
    return "(" + ", ".join(f"{c:.1f}" for c in p) + ")"


def _listed(places: list[str], one: str, many: str | None = None) -> str:
    """'2 {many}: A, B', the first PLACES of them named."""
    rest = len(places) - PLACES
    noun = one if len(places) == 1 else many or one + "s"
    return f"{len(places)} {noun}: " + ", ".join(places[:PLACES]) + (f" and {rest} more" if rest > 0 else "")


# Even sampling places fewer points than asked on some parts, which measures their walls as well; trimesh
# would warn about it on every build.
logging.getLogger("trimesh.sample").setLevel(logging.ERROR)


# --------------------------------------------------------------------------- walls


@dataclass(frozen=True)
class Walls:
    """Wall thickness over a part's surface, at points spread evenly over it (the first `even`, `spacing` mm
    apart) and then at points sampled densely round the thinnest of them (inf: not measured, where no line
    through the part leaves by a wall). Each point reads, of the NEIGHBOURS samples nearest it, the AGREE-th thinnest (`thin`) and
    the AGREE-th thickest (`thick`): the thickness at least AGREE of them go under, or over. The thinnest
    wall, `thinnest`, is at `at`."""

    points: np.ndarray
    thin: np.ndarray
    thick: np.ndarray
    even: int
    spacing: float
    thinnest: float
    at: np.ndarray

    @property
    def measured(self) -> np.ndarray:
        """Whether each even sample was measured."""
        return np.isfinite(self.thin[: self.even])

    def places(self, readings: np.ndarray, where: np.ndarray, *, thickest: bool = False) -> list[str]:
        """The separate places where `where` holds (a mask over `points`), each as its thinnest (or thickest)
        reading and where that is, the worst place first."""
        index = np.flatnonzero(where)
        worst = np.argmax if thickest else np.argmin
        picks = [
            index[group[worst(readings[index[group]])]] for group in _groups(self.points[index], LINK * self.spacing)
        ]
        picks.sort(key=lambda i: readings[i], reverse=thickest)
        return [f"{readings[i]:.2f} mm at {_fmt(self.points[i])}" for i in picks]


def wall_thickness(mesh: trimesh.Trimesh, samples: int) -> Walls:
    """The walls of a closed mesh at `samples` points spread evenly over its surface, and densely round the
    thinnest; `Unmeasurable` where no line through it leaves by a wall."""
    probe = Probe(mesh)
    points, faces = trimesh.sample.sample_surface_even(mesh, samples, seed=0)
    normals = mesh.face_normals[faces]
    spacing = math.sqrt(mesh.area / len(points))
    readings = _thickness(probe, points, -normals)
    centres: list[int] = []
    for i in np.argsort(readings):
        if len(centres) == REFINED or not np.isfinite(readings[i]):
            break
        if all(np.linalg.norm(points[i] - points[j]) > spacing for j in centres):
            centres.append(i)
    dense, on = _around(mesh, points[centres], spacing, readings[centres] / STEP)
    even = len(points)
    points = np.concatenate([points, dense])
    readings = np.concatenate([readings, _thickness(probe, dense, -mesh.face_normals[on])])
    readings[_at_knife_edge(mesh, points, readings)] = np.inf
    _, near = cKDTree(points).query(points, k=NEIGHBOURS)
    nearby = readings[near]
    thin = np.sort(nearby, axis=1)[:, AGREE - 1]
    thick = np.sort(np.where(np.isfinite(nearby), nearby, -np.inf), axis=1)[:, -AGREE]  # of those measured
    if not np.isfinite(thin).any():
        raise Unmeasurable("no line through it leaves by a wall: it has no wall to measure")
    thinnest = int(np.argmin(thin))
    return Walls(points, thin, thick, even, spacing, float(thin[thinnest]), points[thinnest])


def _at_knife_edge(mesh: trimesh.Trimesh, points: np.ndarray, readings: np.ndarray) -> np.ndarray:
    """Whether each reading is the taper of a knife edge: two faces meeting at under WALL_EXIT_DEG (a hole
    leaving a chamfer, a sliver), the part as thin as that wedge is that far from its edge. A slicer rounds
    such an edge off; it is no wall, at any size. A thin wall ending in one is still thin away from it."""
    knife = mesh.face_adjacency_convex & (mesh.face_adjacency_angles > math.radians(180 - WALL_EXIT_DEG))
    if not knife.any():
        return np.zeros(len(points), dtype=bool)
    ends = mesh.vertices[mesh.face_adjacency_edges[knife]]
    step = KNIFE_STEP * float(np.linalg.norm(mesh.extents))
    along = [np.linspace(a, b, int(np.ceil(np.linalg.norm(b - a) / step)) + 1) for a, b in ends]
    distance, nearest = cKDTree(np.concatenate(along)).query(points)
    wedge = 2 * np.tan((math.pi - np.repeat(mesh.face_adjacency_angles[knife], [len(x) for x in along])) / 2)
    return readings <= KNIFE_SLACK * wedge[nearest] * (distance + step)


def _thickness(probe: Probe, points: np.ndarray, inward: np.ndarray) -> np.ndarray:
    """The wall thickness at each point: straight through the part along `inward` (the chord), unless the
    wall where the chord runs is narrower: a chord along a shell, from its rim, or across a wall met at a
    slant, is longer than the wall it is in. That wall is the largest ball touching the surface nearest the
    chord's middle and held by the surface across from there; near an edge, where the face beside holds it,
    the narrowest line across the chord there."""
    chord, _ = _through(probe, points, inward)
    through = np.flatnonzero(np.isfinite(chord))
    middle = points[through] + inward[through] * (chord[through, None] / 2)
    depth, _, touch = probe.nearest(middle)
    # where the middle is half the chord from the surface, the chord is that ball's diameter
    near = depth < chord[through] / 2 * (1 - BALL_PRECISION)
    along, middle, depth, touch = through[near], middle[near], depth[near], touch[near]
    wall = 2 * np.maximum(_ball(probe, touch, (middle - touch) / depth[:, None], chord[along] / 2), depth)
    beside = np.isinf(wall)
    wall[beside] = _across(probe, middle[beside], inward[along[beside]])
    thickness = chord.copy()
    thickness[along] = np.minimum(chord[along], wall)
    return thickness


def _through(probe: Probe, points: np.ndarray, directions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How far each ray runs inside the part before it leaves through a face turned back towards it (inf
    where it leaves through anything else: it crossed a corner, not a wall), and that face's normal."""
    face, length = probe.first_hit(points, directions)
    normal = probe.mesh.face_normals[face]  # where it leaves nothing (-1), read as leaving through a corner
    through = (face >= 0) & (np.einsum("ij,ij->i", normal, directions) >= math.cos(math.radians(WALL_EXIT_DEG)))
    return np.where(through, length, np.inf), normal


def _across(probe: Probe, points: np.ndarray, chords: np.ndarray) -> np.ndarray:
    """The narrowest the part is through each point across its chord: of ACROSS lines through it,
    perpendicular to the chord and evenly turned, the shortest between two faces turned away from each other
    (a line through a corner leaves through two faces at an angle)."""
    u, v = fr.across(chords)
    widths = []
    for angle in np.arange(ACROSS) * math.pi / ACROSS:
        line = math.cos(angle) * u + math.sin(angle) * v
        (ahead, front), (behind, back) = _through(probe, points, line), _through(probe, points, -line)
        apart = np.einsum("ij,ij->i", front, -back) >= math.cos(math.radians(WALL_EXIT_DEG))
        widths.append(np.where(apart, ahead + behind, np.inf))
    return np.min(widths, axis=0)


def _ball(probe: Probe, at: np.ndarray, into: np.ndarray, radius: np.ndarray) -> np.ndarray:
    """The radius of the largest ball inside the part touching its surface at `at`, centred along `into`, if
    no larger than `radius` and held by the surface across from `at` (inf where it is not: held beside where it
    touches, by a corner, it measures no wall). The shrinking ball: while the surface point nearest its centre
    is inside it, it shrinks to the ball through that point (Ma, Bae and Choi, 2012)."""
    radius, far = radius.copy(), np.full_like(at, np.nan)
    shrinking = np.arange(len(at))
    while len(shrinking):
        gap, _, touch = probe.nearest(at[shrinking] + into[shrinking] * radius[shrinking, None])
        inside = gap < radius[shrinking] * (1 - BALL_PRECISION)
        shrinking, touch = shrinking[inside], touch[inside]
        chord = touch - at[shrinking]
        radius[shrinking] = np.einsum("ij,ij->i", chord, chord) / (2 * np.einsum("ij,ij->i", into[shrinking], chord))
        far[shrinking] = touch
    facing = np.einsum("ij,ij->i", at + into * radius[:, None] - far, -into) / radius  # NaN: never shrunk
    return np.where(facing >= math.cos(math.radians(WALL_EXIT_DEG)), radius, np.inf)


def _around(
    mesh: trimesh.Trimesh, centres: np.ndarray, radius: float, spacing: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Points on the surface within `radius` of each centre, about its `spacing` apart (at most PATCH), and the
    faces they lie on."""
    reach = np.linalg.norm(mesh.triangles - mesh.triangles_center[:, None], axis=2).max(axis=1)
    points, faces = [], []
    for centre, step in zip(centres, spacing, strict=True):
        near = np.flatnonzero(np.linalg.norm(mesh.triangles_center - centre, axis=1) <= radius + reach)
        # split into triangles no longer than the radius, so a large face is sampled only where it is near
        vertices, triangles, index = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces[near], radius,
                                                                      return_index=True)  # fmt: skip
        patch = trimesh.Trimesh(vertices, triangles, process=False)
        # a triangle no longer than the radius that reaches into the circle has its centre within radius / √3 more
        keep = np.flatnonzero(
            np.linalg.norm(patch.triangles_center - centre, axis=1) <= radius * (1 + 1 / math.sqrt(3))
        )
        patch = patch.submesh([keep], append=True)
        at, face = trimesh.sample.sample_surface_even(patch, int(min(PATCH, np.ceil(patch.area / step**2))), seed=0)
        inside = np.linalg.norm(at - centre, axis=1) <= radius
        points.append(at[inside])
        faces.append(near[index[keep[face[inside]]]])
    return np.concatenate(points), np.concatenate(faces)


def _groups(points: np.ndarray, reach: float) -> list[np.ndarray]:
    """Indices of the points in groups linked by steps no longer than `reach`, the largest group first."""
    import scipy.sparse
    from scipy.sparse.csgraph import connected_components

    pairs = cKDTree(points).query_pairs(reach, output_type="ndarray")
    links = scipy.sparse.coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(points),) * 2)
    _, labels = connected_components(links, directed=False)
    return sorted((np.flatnonzero(labels == k) for k in np.unique(labels)), key=len, reverse=True)


# --------------------------------------------------------------------------- surface regions


def _regions(mesh: trimesh.Trimesh, where: np.ndarray) -> list[np.ndarray]:
    """The faces where `where` holds, in regions of faces sharing edges, the largest first."""
    pairs = mesh.face_adjacency[where[mesh.face_adjacency].all(axis=1)]
    regions = [np.asarray(r) for r in trimesh.graph.connected_components(pairs, nodes=np.flatnonzero(where))]
    return sorted(regions, key=lambda r: -mesh.area_faces[r].sum())


def _region(mesh: trimesh.Trimesh, faces: np.ndarray) -> str:
    """A region's area and where it is: its face nearest its middle."""
    middle = np.average(mesh.triangles_center[faces], axis=0, weights=mesh.area_faces[faces])
    at = mesh.triangles_center[faces[np.argmin(np.linalg.norm(mesh.triangles_center[faces] - middle, axis=1))]]
    return f"{mesh.area_faces[faces].sum():.0f} mm² at {_fmt(at)}"


def _split(mesh: trimesh.Trimesh, size: float) -> trimesh.Trimesh:
    """The surface in triangles no longer than `size`, so a large face is tested all over, not at one point."""
    vertices, faces = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces, size)
    split = trimesh.Trimesh(vertices, faces, process=False)
    split.merge_vertices()
    return split


def _open(probe: Probe, mesh: trimesh.Trimesh, direction: tuple[float, float, float]) -> np.ndarray:
    """Whether a straight path leaves each face of `mesh` along `direction` without meeting the part (`probe`)."""
    toward = np.tile(np.asarray(direction, dtype=np.float64), (len(mesh.faces), 1))
    face, _ = probe.first_hit(mesh.triangles_center + mesh.face_normals * probe.offset, toward)
    return face < 0


def _spacing(body: Body) -> float:
    """The spacing of the samples the surface is probed at."""
    return math.sqrt(body.mesh.area / body.settings.wall_samples)


def _on_bed(body: Body, mesh: trimesh.Trimesh) -> np.ndarray:
    """Whether each face of `mesh` (the body's surface, or a finer split of it) lies on the bed or the machine
    table: none of it higher than the body's lowest point by more than the mesh's chordal deviation, which is
    how far the facets of a flat base can lie off the face they stand for."""
    return mesh.triangles[:, :, 2].max(axis=1) <= body.mesh.bounds[0][2] + body.settings.mesh_tolerance_mm


# --------------------------------------------------------------------------- FDM


def fdm_checks(body: Body, rules: FdmProcess) -> list[ProcessFinding]:
    walls = _walls(body, "fdm_wall_thickness", rules.min_wall_mm, (rules.nozzle_mm, "nozzle"))
    return [walls, _fdm_overhangs(body, rules), _fdm_bed(body, rules)]


def _walls(body: Body, name: str, minimum: float, cannot: tuple[float, str] | None = None) -> ProcessFinding:
    """No wall thinner than `minimum`; under `cannot` (a size and what it is), the wall does not form at all."""
    try:
        walls = body.walls
    except Unmeasurable as e:
        return _unverified(name, e)
    value = walls.thinnest
    if value >= minimum:
        return ProcessFinding(name=name, passed=True, detail=f"thinnest wall {value:.2f} mm (minimum {minimum:g} mm)")
    thin = walls.thin < minimum
    share = float(np.mean(thin[: walls.even][walls.measured]))
    worse = (
        f", thinner than the {cannot[0]:g} mm {cannot[1]}: it will not print" if cannot and value < cannot[0] else ""
    )
    return ProcessFinding(name=name, passed=False,
                          detail=f"thinnest wall {value:.2f} mm{worse}; under the {minimum:g} mm minimum in "
                                 f"{_listed(walls.places(walls.thin, thin), 'place')} "
                                 f"({f'{share:.0%}' if share >= 0.01 else 'under 1%'} of the surface)")  # fmt: skip


def _fdm_overhangs(body: Body, rules: FdmProcess) -> ProcessFinding:
    """Faces turned down more steeply than the steepest overhang print only on what holds them: a bridge between
    two supports up to `max_bridge_mm` long (the top of a horizontal hole is one), an edge within a nozzle's
    width of what is under it, or else support."""
    mesh, tolerance = body.mesh, body.settings.mesh_tolerance_mm
    on_bed = _on_bed(body, mesh)
    steep = (mesh.face_normals[:, 2] < -math.sin(math.radians(rules.max_overhang_deg))) & ~on_bed
    angle = f"more than {rules.max_overhang_deg:g}° from vertical"
    if rules.supports:
        if not steep.any():
            return ProcessFinding(name="fdm_overhangs", passed=True, detail=f"no overhang {angle}")
        return ProcessFinding(name="fdm_overhangs", passed=True,
                              detail=f"{mesh.area_faces[steep].sum():.0f} mm² overhangs {angle} and prints on support, "
                                     f"{_listed([_region(mesh, r) for r in _regions(mesh, steep)], 'region')}")  # fmt: skip
    unheld = [u for faces in _regions(mesh, steep) if (u := _unheld(mesh, faces, on_bed, rules, tolerance))]
    if not unheld:
        bridged = " (each bridged or held at its edge)" if steep.any() else ""
        return ProcessFinding(name="fdm_overhangs", passed=True, detail=f"no overhang {angle} needs support{bridged}")
    return ProcessFinding(name="fdm_overhangs", passed=False,
                          detail=f"overhangs {angle} print on nothing, {_listed(unheld, 'region')}; chamfer them, "
                                 "turn the part in its own frame or split it")  # fmt: skip


def _unheld(mesh: trimesh.Trimesh, faces: np.ndarray, on_bed: np.ndarray, rules: FdmProcess, tolerance: float) -> str:
    """What of one overhanging region prints on nothing, and where ('' if all of it prints). A point of it is
    held by its supports (its edges where the surface goes on down, or onto the bed): within a nozzle's width
    of one, or between two on either side of it no more than `max_bridge_mm` apart. Measured seen from above."""
    adjacency = mesh.face_adjacency
    inside = np.isin(adjacency, faces)
    edge = np.flatnonzero(inside[:, 0] != inside[:, 1])
    side = inside[edge, 0].astype(int)  # the column of the face outside the region
    outer, beyond = adjacency[edge, side], mesh.face_adjacency_unshared[edge, side]
    ends = mesh.vertices[mesh.face_adjacency_edges[edge]]
    held = on_bed[outer] | (mesh.vertices[beyond, 2] < ends[:, :, 2].min(axis=1) - tolerance)
    step = rules.nozzle_mm
    supports = np.concatenate([np.linspace(a, b, int(np.ceil(np.linalg.norm(b - a) / step)) + 1)
                               for a, b in ends[held]] or [np.empty((0, 3))])[:, :2]  # fmt: skip
    region = mesh.submesh([faces], append=True)
    points, _ = trimesh.sample.sample_surface_even(region, int(np.ceil(region.area / step**2)) + 1, seed=0)
    nearest = np.full(len(points), np.inf)
    span = np.full(len(points), np.inf)
    for start in range(0, len(points) if len(supports) else 0, 1024):
        chunk = slice(start, start + 1024)
        offsets = supports[None] - points[chunk, None, :2]  # point → support
        distance = np.linalg.norm(offsets, axis=2)
        first = np.argmin(distance, axis=1)
        rows = np.arange(len(first))
        across = np.einsum("ijk,ik->ij", offsets, offsets[rows, first]) <= 0  # supports on its other side
        nearest[chunk] = distance[rows, first]
        span[chunk] = nearest[chunk] + np.where(across, distance, np.inf).min(axis=1)
    unheld = (nearest > rules.nozzle_mm) & (span > rules.max_bridge_mm)
    area = region.area * float(np.mean(unheld))
    if area <= rules.ignored_overhang_mm2:
        return ""
    worst = int(np.argmax(np.where(unheld, nearest, -1)))
    if not len(supports):
        what = "with nothing under it"
    elif np.isfinite(span[unheld]).all():
        what = f"bridging {span[unheld].max():.1f} mm (up to {rules.max_bridge_mm:g} mm prints)"
    else:
        what = f"reaching {nearest[unheld].max():.1f} mm past what holds it"
    return f"{area:.0f} mm² at {_fmt(points[worst])} {what}"


def _fdm_bed(body: Body, rules: FdmProcess) -> ProcessFinding:
    """It stands on its flat base on the bed: a base that covers enough of its outline seen from above, and is
    wide enough for its height (or a brim holds it)."""
    mesh = body.mesh
    flat_down = mesh.face_normals[:, 2] < -math.cos(math.radians(body.tolerances.angle_deg))
    contact = flat_down & _on_bed(body, mesh)
    if not contact.any():
        lowest = mesh.vertices[np.argmin(mesh.vertices[:, 2])]
        return ProcessFinding(name="fdm_bed_contact", passed=False,
                              detail=f"no face of it lies flat on the bed: it stands on its lowest point {_fmt(lowest)}, "
                                     "and may detach: give it a flat base there")  # fmt: skip
    base = ConvexHull(mesh.triangles[contact][:, :, :2].reshape(-1, 2))
    share = base.volume / ConvexHull(mesh.vertices[:, :2]).volume  # a 2D hull's volume is its area
    edges = base.points[base.vertices] - np.roll(base.points[base.vertices], 1, axis=0)
    normals = np.column_stack([edges[:, 1], -edges[:, 0]]) / np.linalg.norm(edges, axis=1, keepdims=True)
    width = float(np.min(np.ptp(base.points[base.vertices] @ normals.T, axis=0)))  # its narrowest, across an edge
    tall = float(mesh.extents[2]) / width
    detail = (
        f"{mesh.area_faces[contact].sum():.0f} mm² on the bed; its base spans {share:.0%} of its outline seen "
        f"from above and is {width:.1f} mm across at its narrowest, {tall:.1f} × that tall"
    )
    problems = []
    if share < rules.min_bed_contact_share:
        problems.append(
            f"under the {rules.min_bed_contact_share:.0%} it needs to stay put: widen the base where it "
            f"stands {_fmt(base.points[base.vertices].mean(axis=0))}, or add a brim"
        )
    if tall > rules.max_height_to_base and not rules.brim:
        problems.append(
            f"over {rules.max_height_to_base:g} × as tall as its base is wide, it may tip or wobble "
            "loose: print it with a brim (brim: true), widen its base or lay it down"
        )
    return ProcessFinding(name="fdm_bed_contact", passed=not problems, detail="; ".join([detail, *problems]))


# --------------------------------------------------------------------------- resin


def resin_checks(body: Body, rules: ResinProcess) -> list[ProcessFinding]:
    return [_walls(body, "resin_wall_thickness", rules.min_wall_mm), _resin_drains(body, rules)]


#: Most grid points resin drains are traced on; a larger part is traced on a coarser grid.
DRAIN_GRID = 2_000_000
#: Grid steps across the narrowest drain, at least: what a drain's width is traced to.
DRAIN_STEPS = 8


def _resin_drains(body: Body, rules: ResinProcess) -> ProcessFinding:
    """A hollow inside the part fills with resin that drains only through an opening at least `min_drain_mm`
    across; without one, the resin stays in, uncured. Traced on a grid: the open points, at least half the
    drain's width from the part, that cannot reach the outside without passing nearer to it."""
    from scipy import ndimage

    mesh, drain = body.mesh, rules.min_drain_mm
    sealed = [shell for shell in mesh.split(only_watertight=False) if shell.is_watertight and shell.volume < 0]
    lo, hi = mesh.bounds[0] - drain, mesh.bounds[1] + drain
    step = max(drain / DRAIN_STEPS, float(np.prod(hi - lo) / DRAIN_GRID) ** (1 / 3))
    axes = [np.arange(a, b + step, step) for a, b in zip(lo, hi, strict=True)]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    probe = Probe(mesh)
    air = igl.fast_winding_number(probe.vertices, probe.faces, grid) < 0.5
    room = np.zeros(len(grid), dtype=bool)
    # a drain's width reaches between grid points that are within half a step of its axis or so
    room[air] = probe.nearest(grid[air])[0] >= drain / 2 - step / math.sqrt(2)
    labels, count = ndimage.label(room.reshape([len(a) for a in axes]))
    outside = labels[0, 0, 0]  # the grid's corner is a drain's width clear of the part
    flat = labels.ravel()
    trapped = [grid[flat == k] for k in range(1, count + 1) if k != outside]
    trapped = [p for p in trapped if not any(np.all((s.bounds[0] <= p.mean(axis=0)) & (p.mean(axis=0) <= s.bounds[1]))
                                             for s in sealed)]  # fmt: skip
    # a trapped hollow reaches as far past its points as they are clear of the part
    clear = drain / 2 - step / math.sqrt(2)
    hollows = [f"{_size(s.extents)} sealed at {_fmt(s.centroid)}" for s in sorted(sealed, key=lambda s: s.volume)]
    hollows += [f"{_size(np.ptp(p, axis=0) + 2 * clear)} at {_fmt(p.mean(axis=0))}"
                for p in sorted(trapped, key=len, reverse=True)]  # fmt: skip
    if not hollows:
        return ProcessFinding(name="resin_drains", passed=True,
                              detail=f"every hollow drains through an opening at least {drain:g} mm across")  # fmt: skip
    return ProcessFinding(name="resin_drains", passed=False,
                          detail=f"hollows with no drain at least {drain:g} mm across trap uncured resin, "
                                 f"{_listed(hollows, 'hollow')}: open each with a drain hole")  # fmt: skip


# --------------------------------------------------------------------------- corners


def _size(extents: np.ndarray) -> str:
    return " × ".join(f"{e:.0f}" for e in extents) + " mm"


def _features(body: Body) -> Features:
    (leaf,) = body.leaves
    return leaf.part.features


def _unverified(name: str, why: Unmeasurable) -> ProcessFinding:
    return ProcessFinding(name=name, passed=None, detail=str(why))


def _vertical(corner: InsideCorner, tol_deg: float) -> bool:
    return abs(corner.axis[2]) / float(np.linalg.norm(corner.axis)) >= math.cos(math.radians(tol_deg))


def _corners(corners: list[InsideCorner], what: Callable[[InsideCorner], str], one: str, many: str) -> str:
    return _listed([f"{what(c)} at {_fmt(c.at)}" for c in corners], one, many)


# --------------------------------------------------------------------------- CNC milling


def cnc_milling_checks(body: Body, rules: CncMillingProcess) -> list[ProcessFinding]:
    mesh, tol = body.mesh, body.tolerances
    # A cutter from +Z reaches a face only along a clear path up from it: not one facing down (the top of a
    # horizontal hole), nor one under an overhang. The bottom is the face the part is held by.
    split = _split(mesh, _spacing(body))
    bottom = (split.face_normals[:, 2] < 0) & _on_bed(body, split)
    hidden = [r for r in _regions(split, ~_open(Probe(mesh), split, (0, 0, 1)) & ~bottom)
              if split.area_faces[r].sum() > rules.ignored_area_mm2]  # fmt: skip
    setups = ProcessFinding(name="cnc_setups", passed=not hidden,
                            detail="every face is reachable from +Z or is the bottom" if not hidden else
                            f"faces no cutter from +Z reaches need a second setup, "
                            f"{_listed([_region(split, r) for r in hidden], 'region')}")  # fmt: skip
    try:
        corners = [c for c in _features(body).inside_corners(tol) if _vertical(c, tol.angle_deg)]
    except Unmeasurable as e:
        return [_unverified("cnc_inside_corners", e), _unverified("cnc_pocket_depth", e), setups]
    tool_r = rules.tool_diameter_mm / 2
    sharp = [c for c in corners if c.radius == 0]
    small = sorted((c for c in corners if 0 < c.radius < tool_r), key=lambda c: c.radius)
    reach = ProcessFinding(
        name="cnc_inside_corners", passed=not sharp and not small,
        detail="every vertical inside corner has at least the tool radius" if not sharp and not small else
        "; ".join(filter(None, [
            _corners(sharp, lambda c: f"{c.extent:.1f} mm long", "sharp vertical inside corner",
                     "sharp vertical inside corners") if sharp else "",
            _corners(small, lambda c: f"r={c.radius:.2f}", f"inside radius under the {tool_r:g} mm tool radius",
                     f"inside radii under the {tool_r:g} mm tool radius") if small else "",
        ])) + f": a {rules.tool_diameter_mm:g} mm cutter cannot reach them",
    )  # fmt: skip

    def cutter(corner: InsideCorner) -> float:
        """The cutter that finishes a corner: its own diameter, or the tool's where it is sharp."""
        return 2 * corner.radius or rules.tool_diameter_mm

    deep = sorted((c for c in corners if c.extent > rules.max_depth_ratio * cutter(c)),
                  key=lambda c: -c.extent / cutter(c))  # fmt: skip
    depth = ProcessFinding(
        name="cnc_pocket_depth", passed=not deep,
        detail="no pocket deeper than its corner allows" if not deep else
        f"pockets deeper than {rules.max_depth_ratio:g} × the cutter that finishes their corners, "
        + _corners(deep, lambda c: f"{c.extent:.1f} mm deep for a {cutter(c):g} mm cutter", "corner", "corners"),
    )  # fmt: skip
    return [reach, depth, setups]


# --------------------------------------------------------------------------- sheet metal


def sheet_metal_checks(body: Body, rules: SheetMetalProcess) -> list[ProcessFinding]:
    try:
        walls = body.walls
    except Unmeasurable as e:
        thickness = _unverified("sheet_thickness", e)
    else:
        median = float(np.median(walls.thin[: walls.even][walls.measured]))
        off = np.abs(walls.thin - rules.thickness_mm) > rules.thickness_tolerance_mm
        ok = all(abs(v - rules.thickness_mm) <= rules.thickness_tolerance_mm for v in (walls.thinnest, median))
        thickness = ProcessFinding(name="sheet_thickness", passed=ok,
                                   detail=f"thinnest {walls.thinnest:.2f} mm, median {median:.2f} mm (sheet "
                                          f"{rules.thickness_mm:g} ± {rules.thickness_tolerance_mm:g} mm)"
                                          + ("" if ok else f"; off the sheet's thickness "
                                             f"in {_listed(walls.places(walls.thin, off), 'place')}"))  # fmt: skip
    try:
        bends = _features(body).bends(rules.thickness_mm, rules.thickness_tolerance_mm, body.tolerances)
    except Unmeasurable as e:
        return [thickness, _unverified("sheet_bend_radius", e)]
    sharp = [b for b in bends if b.radius == 0]
    tight = sorted((b for b in bends if 0 < b.radius < rules.min_bend_radius_mm), key=lambda b: b.radius)
    ok = not sharp and not tight
    radius = ProcessFinding(
        name="sheet_bend_radius", passed=ok,
        detail=(f"{len(bends)} bend{'s' if len(bends) != 1 else ''}, every inside radius at least "
                f"{rules.min_bend_radius_mm:g} mm") if ok else
        "; ".join(filter(None, [
            _corners(sharp, lambda b: f"{b.extent:.1f} mm long", "sharp inside bend", "sharp inside bends") if sharp else "",
            _corners(tight, lambda b: f"r={b.radius:.2f}", f"bend tighter than {rules.min_bend_radius_mm:g} mm",
                     f"bends tighter than {rules.min_bend_radius_mm:g} mm") if tight else "",
        ])),
    )  # fmt: skip
    return [thickness, radius]


# --------------------------------------------------------------------------- injection molding


def injection_molding_checks(body: Body, rules: InjectionMoldingProcess) -> list[ProcessFinding]:
    return [_molding_draft(body, rules), _molding_undercuts(body, rules), _molding_walls(body, rules)]


def _molding_draft(body: Body, rules: InjectionMoldingProcess) -> ProcessFinding:
    """Faces along the pull drag on the mold unless drafted. A facet is undrafted where the surface is, all over
    it: its corners' normals, averaged over the facets of one smooth face (libigl's corner normals, creases
    over 20° kept), are within the draft of horizontal too. A curved face that only turns through vertical, as
    a ball does at its parting line, slides free."""
    mesh = body.mesh
    level = math.sin(math.radians(rules.min_draft_deg))
    corners = igl.per_corner_normals(
        np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64)
    )
    undrafted = (np.abs(mesh.face_normals[:, 2]) < level) & (np.abs(corners[:, 2]).reshape(-1, 3) < level).all(axis=1)
    drag = [r for r in _regions(mesh, undrafted) if mesh.area_faces[r].sum() > rules.ignored_area_mm2]
    if not drag:
        return ProcessFinding(name="molding_draft", passed=True,
                              detail=f"every wall has at least {rules.min_draft_deg:g}° of draft along Z")  # fmt: skip
    return ProcessFinding(name="molding_draft", passed=False,
                          detail=f"walls with under {rules.min_draft_deg:g}° of draft along Z drag on the mold, "
                                 f"{_listed([_region(mesh, r) for r in drag], 'region')}")  # fmt: skip


def _molding_undercuts(body: Body, rules: InjectionMoldingProcess) -> ProcessFinding:
    """A two-plate mold opens along Z: each face is formed by the half it faces and must leave it on a clear
    path, up for a face turned up, down for one turned down, either way for one along the pull."""
    mesh = body.mesh
    split, probe = _split(mesh, _spacing(body)), Probe(mesh)
    up, down = _open(probe, split, (0, 0, 1)), _open(probe, split, (0, 0, -1))
    level = math.sin(math.radians(rules.min_draft_deg))
    z = split.face_normals[:, 2]
    caught = np.where(z >= level, ~up, np.where(z <= -level, ~down, ~up & ~down))
    under = [r for r in _regions(split, caught) if split.area_faces[r].sum() > rules.ignored_area_mm2]
    if not under:
        return ProcessFinding(name="molding_undercuts", passed=True, detail="every face leaves its mold half along Z")
    return ProcessFinding(name="molding_undercuts", passed=False,
                          detail=f"undercuts, faces caught behind the part as the mold opens along Z, "
                                 f"{_listed([_region(split, r) for r in under], 'region')}: each needs a side "
                                 "action, or reshape it to open along Z")  # fmt: skip


def _molding_walls(body: Body, rules: InjectionMoldingProcess) -> ProcessFinding:
    """Walls stay within `max_wall_ratio` of the typical (median) wall; thicker sections cool late, sink and warp."""
    try:
        walls = body.walls
    except Unmeasurable as e:
        return _unverified("molding_walls", e)
    median = float(np.median(walls.thin[: walls.even][walls.measured]))
    limit = rules.max_wall_ratio * median
    measured = np.isfinite(walls.thick)
    thick = measured & (walls.thick > limit)
    thick[walls.even :] = False
    area = body.mesh.area * float(np.mean(thick[: walls.even][walls.measured]))
    thickest = float(walls.thick[: walls.even][measured[: walls.even]].max())
    if area <= rules.ignored_area_mm2:
        return ProcessFinding(name="molding_walls", passed=True,
                              detail=f"walls from {walls.thinnest:.2f} to {thickest:.2f} mm, median {median:.2f} mm "
                                     f"(limit {rules.max_wall_ratio:g} × the median)")  # fmt: skip
    return ProcessFinding(name="molding_walls", passed=False,
                          detail=f"{area:.0f} mm² of the part is over {rules.max_wall_ratio:g} × its median wall "
                                 f"({median:.2f} mm) in {_listed(walls.places(walls.thick, thick, thickest=True), 'place')}: "
                                 "thick sections sink and warp; core them out to the walls' thickness")  # fmt: skip
