"""Designing from a picture: a subject is cut out of a transparent PNG, a photo and a sketch alike, and the silhouette
check compares a build with it, saying what strays which way; a changed picture builds again from cached parts."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from mcp.client import Client
from PIL import Image, ImageDraw, ImageFilter

from hitlcad.analysis.model import BuildBodies
from hitlcad.api.mcp import build_mcp
from hitlcad.app import HitlCad
from hitlcad.build.pipeline import load_built
from hitlcad.builtin.pictures.picture import cut_out
from hitlcad.core.report import BuildReport, BuildSpec
from hitlcad.workspace.folder import DesignFolder

from .conftest import edit_json, results, write_design

#: The subject in every picture: a 300 × 400 px figure, a head on a body, in a 600 × 600 px picture.
HEAD, BODY = (230, 100, 370, 240), (200, 220, 400, 500)


def truth() -> np.ndarray:
    im = Image.new("L", (600, 600), 0)
    draw = ImageDraw.Draw(im)
    draw.ellipse(HEAD, fill=255)
    draw.rectangle(BODY, fill=255)
    return np.asarray(im) > 127


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / (a | b).sum())


def picture(kind: str, path: Path) -> Path:
    mask = Image.fromarray(truth().astype(np.uint8) * 255)
    if kind == "cut out":  # a transparent PNG, its colour no different from what was around it
        im = Image.new("RGBA", mask.size, (250, 250, 250, 0))
        im.paste((240, 240, 240, 255), mask=mask)
    elif kind == "photo":  # a shaded subject on an uneven, noisy backdrop, saved lossily
        rng = np.random.default_rng(1)
        backdrop = np.linspace(225, 245, 600)[:, None, None] + rng.normal(0, 4, (600, 600, 3))
        shading = np.linspace(0.6, 1.0, 600)[None, :, None] * np.array([150, 90, 40])
        rgb = np.where(truth()[..., None], shading + rng.normal(0, 6, (600, 600, 3)), backdrop)
        im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1))
        path = path.with_suffix(".jpg")
    else:  # a sketch: its outline in pen on paper
        im = Image.new("RGB", mask.size, "white")
        draw = ImageDraw.Draw(im)
        for colour, inset in (("black", 0), ("white", 4)):
            draw.ellipse([c + s * inset for c, s in zip(HEAD, (1, 1, -1, -1), strict=True)], fill=colour)
            draw.rectangle([c + s * inset for c, s in zip(BODY, (1, 1, -1, -1), strict=True)], fill=colour)
    im.save(path)
    return path


@pytest.mark.parametrize("kind", ["cut out", "photo", "sketch"])
def test_the_subject_is_cut_out_of_a_transparent_png_a_photo_and_a_sketch(kind: str, tmp_path: Path) -> None:
    subject = cut_out(picture(kind, tmp_path / "subject.png"))
    small = np.asarray(Image.fromarray(truth()).resize(subject.mask.shape[::-1])) > 0
    assert iou(subject.mask, small) > 0.95


def figure_design(picture_file: str) -> dict[str, object]:
    """A 30 × 10 × `height` mm block standing on Z=0, compared from the front with a 40 mm tall picture."""
    return {
        "design.json": {
            "hitlcad": 2, "name": "figure", "backend": "build123d", "ground": "figure",
            "parameters": [{"name": "height", "value": 40, "min": 10, "max": 100}],
            "parts": {"figure": {"role": "the figure", "file": "parts/figure.py", "material": "PLA"}},
            "requirements": [{"text": "Looks like the drawing from the front", "check": {
                "kind": "silhouette", "image": picture_file, "view": "front", "height": 40,
                "min_overlap": 0.9, "max_deviation": 2}}],
        },
        "parts/figure.py": "from build123d import Align, Box\n\n\ndef build(p, i):\n"
                           "    return {'figure': (Box(30, 10, p['height'], align=(Align.CENTER, Align.CENTER, Align.MIN)), {})}\n",
        "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'figure': [Pos()]}\n",
    }  # fmt: skip


def drawing(path: Path, eared: bool) -> None:
    """A 300 × 400 px block (30 × 40 mm at 40 mm tall); eared, all but its left 80 px are 100 px (10 mm) lower."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (500, 600), "white")
    draw = ImageDraw.Draw(im)
    draw.rectangle((100, 250 if eared else 150, 399, 549), fill=(90, 60, 30))
    draw.rectangle((100, 150, 179, 549), fill=(90, 60, 30))
    im.save(path)


@pytest.mark.geometry
async def test_a_build_is_compared_with_its_picture_and_a_new_picture_builds_again_from_cached_parts(
    core: HitlCad, tmp_path: Path
) -> None:
    folder = write_design(tmp_path / "figure", figure_design("references/front.png"))
    drawing(folder / "references/front.png", eared=False)
    first = DesignFolder(folder).spec(core.settings.tolerances)
    report = await core.pipeline.run(first, folder, tmp_path / "out1")
    (looks,) = results(report, "requirement")
    assert looks.status == "pass", looks.detail
    assert "apart by up to 0.1 mm" in looks.detail and "reference 30.0 × 40.0 mm" in looks.detail

    drawing(folder / "references/front.png", eared=True)
    second = DesignFolder(folder).spec(core.settings.tolerances)
    assert second.key != first.key and second.checked != first.checked and second.sources == first.sources
    report = await core.pipeline.run(second, folder, tmp_path / "out2")
    assert all(p.cached for p in report.parts)
    (looks,) = results(report, "requirement")
    assert looks.status == "fail" and looks.code == "requirement.silhouette"
    assert ": 1 figure reaches 10.0 mm past the reference towards +Z at X " in looks.detail, looks.detail
    assert check_figure(core, report, second, folder, tmp_path / "out2").startswith(b"\x89PNG")

    drawing(folder / "references/front.png", eared=False)
    edit_json(folder / "design.json", lambda d: d["parameters"][0].update(value=30))  # shorter than its picture
    report = await core.pipeline.run(DesignFolder(folder).spec(core.settings.tolerances), folder, tmp_path / "out3")
    (looks,) = results(report, "requirement")
    assert ": 1 figure stops 10.0 mm short of the reference towards +Z at X " in looks.detail, looks.detail


def check_figure(core: HitlCad, report: BuildReport, spec: BuildSpec, folder: Path, out: Path) -> bytes:
    built = load_built(report, spec, folder, out)
    bodies = BuildBodies(built, core.pipeline.analysis_settings, core.pipeline.results_cache)
    figure = spec.design.requirements[0].check.figure(bodies)  # type: ignore[union-attr]
    assert figure is not None
    return figure


@pytest.mark.geometry
async def test_a_missing_picture_fails_saying_where_to_save_it(core: HitlCad, tmp_path: Path) -> None:
    folder = write_design(tmp_path / "figure", figure_design("references/side.png"))
    spec = DesignFolder(folder).spec(core.settings.tolerances)
    assert spec.checked == {"references/side.png": ""}
    report = await core.pipeline.run(spec, folder, tmp_path / "out")
    (looks,) = results(report, "requirement")
    assert looks.status == "fail" and looks.detail.startswith("there is no picture at side.png"), looks.detail


@pytest.mark.geometry
async def test_views_are_requested_for_a_failing_silhouette_without_rebuilding(core: HitlCad, tmp_path: Path) -> None:
    folder = write_design(tmp_path / "figure", figure_design("references/front.png"))
    drawing(folder / "references/front.png", eared=True)
    async with Client(build_mcp(core)) as client:
        result = await client.call_tool("build_design", {"path": str(folder)})
        assert not result.is_error and all(c.type == "text" for c in result.content)
        assert "Looks like the drawing from the front" in result.content[0].text
        for args in ({}, {"focus": "rev 1"}):
            views = await client.call_tool("explore_design", {"path": str(folder), "include_images": True, **args})
            assert not views.is_error
            texts = [c.text for c in views.content if c.type == "text"]
            assert "✗ requirement 'Looks like the drawing from the front':" in texts
            assert [c.mime_type for c in views.content if c.type == "image"] == ["image/png"]
        assert len(core.designs.ensure(folder).revisions) == 1
        built = await client.call_tool("build_design", {"path": str(folder), "include_images": True})
        assert not built.is_error and [c.mime_type for c in built.content if c.type == "image"] == ["image/png"]
