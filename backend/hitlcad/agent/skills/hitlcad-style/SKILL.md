---
name: hitlcad-style
description: "Develop a coherent visual and construction language for a CAD object implemented in hitlcad. Use when the human requests a style, asks for aesthetic alternatives, or wants a consistent product family. Includes quiet functional, exposed structure, timber-inspired, streamlined, sculptural organic and geometric ornamental directions. Preserve the brief's function and interfaces."
---

# A deliberate design language

Ground the direction in the object's use, audience, materials, construction and the human's
references. A named style is a starting vocabulary, not a substitute for understanding the brief.
Use the `hitlcad` operating skill and the selected backend contract for implementation.

## Choose and express a direction

If the human specifies a style, follow it and resolve only consequential ambiguity. If the brief
is open, brainstorm a few distinct directions when useful: describe silhouette, massing, boundaries,
repetition, surface treatment and material expression. Explain what each direction changes in use
or manufacture. Recommend one against their priorities; do not impose a personal favorite.

Write a short design language in the existing brief's `summary` or `features`: dominant form,
secondary motif, edge/transition family, seam logic and any deliberate exception. Keep this short
enough to guide later edits. Use parameters for actual tunable geometry, not an invented style schema.

Read only the direction being explored or implemented:

| Direction | Reference | Useful starting question |
| --- | --- | --- |
| Quiet functional | [Guide](references/quiet-functional.md) | What should be obvious and what should recede? |
| Exposed structure | [Guide](references/exposed-structure.md) | Which frame, joints and modules explain construction? |
| Timber inspired | [Guide](references/timber-inspired.md) | How can members, bays and joints organize the object? |
| Streamlined | [Guide](references/streamlined.md) | What contour communicates direction or flow? |
| Sculptural organic | [Guide](references/sculptural-organic.md) | Which gesture and masses carry the expression? |
| Geometric ornamental | [Guide](references/geometric-ornamental.md) | Which motif and transformation rule generate variation? |

These are expandable directions, not an exhaustive classification. For another requested style,
derive a similarly concrete vocabulary from its references. Mix directions when their relationship
is intentional; keep one organizing idea legible instead of accumulating unrelated motifs.

## Implement and critique

Begin with geometry that communicates the direction without relying on presentation effects.
Coordinate functional openings, seams, controls and hardware with the form. Preserve exact mating
dimensions while changing the surrounding envelope. Use `hitlcad-form` for composition and
`hitlcad-dfm` when a choice changes walls, joints or process feasibility.

Use the existing preview and human pins to compare the result with the intended language. A specific
visual concern may call for `explore_design`, `measure_geometry` or `compare_revisions`; a new style
idea may need only a sketch or explanation before geometry. The
[tool decision guide](../hitlcad-design/references/tool-decisions.md) covers these branches.

Show concrete alternatives when taste determines the choice, then preserve the accepted direction
during later edits. Keep ordinary refinement moving within the authorized task. Stop when the
requested result is coherent and verified to the available evidence; do not add decorative detail
solely because there is time or empty surface.
