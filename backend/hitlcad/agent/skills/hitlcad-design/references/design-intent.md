# Design intent and changes

Use when choosing parameters, revising a layout or deciding which relationships should survive a
change. Design intent describes expected behavior, not just the current dimensions.

## State relationships before equations

Write the invariant in ordinary language: the shaft remains concentric with its bearing; the vent
field stays inside the removable panel; holes remain symmetric around the mounting datum; a lid
keeps its engagement when enclosure height changes.

Choose a small set of independent inputs. Derive dependent values once. Useful distinctions:

| Value | Where it belongs in hitlcad |
| --- | --- |
| Human choice, such as enclosure width or grip rake | Parameter, with a meaningful label and range |
| Value shared by mating parts, such as bolt circle or engagement | Function in interfaces.py |
| Fixed bought-part dimension | Catalog or vendor data, shared through interfaces when needed |
| Local construction detail | Part code or a shared lib helper when actually reused |

Configurations express supported variants; they do not establish that every combination of slider
values is valid. Set ranges from geometry and process constraints. Validate coupled constraints
where values are computed, and explain why an invalid combination fails.

## Use datums that express purpose

Reference a mounting plane, centerline or shared envelope when that is the relationship intended.
Avoid a chain of unrelated offsets that merely happens to align at the current dimensions. Keep
geometry and placement driven by the same interface values. A cosmetic fillet should not become the
datum for a critical mounting pattern.

Example: increasing case width should move its side walls and alter panel width while preserving
the motherboard's mounting pattern. Scaling every coordinate would move catalog interfaces too.

## Probe meaningful changes

Use `explore_design` before editing a parameter or interface. For substantial parametric work,
exercise a small, normal and large supported variant, plus a meaningful topology boundary such as
the smallest panel that still contains its hole pattern. Build declared configurations through
hitlcad and use `compare_revisions` to inspect consequences. Do not exhaustively sample a continuous
range or claim that a few samples prove all values valid.

Look for clipped patterns, disconnected bodies, vanishing walls, lost engagement and misplaced
connectors. Review whether the intended proportions remain suitable as well as whether the build
passes. If variants need different architecture, make that distinction explicit rather than hiding
it in a collection of arbitrary dimensional corrections.

## Source

[SOLIDWORKS: Design Intent](https://help.solidworks.com/2019/english/SolidWorks/acadhelp/c_Design_Intent.htm?id=3e6f17cfd96949c5b45d27d81a5bc052)
explains model behavior under dimensional changes. The mapping to parameters, interfaces and
configuration review above is specific to hitlcad; it does not authorize cross-importing part files.
