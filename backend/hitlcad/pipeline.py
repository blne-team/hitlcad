"""Backend-agnostic build pipeline: build → inspect → 3D view → render → manifest."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from hitlcad.analysis import cadview
from hitlcad.analysis.assembly import Build, Part, part_name
from hitlcad.analysis.mesh import CheckContext, load_mesh, measure, measure_assembly, run_checks
from hitlcad.backends.base import MESH_FILE
from hitlcad.backends.registry import BackendRegistry
from hitlcad.core.types import Artifact, BuildReport, BuildSpec, BuildStatus, param_values
from hitlcad.render.base import NullRenderer, Renderer

LOG_TAIL_LINES = 40
VIEW_FILE = "view.json"  # three-cad-viewer input, tessellated from the STEP when there is one


def _artifact(out_dir: Path, name: str, media_type: str, role: str) -> Artifact:
    path = out_dir / name
    data = path.read_bytes()
    return Artifact(name=name, path=name, media_type=media_type, role=role,  # type: ignore[arg-type]
                    sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))  # fmt: skip


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-LOG_TAIL_LINES:])


class BuildPipeline:
    def __init__(self, registry: BackendRegistry, renderer: Renderer | None = None) -> None:
        self.registry = registry
        self.renderer: Renderer = renderer or NullRenderer()

    async def run(self, spec: BuildSpec, out_dir: Path) -> BuildReport:
        started = time.monotonic()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "model.py").write_text(spec.source, encoding="utf-8")
        (out_dir / "params.json").write_text(
            json.dumps([p.model_dump() for p in spec.parameters], indent=2), encoding="utf-8"
        )
        base = [_artifact(out_dir, "model.py", "text/x-python", "source"),
                _artifact(out_dir, "params.json", "application/json", "params")]  # fmt: skip

        def failed(error: str, log: str = "") -> BuildReport:
            if log:
                (out_dir / "build.log").write_text(log, encoding="utf-8")
            return BuildReport(status=BuildStatus.FAILED, backend=spec.backend, error=error, log_tail=_tail(log),
                               duration_s=round(time.monotonic() - started, 3), artifacts=base)  # fmt: skip

        try:
            backend = self.registry.get(spec.backend)
        except KeyError as e:  # design.json names a backend that is not enabled
            return failed(str(e))
        available, detail = backend.availability()
        if not available:
            return failed(f"backend {spec.backend} unavailable: {detail}")

        result = await backend.build(spec.source, param_values(spec.parameters), out_dir)
        (out_dir / "build.log").write_text(result.log, encoding="utf-8")
        if result.error:
            return failed(result.error, result.log)
        if MESH_FILE not in {f.name for f in result.files}:
            return failed(f"backend did not produce {MESH_FILE}", result.log)

        by_role = {role: {part_name(f.name): out_dir / f.name for f in result.files if f.role == role}
                   for role in ("part_mesh", "part_exchange")}  # fmt: skip
        step = next((out_dir / f.name for f in result.files if f.role == "exchange"), None)
        try:
            mesh = load_mesh(out_dir / MESH_FILE)
            part_meshes = {name: load_mesh(path) for name, path in by_role["part_mesh"].items()}
        except Exception as e:  # an unreadable mesh is a failed build, not a server error
            return failed(f"could not read the build's meshes: {e}", result.log)
        parts = {name: Part(name, m, measure(m), by_role["part_exchange"].get(name)) for name, m in part_meshes.items()}
        metrics = measure(mesh)
        if parts:
            metrics = measure_assembly(metrics, [p.metrics for p in parts.values()])
        build = Build(Part("model", mesh, metrics, step), parts)
        checks = run_checks(CheckContext(spec, build, result.info))
        verified = await asyncio.to_thread(lambda: [r.verify(build) for r in spec.requirements])
        geometry = step or out_dir / MESH_FILE
        try:
            await asyncio.to_thread(cadview.write_view, geometry, out_dir / VIEW_FILE, by_role["part_mesh"])
        except Exception as e:
            return failed(f"could not prepare the 3D view from {geometry.name}: {e}", result.log)
        artifacts = base + [_artifact(out_dir, f.name, f.media_type, f.role) for f in result.files]
        artifacts.append(_artifact(out_dir, VIEW_FILE, "application/json", "view"))
        preview = await self.renderer.render(out_dir / MESH_FILE, out_dir, metrics)
        if preview:
            artifacts.append(_artifact(out_dir, preview.name, "image/png", "preview"))
        artifacts.append(_artifact(out_dir, "build.log", "text/plain", "log"))

        hard_fail = [c for c in checks if not c.passed and c.severity == "error"]
        report = BuildReport(
            status=BuildStatus.FAILED if hard_fail else BuildStatus.SUCCEEDED,
            backend=spec.backend,
            duration_s=round(time.monotonic() - started, 3),
            error="; ".join(f"{c.name}: {c.detail}" for c in hard_fail) or None,
            log_tail=_tail(result.log) if hard_fail else "",
            metrics=metrics,
            checks=checks,
            parts=[p.info() for p in build.parts.values()],
            requirements=verified,
            artifacts=artifacts,
        )
        (out_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report
