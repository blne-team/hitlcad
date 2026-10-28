"""Measure manufacturing on an existing build, excluding geometry loading and persistent result caching.

Run with the project's Python environment:
    uv run python scripts/profile_manufacturing.py /path/to/design --repeat 3 --out /tmp/manufacturing.json
Add --profile /tmp/manufacturing.prof for cumulative call timings (view with python -m pstats).
The source design.json must describe the saved build. Each repetition gets fresh bodies and wall samples.
"""

from __future__ import annotations

import argparse
import cProfile
import importlib.metadata
import json
import platform
import statistics
import time
from pathlib import Path
from typing import Any

from hitlcad.analysis.base import ResultCache, analysis_code
from hitlcad.analysis.model import AnalysisSettings, BuildBodies
from hitlcad.build.pipeline import load_built
from hitlcad.config import Settings
from hitlcad.core.report import BuildReport
from hitlcad.workspace.folder import DesignFolder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("design", type=Path)
    parser.add_argument("--build", type=Path, help="Saved build directory; defaults to DESIGN/build")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True, help="Per-part timings, settings, mesh digests and findings")
    parser.add_argument("--profile", type=Path, help="Optional cProfile output")
    args = parser.parse_args()
    defaults = Settings()
    folder = DesignFolder(args.design)
    build_dir = args.build or folder.build_dir
    report = BuildReport.model_validate_json((build_dir / "report.json").read_text(encoding="utf-8"))
    spec = folder.spec(defaults.tolerances, report.configuration)
    built = load_built(report, spec, folder.path, build_dir)
    settings = AnalysisSettings(
        overlap_mm3=defaults.overlap_mm3,
        mesh_tolerance_mm=defaults.mesh_tolerance_mm,
        wall_samples=defaults.wall_samples,
    )
    processes = {name: p for name in built.parts if (p := built.spec.process(name)) is not None}
    # Parts/connector analyses have already loaded geometry and metrics before manufacturing in a real build.
    for name in processes:
        _ = built.parts[name].metrics
    records: dict[str, dict[str, Any]] = {
        name: {
            "process": process.model_dump(mode="json"),
            "mesh_digest": built.parts[name].digest,
            "triangles": len(built.parts[name].mesh.faces),
            "seconds": [],
        }
        for name, process in processes.items()
    }
    profile = cProfile.Profile() if args.profile else None
    for repetition in range(args.repeat):
        bodies = BuildBodies(built, settings, ResultCache(defaults.cache_dir / "analysis"))
        for name, process in processes.items():
            body = bodies.part(name)
            started = time.perf_counter()
            if profile is not None:
                profile.enable()
            findings = process.checks(body)
            if profile is not None:
                profile.disable()
            elapsed = time.perf_counter() - started
            records[name]["seconds"].append(elapsed)
            records[name]["findings"] = [finding.model_dump(mode="json") for finding in findings]
            records[name]["wall_points"] = len(body.__dict__["walls"].points) if "walls" in body.__dict__ else 0
            print(f"{repetition + 1}/{args.repeat} {name}: {elapsed:.4f} s", flush=True)
    totals = [sum(row["seconds"][i] for row in records.values()) for i in range(args.repeat)]
    output = {
        "design": str(folder.path),
        "build": str(build_dir),
        "configuration": report.configuration,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "analysis_code": analysis_code(),
        "versions": {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "trimesh", "libigl")},
        "wall_samples": settings.wall_samples,
        "mesh_tolerance_mm": settings.mesh_tolerance_mm,
        "tolerances": built.tolerances.model_dump(mode="json"),
        "profiled": profile is not None,
        "seconds": totals,
        "parts": records,
    }
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    if profile is not None:
        profile.dump_stats(str(args.profile))
    print(f"Manufacturing median: {statistics.median(totals):.4f} s ({len(processes)} parts)")


if __name__ == "__main__":
    main()
