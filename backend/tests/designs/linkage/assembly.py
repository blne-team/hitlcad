import math

from build123d import Pos, Rot


def build(p, i):
    j, up = i.joints, i.layer

    def along(u, v):
        return math.degrees(math.atan2(v[1] - u[1], v[0] - u[0]))

    return {
        "base": [Pos()],
        "crank": [Pos(*j["a"], 0) * Rot(0, 0, along(j["a"], j["b"]))],
        "coupler": [Pos(*j["b"], up) * Rot(0, 0, along(j["b"], j["c"]))],
        "rocker": [Pos(*j["d"], 2 * up) * Rot(0, 0, along(j["d"], j["c"]))],
    }
