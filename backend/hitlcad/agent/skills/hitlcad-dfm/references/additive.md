# Additive design decisions

Use for FDM or resin parts. Begin with the intended orientation and what the surfaces and interfaces
must do. Read process settings from the installed generated guide rather than copying defaults here.

## FDM

Consider orientation, support removal and the direction of important loads together. Layered
construction makes direction relevant to performance. Split a part when it gives a better surface,
access or orientation, accounting for the added joint and assembly work. Plan thin sections, underside
transitions and horizontal openings rather than relying on a universal overhang angle.

Model the part in its manufacturing frame, with a useful bed-contact surface. A chamfer or adjusted
hole profile can reduce an unsupported underside where function allows it. Keep precise circular
interfaces circular when the intended mating feature requires that geometry.

Do not treat slicer infill as equivalent to a modeled wall or structural analysis. Check actual
piece dimensions against the intended bed, including practical room for brim/support where needed.
The wall minimum accepted by a screen may be unsuitable for the object's use.

Source: [Prusa: Modeling with 3D printing in mind](https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135)
discusses orientation, overhangs, splitting and fit adjustments. Apply its recommendations with the
actual printer settings and material; they are not universal manufacturing constants.

## Resin

Plan supports around important visible and mating surfaces. Hollow parts need accessible drainage
and cleaning paths. A cavity that looks open from one view may still trap resin or create suction
in its printing orientation. Review the orientation with the relevant slicer or manufacturer guidance;
the framework's wall/drain checks do not simulate peel forces or support layout.

Source: [Formlabs: SLA orientation](https://formlabs.com/support/Model-Orientation/) explains how
orientation affects finish, dimensional accuracy and print success, including problems with enclosed
or cup-like geometry. Use printer- and resin-specific guidance for numerical limits.

## Fits and coupons

Separate nominal geometry from manufacturing allowance and required assembled clearance. Put shared
dimensions in interfaces.py and declare the fit at the joint. In the current printed-process model,
`hole_allowance_mm` describes holes coming out smaller and `shaft_allowance_mm` shafts coming out
larger; fit calculations account for them. Avoid applying the same compensation twice.

When fit is critical and calibration is absent, propose a small coupon using the intended mating
geometry, orientation and process. A coupon can include a few candidate clearances labeled with their
values. Do not invent measured calibration values. Nominal fit checks establish the declared model,
not the accuracy of an uncalibrated printer.

For both processes, rebuild after changing orientation, thin features or mating dimensions. Report
support/drain choices and what still depends on physical printing. The fully declared teaching example
in `hitlcad-design` demonstrates an FDM assembly with catalog inserts and screws; its dimensions are
example-specific, not recommended defaults for unrelated products.
