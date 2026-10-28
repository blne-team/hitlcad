---
name: hitlcad-design
description: "Develop and critique a hitlcad product concept: interpret an incomplete brief, explore different layouts or construction approaches, establish design intent, and choose a direction. Use for a new product, substantial redesign, competing concepts, or design review. Routine dimensional edits do not need concept exploration. Supplements the hitlcad operating skill."
---

# Developing a design

Use this skill for design decisions. The `hitlcad` skill governs files, tools, builds and verification;
the backend skill governs geometry code. Design advice does not override either contract or the
human's selected parameters, pins, chosen style or requested scope.

## Establish the problem

Identify who uses the object, what they do with it, its environment, what it must contain or connect
to, and how it will be made and maintained. Separate hard requirements, preferences and assumptions.
Resolve missing information that changes the concept; make small reversible assumptions explicitly.
Do not turn exploration into a questionnaire or reopen choices the human already made.

Record intent using the existing brief fields: `summary`, `key_dimensions`, `features`, `assumptions`
and `open_questions`. Measurable obligations belong in requirements too, using checks the installed
framework actually provides. Do not invent fields such as `style` or `concepts` in design.json.

For an edit, state the requested change and the relationships that must remain fixed. Choose
acceptance evidence independently of the construction: a formula used to place a hole is not a
measurement of the resulting hole. Inspect the source's condition before attempting a fragile
operation, and distinguish an inherited defect from a regression caused by the edit.

## Collaborate with the human

Treat selections and pins as concrete design input. Reconcile them with the latest request and the
existing brief before changing geometry. Clarify conflicting instructions or missing choices that
would substantially change function, cost, interfaces or direction. For reversible details, state a
reasonable assumption and proceed. A request to explore concepts does not require manufacturing a
finished design, and a request to build does authorize ordinary refinement and verification.

Present choices through their effect on use, appearance and construction. Invite a preference when
the choice materially depends on taste; give a recommendation so the human need not become the CAD
operator. Preserve accepted decisions in the brief and do not reopen them on every turn. After a
change, explain the visible result and remaining uncertainty. Resolve a pin after its requested
change is built, with a reply saying what changed.

## Explore at the right scale

A focused edit usually needs one local decision. A new concept or substantial redesign benefits from
distinct alternatives: change a layout, mass arrangement, assembly strategy or interaction, while
holding the brief's constraints constant. Explain the benefit and cost of each direction. Build only
enough geometry to resolve the uncertainty; refinement follows selection. Do not require approval at
every stage when the human has authorized proceeding.

Read only references relevant to the decision:

- [Concept exploration](references/concepts.md): alternatives, analogies and avoiding fixation.
- [Design intent](references/design-intent.md): parameters, shared interfaces and robust changes.
- [Architectural patterns](references/architecture-patterns.md): modules, boundaries and access.
- [Worked patterns](references/worked-patterns.md): reusable decisions across general CAD tasks.
- [Evaluation](references/evaluation.md): reviewing a design or evaluating skill effectiveness.
- [Tool decisions](references/tool-decisions.md): choose calls from the current uncertainty, with
  concrete input examples and human feedback handling.
- [Code walkthrough](references/code-walkthrough.md): a complete general assembly example linking
  intent, parameters, catalog hardware, geometry and verification.
- [Teaching assembly](assets/service-enclosure/design.json),
  [interfaces](assets/service-enclosure/interfaces.py),
  [parts](assets/service-enclosure/parts/shell.py),
  [hardware](assets/service-enclosure/parts/hardware.py) and
  [placements](assets/service-enclosure/assembly.py): copyable source, read when implementing a
  similar contract. Adapt the design decisions to the actual brief.

Use `hitlcad-form` for silhouette and composition, `hitlcad-dfm` for process and joining decisions,
and `hitlcad-style` when a visual direction matters. Load the relevant reference, not every guide.

## Refine and review

Choose the next action from what is unknown: inspect an existing design, measure a gap, compare
concepts, find a bought part or build a changed part. These are branches, not mandatory stages on
every task. Return to intent or style if evidence changes the design problem. Keep the operating
skill's structural and verification requirements when implementing a change.

Establish envelopes and interfaces before details. Compare the result with the brief from the
existing preview views and, where useful, sections and motion. Explain a specific observation,
its consequence and a targeted change. Preserve successful decisions while correcting the cause.

Distinguish a reported measurement, a design hypothesis and a human judgment. Good clearances do not
establish comfortable handling, airflow, strength or visual quality. Finish with the selected
direction, consequential tradeoffs, verified requirements and remaining judgments.

Method review: [AgentCAD's canonical guide](https://github.com/jdilla1277/agentcad/blob/52af472ddade4b26d8b0d47f304ac5f9f6183d53/src/agentcad/guide.py)
informed the edit-preservation and independent acceptance guidance. These are original hitlcad
instructions; its commands, injected helpers and mandatory stage sequence are not runtime dependencies.
