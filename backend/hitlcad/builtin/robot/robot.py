"""Robot descriptions from a design: URDF and SDF.

Each placed part instance is a link, with its part's mesh and, where its material is known, its
inertia. Mates become joints along a tree grown from `ground`: rigid mates and fastener sets fixed,
revolute revolute (continuous without angle limits), slider prismatic, cylindrical continuous, planar
planar, ball floating (URDF) or ball (SDF). A gear relation between two turning joints becomes a URDF
<mimic>. A mate that would close a loop is left out (both formats are trees); an instance no mate
reaches is fixed to the base where it stands. Lengths are metres, masses kilograms.
"""

from __future__ import annotations

import math
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from hitlcad.analysis.coverage import Link, nested_links
from hitlcad.core import frames as fr
from hitlcad.core.laws import Linear
from hitlcad.core.structure import Mate, Relation

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign, Leaf

MM = 0.001


def link_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", path.replace("/", "__")).strip("_")


def rpy(r: np.ndarray) -> tuple[float, float, float]:
    """Roll, pitch, yaw (fixed-axis X, Y, Z) of a rotation matrix."""
    pitch = math.asin(max(-1.0, min(1.0, -r[2, 0])))
    if abs(math.cos(pitch)) > 1e-9:
        return (math.atan2(r[2, 1], r[2, 2]), pitch, math.atan2(r[1, 0], r[0, 0]))
    return (math.atan2(-r[1, 2], r[1, 1]), pitch, 0.0)


def pose_text(m: fr.Frame) -> tuple[str, str]:
    xyz = " ".join(f"{v * MM:.9g}" for v in m[:3, 3])
    return xyz, " ".join(f"{v:.9g}" for v in rpy(m[:3, :3]))


@dataclass
class Joint:
    name: str
    parent: str
    child: str
    kind: str  # fixed, revolute, continuous, prismatic, planar, floating
    frame: fr.Frame  # world
    limits: tuple[float, float] | None  # radians or metres
    link: Link | None


@dataclass
class Tree:
    base: str
    leaves: dict[str, Leaf]
    joints: list[Joint]
    frames: dict[str, fr.Frame]  # link frame in world
    mimics: dict[str, tuple[str, float]]  # driven joint -> (driver joint, multiplier)


KINDS = {"rigid": "fixed", "fastener": "fixed", "revolute": "revolute", "slider": "prismatic",
         "cylindrical": "continuous", "planar": "planar", "ball": "floating"}  # fmt: skip


def tree(built: BuiltDesign) -> Tree:
    leaves = {leaf.path: leaf for leaf in built.leaves()}
    found, groups = nested_links(built, lambda design, name: True)
    base_path = built.leaves_of(built.spec.ground)[0].path if built.spec.ground else next(iter(leaves))
    adjacency: dict[str, list[Link]] = {p: [] for p in leaves}
    for link in found:
        if isinstance(link.source, Relation):
            continue
        adjacency[link.a.instance].append(link)
        adjacency[link.b.instance].append(link)
    frames = {base_path: fr.IDENTITY}
    joints: list[Joint] = []
    by_child: dict[str, Joint] = {}
    todo = [base_path]
    while todo:
        parent = todo.pop(0)
        for link in adjacency[parent]:
            child = link.b.instance if link.a.instance == parent else link.a.instance
            if child in frames:
                continue  # closes a loop: trees have none
            kind = KINDS[link.kind]
            limits = None
            if isinstance(link.source, Mate) and (lim := link.source.limits_of()):
                if kind == "revolute" and lim.angle:
                    limits = (math.radians(lim.angle[0]), math.radians(lim.angle[1]))
                elif kind == "prismatic" and lim.travel:
                    limits = (lim.travel[0] * MM, lim.travel[1] * MM)
            if kind == "revolute" and limits is None:
                kind = "continuous"
            if kind == "prismatic" and limits is None:
                kind = "fixed"  # a slide without limits has no range to export
            frames[child] = link.a.frame
            joint = Joint(f"{link_name(parent)}__{link_name(child)}", link_name(parent), link_name(child), kind,
                          link.a.frame, limits, link)  # fmt: skip
            joints.append(joint)
            by_child[child] = joint
            todo.append(child)
    for path in leaves:
        if path not in frames:  # nothing reaches it: fixed where it stands
            frames[path] = leaves[path].transform
            joint = Joint(f"{link_name(base_path)}__{link_name(path)}", link_name(base_path), link_name(path), "fixed",
                          leaves[path].transform, None, None)  # fmt: skip
            joints.append(joint)
            by_child[path] = joint
    mimics = {}
    for link in found:
        if isinstance(link.source, Relation):
            driver, driven = by_child.get(link.a.instance), by_child.get(link.b.instance)
            law = link.source.law(link.ends())
            if (driver and driven and {driver.kind, driven.kind} <= {"revolute", "continuous", "prismatic"}
                    and isinstance(law, Linear)):  # a mimic is a straight line  # fmt: skip
                axis_a, axis_b = link.source.axes(link.a.frame, link.b.frame)
                # joint values turn about (or slide along) each joint's Z, in radians or metres
                sign = np.sign(np.dot(fr.z_axis(driver.frame), axis_a.direction)) * np.sign(
                    np.dot(fr.z_axis(driven.frame), axis_b.direction))  # fmt: skip
                unit = {"angle": math.radians(1), "travel": MM}
                mimics[driven.name] = (driver.name, float(sign * law.ratio * unit[axis_b.kind]
                                                          / unit[axis_a.kind]))  # fmt: skip
    return Tree(base_path, leaves, joints, frames, mimics)


def _meshes(t: Tree, out: Path, folder: str) -> dict[str, str]:
    """Each link's mesh, by its part's own file in the build (`parts/lid.stl`, a component's
    `components/box/parts/lid.stl`): part names are unique only within their design."""
    names = {}
    for leaf in t.leaves.values():
        rel = f"meshes/{leaf.part.mesh_file.relative_to(out).as_posix()}"
        if not (out / folder / rel).exists():
            (out / folder / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(leaf.part.mesh_file, out / folder / rel)
        names[leaf.path] = rel
    return names


def _inertial(leaf: Leaf, link_frame: fr.Frame) -> tuple[float, np.ndarray, np.ndarray] | None:
    """(kg, centre in the link frame in m, inertia about the centre in kg·m² in link axes)."""
    m = leaf.owner.mass(leaf.part, np.linalg.inv(link_frame) @ leaf.transform)
    if m is None:
        return None
    return m.mass_g / 1000, np.array(m.centre_mm) * MM, np.array(m.inertia_g_mm2) * 1e-9  # g·mm² → kg·m²


class UrdfExporter:
    name = "urdf"
    title = "URDF robot description"
    summary = "urdf/<design>.urdf with meshes/: links per part instance, joints from mates (ROS, PyBullet, MuJoCo)"

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        t = tree(built)
        meshes = _meshes(t, out_dir, "urdf")
        robot = ET.Element("robot", name=link_name(built.spec.name) or "design")
        for path, leaf in t.leaves.items():
            link = ET.SubElement(robot, "link", name=link_name(path))
            inertial = _inertial(leaf, t.frames[path])
            if inertial:
                mass, centre, inertia = inertial
                el = ET.SubElement(link, "inertial")
                ET.SubElement(el, "origin", xyz=" ".join(f"{v:.9g}" for v in centre), rpy="0 0 0")
                ET.SubElement(el, "mass", value=f"{mass:.9g}")
                ET.SubElement(el, "inertia", ixx=f"{inertia[0, 0]:.9g}", ixy=f"{inertia[0, 1]:.9g}",
                              ixz=f"{inertia[0, 2]:.9g}", iyy=f"{inertia[1, 1]:.9g}", iyz=f"{inertia[1, 2]:.9g}",
                              izz=f"{inertia[2, 2]:.9g}")  # fmt: skip
            xyz, angles = pose_text(np.linalg.inv(t.frames[path]) @ leaf.transform)
            for tag in ("visual", "collision"):
                el = ET.SubElement(link, tag)
                ET.SubElement(el, "origin", xyz=xyz, rpy=angles)
                geometry = ET.SubElement(el, "geometry")
                ET.SubElement(geometry, "mesh", filename=meshes[path], scale=f"{MM} {MM} {MM}")
        parents = {link_name(p): p for p in t.leaves}
        for joint in t.joints:
            el = ET.SubElement(robot, "joint", name=joint.name, type=joint.kind)
            xyz, angles = pose_text(np.linalg.inv(t.frames[parents[joint.parent]]) @ joint.frame)
            ET.SubElement(el, "origin", xyz=xyz, rpy=angles)
            ET.SubElement(el, "parent", link=joint.parent)
            ET.SubElement(el, "child", link=joint.child)
            if joint.kind != "fixed":
                ET.SubElement(el, "axis", xyz="0 0 1")
            if joint.limits:
                ET.SubElement(el, "limit", lower=f"{joint.limits[0]:.9g}", upper=f"{joint.limits[1]:.9g}", effort="0",
                              velocity="0")  # fmt: skip
            if joint.name in t.mimics:
                driver, multiplier = t.mimics[joint.name]
                ET.SubElement(el, "mimic", joint=driver, multiplier=f"{multiplier:.9g}")
        ET.indent(robot)
        rel = f"urdf/{link_name(built.spec.name) or 'design'}.urdf"
        (out_dir / rel).write_text(ET.tostring(robot, encoding="unicode"), encoding="utf-8")
        return [(rel, "application/xml"), *((f"urdf/{m}", "model/stl") for m in sorted(set(meshes.values())))]


SDF_KINDS = {"fixed": "fixed", "revolute": "revolute", "continuous": "revolute", "prismatic": "prismatic",
             "planar": "fixed", "floating": "ball"}  # fmt: skip


class SdfExporter:
    name = "sdf"
    title = "SDFormat model"
    summary = "sdf/model.sdf with meshes/: the design as an SDFormat model (Gazebo)"

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        t = tree(built)
        meshes = _meshes(t, out_dir, "sdf")
        sdf = ET.Element("sdf", version="1.9")
        model = ET.SubElement(sdf, "model", name=link_name(built.spec.name) or "design")
        for path, leaf in t.leaves.items():
            link = ET.SubElement(model, "link", name=link_name(path))
            ET.SubElement(link, "pose").text = " ".join(pose_text(t.frames[path]))
            inertial = _inertial(leaf, t.frames[path])
            if inertial:
                mass, centre, inertia = inertial
                el = ET.SubElement(link, "inertial")
                ET.SubElement(el, "pose").text = " ".join(f"{v:.9g}" for v in centre) + " 0 0 0"
                ET.SubElement(el, "mass").text = f"{mass:.9g}"
                tensor = ET.SubElement(el, "inertia")
                for tag, (i, j) in {
                    "ixx": (0, 0),
                    "ixy": (0, 1),
                    "ixz": (0, 2),
                    "iyy": (1, 1),
                    "iyz": (1, 2),
                    "izz": (2, 2),
                }.items():
                    ET.SubElement(tensor, tag).text = f"{inertia[i, j]:.9g}"
            pose = " ".join(pose_text(np.linalg.inv(t.frames[path]) @ leaf.transform))
            for tag in ("visual", "collision"):
                el = ET.SubElement(link, tag, name=tag)
                ET.SubElement(el, "pose").text = pose
                mesh = ET.SubElement(ET.SubElement(el, "geometry"), "mesh")
                ET.SubElement(mesh, "uri").text = meshes[path]
                ET.SubElement(mesh, "scale").text = f"{MM} {MM} {MM}"
        children = {link_name(p): p for p in t.leaves}
        for joint in t.joints:
            el = ET.SubElement(model, "joint", name=joint.name, type=SDF_KINDS[joint.kind])
            ET.SubElement(el, "parent").text = joint.parent
            ET.SubElement(el, "child").text = joint.child
            ET.SubElement(el, "pose", relative_to=joint.child).text = " ".join(
                pose_text(np.linalg.inv(t.frames[children[joint.child]]) @ joint.frame)
            )
            if SDF_KINDS[joint.kind] in ("revolute", "prismatic"):
                axis = ET.SubElement(el, "axis")
                ET.SubElement(axis, "xyz").text = "0 0 1"
                if joint.limits:
                    limit = ET.SubElement(axis, "limit")
                    ET.SubElement(limit, "lower").text = f"{joint.limits[0]:.9g}"
                    ET.SubElement(limit, "upper").text = f"{joint.limits[1]:.9g}"
        ET.indent(sdf)
        (out_dir / "sdf").mkdir(exist_ok=True)
        (out_dir / "sdf" / "model.sdf").write_text(ET.tostring(sdf, encoding="unicode"), encoding="utf-8")
        return [
            ("sdf/model.sdf", "application/xml"),
            *((f"sdf/{m}", "model/stl") for m in sorted(set(meshes.values()))),
        ]
