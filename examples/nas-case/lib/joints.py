"""Frames of the screw-into-insert joints, in the case frame, from an interface value such as i.seam_joint: each
stack is [opening of the insert hole, unit direction out of it towards the screw head]."""

from build123d import Vector
from lib.geometry import axial


def _stacks(joint):
    for s in joint["stacks"]:
        yield Vector(*s[:3]), Vector(*s[3:])


def inserts(joint, insert_len):
    """Where each insert's own origin goes (its bottom, `recess` below the opening), Z out of the hole: frames that
    mate rigid with the insert's `placement`."""
    return [axial(o - d * (joint["recess"] + insert_len), d) for o, d in _stacks(joint)]


def seats(joint):
    """Where each screw's head seats, Z out of the hole towards the head: the clearance holes' connector, which
    mates rigid with the screw's `placement`."""
    return [axial(o + d * joint["clamp"], d) for o, d in _stacks(joint)]
