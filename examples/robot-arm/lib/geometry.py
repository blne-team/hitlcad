"""Primitive helpers for rigid robot housings; dimensions are supplied by interfaces."""
import math
from build123d import Align, Box, Color, Cylinder, Location, Pos, Rot

ON = (Align.CENTER, Align.CENTER, Align.MIN)
NAMES = ['base', 'turret', 'upper_arm', 'forearm', 'wrist_roll', 'wrist_pitch', 'tool_flange']


def cylinder(radius, length, origin, rotation=(0, 0, 0)):
    """Place a cylinder from its first axial face."""
    return Location(origin, rotation) * Cylinder(radius, length, align=ON)


def cross_drum(x, z, y, depth, radius):
    """Place a joint housing whose axis runs along positive Y."""
    return cylinder(radius, depth, (x, y, z), (-90, 0, 0))


def beam(a, b, y, depth, width):
    """Connect two XZ points with a rectangular housing web."""
    dx, dz = b[0]-a[0], b[1]-a[1]
    return Pos(a[0], y+depth/2, a[1]) * Rot(0, math.degrees(math.atan2(dx,dz)), 0) * Box(
        width, depth, math.hypot(dx,dz), align=ON)


def finish(name, shape, i, color):
    """Convert home geometry and joint frames into the part's own frame."""
    n = NAMES.index(name)
    data = i.layout
    origin = data['origins'][n]
    local = Pos(*[-v for v in origin])
    connectors = {}
    if n:
        connectors['input'] = local * Location(data['axes'][n-1], data['rotations'][n-1]) * Rot(180,0,0)
    if n < 6:
        connectors['output'] = local * Location(data['axes'][n], data['rotations'][n])
    result = local * shape
    result.color = Color(color)
    result.label = name.replace('_', ' ').title()
    return {name: (result, connectors)}
