"""Mating values of the gearbox, each computed once. Parts and the assembly read them as i.<name>."""

import math


def ring_teeth(p, i):
    return p["sun_teeth"] + 2 * p["planet_teeth"]


def centre_distance(p, i):
    m, zs, zp, n = p["module"], p["sun_teeth"], p["planet_teeth"], p["planets"]
    if (zs + i.ring_teeth) % n:
        raise ValueError(
            f"planets cannot be spaced evenly: sun + ring teeth ({zs + i.ring_teeth}) is not a multiple of {n}"
        )
    a = m * (zs + zp) / 2
    if 2 * a * math.sin(math.pi / n) <= m * (zp + 2) + 1:
        raise ValueError("planets collide with each other: fewer planets or more sun teeth")
    return a


def planet_poses(p, i):
    """[x, y, turn in degrees] of each planet: every one meshes with the sun and the ring."""
    n, zs, zp = p["planets"], p["sun_teeth"], p["planet_teeth"]
    poses = []
    for k in range(n):
        theta = 2 * math.pi * k / n
        phase = math.pi + math.pi / zp + theta * (1 + zs / zp)  # planet 0 shows the sun (at -X) a tooth space
        poses.append([i.centre_distance * math.cos(theta), i.centre_distance * math.sin(theta), math.degrees(phase)])
    return poses


def ring_phase_deg(p, i):
    return 0.0 if p["planet_teeth"] % 2 else 180.0 / i.ring_teeth


def z_gear(p, i):
    return p["floor"] + p["axial_gap"]


def z_carrier(p, i):
    return i.z_gear + p["face_width"] + p["axial_gap"]


def housing_height(p, i):
    return i.z_carrier + p["carrier_thickness"] + p["axial_gap"]


def ring_root_r(p, i):
    return p["module"] * (i.ring_teeth / 2 + 1.25)


def lid_bolt_xy(p, i):
    r = (p["housing_od"] / 2 + i.ring_root_r) / 2
    return [[r * math.cos(a), r * math.sin(a)] for a in (math.pi / 4 + k * math.pi / 2 for k in range(4))]


def nema_xy(p, i):
    s = p["nema_spacing"] / 2
    return [[s, s], [-s, s], [-s, -s], [s, -s]]


def motor(p, i):
    """The NEMA 17's datasheet: what the housing, the carrier and the motor model all fit to."""
    return {
        "body": 42.3,
        "length": 40.0,
        "boss_d": 22.0,
        "boss_h": 2.0,
        "shaft_length": 22.0,
        "tap_d": 2.5,
        "tap_depth": 6.5,
    }


def insert(p, i):
    return "M3-0.5-Standard"  # the insert and the hole cut for it are the same size by construction


def lid_screw_length(p, i):
    return 14  # through the lid, into the insert by at least its brass minimum


def motor_screw_length(p, i):
    return 8


def bearing(p, i):
    return "M8-22-7"  # a 608
