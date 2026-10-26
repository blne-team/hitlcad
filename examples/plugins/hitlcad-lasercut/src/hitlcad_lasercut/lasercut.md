# Laser cutting (hitlcad-lasercut)

A part cut from sheet: `"manufacturing": {"process": "laser_cut", "thickness_mm": 3, "kerf_mm": 0.15}`, and
the design names the plugin: `"plugins": {"hitlcad-lasercut": ">=0.1"}`.

- Model the part lying flat in its own frame: a profile extruded `thickness_mm` along Z from Z = 0.
- No hole narrower than the sheet is thick.
- The beam takes `kerf_mm`: holes come out that much larger and outlines that much smaller; fits account for it.
- `from hitlcad_lasercut.blocks import plate` makes a plate with round holes and its `holes` connector frames:

```python
from hitlcad_lasercut.blocks import plate


def build(p, i):
    shape, connectors = plate(80, 40, p["sheet"], holes=i.mount_holes, hole_diameter=3.4)
    return {"bracket": (shape, connectors)}
```
