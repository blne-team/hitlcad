# Joints, retention and fits

Use when parts must locate, remain attached or move relative to one another. Begin with the intended
degrees of freedom, load path, assembly direction and service requirement. Separate locating,
clamping and retention functions even when one feature serves more than one of them.

## Select a connection

| Need | Candidate approach | Decide explicitly |
| --- | --- | --- |
| Repeated service | Screws into nuts, inserts or suitable tapped material | Tool access, engagement, clamped thickness and bottoming |
| Fast assembly with limited opening cycles | Snap or compliant latch | Strain, insertion/removal force, material and cycle life |
| Repeatable location | Datums, pilots, pins, shoulders or keys | Required degrees of freedom and tolerance compatibility |
| Rotation | Shaft and bearings/bushings | Radial/axial support, retention, fit, load and operating conditions |
| Permanent joining | Bonding, welding or process-specific joining | Compatible material, joint preparation and applicable process |

The latter methods may need capabilities beyond installed geometric checks. Do not claim a snap
is durable, an adhesive is suitable or a bearing is adequately rated from clearance alone.

## Catalog fasteners and inserts

Use `search_catalog` for real sizes and returned construction calls. Use catalog hole operations
and their connector frames where provided, keeping geometry and declared feature locations together.
Choose screw length from the clamped stack plus the required engagement, with room before a blind
bottom. Verify through holes, seats and the actual thread opening.

For an insert, use the manufacturer's host-hole and installation recommendations for that type and
plastic process. Model surrounding material and access for the installation tool. Heat-set advice
for a thermoplastic must not be silently applied to a thermoset resin part.

Sources: [SPIROL host-hole guide](https://www.spirol.com/resources/white-papers/how-to-design-the-proper-hole-for-heat-ultrasonic-inserts/)
and [SPIROL inserts for printed assemblies](https://ca.spirol.com/resources/white-papers/how-to-select-a-threaded-insert-for-your-3d-printed-assembly/).
Use their type-specific data; successful geometric engagement is not a pull-out test.

## Clearance and variation

For a cylindrical pair, diametral clearance is hole diameter minus shaft diameter. Radial clearance
is half that value for a concentric pair. State which quantity is intended. Include process
allowances consistently, and keep nominal size distinct from a tolerance interval.

A worst-case clearance uses the smallest hole and largest shaft. The opposite extremes describe
the largest clearance. Tolerance stacks need signed relationships and realistic bounds; statistical
combination requires assumptions beyond a nominal fit check. The generated skill describes the
installed `fit` and `stack` syntax. Do not substitute arbitrary ISO fit classes for process calibration.

## Bearing arrangements

Determine which ring sees a rotating load, how axial position is controlled, and how thermal
expansion or required axial movement is accommodated. Fit choice depends on load and operating
conditions; not every bearing seat should use the same interference. Specify axial retention rather
than assuming a press fit supplies every needed constraint.

Source: [SKF bearing maintenance handbook](https://www.skf.com/binaries/pub12/Images/0901d1968013be94-SKF-bearing-maintenance-handbook---10001_1-EN%281%29_tcm_12-463040.pdf)
describes fit selection conditions. Read the selected bearing's manufacturer data for its actual
application. The framework can verify declared geometry and fits; capacity, preload, life and
lubrication need separate evidence.
