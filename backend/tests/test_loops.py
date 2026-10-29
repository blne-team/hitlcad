"""Motion without a build: what relations count as moved, the block solver on four-bars whose motion is known in
closed form, and the gaps motion measures between solids."""

from __future__ import annotations

import math

import manifold3d
import numpy as np
import pytest

from hitlcad.builtin.gears.relations import GearRelation, RackRelation, ScrewRelation
from hitlcad.builtin.joints.mates import RevoluteMate
from hitlcad.builtin.solvers.sqp import Sqp
from hitlcad.core import frames as fr
from hitlcad.core.loops import Block, BlockMate, Position
from hitlcad.core.structure import Ends, Tolerances
from hitlcad.geometry.mesh import Collider

TOL = Tolerances(position_mm=0.01, angle_deg=0.05, motion_steps=2000)
Z = (0.0, 0.0, 1.0)


def turned(f: fr.Frame, point: tuple[float, float, float], degrees: float) -> fr.Frame:
    return fr.rotation_about(point, Z, degrees) @ f


def test_gears_on_fixed_axes_turn_by_their_ratio() -> None:
    gear = GearRelation(type="gear", a="sun.teeth", b="planet.teeth")
    fa, fb = fr.IDENTITY, fr.translation([30.0, 0.0, 0.0])
    ratio = gear.law(Ends(fa, fb, {"teeth": 12}, {"teeth": 18}, lambda ref: None)).ratio
    qa, qb = gear.coordinates(fa, fb, turned(fa, (0, 0, 0), 30.0), turned(fb, (30, 0, 0), 30.0 * ratio))
    assert (qa, qb) == pytest.approx((30.0, -20.0))
    assert qb == pytest.approx(ratio * qa)


def test_a_planet_on_a_moving_carrier_turns_by_willis() -> None:
    """The carrier turns 10°, the sun stays: the planet turns against the line of centres as the sun does, by the
    ratio, so (planet − carrier) = −z_s/z_p × (sun − carrier)."""
    gear = GearRelation(type="gear", a="sun.teeth", b="planet.teeth")
    fa, fb = fr.IDENTITY, fr.translation([30.0, 0.0, 0.0])
    planet = turned(turned(fb, (30, 0, 0), 10.0 * 12 / 18), (0, 0, 0), 10.0)  # spun on its pin, then carried
    qa, qb = gear.coordinates(fa, fb, fa, planet)
    assert qa == pytest.approx(-10.0)  # the sun against the line of centres, which turned 10°
    assert qb == pytest.approx(gear.law(Ends(fa, fb, {"teeth": 12}, {"teeth": 18}, lambda ref: None)).at(qa))


def test_a_rack_travels_the_pinion_s_pitch_arc() -> None:
    rack = RackRelation(type="rack", a="pinion.teeth", b="rack.pitch_line")
    fa = fr.IDENTITY
    fb = fr.translation([0.0, -10.0, 0.0]) @ fr.rotation_about((0, 0, 0), (1, 0, 0), 90.0)  # pitch line below
    _, along = rack.axes(fa, fb)
    ratio = rack.law(Ends(fa, fb, {"module": 1.0, "teeth": 20}, {}, lambda ref: None)).ratio
    qa, qb = rack.coordinates(fa, fb, turned(fa, (0, 0, 0), 36.0), fr.translation(along.direction * ratio * 36.0) @ fb)
    assert (qa, qb) == pytest.approx((36.0, ratio * 36.0))


def test_a_nut_travels_its_lead_per_turn_of_the_screw() -> None:
    screw = ScrewRelation(type="screw", a="leadscrew.axis", b="nut.axis", lead=8)
    fa, fb = fr.IDENTITY, fr.translation([0.0, 0.0, 20.0])
    qa, qb = screw.coordinates(fa, fb, turned(fa, (0, 0, 0), 90.0), fr.translation([0.0, 0.0, -2.0]) @ fb)
    assert (qa, qb) == pytest.approx((90.0, 2.0))


def four_bar(crank: float, coupler: float, rocker: float, ground: float) -> Block:
    """A planar four-bar in XY with the crank at 90°, every pivot a revolute about Z: overconstrained, as CAD has it."""
    b = (0.0, crank)
    dx, dy = ground - b[0], -b[1]
    bd = math.hypot(dx, dy)
    along = (coupler**2 - rocker**2 + bd**2) / (2 * bd)
    up = math.sqrt(coupler**2 - along**2)
    c = (b[0] + along * dx / bd - up * dy / bd, b[1] + along * dy / bd + up * dx / bd)

    def at(p: tuple[float, float], facing: bool = False) -> fr.Frame:
        f = fr.translation([p[0], p[1], 0.0])
        return f @ fr.flip_x() if facing else f

    holds = frozenset({"origin_xy", "origin_z", "axis"})
    pivots = [("A", "g", "c", (0.0, 0.0)), ("B", "c", "k", b), ("C", "k", "r", c), ("D", "g", "r", (ground, 0.0))]
    mates = [BlockMate(name, RevoluteMate(type="revolute", a=f"{a}.x", b=f"{m}.x"), a, m, at(p), at(p, True), holds)
             for name, a, m, p in pivots]  # fmt: skip

    def bar(u: tuple[float, float], v: tuple[float, float]) -> np.ndarray:
        return np.array([[*u, -1.0], [*u, 1.0], [*v, -1.0], [*v, 1.0]])

    corners = {"c": bar((0.0, 0.0), b), "k": bar(b, c), "r": bar(c, (ground, 0.0))}
    return Block(["c", "k", "r"], [], mates, [], 0, "angle", corners, TOL)


def trace(block: Block, end: float) -> list[Position]:
    """The block's positions from where it sits towards its driver at `end`, a step at most half a degree, halved
    where it does not settle, until halving gets nowhere."""
    solver, at, out = Sqp(), block.start, []
    sign, step, rate = math.copysign(1.0, end - block.start.value), 0.5, np.zeros(18)
    while abs(end - at.value) > 1e-9 and step > 1e-4:
        h = min(step, abs(end - at.value))
        value = at.value + sign * h
        x = solver.settle(block, rate * h, block.parameters(value, at, {}, {}))
        found = None if x is None else block.check(block.moved(x, at), value, at, {})
        if not isinstance(found, Position):
            step = h / 2
            continue
        assert x is not None
        at, rate, step = found, x / h, 0.5
        out.append(found)
    return out


def test_a_crank_rocker_turns_its_crank_a_full_turn_and_swings_its_rocker_as_it_must() -> None:
    block = four_bar(15, 40, 30, 40)
    assert block.freedoms() == (True, 0)
    start, swing = block.start, []
    for end in (start.value + 180, start.value - 180):
        positions = trace(block, end)
        assert positions[-1].value == pytest.approx(end)
        swing += [block.coordinate(p, 3, "angle") - block.coordinate(start, 3, "angle") for p in positions]
    assert (min(swing), max(swing)) == pytest.approx((-18.11, 45.90), abs=0.02)  # in closed form


def test_a_linkage_that_cannot_turn_its_crank_locks_where_it_must() -> None:
    """15/20/30/40: the crank locks where B is coupler + rocker from D, 124.23° from the ground line, 34.23° on from
    where it sits; the joints' position tolerance lets it go a little further, never less far."""
    block = four_bar(15, 20, 30, 40)
    start = block.start
    up = trace(block, start.value + 180)[-1].value - start.value
    assert 34.23 <= up <= 34.8
    assert trace(block, start.value - 180)[-1].value == pytest.approx(start.value - 180)  # the other way it turns on


def test_a_solid_wholly_inside_another_is_no_distance_from_it() -> None:
    """A pin that a step takes wholly into a block, its surface clear of the block's, has met it."""
    block, pin = (
        Collider(manifold3d.Manifold.cube((10, 10, 10), True)),
        Collider(manifold3d.Manifold.cube((1, 1, 1), True)),
    )
    moved = np.eye(4)
    for at, apart in ((0.0, 0.0), (5.0, 0.0), (20.0, 14.5)):
        moved[0, 3] = at
        assert pin.distance(block, moved) == pytest.approx(apart) and block.distance(pin, None, moved) == pytest.approx(
            apart
        )
        assert pin.meets(block, moved) is (apart == 0.0) and block.meets(pin, None, moved) is (apart == 0.0)
