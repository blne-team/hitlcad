"""NEMA 17 planetary gearbox: housing with internal ring gear, sun, planets, carrier, lid."""

import math

from build123d import Align, Box, Cylinder, Edge, Face, Pos, Wire, extrude


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


def build(p):
    m, pa = p["module"], math.radians(p["pressure_angle"])
    zs, zp, n_pl = p["sun_teeth"], p["planet_teeth"], p["planets"]
    zr = zs + 2 * zp
    bl, fw, n = p["backlash"], p["face_width"], p["flank_points"]
    floor, gap, carrier_t, lid_t = p["floor"], p["axial_gap"], p["carrier_thickness"], p["lid_thickness"]
    od = p["housing_od"]
    a = m * (zs + zp) / 2  # planet centre distance

    if (zs + zr) % n_pl:
        raise ValueError(f"planets cannot be spaced evenly: sun + ring teeth ({zs + zr}) is not a multiple of {n_pl}")
    if 2 * a * math.sin(math.pi / n_pl) <= m * zp + 2 * m + 1:
        raise ValueError("planets collide with each other: fewer planets or more sun teeth")
    ring_root = m * zr / 2 + 1.25 * m
    if od / 2 - ring_root < p["insert_d"] + 2 * p["min_wall"]:
        raise ValueError(f"housing_od {od} leaves no room for the lid screw inserts outside the ring gear")

    z_gear = floor + gap
    z_carrier = z_gear + fw + gap
    height = z_carrier + carrier_t + gap  # housing top, where the lid sits

    # --- housing: cup with the internal ring gear, NEMA 17 face on the bottom
    housing = Cylinder(od / 2, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    ring_space = math.pi * m / 2 + bl / 2  # the ring's tooth space is an external tooth of the same base circle
    phase_planet0 = math.pi + math.pi / zp  # planet 0 shows the sun (at -X) a tooth space
    ring_phase = 0.0 if zp % 2 else math.pi / zr  # an odd planet then points a tooth at the ring, an even one a space
    cutter = gear_outline(zr, m, pa, ring_space, ring_root, m * zr / 2 - m, n, ring_phase)
    housing -= Pos(0, 0, floor) * extrude(Face(cutter), height)
    housing -= Cylinder(p["pilot_d"] / 2, p["pilot_depth"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    housing -= Cylinder(p["motor_clearance_d"] / 2, floor, align=(Align.CENTER, Align.CENTER, Align.MIN))
    s = p["nema_spacing"] / 2
    for x, y in ((s, s), (-s, s), (-s, -s), (s, -s)):
        housing -= Pos(x, y, 0) * Cylinder(p["screw_d"] / 2, floor, align=(Align.CENTER, Align.CENTER, Align.MIN))
        cbore = Cylinder(p["cbore_d"] / 2, p["cbore_depth"], align=(Align.CENTER, Align.CENTER, Align.MAX))
        housing -= Pos(x, y, floor) * cbore
    bolt_r = (od / 2 + ring_root) / 2
    for i in range(4):
        x, y = _polar(bolt_r, math.pi / 4 + i * math.pi / 2)
        housing -= Pos(x, y, height) * Cylinder(
            p["insert_d"] / 2, p["insert_depth"], align=(Align.CENTER, Align.CENTER, Align.MAX)
        )

    # --- sun on the motor's D shaft
    sun = Pos(0, 0, z_gear) * spur(zs, m, pa, bl, fw, n)
    r = p["shaft_d"] / 2 + p["shaft_clearance"]
    flat = p["shaft_flat"] - p["shaft_d"] / 2 + p["shaft_clearance"]
    d_bore = Cylinder(r, fw) & Pos((flat - r) / 2, 0, 0) * Box(flat + r, 2 * r, fw)
    sun -= Pos(0, 0, z_gear + fw / 2) * d_bore

    # --- planets, phased so every one meshes with the sun and the ring
    parts = {"housing": housing, "sun": sun}
    pin_r = p["pin_d"] / 2
    for i in range(n_pl):
        theta = 2 * math.pi * i / n_pl
        phi = phase_planet0 + theta * (1 + zs / zp)
        planet = spur(zp, m, pa, bl, fw, n, phi)
        planet -= Cylinder(pin_r + p["pin_clearance"], fw, align=(Align.CENTER, Align.CENTER, Align.MIN))
        parts[f"planet_{i}"] = Pos(*_polar(a, theta), z_gear) * planet

    # --- carrier: plate above the planets, pins down into them, output shaft up through the lid
    plate_r = a + pin_r + p["min_wall"] * 2
    if plate_r >= m * zr / 2 - m - gap:
        raise ValueError("carrier plate would touch the ring gear teeth")
    carrier = Pos(0, 0, z_carrier) * Cylinder(plate_r, carrier_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for i in range(n_pl):
        x, y = _polar(a, 2 * math.pi * i / n_pl)
        carrier += Pos(x, y, z_gear) * Cylinder(pin_r, fw + gap, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shaft_top = height + lid_t + p["output_length"]
    carrier += Pos(0, 0, z_carrier + carrier_t) * Cylinder(
        p["output_d"] / 2, shaft_top - z_carrier - carrier_t, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    parts["carrier"] = carrier

    # --- lid: 608 bearing pocket, four screws into the housing's heat-set inserts
    lid = Pos(0, 0, height) * Cylinder(od / 2, lid_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    if p["bearing_width"] >= lid_t - p["min_wall"]:
        raise ValueError("the lid is too thin for the bearing pocket")
    lid -= Pos(0, 0, height + lid_t) * Cylinder(
        p["bearing_od"] / 2, p["bearing_width"], align=(Align.CENTER, Align.CENTER, Align.MAX)
    )
    lid -= Pos(0, 0, height) * Cylinder(p["output_d"] / 2 + 1, lid_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for i in range(4):
        x, y = _polar(bolt_r, math.pi / 4 + i * math.pi / 2)
        lid -= Pos(x, y, height) * Cylinder(p["screw_d"] / 2, lid_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    parts["lid"] = lid
    return parts
