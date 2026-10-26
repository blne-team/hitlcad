"""The base (two blocks under the pivots, and a post the rocker swings into), and the three links: bars with round
ends, each made along +X from its first pivot, in its own layer."""

import math

from build123d import Align, Box, Cylinder, Location, Pos, Rot

ON = (Align.CENTER, Align.CENTER, Align.MIN)


def _bar(length, width, thickness):
    return (
        Pos(length / 2, 0, 0) * Box(length, width, thickness, align=ON)
        + Cylinder(width / 2, thickness, align=ON)
        + Pos(length, 0, 0) * Cylinder(width / 2, thickness, align=ON)
    )


def _length(u, v):
    return math.hypot(v[0] - u[0], v[1] - u[1])


def build(p, i):
    j, w, t, gap, up = i.joints, p["width"], p["thickness"], p["gap"], i.layer
    under = (Align.CENTER, Align.CENTER, Align.MAX)
    base = Pos(*j["a"], -gap) * Box(2 * w, 2 * w, 4, align=under) + Pos(*j["d"], -gap) * Box(
        2 * w, 2 * w, 4, align=under
    )
    if p["post_h"] > 0:  # on the rocker's layer, where it swings to `post_angle` past where it sits
        turn = math.atan2(j["c"][1] - j["d"][1], j["c"][0] - j["d"][0]) + math.radians(p["post_angle"])
        reach = p["rocker_l"] * 2 / 3
        post = Pos(j["d"][0] + reach * math.cos(turn), j["d"][1] + reach * math.sin(turn), 2 * up)
        base += post * Box(4, 4, p["post_h"], align=ON)
    down = Rot(180, 0, 0)  # a pivot's frame on a link's underside: Z down, facing the pivot below
    crank, coupler, rocker = (
        _bar(_length(u, v), w, t) for u, v in ((j["a"], j["b"]), (j["b"], j["c"]), (j["d"], j["c"]))
    )
    return {
        "base": (base, {"a": Location((*j["a"], 0)), "d": Location((*j["d"], 2 * up))}),
        "crank": (crank, {"a": Location((0, 0, 0)) * down, "b": Location((_length(j["a"], j["b"]), 0, t))}),
        "coupler": (coupler, {"b": Location((0, 0, 0)) * down, "c": Location((_length(j["b"], j["c"]), 0, t))}),
        "rocker": (rocker, {"d": Location((0, 0, 0)) * down, "c": Location((_length(j["d"], j["c"]), 0, 0)) * down}),
    }
