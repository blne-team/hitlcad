"""Section views: a built design cut by the plane through a joint's axis, each instance's cut filled in its own
colour and labelled. A section through a joint shows a fit problem an iso view does not."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from hitlcad.core import frames as fr
from hitlcad.kernel.errors import HitlcadError

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign

PALETTE = [(31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40), (148, 103, 189), (140, 86, 75),
           (227, 119, 194), (127, 127, 127), (188, 189, 34), (23, 190, 207)]  # fmt: skip


def section_png(built: BuiltDesign, frame: fr.Frame, focus: list[str], size: int, title: str) -> bytes | None:
    """PNG of the cut through `frame`'s Z axis (the plane holding Z and X), framed on the `focus` instances."""
    origin, x_axis, z_axis = fr.origin(frame), fr.x_axis(frame), fr.z_axis(frame)
    normal = np.cross(z_axis, x_axis)  # the plane holds the axis (Z) and X
    u, v = x_axis, z_axis  # drawing axes: across the joint, along it
    cuts: list[tuple[str, list[np.ndarray]]] = []
    for leaf in built.leaves():
        section = leaf.mesh.section(plane_origin=origin, plane_normal=normal)
        if section is None:
            continue
        loops = [np.array([[np.dot(p - origin, u), np.dot(p - origin, v)] for p in loop])
                 for loop in section.discrete]  # fmt: skip
        if loops:
            cuts.append((leaf.path, loops))
    framed = [pts for path, loops in cuts if path in focus for pts in loops] or [
        pts for _, loops in cuts for pts in loops
    ]
    if not framed:
        return None
    allpts = np.vstack(framed)
    lo, hi = allpts.min(axis=0), allpts.max(axis=0)
    pad = 0.15 * float(max(hi - lo)) or 1.0
    lo, hi = lo - pad, hi + pad
    scale = (size - 40) / float(max(hi - lo))
    image = Image.new("RGB", (size, size), (250, 250, 247))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(11, size // 40))

    def px(p: np.ndarray) -> tuple[float, float]:
        return (20 + (p[0] - lo[0]) * scale, size - 20 - (p[1] - lo[1]) * scale)

    labels = []
    for k, (path, loops) in enumerate(cuts):
        colour = PALETTE[k % len(PALETTE)]
        fill = tuple(int(c + (255 - c) * 0.55) for c in colour)
        for pts in loops:
            draw.polygon([px(p) for p in pts], fill=fill, outline=colour)  # type: ignore[arg-type]
        centre = np.vstack(loops).mean(axis=0)
        if lo[0] <= centre[0] <= hi[0] and lo[1] <= centre[1] <= hi[1]:
            labels.append((px(centre), path, colour))
    axis_a, axis_b = px(np.array([0.0, lo[1]])), px(np.array([0.0, hi[1]]))
    draw.line([axis_a, axis_b], fill=(90, 90, 90), width=1)
    for (x, y), path, colour in labels:
        draw.text((x + 3, y - 6), path, fill=tuple(int(c * 0.6) for c in colour), font=font)
    draw.text((10, 6), title, fill=(30, 30, 30), font=font)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def joint_frames(built: BuiltDesign, result: Any) -> tuple[fr.Frame, list[str]] | None:
    """Where to cut for a failing mate or fastener result: its first end's frame, and the instances involved."""
    instances = [a for a in result.about if "." not in a]
    connectors = [a for a in result.about if "." in a and "+" not in a]
    for address in connectors:
        try:
            frames = built.frames(_declared(address))
        except HitlcadError:
            continue
        hit = next((f for f in frames if f.instance in instances), None)
        if hit is not None:
            return hit.frame, instances
    return None


def _declared(address: str) -> str:
    """'planet[1].bore' -> 'planet.bore': the end as design.json declares it."""
    body, _, connector = address.rpartition(".")
    owner = body.split("/")[0].split("[")[0]
    return f"{owner}.{connector.split('[')[0]}"
