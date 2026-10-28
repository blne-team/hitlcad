import math


def lift_table(p, i):
    """The follower's lift at each degree the cam has turned: a cycloidal rise over `rise`°, a dwell, a cycloidal
    return over as many, and a dwell, [[θ°, mm], …]."""
    rise, lift = p["rise"], p["lift"]

    def cycloid(t):
        return lift * (t - math.sin(2 * math.pi * t) / (2 * math.pi))

    def at(theta):
        if theta < rise:
            return cycloid(theta / rise)
        if theta < 180:
            return lift
        if theta < 180 + rise:
            return lift - cycloid((theta - 180) / rise)
        return 0.0

    return [[theta, at(theta)] for theta in range(360)]


def follower_at(p, i):
    """How far the follower's face is from the cam's axis as assembled."""
    return p["base_r"] + dict(i.lift_table)[p["cam_angle"] % 360]
