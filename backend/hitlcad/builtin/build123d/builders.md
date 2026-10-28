# build123d construction helpers

Import from `hitlcad_build123d` in part files. The build process supplies this package; no installation
or direct export is needed. Dimensions are millimetres, Z up. Helpers return `(shape, connectors)`:
return that value under the exact part name declared in design.json.

## Mounting plate, with optional screw bosses

```python
from hitlcad_build123d import mounting_plate


def build(p, i):
    return {
        "plate": mounting_plate(
            width=p["width"],
            depth=p["depth"],
            thickness=p["thickness"],
            holes=i.bolt_xy,
            hole_diameter=p["hole_diameter"],
            boss_height=p["boss_height"],
            boss_diameter=p["boss_diameter"],
        )
    }
```

All arguments are keyword-only:

| Argument | Meaning |
| --- | --- |
| `width`, `depth`, `thickness` | Positive plate dimensions; centered in X/Y, bottom at Z=0 |
| `holes` | One or more `(x, y)` centers, in input order, wholly inside the plate |
| `hole_diameter` | Positive through-hole diameter; no thread or manufacturing allowance implied |
| `boss_height=0` | Additional height above the plate; zero makes a plain drilled plate |
| `boss_diameter=None` | Required with raised bosses; must exceed the hole diameter |

Returned connectors:

| Name | Frame | Appropriate declaration |
| --- | --- | --- |
| `bottom` | Plate center at Z=0, facing -Z | `{"kind": "face"}` when the center is material; otherwise a datum |
| `top` | Plate center at Z=thickness, facing +Z | `{"kind": "face"}` when the center is material; otherwise a datum |
| `holes` | Upper hole openings at Z=thickness+boss_height, facing +Z | `{"kind": "hole", "count": 4}` for four holes |

Declare the connectors you use; the framework ignores undeclared returned connectors. These are
plain round bores, not threads, catalog fastener seats or proven screw fits. For catalog-sized
clearance/tap/insert holes, use the catalog provider's hole operations in the backend guide.

An invalid dimension or hole position raises a `ValueError` naming the argument/index to change.
For example, `holes[0] ... leaves no plate border` asks you to move that center inward or enlarge
the plate. Do not remove its declared hole or weaken a requirement to hide the failure.

Start from the complete [mounting-plate recipe](../../hitlcad-design/assets/mounting-plate/design.json),
[part](../../hitlcad-design/assets/mounting-plate/parts/plate.py),
[interfaces](../../hitlcad-design/assets/mounting-plate/interfaces.py) and
[assembly](../../hitlcad-design/assets/mounting-plate/assembly.py). Copy the folder's source files to
the design folder, change the brief and parameter values, and use the normal structure/build workflow.
The recipe declares measured hole and size requirements and a compact configuration.
