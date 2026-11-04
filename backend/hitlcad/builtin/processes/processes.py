"""The manufacturing processes: FDM and resin printing, 3-axis CNC milling, sheet metal and injection molding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from hitlcad.core.checks import (
    PrintedProcess,
    PrintFacts,
    PrintNeeds,
    PrintSetting,
    PrintWay,
    Process,
    ProcessFinding,
)

if TYPE_CHECKING:
    from hitlcad.analysis.model import Body


class FdmProcess(PrintedProcess):
    """FDM (filament) 3D printing, printed the way `up` turns the part, on its lowest point: wall thickness,
    overhangs and bed contact. `up` is the direction of the part's own frame that points up as it prints
    (`[0, -1, 0]` stands it on its +Y face; as modelled by default), so the part stays modelled for its assembly.
    An overhang prints where it bridges between two supports no more than `max_bridge_mm` apart (the top of a
    horizontal hole is such a bridge) or lies within a nozzle's width of what holds it. With `supports`, overhangs
    print on support and are reported, not failed: for shapes that cannot avoid them, like figurines. With `brim`,
    a tall, narrow part is held down by a brim. `hole_allowance_mm` / `shaft_allowance_mm`: how much printed holes
    come out smaller and shafts larger than modelled, for fit checks. Its print settings (`layer_mm`, `walls`,
    `infill_percent`, `supports`, `brim`) left unset are recommended in the print plan from what the build finds
    (a tight fit, screws threading in, overhangs, a tall narrow stance); set one where you judge otherwise, and
    say why in `why`."""

    process: Literal["fdm"]
    nozzle_mm: float = Field(default=0.4, gt=0)
    min_wall_mm: float = Field(default=0.8, gt=0, description="Thinnest wall that prints solid: two perimeters")
    max_overhang_deg: float = Field(
        default=45, ge=0, lt=90, description="Steepest overhang, from vertical, without support"
    )
    max_bridge_mm: float = Field(default=10.0, ge=0, description="Longest span bridged between two supports")
    min_bed_contact_share: float = Field(
        default=0.10, ge=0, le=1, description="Of the part's outline seen from above, the share its base must span"
    )
    max_height_to_base: float = Field(
        default=4.0, gt=0, description="Tallest a part stands, × its base's narrowest width, without a brim"
    )
    teardrop_from_mm: float = Field(
        default=8.0,
        gt=0,
        description="Round holes lying on their side from this diameter: their tops print as bridged "
        "arcs that sag into them; a teardrop's 45° point prints without",
    )
    first_layer_squash_mm: float = Field(
        default=0.2,
        ge=0,
        description="How far the first layer, pressed onto the bed, spreads past its outline each side",
    )
    layer_bond: float = Field(
        default=0.5,
        gt=0,
        le=1,
        description="How strongly its layers hold to each other, as a share of its strength along them: what flexes "
        "must not pull them apart harder",
    )
    ignored_overhang_mm2: float = Field(
        default=1.0, ge=0, description="An overhang that prints on nothing over less than this still prints"
    )
    supports: bool | None = Field(default=None, description="Printed with support under its overhangs; unset: "
                                  "recommended")  # fmt: skip
    brim: bool | None = Field(default=None, description="Printed with a brim round its base; unset: recommended")
    layer_mm: float | None = Field(default=None, gt=0, description="Layer height; unset: recommended")
    walls: int | None = Field(default=None, ge=1, description="Perimeters round each layer; unset: recommended")
    infill_percent: float | None = Field(default=None, ge=0, le=100, description="Unset: recommended")
    example = {"process": "fdm", "hole_allowance_mm": 0.15}
    findings = {
        "fdm_wall_thickness": (
            "A printed wall thinner than FDM's minimum",
            "Thicken the walls to the minimum (about two nozzle widths).",
        ),
        "fdm_overhangs": (
            "An FDM overhang that needs support",
            "Keep overhangs within the angle (a chamfer rather than a fillet underneath, teardrop holes), or print it another way up (`up`).",
        ),
        "fdm_teardrops": (
            "A round hole lying on its side whose top sags as it prints",
            "Make the hole a teardrop, its top drawn up to a 45° point (the circle with a square turned 45° on its top "
            "quarter), or turn the part so it prints upright (`up`).",
        ),
        "fdm_round": (
            "A round feature with a fit, printed so its layers step it out of round past its fit",
            "Turn the part so the feature's axis stands upright as it prints (`up`), or split the part so it can.",
        ),
        "fdm_first_layer": (
            "A round feature with a fit whose edge on the bed the first layer squashes past its fit",
            "Chamfer the edge where it meets the bed, at least the first layer's squash, so the fit holds there.",
        ),
        "fdm_flex": (
            "What of an FDM part flexes, bent across its layers, pulls them apart",
            "Turn the part so what flexes runs along its layers (`up`), or split it so that part can.",
        ),
        "fdm_bed_contact": (
            "An FDM part without a face to stand on the bed",
            "Give the part a flat face to stand on, large enough to hold it, and turn it onto that face (`up`).",
        ),
    }

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.builtin.processes.checks import fdm_checks

        return fdm_checks(body, self)

    def way(self, body: Body, needs: PrintNeeds) -> PrintWay:
        from hitlcad.builtin.processes.checks import fdm_way

        return fdm_way(body, self, needs)

    def recommend(self, facts: PrintFacts) -> list[PrintSetting]:
        from hitlcad.builtin.processes.checks import fdm_recommend

        return fdm_recommend(self, facts)


class ResinProcess(PrintedProcess):
    """Resin 3D printing (SLA, DLP, MSLA), on support: walls of at least `min_wall_mm`, and every hollow
    inside the part drained through an opening at least `min_drain_mm` across, or it traps uncured resin
    (Formlabs' design specifications ask for 3.5 mm). `up`, `hole_allowance_mm` and `shaft_allowance_mm` as for FDM."""

    process: Literal["resin"]
    min_wall_mm: float = Field(default=0.6, gt=0, description="Thinnest wall that prints and survives washing")
    min_drain_mm: float = Field(default=3.5, gt=0, description="Narrowest opening resin drains out of a hollow by")
    max_overhang_deg: float = Field(
        default=45, ge=0, lt=90, description="Steepest overhang, from vertical, that cures without support"
    )
    example = {"process": "resin", "min_wall_mm": 0.6}
    findings = {
        "resin_wall_thickness": ("A resin wall thinner than the minimum", "Thicken the walls to the minimum."),
        "resin_drains": (
            "A hollow in a resin part without drain holes",
            "Add drain holes of at least the minimum diameter to every hollow.",
        ),
        "resin_supports": (
            "What of a resin part cures on support",
            "Turn the part so less of it faces down (`up`), or chamfer what does; supports mark the faces they hold.",
        ),
    }

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.builtin.processes.checks import resin_checks

        return resin_checks(body, self)

    def way(self, body: Body, needs: PrintNeeds) -> PrintWay:
        from hitlcad.builtin.processes.checks import resin_way

        return resin_way(body, self)


class CncMillingProcess(Process):
    """3-axis CNC milling from +Z (the part's own frame): every face but the bottom reachable along a clear
    path up from it, concave vertical corners with at least the tool radius, and no corner deeper than
    `max_depth_ratio` × the cutter that finishes it (its own diameter, or the tool's where it is sharp)."""

    process: Literal["cnc_milling"]
    tool_diameter_mm: float = Field(default=6.0, gt=0)
    max_depth_ratio: float = Field(default=4.0, gt=0)
    ignored_area_mm2: float = Field(
        default=1.0, ge=0, description="Faces out of reach over less than this (a sliver at a seam) do not fail"
    )
    example = {"process": "cnc_milling", "tool_diameter_mm": 6}
    findings = {
        "cnc_setups": (
            "A face a 3-axis mill cannot reach from +Z",
            "Make every face reachable from +Z of the part's frame, or split the part into setups.",
        ),
        "cnc_inside_corners": (
            "An inside corner sharper than the cutter",
            "Round inside vertical corners to at least the cutter's radius.",
        ),
        "cnc_pocket_depth": (
            "A pocket too deep for the cutter that finishes it",
            "Make the pocket shallower or wider: its depth within the ratio times the cutter's diameter.",
        ),
    }

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.builtin.processes.checks import cnc_milling_checks

        return cnc_milling_checks(body, self)


class SheetMetalProcess(Process):
    """Sheet metal: the thinnest and the median wall are `thickness_mm` (± `thickness_tolerance_mm`), and
    every bend's inner radius is at least `min_bend_radius_mm`. A bend is a pair of coaxial cylinders, inside
    r and outside r + the thickness, or a sharp inside edge between two faces each with the sheet's other
    side behind it (radius 0); the corners of a cut-out are not bends."""

    process: Literal["sheet_metal"]
    thickness_mm: float = Field(gt=0)
    thickness_tolerance_mm: float = Field(default=0.05, ge=0)
    min_bend_radius_mm: float = Field(gt=0)
    example = {"process": "sheet_metal", "thickness_mm": 1.5, "min_bend_radius_mm": 1.5}
    findings = {
        "sheet_thickness": (
            "A sheet metal part not of one thickness",
            "Model the part at the sheet's thickness throughout.",
        ),
        "sheet_bend_radius": (
            "A bend tighter than the sheet allows",
            "Give each bend at least the minimum inside radius.",
        ),
    }

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.builtin.processes.checks import sheet_metal_checks

        return sheet_metal_checks(body, self)


class InjectionMoldingProcess(Process):
    """Injection molding in a two-plate mold that opens along Z of the part's own frame: faces along the pull
    need at least `min_draft_deg` of draft, every face must leave its mold half on a clear path along Z (no
    undercuts), and no wall may be over `max_wall_ratio` × the median wall."""

    process: Literal["injection_molding"]
    min_draft_deg: float = Field(default=1.0, ge=0)
    max_wall_ratio: float = Field(default=2.0, gt=1, description="Thickest wall, × the median wall")
    ignored_area_mm2: float = Field(
        default=1.0, ge=0, description="Faces that fail over less than this (a sliver at a seam) do not fail"
    )
    example = {"process": "injection_molding", "min_draft_deg": 1}
    findings = {
        "molding_draft": ("A molded face without draft", "Draft faces along the pull by at least the minimum angle."),
        "molding_undercuts": (
            "A molded face that locks the part in the mold",
            "Remove the undercut, or plan a side action.",
        ),
        "molding_walls": (
            "Molded walls of uneven thickness",
            "Keep walls even: core out sections thicker than the ratio times the median wall.",
        ),
    }

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.builtin.processes.checks import injection_molding_checks

        return injection_molding_checks(body, self)
