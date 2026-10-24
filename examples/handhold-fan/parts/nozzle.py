"""The nozzle: a cone from the duct down to a narrow jet, a collar that stops on the duct's outlet and a
spigot that plugs into it. Printed tip-down: a flat lip round the tip gives it enough bed contact, and the
collar's underside slopes at 40° so nothing needs support."""

import math

from build123d import BuildPart, BuildSketch, Location, Plane, Polygon, revolve

SLOPE_DEG = 40
COLLAR_H = 3
LIP_H = 1.2
BED_SHARE = 0.11  # of the footprint, over the 10% the FDM check asks for


def build(p, i):
    w, zc = p["nozzle_wall"], p["nozzle_len"]
    ri0, ro_s = p["nozzle_d"] / 2, i.spigot_od / 2
    ri1, rc = ro_s - w, i.duct_od / 2
    if ri0 + w >= ri1:
        raise ValueError("the nozzle outlet is as wide as the duct: reduce nozzle_d")
    slope = math.tan(math.radians(SLOPE_DEG))
    r_lip = max(ri0 + w, math.sqrt(BED_SHARE * (2 * rc) ** 2 / math.pi + ri0**2))
    cone = (ri1 - ri0) / zc  # the outer cone runs from (ri0 + w, 0) to (ri1 + w, zc)
    # where the collar's 40° underside, rising to (rc, zc - COLLAR_H), meets the outer cone
    z_collar = (zc - COLLAR_H - (rc - ri0 - w) / slope) / (1 - cone / slope)
    profile = [
        (ri0, 0),
        (r_lip, 0),
        (r_lip, LIP_H),
        (ri0 + w + cone * LIP_H, LIP_H),
        (ri0 + w + cone * z_collar, z_collar),
        (rc, zc - COLLAR_H),
        (rc, zc),
        (ro_s, zc),
        (ro_s, zc + p["spigot_len"]),
        (ri1, zc + p["spigot_len"]),
        (ri1, zc),
    ]
    with BuildPart() as nozzle:
        with BuildSketch(Plane.XZ):
            Polygon(*profile, align=None)
        revolve()

    return {
        "nozzle": (
            nozzle.part,
            {"seat": Location((0, 0, zc)), "spigot": Location((0, 0, zc))},
        )
    }
