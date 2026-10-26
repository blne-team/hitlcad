import math


def joints(p, i):
    """The four pivots of the linkage as assembled, with the crank at `crank_angle`: A and D on the base, B where the
    crank meets the coupler, C where the coupler meets the rocker (on the open branch)."""
    a, d = (0.0, 0.0), (p["ground_l"], 0.0)
    t = math.radians(p["crank_angle"])
    b = (p["crank_l"] * math.cos(t), p["crank_l"] * math.sin(t))
    dx, dy = d[0] - b[0], d[1] - b[1]
    bd = math.hypot(dx, dy)
    along = (p["coupler_l"] ** 2 - p["rocker_l"] ** 2 + bd**2) / (2 * bd)
    up = math.sqrt(p["coupler_l"] ** 2 - along**2)
    c = (b[0] + along * dx / bd - up * dy / bd, b[1] + along * dy / bd + up * dx / bd)
    return {"a": a, "b": b, "c": c, "d": d}


def layer(p, i):
    """How far each link's layer is above the one below it: its thickness and the gap between them."""
    return p["thickness"] + p["gap"]
