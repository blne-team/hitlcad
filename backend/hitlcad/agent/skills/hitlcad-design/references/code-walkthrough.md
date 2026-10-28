# A complete teaching assembly

Read this when translating general design reasoning into hitlcad's file contracts. The bundled
`assets/service-enclosure/` is a small purpose-built example. Its dimensions and construction are
illustrative; choose those of a real task from the brief, catalog data and process conditions.

## From intent to implementation

Intent: a removable panel, shared attachment geometry and a centered repeated field. The choices are
encoded across the actual framework files:

| File | What to learn |
| --- | --- |
| `design.json` | Brief, tunable inputs, part roles, real connectors, mates, fastener sets and requirements |
| `interfaces.py` | Shared bolt coordinates, hardware dimensions and slot relationships computed once |
| `parts/shell.py` | Related parts returned in their own frames; hole operations generate their connector frames |
| `parts/hardware.py` | Catalog parts retain geometry, materials and connectors |
| `assembly.py` | Placement reads the same values as part geometry |

The bolt field follows the housing's edges. The slot field retains its own width and pitch; excess
count fails with a useful explanation instead of silently clipping a slot. Mating values remain
independent of decorative scaling. Insert length and thread pitch come from catalog metadata.

Resolve interface reads that construct catalog geometry before entering a build123d builder
context. A constructor invoked while a builder is active can add unintended geometry to that part;
`shell.py` resolves its catalog-derived hole depth before opening either `BuildPart`.

## Use the example through the framework

For an agent design session, copy the example directory's source into the intended project design
folder using ordinary file tools, then call `build_design` with its absolute path. Do not build or
export by executing these part files directly. Read `hitlcad` and `hitlcad-build123d` for the installed
contract before adapting the source.

For example, inputs to `build_design` after the files are in place:

```json
{"path": "/absolute/project/designs/service-enclosure", "scope": "structure"}
```

```json
{"path": "/absolute/project/designs/service-enclosure", "configuration": "all"}
```

Use catalog search before choosing hardware in a real task; this teaching source already names its
specific catalog items. Slots illustrate geometric rhythm and protected borders; they do not
establish a ventilation target.

## Practice an edit

Change width while preserving slot width/pitch and catalog dimensions. Inspect dependencies with
`explore_design`, edit the parameter, build and compare the real revisions. Inspect the resulting
field and hardware rather than assuming the formula proves the geometry. Next increase slot count
until it conflicts with the protected border; explain whether the brief favors fewer slots or a
wider panel before changing the constraint.

The built-in wall, connector, fastener, interference and body checks evaluate geometry. A removal
path is described but is not a simulated disassembly check. Screw torque, insert pull-out strength,
contents, cooling, sealing and the full continuous parameter space remain outside this example's
claims. Its declared configurations are covered by the repository's teaching-example build tests.
