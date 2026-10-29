"""MCP tools that plug hitlcad into the user's own coding agent (Claude Code, Codex, …).

The agent keeps its normal workflow: it edits design files with its own tools and talks to the human in
its own chat. hitlcad adds what a coding agent cannot do alone: build and verify CAD assemblies, explore
their structure, find standard parts, measure geometry, and read what the human pointed at. Every tool
takes the design folder's absolute path; every name a tool answers with can be passed back to a tool.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from hitlcad import catalogs, materials
from hitlcad.agent import describe
from hitlcad.agent.graph import MARK, revision_summary
from hitlcad.agent.sections import joint_frames, section_png
from hitlcad.analysis.model import BuildBodies
from hitlcad.app import HitlCad
from hitlcad.config import DEFAULT_BACKEND
from hitlcad.core.report import Revision
from hitlcad.kernel import plugins
from hitlcad.kernel.errors import MOTION_SAMPLED, HitlcadError, agent_text, diagnose
from hitlcad.render.base import PREVIEW_FILE
from hitlcad.review import measure

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
hitlcad builds, verifies and renders parametric CAD assemblies; the human reviews them in VS Code.
Tools addressing a design take its folder's absolute path.

- Read the hitlcad skill for the design workflow and hitlcad-<backend> before editing model code;
  load their references when needed. Edit source files with your own file tools; never edit build/.
- Declare structure in design.json, then build_design(path, scope="structure") before geometry.
  Search the catalog before modelling standard hardware; compute shared values in interfaces.py.
- build_design returns a compact problem summary. Use explore_design(path, focus) for details;
  include_images=True on either tool adds views for geometry diagnosis or appearance review.
- Use list_geometry and measure_geometry for measurements, compare_revisions for changes, and
  explore_design(path, focus="review") for human selections and pins. Resolve addressed pins.
- Claim only what checks verify. For [motion.sampled], ask the human to watch the loop in the panel.
"""


def build_mcp(core: HitlCad) -> MCPServer:
    designs, builds, feedback, explore, registry = core.designs, core.builds, core.feedback, core.explore, core.registry
    mcp = MCPServer("hitlcad", instructions=INSTRUCTIONS)

    def guard(fn: Any) -> Any:
        """Every error as a tool error the agent can act on: its code, what it says and what to do, and for a bug
        in hitlcad, that it is not the design's."""

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                if not isinstance(e, HitlcadError):
                    log.exception("tool %s failed", fn.__name__)
                raise ToolError(agent_text(diagnose(e))) from e

        return wrapper

    rendering: defaultdict[Path, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def previewed(design_id: str, rev: Revision) -> Path | None:
        """A revision's rendered views: its `preview` output, else rendered now and kept with it."""
        directory = feedback.revision_dir(design_id, rev)
        if written := rev.report.artifact("preview"):
            return directory / written.path
        async with rendering[directory]:
            if (directory / PREVIEW_FILE).is_file():
                return directory / PREVIEW_FILE
            return await core.pipeline.preview(feedback.built(design_id, rev), directory, rev.report.metrics)

    async def images(design_id: str, rev: Revision) -> list[str | Image]:
        out: list[str | Image] = []
        if rev.report.status == "succeeded" and (preview := await previewed(design_id, rev)) and preview.is_file():
            out += [describe.PREVIEW_CAPTION, Image(data=preview.read_bytes(), format="png")]
        leads = [rev.report.results[p.findings[0].results[0]] for p in rev.report.problems if p.status == "fail"]
        failing = [r for r in leads if r.analysis in ("mate", "fastener", "relation")]  # one per problem
        if failing and rev.report.status == "succeeded":
            built = feedback.built(design_id, rev)
            for result in failing[:3]:
                cut = joint_frames(built, result)
                if cut is None:
                    continue
                frame, instances = cut
                png = section_png(
                    built, frame, instances, core.settings.section_size, f"{MARK['fail']} {result.subject}"
                )
                if png:
                    out += [
                        f"Section through {result.subject} (the plane holding its axis):",
                        Image(data=png, format="png"),
                    ]
        checks = {req.text: req.check for req in rev.spec.design.requirements if req.check} if rev.spec else {}
        pictured = [(r, checks[r.subject]) for r in leads if r.analysis == "requirement" and r.subject in checks]
        if pictured and rev.report.status == "succeeded":
            bodies = BuildBodies(feedback.built(design_id, rev), core.pipeline.analysis_settings,
                                 core.pipeline.results_cache)  # fmt: skip
            for result, check in pictured:
                if png := check.figure(bodies):
                    out += [f"{MARK['fail']} requirement {result.subject!r}:", Image(data=png, format="png")]
        return out

    @mcp.tool(structured_output=False)
    @guard
    async def list_backends() -> str:
        """CAD backends with what each is good at, the materials, catalog providers and outputs there are, and the
        plugins installed (a design names those it needs in design.json `plugins`)."""
        lines = ["Backends:"] + [f"- {b.name}: {b.title} — {b.summary}" for b in registry.available()]
        lines.append("Materials (add your own in a materials.json): " + materials.names(materials.builtin()))
        lines.append("Catalog providers: " + "; ".join(f"{p.name} ({p.usage})" for p in catalogs.providers().values()))
        lines.append(
            "Outputs (each build writes those design.json `outputs` names): "
            + "; ".join(f"{name}: {summary}" for name, (_, summary) in core.pipeline.outputs.items())
        )
        lines += [
            "Model packages (import from part files; see the backend skill for recipes):",
            *describe.model_package_lines(),
        ]
        found = plugins.registry()
        others = [f"{p.distribution} {p.version}" for p in found.plugins.values() if not p.builtin]
        lines.append("Plugins beyond hitlcad's own: " + (", ".join(others) or "none"))
        lines += [f"Plugin problem: {agent_text(d)}" for d in found.failures]
        return "\n".join(lines)

    @mcp.tool(structured_output=False)
    @guard
    async def create_design(path: str, name: str, backend: str = DEFAULT_BACKEND) -> str:
        """Scaffold a design folder at `path` (absolute): design.json, interfaces.py, assembly.py and parts/.
        Declare the structure in design.json, then build_design(path, scope="structure")."""
        state, created = designs.open(path, backend, name)
        return (f"Created {', '.join(created) or 'nothing (the design already existed)'} in {state.path}. Read the "
                f"hitlcad and hitlcad-{state.backend} skills, declare parts, connectors and mates in design.json, then "
                "check them with build_design(path, scope='structure').")  # fmt: skip

    @mcp.tool(structured_output=False)
    @guard
    async def search_catalog(query: str, limit: int = 10) -> str:
        """Standard parts matching `query` ("M3 socket head screw", "608 bearing", "M3 heat-set insert"): the
        designation, the call that makes it in a part file, its sizes and its connectors."""
        items = catalogs.search(query, limit)
        if not items:
            return f"Nothing matches {query!r}. Vendor parts (motors, boards) come from STEP files: hitlcad.builtin.catalogs.step."
        lines = []
        for item in items:
            sizes = (
                f" · sizes {', '.join(item.sizes[:12])}{' …' if len(item.sizes) > 12 else ''}"
                if len(item.sizes) > 1
                else ""
            )
            lines.append(f"- {item.designation} [{item.family}, {item.provider}, {item.material}]: {item.call}{sizes}")
            lines.append("  connectors: " + "; ".join(f"{k} ({v})" for k, v in item.connectors.items()))
        return "\n".join(lines)

    @mcp.tool(structured_output=False)
    @guard
    async def build_design(path: str, scope: str | None = None, configuration: str | None = None,
                           summary: str = "", include_images: bool = False) -> list[str | Image]:  # fmt: skip
        """Build and verify the design. scope="structure" checks design.json, interfaces.py and assembly.py
        without building geometry (seconds). scope="<part or instance>" builds (unchanged parts come from the
        cache) and answers about that part. configuration="<name>" builds one configuration, "all" every one.
        Returns up to five grouped problems; a part scope gives more detail. include_images=True adds the
        preview, failing joint sections and requirement figures. explore_design can request views without rebuilding."""
        state = designs.ensure(path)
        if scope == "structure":
            results, plan = await builds.check_structure(state.id)
            lines = [f"Structure of {state.name}:"]
            if plan and not plan.error:
                lines.append(f"  interface values: {len(plan.interfaces)}; placed: "
                             + ", ".join(f"{n} ×{len(p)}" if len(p) > 1 else n for n, p in plan.placements.items()))  # fmt: skip
            bad = [r for r in results if r.status != "pass"]
            lines += [f"  {MARK[r.status]} {r.subject}: {r.detail}" for r in bad] or ["  ✓ every reference resolves"]
            return ["\n".join(lines)]
        if configuration == "all":
            revisions = await builds.build_all(state.id, summary)
            out: list[str | Image] = []
            for rev in revisions:
                out.append(revision_summary(rev, limit=5,
                                            previous=designs.earlier(state.id, rev.id, rev.configuration)))  # fmt: skip
                if include_images:
                    out += await images(state.id, rev)
            return out
        rev = await builds.build(state.id, summary, configuration=configuration)
        text = revision_summary(rev, [scope] if scope else None, limit=core.settings.explore_limit if scope else 5,
                                previous=designs.earlier(state.id, rev.id, rev.configuration))  # fmt: skip
        if rev.report.status == "succeeded" and configuration is None:
            text += f"\nFiles in {designs.folder(state.id).build_dir}/ (the human can open them there)."
        if pins := len(designs.get(state.id).open_pins):
            text += f"\n{pins} open pin(s) from the human: explore_design(path, focus='review') lists them."
        if loops := len({r.cause for r in rev.report.results if r.code == MOTION_SAMPLED.code}):
            text += f"\n{loops} loop motion(s) checked only at solved positions: ask the human to watch them move."
        return [text, *await images(state.id, rev)] if include_images else [text]

    @mcp.tool(structured_output=False)
    @guard
    async def explore_design(path: str, focus: str | None = None, configuration: str | None = None,
                             include_images: bool = False) -> list[str | Image]:  # fmt: skip
        """The design graph. Without focus: a compact overview (failures, review, parts, staleness).
        focus = a part, instance (planet[2], tilt/planet[2]), component, connector (lid.screws), mate (a+b),
        relation (a~b), fastener set, parameter (with what it affects), interfaces.<name>, configuration, 'rev N'
        or 'review' (all selections and open pins). include_images=True adds views from that revision or the
        latest successful build of the configuration, without rebuilding."""
        state = designs.ensure(path)
        focus = focus.strip() if focus else None
        graph = await asyncio.to_thread(explore.graph, state.id, configuration)
        out: list[str | Image] = [await asyncio.to_thread(graph.explore, focus)]
        if include_images:
            rev = (
                designs.revision(state.id, int(focus.split()[1]))
                if focus and focus.startswith("rev ")
                else graph.ctx.revision
            )
            if rev:
                out += await images(state.id, rev)
        return out

    @mcp.tool(structured_output=False)
    @guard
    async def compare_revisions(path: str, base: int, target: int | None = None) -> str:
        """What changed from revision `base` to `target` (default: the latest): parameters, file diffs, results
        that changed status, parts rebuilt, size and mass."""
        return explore.compare(designs.ensure(path).id, base, target)

    @mcp.tool(structured_output=False)
    @guard
    async def list_geometry(path: str, target: str, kind: str = "faces", revision: int | None = None) -> str:
        """Every face (kind="faces"), edge or vertex of an instance ("lid[0]", placed) or a part ("part lid", its
        own frame) in the latest build (or `revision`), numbered as picks and measure_geometry number them:
        "face 9 of lid[0]" is faces()[9] of build/parts/lid.step, placed."""
        state = designs.ensure(path)
        rev = feedback.built_revision(state.id, revision)
        built = await asyncio.to_thread(feedback.built, state.id, rev)
        return f"Revision {rev.id}\n" + await asyncio.to_thread(measure.list_geometry, built, target, kind)

    @mcp.tool(structured_output=False)
    @guard
    async def measure_geometry(path: str, a: str, b: str, revision: int | None = None) -> str:
        """Minimum distance between two addresses, with the angle where both have a direction and the gap between
        parallel faces: "face 9 of lid[0]", an instance "lid[0]", a part "part lid", a connector frame
        "lid.screws[2]" (its origin, Z as direction), or a point "(0, 0, 10)" in mm."""
        state = designs.ensure(path)
        rev = feedback.built_revision(state.id, revision)
        built = await asyncio.to_thread(feedback.built, state.id, rev)
        return f"Revision {rev.id}\n" + await asyncio.to_thread(measure.measure, built, a, b)

    @mcp.tool(structured_output=False)
    @guard
    async def resolve_pins(path: str, pin_ids: list[str], reply: str = "") -> str:
        """Mark pins as addressed once your change is built; `reply` tells the human what you did."""
        state = designs.ensure(path)
        resolved = feedback.resolve_pins(state.id, pin_ids, reply)
        left = len(designs.get(state.id).open_pins)
        return f"Resolved {len(resolved)} pin(s); {left} still open."

    return mcp
