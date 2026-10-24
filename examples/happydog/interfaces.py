"""Mating values: one function per value, def name(p, i). Parts and assembly.py read them as i.<name>.

The sculpt is modelled in dog units (lib/sculpt.py) and scaled as a whole so the figurine is `height`
tall. The body stands as it prints, so its own frame is the figurine's frame: millimetres, Z up, on its
flat bottom at Z=0, facing -Y. The frames below are in that frame.
"""

from math import radians

import numpy as np
from lib import sculpt


def scale(p, i):
    """Millimetres per dog unit: the top of the head is `height` above the flat bottom."""
    return p["height"] / (sculpt.top(p) - sculpt.FLOOR)


def _mm(p, i, v):
    return [i.scale * c for c in np.asarray(v, float) - (0, 0, sculpt.FLOOR)]


def glue_gap(p, i):
    """The glue line left between parts that are glued together (head on body, tail on rump)."""
    return 0.1


def neck_joint(p, i):
    """Where the head is glued on: the plane through the middle of the collar, Z up the neck, X sideways.
    The collar's outer wall stands square to it all round, so neither part ends in a feather edge there.
    The glue line is centred on it."""
    tilt = radians(sculpt.COLLAR_TILT_DEG)
    axis = np.array([0, -np.sin(tilt), np.cos(tilt)])
    return sculpt.frame(_mm(p, i, sculpt.COLLAR_CENTRE), (1, 0, 0), axis)


def peg_d(p, i):
    """The locating peg standing up out of the body's neck, which the head's socket takes."""
    return round(0.045 * p["height"], 1)


def peg_len(p, i):
    return i.peg_d


def socket_d(p, i):
    """The head's socket: a slip fit on the peg, filled with glue."""
    return round(i.peg_d + 0.3, 2)


def socket_depth(p, i):
    return i.peg_len + 0.5


def tail_root(p, i):
    """The tail's flat root face, inside a socket in the rump: on the tail's centre line, Z along it out of
    the body, X across the tail's (wagged) plane. It is also the tail's own frame: it prints standing on
    this face."""
    a, b = sculpt.tail_curve(p)[:2]
    along = (b - a) / np.linalg.norm(b - a)
    wag = sculpt.rot_y(radians(p["tail_wag_deg"]))
    return sculpt.frame(_mm(p, i, a + along * sculpt.TAIL_PLUG), wag @ (1, 0, 0), along)
