"""Structure: what design.json declares refers to what exists. Every mate, relation and fastener end names a
declared part's connector or a component's export, relations join the connector kinds they need,
fasteners are catalog parts, materials are in the registry, and assembly.py places what is declared.

It needs no geometry, so it also runs alone, right after the plan, as the agent's plan check
(`build_design(scope="structure")`).
"""

from __future__ import annotations

from collections.abc import Iterable

from hitlcad.analysis.base import Context, result
from hitlcad.core.address import End, InstancePath
from hitlcad.core.design import DesignSpec
from hitlcad.core.report import Result
from hitlcad.materials import Material

NAME = "structure"


def _end(design: DesignSpec, exports: dict[str, dict[str, str]], text: str) -> tuple[str | None, str | None]:
    """(problem, connector kind) of a connector end; the kind is None for a component export."""
    end = End.parse(text)
    owner = end.owner.name
    if owner in design.components:
        known = exports.get(owner, {})
        if end.connector not in known:
            return (
                f"component {owner} exports no connector {end.connector!r}; exports: {', '.join(known) or 'none'}",
                None,
            )
        return None, None
    if owner not in design.parts:
        return f"{owner!r} is not a part or component of {design.name}", None
    connectors = design.parts[owner].connectors
    if end.connector not in connectors:
        return (
            f"part {owner} declares no connector {end.connector!r}; connectors: {', '.join(connectors) or 'none'}",
            None,
        )
    return None, connectors[end.connector].kind


def structure_results(design: DesignSpec, placed: Iterable[str], exports: dict[str, dict[str, str]],
                      materials: dict[str, Material]) -> list[Result]:  # fmt: skip
    """Problems with the references design.json makes, given what assembly.py placed and what each
    component exports."""
    out = []
    placed = set(placed)
    for name in [*design.parts, *design.components]:
        if name not in placed:
            out.append(result(NAME, name, False, f"{name} is declared but assembly.py does not place it", [name],
                              severity="warning"))  # fmt: skip
    if placed and design.ground is None:
        out.append(
            result(NAME, "ground", False, "set `ground` in design.json: the instance every mate chain leads back to")
        )
    elif design.ground and InstancePath.parse(design.ground).head.name not in placed:
        out.append(result(NAME, "ground", False, f"ground {design.ground} is not placed by assembly.py"))
    for mate in design.mates:
        for text in (mate.a, mate.b):
            problem, _ = _end(design, exports, text)
            if problem:
                out.append(result(NAME, mate.address, False, problem, [text]))
    for rel in design.relations:
        for text, kind in ((rel.a, rel.joins[0]), (rel.b, rel.joins[1])):
            problem, found = _end(design, exports, text)
            if problem is None and found is not None and found != kind:
                problem = f"a {rel.type} relation joins a {rel.joins[0]} and a {rel.joins[1]}; {text} is a {found}"
            if problem:
                out.append(result(NAME, rel.address, False, problem, [text]))
    for fs in design.fasteners:
        for text in (fs.fastener, *fs.through, fs.into):
            problem, _ = _end(design, exports, text)
            if problem:
                out.append(result(NAME, fs.address, False, problem, [text]))
        owner = End.parse(fs.fastener).owner.name
        if owner in design.parts and design.parts[owner].catalog is None:
            out.append(result(NAME, fs.address, False, f"{owner} is not a catalog part; a fastener is bought", [owner]))
    for text in design.exports.values():
        problem, _ = _end(design, exports, text)
        if problem:
            out.append(result(NAME, f"exports: {text}", False, problem, [text]))
    for name, part in design.parts.items():
        if part.material and part.material not in materials:
            out.append(result(NAME, name, False, f"material {part.material!r} is not in the material registry "
                              f"(add it to a materials.json)", [name]))  # fmt: skip
        if part.catalog is None and part.material is None:
            out.append(result(NAME, name, False, f"{name} is made but has no material: mass and thread engagement "
                              "cannot be judged", [name], severity="warning"))  # fmt: skip
    return out


class Structure:
    name = NAME

    def run(self, ctx: Context) -> list[Result]:
        built = ctx.built
        exports = {name: child.spec.exports for name, child in built.components.items()}
        return structure_results(built.spec, built.placements, exports, built.materials)
