# design.json reference

**Connectors** (`"connectors": {"name": {...}}` on a part). A frame's origin is where the mating
connector's origin will be, and its Z points out of the part, so mated connectors face each other;
X fixes the rotation about Z where it matters (a bolt pattern's clocking), `symmetry` says where it
does not. `count` (a number or a parameter name) is how many frames a pattern has. Kinds:
$connectors

**Mates** (`"mates": [...]`). Ends are `part.connector` (every instance of the part),
`part[k].connector` or `component.export`; patterns pair one-to-one by position. After placement the
frames must agree in what the type fixes (within `verification`). Optional: `fit`
(`{"clearance": [min, max]}` diametral mm, negative is interference, or `{"iso": "H7/g6"}`, with hole
and shaft modelled at one basic size: the result gives the limits for the drawing) between a hole and a
shaft; `limits` (`{"angle": [lo, hi]}` degrees, `{"travel": [lo, hi]}` mm), swept for
collisions; `offset` (mm along a's Z); `flip: true` when b's Z runs with a's (a shaft entering a bore
from the end opposite its declared opening); `overlap: true` when b's part is set into a hole of a's
part and overlaps its wall (a heat-set insert's knurls, a modelled thread): a has no material on the
hole's axis where b sits. An interference fit allows the ring its diameters make, no more. Types:
$mates

**Relations** (`"relations": [...]`), verified from the frames and the connectors' nominal values:
$relations

**Fastener sets** (`"fasteners": [...]`): seated, the shank clears every through hole, the thread
engages at least the into part's material minimum (or `engagement` × diameter) without bottoming out:
$fasteners

**Requirements** (`"requirements": [{"text": "…", "check": {…}}]`). `target` is a part (its own
frame), an instance or component path (placed), or nothing (the whole design). Checks:
$checks

**Manufacturing** (`"manufacturing"` on the design, or on a made part). Each made part is checked in
its own frame:
$processes

**Components** (`"components": {"name": {"design": "../other", "configuration": …, "bind":
{"param": "my_param" or "interfaces.value"}, "flexible": false}}`). A component exposes connectors
through its own `exports` (`{"output": "carrier.output"}`); mate to those, never inside it.

**Plugins** (`"plugins": {"hitlcad-lasercut": ">=0.1"}`): the installed plugins a design needs beyond
hitlcad's own, for the kinds, types, checks and processes they add (`list_backends` lists those installed).
Their reference files:
$references

**Configurations** (`"configurations": {"ratio_5": {"sun_teeth": 12}}`), **verification**
(`{"position_mm", "angle_deg", "motion_steps"}`, defaults from hitlcad's settings) and **outputs**
(exporters run on every build: see `list_backends`) complete the file.
