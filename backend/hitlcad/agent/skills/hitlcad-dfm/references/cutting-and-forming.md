# Cutting, bending and molding

Read only the process section relevant to the task. The built-in process's assumed manufacturing
direction is the part's own frame, separate from its placement in the assembled product.

## CNC milling

Choose the stock, accessible faces and workholding strategy before small pockets. A rotating cutter
leaves an internal radius; tool reach and holder clearance can constrain deep features. Favor
accessible geometry or a deliberate second operation over decorative detail that cannot be cut.
Give a mating square corner relief only where the functional contact allows it.

In hitlcad, `cnc_milling` screens +Z access, internal corners and pocket depth against the declared
tool settings. It does not generate toolpaths, prove fixture clearance or certify thin-wall behavior.
If a feature requires another setup, explain that plan and its unverified conditions rather than
pretending one +Z check covers it.

Source: [Protolabs: CNC milling design](https://www.protolabs.com/en-gb/services/cnc-machining/cnc-milling/high-speed-cnc-milling/).
Supplier limits depend on their tooling and service; derive the actual cutter assumptions for the task.

## Sheet and profile cutting

A cut profile needs sufficient material between openings and around attachment features. Account
for kerf using the installed process/plugin's convention. An SVG stroke's display width is not
automatically the tool's cut width. A flat profile and a folded sheet solve different structural
and assembly problems.

For bent sheet, maintain nominal thickness and select an inside radius compatible with the material
and tooling. Plan bend relief, flange length, feature distance from bends and bending sequence.
Holes near a bend can distort; nominal hole size alone cannot establish final fit. Obtain bend
allowance or deduction from the actual process when a developed blank matters.

The built-in sheet screen checks thickness and bend radius. It does not check every flange length,
hole-to-bend distance, bend relief, developed blank or press-brake collision. Keep these as explicit
design reviews unless an installed plugin supplies the relevant evidence.

Source: [Protolabs: sheet metal guidelines](https://www.protolabs.com/services/sheet-metal-fabrication/design-guidelines/).
Its feature distances and available radii are supplier-specific; do not promote them into universal
framework limits.

## Injection molding

Choose a release direction and a plausible parting strategy. Use draft on relevant pull surfaces,
control wall variation and core out bulky regions. Think through how ribs, bosses and textures affect
release and visible surfaces. Undercuts may require a different split or tooling strategy; a side
action is a manufacturing decision, not a geometry-error suppression flag.

The built-in screen assumes a two-plate mold opening along local Z and checks draft, release access
and wall ratio. It does not simulate filling, shrinkage, cooling, ejection, gate placement or warpage.
A printed mock-up does not establish moldability.

Source: [Protolabs: designing for moldability](https://www.protolabs.com/en-gb/resources/guides-and-trend-reports/designing-for-mouldability-volume-1/).
Use material- and tool-specific guidance for actual thickness and draft; the qualitative decisions
above do not establish a production-ready mold.
