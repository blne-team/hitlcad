"""Mates: after placement, the two ends' frames agree in the degrees of freedom the mate type fixes, the
free motion sits within its limits, and a fit holds between the measured hole and shaft."""

from __future__ import annotations

from typing import Any

from hitlcad.analysis import iso286
from hitlcad.analysis.base import Context, label, pair, result, structural
from hitlcad.analysis.model import PlacedFrame, StructureError
from hitlcad.core.report import Result
from hitlcad.core.structure import ROUND_KINDS, _Mate, symmetry_of


def frames_of(ctx: Context, mate: _Mate) -> tuple[list[PlacedFrame], list[PlacedFrame]]:
    return ctx.built.frames(mate.a), ctx.built.frames(mate.b)


def fit_check(ctx: Context, mate: _Mate, fa: PlacedFrame, fb: PlacedFrame) -> tuple[bool | None, str, dict[str, Any]]:
    """The fit between the measured hole and shaft of the two ends, after each part's process allowance: within a
    clearance range, or, for an ISO fit, both at one basic size, with the classes' limits there for the drawing. A
    mate that moves needs a fit that leaves room to move."""
    assert mate.fit is not None
    kinds = {fa.spec.kind: fa, fb.spec.kind: fb}
    if set(kinds) != ROUND_KINDS:
        return False, f"a fit is between a hole and a shaft; this mate joins {fa.spec.kind} and {fb.spec.kind}", {}
    hole, shaft = kinds["hole"], kinds["shaft"]
    if hole.measured.status != "pass" or shaft.measured.status != "pass":
        why = hole.measured.detail if hole.measured.status != "pass" else shaft.measured.detail
        return None, f"fit not measurable: {why}", {}
    hole_d, shaft_d = hole.measured.values["diameter"], shaft.measured.values["diameter"]
    hole_allow, shaft_allow = allowance(hole, "hole"), allowance(shaft, "shaft")
    made_hole, made_shaft = hole_d - hole_allow, shaft_d + shaft_allow
    clearance = made_hole - made_shaft
    # A mesh measures a diameter only to within its facets: the fit fails when it is out by more than that.
    meshes = hole.measured.uncertainty_mm + shaft.measured.uncertainty_mm
    slack = ctx.tol.position_mm + meshes
    measured = (f"hole Ø{hole_d:.3f}, shaft Ø{shaft_d:.3f}"
                + (f", after process allowances ({hole_allow:g} hole, {shaft_allow:g} shaft)"
                   if hole_allow or shaft_allow else "")
                + (f", ±{meshes:.3f} on the meshes" if meshes else ""))  # fmt: skip
    values = {"clearance_mm": clearance, "hole_mm": hole_d, "shaft_mm": shaft_d}
    if mate.fit.iso:
        basic = round((made_hole + made_shaft) / 2, 3)
        try:
            (hole_hi, hole_lo), (shaft_hi, shaft_lo) = iso286.limits(mate.fit.iso, basic)
        except ValueError as e:
            return False, f"fit {mate.fit.iso}: {e}", values
        lo, hi = hole_lo - shaft_hi, hole_hi - shaft_lo
        values |= {"basic_mm": basic, "least_clearance_mm": lo, "greatest_clearance_mm": hi}
        ok = abs(clearance) <= slack
        detail = (f"fit {mate.fit.iso} at Ø{basic:g}: hole {_mm(hole_hi)}/{_mm(hole_lo)}, shaft {_mm(shaft_hi)}/"
                  f"{_mm(shaft_lo)}, clearance {lo:.3f} … {hi:.3f} mm ({measured})" if ok else
                  f"fit {mate.fit.iso}: the hole and shaft differ by {clearance:.3f} mm ({measured}); an ISO fit's "
                  "hole and shaft are modelled at one basic size, and its classes go on the drawing")  # fmt: skip
    else:
        lo, hi = mate.fit.limits(made_hole)
        ok = lo - slack <= clearance <= hi + slack
        detail = f"fit {clearance:.3f} mm ({measured}), needs {lo:g} … {hi:g}"
    if mate.free_motion and hi <= 0:
        ok = False
        detail += f"; a {mate.type} mate moves, but its fit leaves no room: greatest clearance {hi:.3f} mm"
    return ok, detail, values


def _mm(v: float) -> str:
    return f"{v:+.3f}" if v else "0"


def allowance(f: PlacedFrame, feature: str) -> float:
    """How much the frame's part's process makes a round feature smaller (a hole) or larger (a shaft) than modelled."""
    process = f.owner.spec.process(f.part.name)
    return process.allowance(feature) if process else 0.0  # type: ignore[arg-type]


def _limits(mate: _Mate, free: dict[str, float]) -> str:
    limits = mate.limits_of()
    if limits is None:
        return ""
    out = []
    for kind, value in free.items():
        bounds = getattr(limits, kind)
        if bounds and not bounds[0] <= value <= bounds[1]:
            unit = "°" if kind == "angle" else " mm"
            out.append(f"{kind} {value:.2f}{unit} outside {bounds[0]:g} … {bounds[1]:g}")
    return "; ".join(out)


class Mates:
    name = "mate"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for mate in ctx.built.spec.mates:
            try:
                pairs = pair(*frames_of(ctx, mate))
            except StructureError as e:
                out.append(structural(mate.address, e, [mate.a, mate.b]))
                continue
            for fa, fb in pairs:
                out.append(self._pair(ctx, mate, fa, fb))
        return out

    def _pair(self, ctx: Context, mate: _Mate, fa: PlacedFrame, fb: PlacedFrame) -> Result:
        check = mate.verify(fa.frame, mate.facing(fb.frame), symmetry_of(fa.spec.symmetry, fb.spec.symmetry), ctx.tol)
        status: bool | None = check.passed
        details = [f"{label(fa)} ↔ {label(fb)}: {mate.type}, {check.detail}"]
        values: dict[str, Any] = {**check.values, **{f"free_{k}": v for k, v in check.free.items()}}
        if out_of_range := _limits(mate, check.free):
            status = False
            details.append(out_of_range)
        if mate.fit:
            fit_ok, fit_detail, fit_values = fit_check(ctx, mate, fa, fb)
            details.append(fit_detail)
            values |= fit_values
            if fit_ok is False:
                status = False
            elif fit_ok is None and status:
                status = None
        about = [fa.instance, fb.instance, fa.address, fb.address, mate.a, mate.b]
        return result(self.name, mate.address, status, "; ".join(details), about, values)
