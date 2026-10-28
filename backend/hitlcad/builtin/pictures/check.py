"""The silhouette check: the model seen from one side against a picture of what it should look like."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import numpy as np
from pydantic import Field

from hitlcad.builtin.pictures.picture import (
    FROM,
    VIEWS,
    Comparison,
    PictureError,
    Placed,
    Spot,
    compare,
    cut_out,
    figure,
    place,
    plane,
    towards,
)
from hitlcad.core.checks import Number, OnTarget, Verdict, need

if TYPE_CHECKING:
    from hitlcad.analysis.model import Body, BuildBodies


class SilhouetteCheck(OnTarget):
    """Looks like a picture from one side: the model's outline seen from `view` against the subject's, cut out of
    the picture (a photo, render or sketch under references/; a PNG with a transparent background as it is cut),
    scaled to `height` mm and placed standing on Z=0, centred across (from top or bottom: centred on the origin).
    Views: front (from −Y, right is +X), back, right (from +X, right is +Y), left, top (from +Z, up is +Y), bottom.
    Measures the share of the two outlines' union they share (`min_overlap`) and how far either strays from the
    other (`max_deviation`, mm). The requirement's text is what the human says about the picture. A failing result
    names what strays, where and which way, with a figure: both outlines over the picture on a millimetre grid."""

    kind: Literal["silhouette"]
    image: str = Field(description="The picture, relative to the design folder: references/front.jpg")
    view: Literal["front", "back", "right", "left", "top", "bottom"]
    height: Number = Field(description="The subject's extent up the picture, mm")
    min_overlap: Number = Field(default=0.9, description="Intersection over union of the outlines, 0 to 1")
    max_deviation: Number | None = Field(default=None, description="mm")
    example = {"kind": "silhouette", "image": "references/front.jpg", "view": "front", "height": "height",
               "min_overlap": 0.9, "max_deviation": 3}  # fmt: skip
    hint = (
        "Reshape or move what the message names the way it says, seen from the picture's side: the figure shows "
        "both outlines. Where the reference outline misses the subject, ask the human for a picture on a plain "
        "background, or cut out (a PNG with a transparent background)."
    )

    def files(self) -> list[str]:
        return [self.image]

    def verify(self, build: BuildBodies) -> Verdict:
        try:
            body, placed, compared = self._compare(build)
        except PictureError as e:
            return False, str(e)
        least, most = build.number(self.min_overlap), build.number(self.max_deviation)
        passed = compared.overlap >= least and (most is None or compared.deviation <= most)  # type: ignore[operator]
        strays = [f"{n} {self._stray(body, placed, spot)}" for n, spot in enumerate(compared.spots, 1)]
        dh, dv = compared.offset
        if max(abs(dh), abs(dv)) > self._tolerance(build, placed):
            strays.append(f"the model's box is centred {np.hypot(dh, dv):.1f} mm towards {towards(self.view, dh, dv)} "
                          "of the reference's")  # fmt: skip
        left, bottom, right, top = placed.box
        return passed, (f"{self.view} (from {FROM[self.view]}): overlap {compared.overlap:.2f} (needs ≥ {least:g}), "
                        f"apart by up to {compared.deviation:.1f} mm" + (f" (needs {need(None, most)})" if most else "")
                        + (f": {'; '.join(strays)}" if strays else "")
                        + f"; reference {right - left:.1f} × {top - bottom:.1f} mm")  # fmt: skip

    def figure(self, build: BuildBodies) -> bytes | None:
        try:
            _, placed, compared = self._compare(build)
        except PictureError:
            return None
        return figure(placed, compared, f"{self.image} from the {self.view} ({FROM[self.view]}): overlap "
                      f"{compared.overlap:.2f}, apart by up to {compared.deviation:.1f} mm")  # fmt: skip

    def _tolerance(self, build: BuildBodies, placed: Placed) -> float:
        """How far apart counts as straying: `max_deviation`, else 2 % of the reference's larger side."""
        left, bottom, right, top = placed.box
        most = build.number(self.max_deviation)
        return most if most is not None else 0.02 * max(right - left, top - bottom)

    def _compare(self, build: BuildBodies) -> tuple[Body, Placed, Comparison]:
        placed = place(cut_out(build.built.folder / self.image), self.view, build.number(self.height))  # type: ignore[arg-type]
        body = self.subject(build)
        hv = plane(body.mesh.vertices, self.view)
        return body, placed, compare(placed, hv, body.mesh.faces, self._tolerance(build, placed))

    def _stray(self, body: Body, placed: Placed, spot: Spot) -> str:
        """'head (ear_left) stops 4.2 mm short of the reference towards +Z at X −18.0, Z 168.0': the way from the
        other outline's nearest point to the spot, which is the model's where it reaches past the reference."""
        way = towards(self.view, spot.at[0] - spot.nearest[0], spot.at[1] - spot.nearest[1])
        what = f"reaches {spot.depth:.1f} mm past" if spot.beyond else f"stops {spot.depth:.1f} mm short of"
        model = spot.at if spot.beyond else spot.nearest
        (h, hs), (v, vs) = VIEWS[self.view]
        at = f"{'XYZ'[h]} {spot.at[0] * hs:.1f}, {'XYZ'[v]} {spot.at[1] * vs:.1f}"
        return f"{_named(body, self.view, model)} {what} the reference towards {way} at {at}"


def _named(body: Body, view: str, point: tuple[float, float]) -> str:
    """The instance (and its object, where it has several) whose outline is nearest a point of the picture."""
    gaps = [np.linalg.norm(plane(leaf.vertices, view) - point, axis=1) for leaf in body.leaves]
    k = int(np.argmin([g.min() for g in gaps]))
    leaf = body.leaves[k]
    name = leaf.part.features.named_at(leaf.part.mesh.vertices[int(np.argmin(gaps[k]))])
    return f"{leaf.path} ({name})" if name else leaf.path
