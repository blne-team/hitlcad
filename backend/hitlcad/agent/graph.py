"""The design graph: an agent's context of a design, projected from its design.json, its latest build report
and its review state (pins, selection). It has no store of its own.

Nodes are parts, components, instances, connectors, mates, relations, fastener sets, parameters,
interface values, requirements and pins; edges come from design.json (declarations), from the build
(what each piece of code read, what each result is about) and from the event log. `explore` answers
in the address grammar, bounded so a large product does not flood an agent's context; `impact`
follows recorded reads backwards, so it is exact rather than a guess from parsing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hitlcad.agent.describe import feedback_text
from hitlcad.core.address import INTERFACE_PREFIX, End, InstancePath
from hitlcad.core.design import DESIGN_FILE, DesignSpec
from hitlcad.core.events import DesignState
from hitlcad.core.report import BuildReport, Earlier, Finding, Problem, Result, Revision, problems, tally
from hitlcad.kernel.errors import advice

MARK = {"pass": "✓", "fail": "✗", "unverified": "?"}
WARNING = "!"


@dataclass
class Context:
    state: DesignState
    design: DesignSpec | None  # design.json as it is now; None when it does not load
    design_error: str | None  # why design.json does not load
    revision: Revision | None  # the latest successful build of the configuration explored
    limit: int  # results listed in full per answer (Settings.explore_limit)
    stale: list[str] = field(default_factory=list)  # files changed since that build
    used_by: list[str] = field(default_factory=list)  # names of designs using this one
    load: Callable[[int], Revision] | None = None  # another revision of the design, in full


def _owner(text: str) -> str:
    return End.parse(text).owner.name


class DesignGraph:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.design = ctx.design or (ctx.revision.spec.design if ctx.revision and ctx.revision.spec else None)
        self.report: BuildReport | None = ctx.revision.report if ctx.revision else None
        self.results: list[Result] = self.report.results if self.report else []

    # ------------------------------------------------------------------ helpers

    def _about(self, *addresses: str) -> list[Result]:
        """Results about any of these addresses, or about anything inside them (an instance of a part, a
        connector of an instance)."""
        wanted = set(addresses)

        def touches(a: str) -> bool:
            if a in wanted:
                return True
            head = a.split(".", 1)[0]
            try:
                base = InstancePath.parse(head.split("/")[0]).head.name
            except ValueError:  # not an instance path
                return False
            return head in wanted or base in wanted

        return [r for r in self.results if r.subject in wanted or any(touches(a) for a in r.about)]

    def _lines(self, results: list[Result], limit: int | None = None) -> list[str]:
        limit = limit or self.ctx.limit
        order = {"fail": 0, "unverified": 1, "pass": 2}
        shown = sorted(results, key=lambda r: order[r.status])
        lines = [f"  {MARK[r.status]} {r.analysis} {r.subject}: {r.detail}" for r in shown[:limit]]
        if len(shown) > limit:
            rest = shown[limit:]
            counts = ", ".join(f"{sum(r.status == s for r in rest)} {s}" for s in ("fail", "unverified", "pass")
                               if any(r.status == s for r in rest))  # fmt: skip
            lines.append(f"  … {len(rest)} more ({counts}): focus a part, instance or connector for detail")
        return lines

    def _instances(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for inst in self.report.instances if self.report else []:
            out[inst.part or inst.component or ""].append(inst.path)
        return out

    def _pins(self, *addresses: str) -> list[str]:
        out = []
        for pin in self.ctx.state.open_pins:
            where = pin.connector or pin.instance or ""
            if not addresses or any(where == a or where.startswith((f"{a}.", f"{a}[", f"{a}/")) for a in addresses):
                moved = f" {pin.motion.text()}" if pin.motion else ""
                out.append(f"  pin {pin.id} on {pin.connector or pin.label}{moved}: {pin.note.strip() or '(no note)'}")
        return out

    # ------------------------------------------------------------------ overview

    def overview(self) -> str:
        s, d = self.ctx.state, self.design
        if d is None:
            return f"Design {s.name!r} at {s.path}: design.json does not load ({self.ctx.design_error})"
        rev = self.ctx.revision
        limit = min(5, self.ctx.limit)

        def listed(items: list[str]) -> str:
            text = ", ".join(items[:limit])
            return text + (f", … {len(items) - limit} more" if len(items) > limit else "")

        made = [n for n, p in d.parts.items() if p.catalog is None]
        bought = [n for n, p in d.parts.items() if p.catalog is not None]
        instances = self._instances()
        head = (f"{d.name} at {s.path} ({f'rev {rev.id}' if rev else 'not built yet'}"
                + (f", configuration {rev.configuration}" if rev and rev.configuration else "") + "): "
                f"{len(made)} made + {len(bought)} catalog parts, {len(d.components)} components, "
                f"{sum(len(v) for v in instances.values())} instances, {len(d.mates)} mates, {len(d.relations)} relations, "
                f"{len(d.fasteners)} fastener sets, {len(s.open_pins)} open pins")  # fmt: skip
        lines = [head]
        if self.ctx.used_by:
            lines.append(f"  used by: {listed(self.ctx.used_by)}")
        if d.configurations:
            lines.append(f"  configurations: {listed(list(d.configurations))}")
        if s.building:
            lines.append(f"  a build of revision {s.building.revision} is running: {s.building.doing}")
        if s.error:
            lines.append(f"  stopped: [{s.error.code}] {s.error.message}")
        if self.ctx.design_error:
            lines.append(
                f"  ✗ {DESIGN_FILE} does not load, so this is rev {rev.id if rev else '?'}'s: {self.ctx.design_error}"
            )
        if self.ctx.stale:
            lines.append(f"  stale: changed since rev {rev.id if rev else '?'}: {listed(self.ctx.stale)}")
        latest = s.current_revision
        if latest and latest.status == "failed" and (why := latest.failure):
            lines.append(f"  ✗ the latest build (rev {latest.id}) failed: {why.subject} [{why.code}]: {why.detail}")
        if self.report:
            lines += problem_lines(self.results, self.report.problems, limit)
        review = self._pins() + [f"  selected: {item.label}" for item in s.selection]
        lines += review[:limit]
        if len(review) > limit:
            lines.append(f"  … {len(review) - limit} more review items: explore_design(path, focus='review')")
        lines.append("  " + (self.report.tally.text if self.report else "not built yet"))
        lines.append("  parts: " + listed(self._counted(made, instances)) if made else "  parts: none")
        if bought:
            lines.append("  catalog: " + listed(self._counted(bought, instances)))
        if d.components:
            lines.append("  components: " + listed([f"{n} ({c.design})" for n, c in d.components.items()]))
        if len(made) > limit or len(bought) > limit or len(d.components) > limit or len(d.configurations) > limit:
            lines.append("  Read design.json for all names; explore_design(path, focus) gives details.")
        return "\n".join(lines)

    @staticmethod
    def _counted(names: list[str], instances: dict[str, list[str]]) -> list[str]:
        return [f"{n} ×{len(instances[n])}" if len(instances.get(n, [])) > 1 else n for n in names]

    # ------------------------------------------------------------------ focus

    def explore(self, focus: str | None = None) -> str:
        if not focus:
            return self.overview()
        f = focus.strip()
        if f == "review":
            return feedback_text(self.ctx.state)
        d = self.design
        if d is None:
            return self.overview()
        if f.startswith("rev "):
            return self._revision(int(f.split()[1]))
        if f.startswith(INTERFACE_PREFIX):
            return self._interface(f.removeprefix(INTERFACE_PREFIX))
        if "+" in f:
            return self._declared(f, [m for m in d.mates if m.address == f], "mate")
        if "~" in f:
            return self._declared(f, [r for r in d.relations if r.address == f], "relation")
        if ":" in f:
            return self._declared(f, [x for x in d.fasteners if x.address == f], "fastener set")
        if "." in f:
            return self._connector(f)
        if f in d.parts:
            return self._part(f)
        if f in d.components:
            return self._component(f)
        if f in {p.name for p in d.parameters}:
            return self._parameter(f)
        if f in d.configurations:
            return self._configuration(f)
        if "[" in f or "/" in f or f in self._instances():
            return self._instance(f)
        known = ", ".join([*d.parts, *d.components][:12])
        return (
            f"Nothing named {f!r} in {d.name}. Parts and components: {known}. Focus also takes connectors "
            "(lid.screws), mates (a+b), parameters, interfaces.<name>, configurations and 'rev N'."
        )

    def _part(self, name: str) -> str:
        d = self.design
        assert d is not None
        spec = d.parts[name]
        pr = self.report.part(name) if self.report else None
        what = (
            f"catalog: {spec.catalog.designation} ({spec.catalog.fidelity})"
            if spec.catalog
            else f"made of {spec.material or 'an unset material'}, "
            + (getattr(d.process(name), "process", None) or "no process")
        )
        lines = [f"{name}: {spec.role} · {spec.file} · {what}"]
        if pr:
            m = pr.metrics
            mass = f" · {pr.mass.mass_g:.1f} g" if pr.mass else ""
            lines.append(
                f"  {m.size[0]:.1f} × {m.size[1]:.1f} × {m.size[2]:.1f} mm{mass} · "
                f"{'from the cache' if pr.cached else 'rebuilt'} in rev {self.ctx.revision.id}"
            )  # type: ignore[union-attr]
            reads = [*pr.reads.parameters, *(f"interfaces.{i}" for i in pr.reads.interfaces), *pr.reads.modules]
            lines.append(f"  reads: {', '.join(reads) or 'nothing'}")
            if pr.catalog:
                lines.append(f"  provider {pr.catalog.provider}: {pr.catalog.designation}"
                             + "".join(f", {k} {v}" for k, v in pr.catalog.metadata.items() if k != "family"))  # fmt: skip
        connectors = []
        for cname, c in spec.connectors.items():
            info = pr.connectors.get(cname) if pr else None
            measured = info.measured[0].detail if info and info.measured else "not measured"
            count = f" ×{len(info.frames)}" if info and len(info.frames) > 1 else ""
            connectors.append(f"{cname} ({c.kind}{count}: {measured})")
        if connectors:
            lines.append("  connectors: " + " · ".join(connectors))
        instances = self._instances().get(name, [])
        lines.append(f"  instances: {', '.join(instances) or 'not placed'}")
        lines += self._joints(name)
        lines += self._lines(self._about(name, *instances))
        lines += self._pins(name, *instances)
        return "\n".join(lines)

    def _joints(self, *owners: str) -> list[str]:
        d = self.design
        assert d is not None
        out = []
        for m in d.mates:
            if {_owner(m.a), _owner(m.b)} & set(owners):
                out.append(
                    f"  mate {m.address} ({m.type}" + (f", fit {m.fit.iso or m.fit.clearance}" if m.fit else "") + ")"
                )
        for r in d.relations:
            if {_owner(r.a), _owner(r.b)} & set(owners):
                out.append(f"  relation {r.address} ({r.type})")
        for fs in d.fasteners:
            if {_owner(fs.fastener), *map(_owner, fs.through), _owner(fs.into)} & set(owners):
                out.append(f"  fastener set {fs.address}")
        return out

    def _instance(self, address: str) -> str:
        inst = next((i for i in (self.report.instances if self.report else []) if i.path == address), None)
        if inst is None:
            return f"No instance {address!r} in the latest build. Instances: " + ", ".join(
                i.path for i in (self.report.instances if self.report else [])[: self.ctx.limit]
            )
        x, y, z = (inst.transform[r][3] for r in range(3))
        what = f"part {inst.part}" if inst.part else f"component {inst.component}"
        lines = [f"{address}: an instance of {what}, at ({x:.2f}, {y:.2f}, {z:.2f})"]
        lines += self._lines(self._about(address))
        lines += self._pins(address)
        return "\n".join(lines)

    def _component(self, name: str) -> str:
        d = self.design
        assert d is not None
        use = d.components[name]
        lines = [
            f"{name}: the design at {use.design}"
            + (f", configuration {use.configuration}" if use.configuration else "")
            + (", flexible" if use.flexible else "")
        ]
        if use.bind:
            lines.append("  binds: " + ", ".join(f"{k} = {v}" for k, v in use.bind.items()))
        child = self.report.components.get(name) if self.report else None
        if child:
            lines.append(f"  its build: {child.status.value}, {len(child.parts)} parts, {child.tally.text}")
            lines += problem_lines(child.results, child.problems, 10)
        lines.append(f"  instances: {', '.join(self._instances().get(name, [])) or 'not placed'}")
        lines += self._joints(name)
        lines += self._lines(self._about(name, *self._instances().get(name, [])))
        return "\n".join(lines)

    def _connector(self, text: str) -> str:
        d = self.design
        assert d is not None
        try:
            end = End.parse(text)
        except ValueError as e:
            return str(e)
        owner = end.owner.name
        lines = []
        if owner in d.parts:
            spec = d.parts[owner].connectors.get(end.connector)
            if spec is None:
                return f"part {owner} has no connector {end.connector!r}; connectors: {', '.join(d.parts[owner].connectors)}"
            pr = self.report.part(owner) if self.report else None
            info = pr.connectors.get(end.connector) if pr else None
            lines.append(f"{text}: {spec.kind} on part {owner}" + (f", {len(info.frames)} frames" if info else ""))
            for k, m in enumerate(info.measured if info else []):
                lines.append(f"  [{k}] {MARK[m.status]} {m.detail}")
        elif owner in d.components:
            lines.append(f"{text}: export {end.connector} of component {owner}")
        else:
            return f"{owner!r} is not a part or component"
        uses = [f"  mate {m.address} ({m.type})" for m in d.mates if text in (m.a, m.b)]
        uses += [f"  relation {r.address} ({r.type})" for r in d.relations if text in (r.a, r.b)]
        uses += [f"  fastener set {f.address}" for f in d.fasteners if text in (f.fastener, f.into, *f.through)]
        lines += uses or ["  no mate, relation or fastener set uses it"]
        lines += self._lines(self._about(text))
        lines += self._pins(text)
        return "\n".join(lines)

    def _declared(self, address: str, found: list[Any], what: str) -> str:
        if not found:
            return f"No {what} {address!r}."
        spec = found[0]
        lines = [f"{what} {address}: " + spec.model_dump_json(exclude_defaults=True)]
        lines += self._lines([r for r in self.results if r.subject == address])
        return "\n".join(lines)

    def _parameter(self, name: str) -> str:
        d = self.design
        assert d is not None
        p = next(p for p in d.parameters if p.name == name)
        value = self.report.values.get(name, p.value) if self.report else p.value
        bounds = f" ({p.min:g} … {p.max:g})" if p.min is not None and p.max is not None else ""
        lines = [f"{name} = {value} {p.unit}{bounds}" + (f": {p.description}" if p.description else "")]
        lines += self.impact(name)
        return "\n".join(lines)

    def _interface(self, name: str) -> str:
        iv = next((i for i in (self.report.interfaces if self.report else []) if i.name == name), None)
        if iv is None:
            return f"No interface value {name!r} in the latest build."
        reads = [*iv.reads.parameters, *(f"interfaces.{i}" for i in iv.reads.interfaces)]
        lines = [f"interfaces.{name} = {iv.value} · reads {', '.join(reads) or 'nothing'}"]
        lines += self.impact(f"{INTERFACE_PREFIX}{name}")
        return "\n".join(lines)

    def _configuration(self, name: str) -> str:
        d = self.design
        assert d is not None
        values = d.configurations[name]
        latest = self.ctx.state.latest(name)
        lines = [f"configuration {name}: " + ", ".join(f"{k} = {v}" for k, v in values.items())]
        if latest and self.ctx.load:
            report = self.ctx.load(latest.id).report
            lines.append(f"  latest build rev {latest.id}: {report.tally.text}")
            lines += problem_lines(report.results, report.problems, 10)
        else:
            lines.append("  not built yet: build_design(path, configuration=…)")
        return "\n".join(lines)

    def _revision(self, rev_id: int) -> str:
        if self.ctx.state.revision(rev_id) is None or self.ctx.load is None:
            return f"No revision {rev_id}."
        return revision_summary(self.ctx.load(rev_id), limit=self.ctx.limit)

    # ------------------------------------------------------------------ impact

    def impact(self, ref: str) -> list[str]:
        """What depends on a parameter or interface value: interface values, parts, placements, components
        bound to it, their connectors' mates, relations and fastener sets, requirements, and the designs
        that use this one."""
        r = self.report
        d = self.design
        if r is None or d is None:
            return ["  impact: not built yet, so what reads it is unknown"]
        is_interface = ref.startswith(INTERFACE_PREFIX)
        name = ref.removeprefix(INTERFACE_PREFIX)
        affected_if = {name} if is_interface else set()
        changed = True
        while changed:  # interface values reading the parameter, or reading affected values
            changed = False
            for iv in r.interfaces:
                if iv.name in affected_if:
                    continue
                if (not is_interface and name in iv.reads.parameters) or affected_if & set(iv.reads.interfaces):
                    affected_if.add(iv.name)
                    changed = True

        def reads_it(reads: Any) -> bool:
            return (not is_interface and name in reads.parameters) or bool(affected_if & set(reads.interfaces))

        parts = [p.name for p in r.parts if reads_it(p.reads)]
        placements = reads_it(r.assembly_reads)
        bound = [c for c, use in d.components.items() if ref in use.bind.values() or affected_if & {
            v.removeprefix(INTERFACE_PREFIX) for v in use.bind.values() if v.startswith(INTERFACE_PREFIX)}]  # fmt: skip
        owners = set(parts) | set(bound)
        joints = [j.strip() for j in self._joints(*owners)] if owners else []
        if placements:
            joints = (
                [f"mate {m.address}" for m in d.mates]
                + [f"relation {x.address}" for x in d.relations]
                + [f"fastener set {f.address}" for f in d.fasteners]
            )
        reqs = [
            q.text for q in d.requirements if q.check and (not q.check.targets() or set(q.check.targets()) & owners)
        ]
        lines = []
        derived = sorted(affected_if - {name})
        if derived:
            lines.append("  interface values: " + ", ".join(f"interfaces.{v}" for v in derived))
        lines.append(
            "  read by: " + (", ".join(parts) or "no part") + (" and assembly.py (placements)" if placements else "")
        )
        if bound:
            lines.append("  bound into components: " + ", ".join(bound))
        if joints:
            lines.append("  affects: " + "; ".join(joints[: self.ctx.limit]))
        if reqs:
            lines.append("  requirements: " + "; ".join(reqs))
        if self.ctx.used_by and (parts or placements or bound):
            lines.append("  and the designs that use this one: " + ", ".join(self.ctx.used_by))
        return lines

    # ------------------------------------------------------------------ for the panel

    def structure(self) -> dict[str, Any]:
        """Nodes and edges for the panel's Structure view, each with the worst status of its results."""
        d = self.design
        if d is None:
            return {"nodes": [], "edges": []}
        instances = self._instances()

        def status(results: list[Result]) -> str:
            """The worst: a failing error, a failing warning, unverified, pass; none without results."""
            if any(r.status == "fail" and r.severity == "error" for r in results):
                return "fail"
            if any(r.status == "fail" for r in results):
                return "warning"
            statuses = {r.status for r in results}
            return "unverified" if "unverified" in statuses else "pass" if statuses else "none"

        def own(address: str, analysis: str) -> list[Result]:
            """An edge's own results, and any collision through its range of motion."""
            return [r for r in self.results if r.subject == address
                    and (r.analysis == analysis or r.analysis == "motion" and r.status == "fail")]  # fmt: skip

        nodes = [{"id": n, "kind": "catalog" if p.catalog else "made", "label": n, "role": p.role,
                  "count": len(instances.get(n, [])), "status": status(self._about(n, *instances.get(n, [])))}
                 for n, p in d.parts.items()]  # fmt: skip
        nodes += [{"id": n, "kind": "component", "label": n, "role": c.design, "count": len(instances.get(n, [])),
                   "status": status(self._about(n))} for n, c in d.components.items()]  # fmt: skip
        edges = []
        for m in d.mates:
            edges.append({"id": m.address, "kind": "mate", "type": m.type, "source": _owner(m.a), "target": _owner(m.b),
                          "status": status(own(m.address, "mate"))})  # fmt: skip
        for x in d.relations:
            edges.append({"id": x.address, "kind": "relation", "type": x.type, "source": _owner(x.a), "target": _owner(x.b),
                          "status": status(own(x.address, "relation"))})  # fmt: skip
        for fs in d.fasteners:
            for hole in [*fs.through, fs.into]:
                edges.append({"id": f"{fs.address}|{hole}", "kind": "fastener", "type": "fastener",
                              "source": _owner(fs.fastener), "target": _owner(hole),
                              "status": status(own(fs.address, "fastener"))})  # fmt: skip
        return {"nodes": nodes, "edges": edges, "ground": d.ground}


def revision_summary(rev: Revision, scope: list[str] | None = None, *, limit: int,
                     previous: Earlier | None = None) -> str:  # fmt: skip
    """A build's outcome for an agent: its problems with the usual fix for each kind, then a tally; scoped to some
    addresses when asked. Problems that `previous` (the configuration's revision before) had too say so."""
    r = rev.report
    head = (
        f"Revision {rev.id}"
        + (f" ({rev.configuration})" if rev.configuration else "")
        + f" — {r.status.value.upper()} in {r.duration_s:.1f}s"
        + (f": {rev.summary}" if rev.summary else "")
    )
    lines = [head]
    results, found, counts = r.results, r.problems, r.tally
    if scope and rev.spec:
        graph = DesignGraph(
            Context(state=DesignState(id=""), design=rev.spec.design, design_error=None, revision=rev, limit=limit)
        )
        results = graph._about(*scope)
        found = problems(results)
        counts = tally(results, found)
        lines.append(f"Scoped to {', '.join(scope)}: {len(results)} of {len(r.results)} results")
    lines += problem_lines(results, found, limit, hints=True, previous=previous)
    lines.append(f"  {counts.text}")
    if m := r.metrics:
        lines.append(f"Assembly: {m.size[0]:.2f} × {m.size[1]:.2f} × {m.size[2]:.2f} mm, {len(r.instances)} instances"
                     + (f", {r.mass.mass_g:.1f} g" if r.mass else ""))  # fmt: skip
    cached = [p.name for p in r.parts if p.cached]
    if r.parts:
        lines.append(f"Parts: {len(r.parts) - len(cached)} rebuilt, {len(cached)} from the cache")
    return "\n".join(lines)


def problem_lines(results: list[Result], found: list[Problem], limit: int, *, hints: bool = False,
                  previous: Earlier | None = None) -> list[str]:  # fmt: skip
    """One line per problem: what leads it, where in the files, its copies at their pattern indices, its code, and
    what else saw it. With `hints`, the usual fix follows the first problem of each code; with `previous`, a
    problem it had too says so."""
    lines = []
    hinted: set[str] = set()
    for p in found[:limit]:
        lead, *also = p.findings
        r = results[lead.results[0]]
        mark = WARNING if p.status == "fail" and p.severity == "warning" else MARK[p.status]
        line = f"  {mark} {r.analysis} {r.subject}{_where(r)}{_copies(lead)} [{r.code}]: {r.detail}"
        if also:
            line += " (also: " + ", ".join(_named(results[f.results[0]], f) for f in also) + ")"
        lines.append(line)
        if hints and r.code not in hinted and (fix := advice(r.code)):
            hinted.add(r.code)
            lines.append(f"    → {fix}")
        if previous and (r.code, r.subject) in previous.failing:
            lines.append(f"    Same as revision {previous.revision}: the change since did not reach it.")
    if len(found) > limit:
        lines.append(f"  … {len(found) - limit} more problems: focus a part, instance or connector for detail")
    return lines


def _where(r: Result) -> str:
    """Where in the design's files, past what the subject says: ' line 31' after a file, ' (design.json)' after
    a field of it."""
    w = r.where
    if w is None:
        return ""
    file = w.file if w.file and w.file != r.subject else ""
    address = w.address if w.address and w.address != r.subject else ""
    at = " ".join(x for x in (file, f"line {w.line}" if w.line else "") if x)
    text = ", ".join(x for x in (at, address) if x)
    return "" if not text else f" {text}" if text.startswith("line") else f" ({text})"


def _copies(f: Finding) -> str:
    """' ×4 at [0, 1, 2, 3]' for a finding of several results."""
    if len(f.results) == 1:
        return ""
    return f" ×{len(f.results)}" + (f" at [{', '.join(map(str, f.indices))}]" if f.indices else "")


def _named(r: Result, f: Finding) -> str:
    subject = f"'{r.subject}'" if " " in r.subject else r.subject
    return f"{r.analysis} {subject}" + (f" ×{len(f.results)}" if len(f.results) > 1 else "")
