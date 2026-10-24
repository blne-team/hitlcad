"""Involute spur gears: outlines and extruded gears, in the gear's own frame (axis Z, teeth from Z=0)."""

import math

from build123d import Box, Cylinder, Edge, Face, Pos, Wire, extrude


def _inv(a):
    return math.tan(a) - a


def _flank_angle(r, rb, half_at_base):
    """Polar half-angle of an involute flank at radius r, measured from the tooth centre."""
    return half_at_base - (_inv(math.acos(rb / r)) if r > rb else 0.0)


def _polar(r, a):
    return (r * math.cos(a), r * math.sin(a))


def gear_outline(z, m, pa, thickness, tip_r, root_r, n, phase=0.0):
    """Closed wire of an involute spur outline. `thickness` is the tooth thickness on the pitch circle."""
    rp = m * z / 2
    rb = rp * math.cos(pa)
    half_at_base = thickness / (2 * rp) + _inv(pa)
    if _flank_angle(tip_r, rb, half_at_base) <= 0:
        raise ValueError(f"{z}-tooth gear: teeth come to a point before the tip circle; reduce backlash or module")
    start = max(rb, root_r)
    radii = [start + (tip_r - start) * i / (n - 1) for i in range(n)]
    pitch = 2 * math.pi / z
    edges = []
    for k in range(z):
        c = phase + k * pitch
        right = [_polar(r, c - _flank_angle(r, rb, half_at_base)) for r in radii]
        left = [_polar(r, c + _flank_angle(r, rb, half_at_base)) for r in reversed(radii)]
        a_start = _flank_angle(start, rb, half_at_base)
        if root_r < start:
            edges.append(Edge.make_line(_polar(root_r, c - a_start), right[0]))
        edges.append(Edge.make_spline(right))
        edges.append(Edge.make_three_point_arc(right[-1], _polar(tip_r, c), left[0]))
        edges.append(Edge.make_spline(left))
        root_from = left[-1]
        if root_r < start:
            root_from = _polar(root_r, c + a_start)
            edges.append(Edge.make_line(left[-1], root_from))
        nxt = c + pitch
        root_to = _polar(root_r if root_r < start else start, nxt - a_start)
        edges.append(Edge.make_three_point_arc(root_from, _polar(root_r, c + pitch / 2), root_to))
    return Wire(edges)


def spur(z, m, pa, backlash, height, n, phase=0.0):
    rp = m * z / 2
    wire = gear_outline(z, m, pa, math.pi * m / 2 - backlash / 2, rp + m, rp - 1.25 * m, n, phase)
    return extrude(Face(wire), height)


def d_bore(shaft_d, flat, clearance, height):
    """A D-shaped bore for a shaft of diameter `shaft_d` with a flat `flat` across, `clearance` added on the radius."""
    r = shaft_d / 2 + clearance
    across = flat - shaft_d / 2 + clearance
    return Pos(0, 0, height / 2) * (Cylinder(r, height) & Pos((across - r) / 2, 0, 0) * Box(across + r, 2 * r, height))
