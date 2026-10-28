# Choose tools to resolve a question

This is a decision guide for the existing MCP tools, not a fixed sequence. Edit source files with
the agent's normal file tools. All tools with a `path` argument take the absolute design-folder path;
`list_backends` and `search_catalog` do not take it. Names below are illustrative. Use names returned
by tools for the actual design, and consult the live tool schema when calling it.

| Current question | Useful action | What it establishes |
| --- | --- | --- |
| What exists, failed, changed or received feedback? | `explore_design(path)` | Current overview, staleness, selection and pins |
| What will a parameter change affect? | `explore_design(path, focus="width")` or `focus="interfaces.bolt_xy"` | Declared/read dependencies |
| Which backends or materials are available? | `list_backends()` | Installed capabilities, not hypothetical ones |
| Does a standard component exist? | `search_catalog(query="M3 heat-set insert")` | Catalog calls, sizes and connectors |
| Is the proposed assembly declaration consistent? | `build_design(path, scope="structure")` | References, interfaces and placements before geometry |
| What happened to this part after editing? | `build_design(path, scope="lid")` | Build and scoped reporting; scope does not excuse assembly checks |
| Is a visible gap real? | `list_geometry`, then `measure_geometry` with returned addresses | Actual geometry in the selected revision |
| Did the intended change have side effects? | `compare_revisions(path, base=previous_revision)` | Geometry metrics, inputs and results that changed |
| Do the declared product variants pass? | `build_design(path, configuration="all")` | Results for those configurations |
| Has a specific pin been addressed? | Build, inspect, then `resolve_pins` | A reply and resolved feedback state |

## Existing-design edit

Human: “Make this panel wider but keep its mounting holes where they are.” Read the attached pick
or pin. If the target or its driving values are unclear, explore before editing:

```json
{"path": "/absolute/project/designs/object", "focus": "width"}
```

This is input to `explore_design`. Determine which envelope changes and which interfaces stay
fixed; edit their sources, build and compare against the actual earlier revision. Do not measure
every face when the dependency graph already answers the question.

## Geometric ambiguity

Human: “Is the lid touching this boss?” Use `list_geometry` with the actual instance and inspect its
returned addresses. A measurement call can then use inputs such as:

```json
{"path": "/absolute/project/designs/object", "a": "lid[0]", "b": "base[0]"}
```

That call measures the two complete instances; if they touch elsewhere, it does not answer the
specific boss gap. Use the identified faces, connectors or points instead. Connector measurements
describe frames and should not be confused with surface clearance. Use `revision` when comparing
an older build. Face indices should be refreshed after geometry changes.

## Open concept brief

Human: “Design a desk organizer with a playful architectural character.” Establish contents, reach,
available space and process assumptions. Explore a few different spatial arrangements and style
grammars, explain their compromises, and use simple geometry only where it helps choose. A new
design needs `create_design` and the operating skill's structure check before detailed implementation;
brainstorming alone does not require any build.

## Feedback and stopping

Human feedback can revise the brief, identify an error or express taste. Distinguish those cases.
Use pins to identify a local change; if they conflict with a required mounting feature, explain the
concrete conflict and seek the missing priority while continuing independent work.

After building an addressed pin, call `resolve_pins` with its actual `pin_ids` and a concise `reply`.
Do not resolve unrelated feedback or claim the human accepted the appearance. A traced motion
result marked sampled still requires the human's motion review through the existing workflow.
Framework bugs and missing capabilities are not reasons to bypass its build pipeline.
