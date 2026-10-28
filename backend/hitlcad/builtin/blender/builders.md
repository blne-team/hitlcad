# Blender construction helpers

Import from `hitlcad_blender` in part files. The build process supplies this package inside Blender;
it needs only the standard library, bpy and mathutils. 1 unit = 1 mm, Z up. Helpers return
`(object, connectors)`: return that value under the exact part name declared in design.json.

## Hollow section form

```python
from hitlcad_blender import section_form


def build(p, i):
    return {
        "form": section_form(
            sections=[(0, p["radius"]), (p["height"], p["radius"])],
            wall=p["wall"],
            floor=p["wall"],
            chord_tolerance=0.05,
            name="form",
        )
    }
```

All arguments are keyword-only:

| Argument | Meaning |
| --- | --- |
| `sections` | At least two `(z, outer_radius)` pairs; Z starts at 0 and strictly increases |
| `wall` | Positive radial offset, smaller than every outer radius |
| `floor=None` | Positive inner floor height, below the mouth; defaults to wall |
| `chord_tolerance=0.05` | Maximum circumferential radial faceting error, mm |
| `name="form"` | Name shown when the human picks the object |

The helper creates outer and inward-facing inner walls, a floor, and a joined rim. The mouth is
open; the material boundary is closed. The silhouette is linear between sections; add sections
where axial curvature needs resolution. Ring segments are chosen from the largest radius and
chord tolerance, rounded up to a multiple of four to preserve the X/Y envelope. Requests needing
more than 4096 segments per ring are rejected with a resolution hint.

`wall` is a radial offset, **not uniform thickness perpendicular to a sloping surface**. Use a
`wall` requirement and manufacturing results to verify actual thickness. The construction does not
establish containment, food contact, load capacity or printability by itself.

Returned connectors are datums: `bottom` at Z=0 facing -Z, and `rim` at the mouth center facing +Z.
Declare them as `{"kind": "frame"}` if needed; the center of the mouth is empty, not a planar face.
The framework ignores undeclared returned connectors.

Invalid sections raise a `ValueError` naming the section and relationship to fix, before creating
scene objects. For example, `sections[2].outer_radius ... must exceed wall` asks for a larger radius
or a smaller intended wall offset. Preserve the brief's required dimensions when choosing the repair.

Start from the complete [section-form recipe](../../hitlcad-form/assets/section-form/design.json),
[part](../../hitlcad-form/assets/section-form/parts/form.py) and
[assembly](../../hitlcad-form/assets/section-form/assembly.py). Its controls vary height, radius,
taper and swell without requiring you to write face indices or winding logic.
