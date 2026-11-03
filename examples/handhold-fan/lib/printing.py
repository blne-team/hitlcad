"""FDM helpers shared by the part files."""

import math

from build123d import BuildPart, BuildSketch, Circle, GeomType, Location, Mode, Plane, Polygon, Vector, chamfer, extrude

TEARDROP_DEG = 40  # the roof's slope from vertical: under the 45° overhang limit
FIRST_LAYER = 0.3  # mm: a chamfer past the first layer's squash, where a fit meets the bed


def teardrop(at, d, depth):
    """A horizontal hole of diameter d drilled `depth` along -Z of the frame `at` (Z out of its opening, Z
    horizontal), with a pointed roof towards the part's +Z so it prints without support."""
    a = Plane(at).z_dir
    if abs(a.Z) > 1e-6:
        raise ValueError("teardrop holes are for horizontal holes")
    origin = at.position
    x_dir = Vector(0, 0, 1).cross(-a)
    r, t = d / 2, math.radians(TEARDROP_DEG)
    with BuildPart() as hole:
        with BuildSketch(Plane(origin=origin, x_dir=x_dir, z_dir=-a)):
            Circle(r)
            Polygon((r * math.cos(t), r * math.sin(t)), (0, r / math.sin(t)), (-r * math.cos(t), r * math.sin(t)), (0, 0), align=None)
        extrude(amount=depth)
    return hole.part


def insert_hole(at, insert_len, depth, d=4.09, d_tip=3.4):
    """A teardrop hole for a CNC Kitchen M3 × 5.7 heat-set insert, opening at `at` (Z out of it), and the
    frame where the insert's placement goes (as bd_warehouse's InsertHole records it)."""
    hole = teardrop(at, d, insert_len) + teardrop(at * Location((0, 0, -insert_len)), d_tip, depth - insert_len)
    return hole, at * Location((0, 0, -insert_len))


def chamfer_on_bed(part, radius, size=FIRST_LAYER):
    """Chamfer the round edge of `radius` where the part being built (`part`, a BuildPart) stands on the bed (Z = 0):
    the first layer, pressed onto the bed, spreads past its outline, and a fit there keeps its size past it."""
    edges = [e for e in part.edges().filter_by(GeomType.CIRCLE)
             if abs(e.position_at(0).Z) < 1e-6 and abs(e.radius - radius) < 1e-6]  # fmt: skip
    if not edges:
        raise ValueError(f"no round edge of radius {radius:g} on the bed")
    chamfer(edges, length=size)
