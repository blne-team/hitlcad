# Reusable design cases

These cases illustrate decisions across CAD tasks. Dimensions, materials and acceptance criteria
come from the actual brief. Each case describes a problem, competing needs, a design move and an
evaluation; none is a universally preferred product architecture.

## Locate accurately while allowing assembly

**Problem:** a plate must return to the same position after removal. **Competing needs:** positional
repeatability, assembly clearance and manufacturing variation.

Identify which degrees of freedom need location and which features supply it. Consider a mounting
face, a round locating feature and a second feature relieved in one direction rather than assuming
several close-fitting round pins will always assemble. Keep clamping and locating functions clear.
The appropriate locating scheme depends on the required motion, materials and precision.

**Evaluate:** inspect the datum relationships, worst-case clearances and installation direction.
Nominal alignment does not prove repeatability or assembly under variation. Physical fixtures may
need tolerance analysis and testing beyond the framework's geometry checks.

## Make a serviceable assembly

**Problem:** a component fits inside a housing but needs replacement. **Competing needs:** compact
packing, stiffness, tool access and a useful removal opening.

Reserve the component's removal path, fastener access and connector release space before fixing
the exterior. Compare a split housing, removable panel and open frame against the same contents.
Choose seams from the assembly and service strategy, then integrate them into the composition.

**Evaluate:** review installation and removal separately. Check the actual manufacturing envelope
for every made part. A small assembled bounding box does not establish accessibility or that each
piece fits the intended machine.

## Adapt a profile to its process

**Problem:** a two-dimensional bracket outline must become a usable part. **Competing needs:** a
compact silhouette, stiffness, attachment clearances and process limitations.

Compare a flat cut plate, a bent sheet and a milled section. A bend may provide depth efficiently
but introduces tooling, relief and feature-placement constraints. A milled pocket needs cutter
access and suitable internal radii. A printed version introduces orientation and support choices.
Preserve the attachment interfaces while changing the construction.

**Evaluate:** compare the process assumptions and load paths. Equal silhouettes or material volumes
do not imply equal performance. Verify geometry and process conditions separately from load capacity.

## Preserve a pattern through dimensional changes

**Problem:** a panel contains repeated slots or mounting features. **Competing needs:** consistent
rhythm, protected borders and valid end conditions over several sizes.

Choose which values stay fixed: pitch, count, opening size or margin. Derive the others and state
what happens at a threshold. Bought-part mounting patterns stay tied to their specification even
when the surrounding panel changes. Keep decorative repetition separate from exact mating geometry.

**Evaluate:** build the smallest and largest supported variants and a value near a count transition.
Inspect end features, webs, boss clearances and connector count. A successful middle size is weak
evidence for the whole family.

## Develop an expressive object

**Problem:** a figurative or sculptural object must communicate a recognizable character or gesture.
**Competing needs:** expressive proportion, coherent three-dimensional form, stable attachment and
manufacturable detail.

Establish gesture and primary masses before secondary features. Compare silhouette and negative
space in several views. Choose part boundaries through sufficiently thick regions, preserving
important visible contours. Keep mating dimensions independent of expressive scaling.

**Evaluate:** inspect the back and top as well as the main view; review small details, joints and
the selected manufacturing process. A good single view does not establish the complete form,
physical stability or successful fabrication.
