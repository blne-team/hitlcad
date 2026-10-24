"""FDM helpers shared by the part files."""

import math

from build123d import BuildPart, BuildSketch, Circle, Location, Mode, Plane, Polygon, Vector, extrude

TEARDROP_DEG = 40  # the roof's slope from vertical: under the 45° overhang limit


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
