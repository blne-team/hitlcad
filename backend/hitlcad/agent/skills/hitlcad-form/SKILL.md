---
name: hitlcad-form
description: "Develop or critique the silhouette, proportions, negative space, repeated features and surface transitions of a hitlcad model. Use for shaping a product or figurine, translating vector or picture references into form, or improving visual composition. Supplements hitlcad and its backend code contract; does not replace manufacturing checks."
---

# Form and composition

Use the human's brief and selected style as the criteria. Work through the existing hitlcad tools;
the `hitlcad` and backend skills remain the operating contracts.

## Build a hierarchy of form

Establish the main masses, their relationships and the spaces between them. Inspect the front,
right, top and isometric previews. A recognisable single profile can still make an unsatisfactory
three-dimensional object. When the human gives a picture, model against it with a `silhouette`
requirement: the hitlcad skill's [pictures reference](../hitlcad/reference/pictures.md) says how.

Resolve silhouette and proportion before small details. Give secondary features a role: reinforce
direction, communicate use, connect masses or articulate a boundary. Keep ornament subordinate to
the hierarchy unless the brief deliberately calls for dense pattern or competing focal points.

Make the composition decisions directly: identify the dominant volume, place secondary masses to
support its purpose, reserve useful negative space, and relate seams, borders and repeated details
to a small set of datums. Decide which region should attract attention and which should recede.
Use consistent transitions where the forms belong together and deliberate boundaries where functions
or assembly change. Inspect the actual preview; a plausible construction description is insufficient.

For profile-driven form, reason from closed outlines and material regions: distinguish a filled
profile from a drawn stroke, give openings protected borders, and derive repeated motifs from shared
dimensions. For organic form, begin with gesture and primary masses, develop secondary forms, and
keep attachment geometry independent of expressive scaling. These methods are available immediately;
the references below add conditional technical detail and examples.

Read the appropriate reference:

- [Composition](references/composition.md): balance, spacing, hierarchy and surface transitions.
- [SVG to solid](references/svg-to-solid.md): profiles, motifs, negative space and code examples.
- [Organic form](references/organic-form.md): gesture, primary masses, sculptural parts and joints.
- [Modeling decisions](references/modeling-decisions.md): choose extrude/revolve/sweep/loft,
  B-rep/mesh representation, selectors and mesh resolution using adapted local skill insights.
- [Section-form example](assets/section-form/design.json), [geometry](assets/section-form/parts/form.py)
  and [placement](assets/section-form/assembly.py): a complete Blender design with gesture controls,
  an open cavity and a closed wall/floor mesh built by `hitlcad_blender.section_form`. Use it to study parametric form, not as
  an application-rated container.

Read a reference when it resolves a relevant uncertainty; ordinary composition work does not require
reading all three. For existing geometry or an unclear selection, use `explore_design`. For a
dimensional claim, use `list_geometry` and `measure_geometry`; for a changed form, build and inspect
the returned views, using `compare_revisions` when preservation matters. See the
[tool decision guide](../hitlcad-design/references/tool-decisions.md) for concrete calls.

Use `hitlcad-style` for a specific visual vocabulary and `hitlcad-dfm` when shaping affects walls,
tool access, draft, support, joints or contact surfaces. Do not load all style or process references.

## Expose meaningful controls

Prefer controls such as taper, head/body ratio, border width, rib pitch or transition length over
independent vertex coordinates. Derive related dimensions together. Exact mating geometry remains
driven by interfaces, even when the outer form is expressive.

Proportional relationships are hypotheses to judge against use and appearance; there is no universal
ratio that makes a design good. Keep real dimensions in contact regions and bought-part interfaces.
Do not scale a screw hole or grip clearance with the decorative motif around it.

## Critique what is visible

Translate human adjectives such as “lighter”, “friendlier” or “more architectural” into a proposed
geometric interpretation, showing alternatives when the distinction matters. Honor pinned regions
and previously accepted proportions. Clarify a conflict with functional geometry; proceed on
reversible refinements within the authorized request. After building, explain which visible features
changed and invite judgment on appearance without implying that a passing check settles taste.

Name the view and feature, explain the effect and propose a focused adjustment. Evaluate silhouette,
spacing and transitions under consistent presentation. Colour can clarify hierarchy, but geometry
should support the intended reading too. Do not change a required shape simply to hide an awkward
view, or claim comfort, stability or flow from appearance alone.
