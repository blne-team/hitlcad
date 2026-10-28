# From vector composition to solid geometry

Use when a design is driven by an outline, repeated motif, relief or cutout. SVG is useful for
thinking about paths and composition; a rendered graphic is not automatically a manufacturable
profile. This guide does not assume a native SVG-import tool in hitlcad.

## Translate the geometry deliberately

A closed filled outline can suggest an extrusion. An internal contour can suggest a hole. A stroke
needs a width and a closed boundary before it can represent material or a slot. Gradients, shadows,
masks and clipping can create apparent shape without defining a solid. Establish units and resolve
transforms before treating any coordinates as millimetres.

Keep contours closed and inspect self-intersections, tiny islands and narrow necks. Choose whether
disconnected shapes are separate parts, raised details attached to a backing, or openings. Boolean
subtraction can leave weaker webs than the original drawing suggests.

Build a motif once and place it from derived coordinates. In CAD, repeated appearance still needs
appropriate bodies, walls and connectors; an SVG group's organization does not create an assembly.

## Small code example: a bordered vent field

This is a fragment inside a build123d part's `BuildPart` context, after making a lid whose bottom
is Z = 0. `i.vent_centers`, `i.vent_width`, `i.vent_length` and `i.lid_thickness` are shared values.

```python
from build123d import Align, Box, Locations, Mode, Pos

# Use within the lid's BuildPart context.
for x in i.vent_centers:
    with Locations(Pos(x, 0, 0)):
        Box(
            i.vent_width,
            i.vent_length,
            i.lid_thickness,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
            mode=Mode.SUBTRACT,
        )
```

The fragment has no build or export side effects. The complete example in `hitlcad-design` supplies
the interfaces, material, connectors and placements. The rectangular slots are chosen to make the
spacing relationship obvious; a rounded profile is a separate geometric choice.

## Evaluate

Inspect front and top profiles and the isometric depth. Measure walls and gaps through hitlcad.
Review islands, end margins, corners and the smallest supported variant. A decorative opening near
a screw boss may consume necessary material even if its outline looks well composed. Open area is
a geometric property; airflow requires more evidence.

## Sources

[W3C SVG paths](https://www.w3.org/TR/SVG2/paths.html) defines lines, curves, arcs and closed paths.
[SVG document structure](https://www.w3.org/TR/SVG2/struct.html) describes grouping and reusable
elements. The material interpretation and vent construction above are CAD adaptations, not SVG
manufacturing guarantees.
