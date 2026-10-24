"""Mates: after placement, the two ends' frames agree in the degrees of freedom the mate type fixes, the
free motion sits within its limits, and a fit holds between the measured hole and shaft."""

from __future__ import annotations

from typing import Any

from hitlcad.analysis.base import Context, label, pair, result, structural
from hitlcad.analysis.model import PlacedFrame, StructureError
from hitlcad.core.report import Result
from hitlcad.core.structure import ROUND_KINDS, _Mate, symmetry_of


def frames_of(ctx: Context, mate: _Mate) -> tuple[list[PlacedFrame], list[PlacedFrame]]:
    return ctx.built.frames(mate.a), ctx.built.frames(mate.b)


def fit_check(ctx: Context, mate: _Mate, fa: PlacedFrame, fb: PlacedFrame) -> tuple[bool | None, str, dict[str, Any]]:
    """The fit between the measured hole and shaft of the two ends, after each part's process allowance."""
    assert mate.fit is not None
    kinds = {fa.spec.kind: fa, fb.spec.kind: fb}
    if set(kinds) != ROUND_KINDS:
        return False, f"a fit is between a hole and a shaft; this mate joins {fa.spec.kind} and {fb.spec.kind}", {}
    hole, shaft = kinds["hole"], kinds["shaft"]
    if hole.measured.status != "pass" or shaft.measured.status != "pass":
        why = hole.measured.detail if hole.measured.status != "pass" else shaft.measured.detail
        return None, f"fit not measurable: {why}", {}
    hole_d, shaft_d = hole.measured.values["diameter"], shaft.measured.values["diameter"]
    hole_allow = _allowance(hole, "hole")
    shaft_allow = _allowance(shaft, "shaft")
    clearance = (hole_d - hole_allow) - (shaft_d + shaft_allow)
    nominal = (hole_d + shaft_d) / 2
    try:
        lo, hi = mate.fit.limits(nominal)
    except ValueError as e:
        return False, str(e), {}
    # A mesh measures a diameter only to within its facets: the fit fails when it is out by more than that.
    slack = ctx.tol.position_mm + hole.measured.uncertainty_mm + shaft.measured.uncertainty_mm
    ok = lo - slack <= clearance <= hi + slack
    spec = mate.fit.iso or f"{lo:g} … {hi:g}"
    allowances = (
        f" after process allowances ({hole_allow:g} hole, {shaft_allow:g} shaft)" if hole_allow or shaft_allow else ""
    )
    meshes = hole.measured.uncertainty_mm + shaft.measured.uncertainty_mm
    detail = (f"fit {clearance:.3f} mm (hole Ø{hole_d:.3f}, shaft Ø{shaft_d:.3f}){allowances}"
              + (f" ±{meshes:.3f} on the meshes" if meshes else "") + f", needs {spec}"
              + ("" if ok else f" ({lo:.3f} … {hi:.3f})" if mate.fit.iso else ""))  # fmt: skip
    return ok, detail, {"clearance_mm": clearance, "hole_mm": hole_d, "shaft_mm": shaft_d}


def _allowance(f: PlacedFrame, feature: str) -> float:
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
