# Architectural patterns for objects

Use architectural reasoning to organize a CAD product's structure, spaces and assembly. These are
adaptations for product design, not building-code guidance or structural certification.

## Repeated bay

**Problem:** several modules need consistent support and replacement. **Competing needs:** repetition
simplifies assembly, while edge conditions and different contents need exceptions.

Choose a bay from actual contents and tool access. Derive mounting positions and panel boundaries
from it. Expose module count and pitch only where useful. A repeated vent field can reinforce the
same rhythm without making every surface identical.

**Failure:** a nominal grid forces a larger drive, connector or end panel into an undersized space.
**Evaluate:** build the end conditions as well as the repeated middle; inspect replacement paths.

## Frame and infill

**Problem:** structural support and the outer surface have different jobs. **Competing needs:**
separate panels improve access and variation, but add parts, joints and potential looseness.

Establish a frame, attachment datums and panel removal direction. Decide which pieces carry loads
and which close openings. Use genuine panel boundaries to organize seams. Avoid describing a frame
as stiff solely because it looks structural.

**Failure:** panels cannot be removed without dismantling the frame, or unsupported infill is
implicitly carrying a mounting load. **Evaluate:** inspect the section, attachment chain and assembly
sequence; record any load assessment still needed.

## Service corridor

**Problem:** an installed component must later leave the product. **Competing needs:** compact packing
reduces size, while replacement requires swept space, tool access and sometimes cable slack.

Reserve an access path from component to opening. Include handles, screws and connectors in the
reasoning. A visually empty region may have a functional role. Keep that role in the brief so later
ornament or ribs do not consume it.

**Failure:** a drive fits inside a case but cannot pass through its service opening. **Evaluate:**
review removal in the panel; use declared motion and clearance checks where they represent the
actual path. An assembled-state clearance alone does not establish removability.

## Threshold and boundary

**Problem:** users need to recognize where to grasp, open or operate something. **Competing needs:**
a seamless exterior can conflict with discoverability.

Give a lid edge, control zone or grip a deliberate transition in depth, texture or contour. Relate
the visible cue to the real opening or action. Keep seams away from contact zones when practical.

**Failure:** a decorative groove looks like the opening while the real release is hidden.
**Evaluate:** describe the intended action from the preview, then have the human judge discoverability.

## Sources and limits

[PatternLanguage.com](https://www.patternlanguage.com/aims/intro.html) describes interconnected patterns
for recurring architectural problems. The four product patterns above are original adaptations.

[UNESCO's account of Chinese timber craftsmanship](https://ich.unesco.org/en/RL/chinese-traditional-architectural-craftsmanship-for-timber-framed-structures-00223?RL=00223)
documents prefabricated members, tenon joints and systematic craft processes. Borrow construction
logic with attribution; material behavior and historical structural performance do not transfer
automatically to printed or machined objects.
