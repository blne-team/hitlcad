# Select a construction and process

Use when process choice can change the architecture. Compare processes against the actual quantity,
dimensions, precision, material, finish and available equipment; do not choose solely by visual style.

| Process | Useful design tendency | Questions that change the construction |
| --- | --- | --- |
| FDM | Shells, ribs, integrated features; deliberate print orientation | Which direction carries load? Can support be removed? Does each piece fit the bed? |
| Resin | Fine detail and small sculptural features; supported surfaces | Can hollows drain and be cleaned? Which surfaces tolerate support marks? |
| CNC milling | Accessible pockets, prismatic stock and precise interfaces | Can the cutter and holder reach? How is the work held and referenced? |
| Sheet metal | Constant-thickness panels, flanges and folded sections | Which tooling, bend radii, reliefs and bend sequence are available? |
| Injection molding | Shells with controlled thickness, release direction and integrated features | What quantity justifies tooling? Where are the parting line, draft and undercuts? |

The table guides questions; it does not prove cost, performance or availability. Material grade,
finishing and equipment can reverse an apparent advantage. A printed prototype can communicate a
molded product's form while still needing a separate production design.

## Capture consequential assumptions

Record the selected process and material in the made part declarations. Use brief assumptions for
equipment envelope, finish expectations, assembly method and unresolved performance targets. For
supplier-specific rules, record the supplier and conditions rather than generalizing their limits.

For material selection, consider the use temperature, moisture or chemical exposure, UV, repeated
deformation, contact requirements and finish as relevant to the actual task. Look up grade-specific
data when a property determines the design. A material name in the framework supplies only its
registered properties; it does not certify suitability for every application.

## Map advice to current checks

The generated `hitlcad` reference is the installed authority. The built-in processes currently check
FDM walls/overhangs/bed contact, resin walls/drains, CNC access/corners/pocket depth, sheet thickness/
bend radius, and molding draft/undercuts/wall variation. These are bounded geometric screens.

Machine-envelope checks can be expressed as per-part `fits_within` requirements using the actual
dimensions and orientation. There is no automatic universal printer-volume selection. Multiple CNC
setups and per-process manufacturing orientations need care: the current part frame is how it is
made, and the built-in CNC screen assumes access from +Z. Do not claim a multi-setup plan was checked
by that single-direction screen.

## Source

[Formlabs: Design for Manufacturing](https://formlabs.com/eu/blog/design-for-manufacturing-with-3d-printing/)
discusses technology-specific DFM and assembly considerations. The comparison table and current
check mapping are our design guide and an account of hitlcad's implementation, respectively.
