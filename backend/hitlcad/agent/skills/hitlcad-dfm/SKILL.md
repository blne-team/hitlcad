---
name: hitlcad-dfm
description: "Choose manufacturing processes, part divisions, joints, fasteners, bearing arrangements and fits for general CAD tasks implemented in hitlcad. Use when a design must be made or assembled, a process check fails, or manufacturing tradeoffs affect the concept. Covers FDM, resin, CNC milling, sheet metal and injection molding. Supplements the hitlcad operating contract."
---

# Designing for manufacture and assembly

Start from the intended material, quantity, equipment and required function. Use the `hitlcad` and
backend skills for the actual file and tool contracts. The following decisions can be revisited as
the design develops; do not run every process review for every task.

## Choose a decision path

| Situation | Read | Next useful evidence |
| --- | --- | --- |
| Process or material is open | [Process selection](references/process-selection.md) | Candidate constructions and equipment constraints |
| A part will be printed | [Additive design](references/additive.md) | Orientation, walls, support/drain strategy and fit calibration |
| A part will be cut, bent, milled or molded | [Cutting and forming](references/cutting-and-forming.md) | Tool access, thickness, bends or mold release |
| Pieces must locate, move or stay together | [Joints and fits](references/joints-and-fits.md) | Assembly sequence, catalog data, tolerance and retention |

Use `list_backends` when installed capabilities are unknown and `search_catalog` before choosing
standard hardware geometry. For an existing failure, inspect the build finding and its section view
before redesigning the whole product. Use `explore_design` for shared-value dependencies and
`measure_geometry` for a concrete geometric question. Rebuild the changed design, then check affected
configurations. The [tool decision guide](../hitlcad-design/references/tool-decisions.md) provides
adaptive call examples.

## Work with the human's constraints

Keep their stated process, material, equipment and bought components unless they authorize a change.
Explain a concrete incompatibility, such as an inaccessible cutter path or an oversized printed
piece, and offer viable choices. Ask for missing machine or material information when it changes
the construction; otherwise label a provisional assumption and proceed with reversible work.

Choose divisions with assembly, tools, service access and visible seams in mind. A successful
assembled model can still be impossible to put together. Geometric interference checks do not
establish a collision-free installation path unless that path was actually represented and checked.

## Use evidence with its conditions

Attach process, material, orientation, source and calibration status to important numerical advice.
Framework defaults are screening settings, not universal shop capabilities. Do not loosen a limit,
hide a collision or replace a measured connector with a datum frame merely to obtain a passing build.

Manufacturing checks cover particular geometric conditions. Distinguish those results from
unverified load capacity, fatigue, shrinkage, warpage, comfort and production yield. Report what
needs physical validation without blocking ordinary design work on hypothetical concerns.
