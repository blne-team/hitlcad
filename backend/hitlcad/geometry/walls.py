"""Wall thickness over a part's surface: part of the measuring toolkit, which the manufacturing processes'
checks, the wall requirement and interference all read."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from hitlcad.core import frames as fr
from hitlcad.geometry.mesh import Probe
from hitlcad.geometry.sampling import surface_patch
from hitlcad.kernel.errors import Unmeasurable

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
NO_WALL = "no line through it leaves by a wall: it has no wall to measure"


def fmt(p: Any) -> str:
    return "(" + ", ".join(f"{c:.1f}" for c in p) + ")"


def listed(places: list[str], one: str, many: str | None = None) -> str:
    """'2 {many}: A, B', the first PLACES of them named."""
    rest = len(places) - PLACES
    noun = one if len(places) == 1 else many or one + "s"
    return f"{len(places)} {noun}: " + ", ".join(places[:PLACES]) + (f" and {rest} more" if rest > 0 else "")


# Even sampling places fewer points than asked on some parts, which measures their walls as well; trimesh
# would warn about it on every build.
logging.getLogger("trimesh.sample").setLevel(logging.ERROR)


@dataclass(frozen=True)
class Walls:
    """Wall thickness over a part's surface, at points spread evenly over it (the first `even`, `spacing` mm
    apart) and then at points sampled densely round the thinnest of them (inf: not measured, where no line
    through the part leaves by a wall). Each point reads, of the NEIGHBOURS samples nearest it, the AGREE-th
    thinnest (`thin`) and the AGREE-th thickest (`thick`): the thickness at least AGREE of them go under, or
    over. The thinnest wall, `thinnest`, is at `at`."""

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
        return [f"{readings[i]:.2f} mm at {fmt(self.points[i])}" for i in picks]


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
    if not centres:
        raise Unmeasurable(NO_WALL)
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
        raise Unmeasurable(NO_WALL)
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
    points, faces = [], []
    for centre, step in zip(centres, spacing, strict=True):
        patch, source = surface_patch(mesh, centre, radius)
        at, face = trimesh.sample.sample_surface_even(patch, int(min(PATCH, np.ceil(patch.area / step**2))), seed=0)
        inside = np.linalg.norm(at - centre, axis=1) <= radius
        points.append(at[inside])
        faces.append(source[face[inside]])
    return np.concatenate(points), np.concatenate(faces)


def _groups(points: np.ndarray, reach: float) -> list[np.ndarray]:
    """Indices of the points in groups linked by steps no longer than `reach`, the largest group first."""
    import scipy.sparse
    from scipy.sparse.csgraph import connected_components

    pairs = cKDTree(points).query_pairs(reach, output_type="ndarray")
    links = scipy.sparse.coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(points),) * 2)
    _, labels = connected_components(links, directed=False)
    return sorted((np.flatnonzero(labels == k) for k in np.unique(labels)), key=len, reverse=True)
