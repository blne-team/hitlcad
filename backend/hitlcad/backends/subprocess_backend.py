"""Base class for backends that run model code in a child process via an entry script.

Protocol between parent and entry script (cwd = output directory):

    in:  model.py, job.json {"params": {...}, ...}
    out: result.json {"ok": bool, "error": str|None, "files": [{name, media_type, role}], "info": {...}}
"""

from __future__ import annotations

import json
from abc import abstractmethod
from pathlib import Path
from typing import Any

from hitlcad.backends.base import BackendBuild, CADBackend, OutputFile
from hitlcad.backends.executor import Executor, LocalExecutor


class SubprocessBackend(CADBackend):
    def __init__(
        self, *, executor: Executor | None = None, timeout_s: float = 180.0, memory_mb: int | None = None
    ) -> None:
        self.executor = executor or LocalExecutor()
        self.timeout_s = timeout_s
        self.memory_mb = memory_mb

    @abstractmethod
    def command(self, job_file: Path) -> list[str]:
        """argv that runs the entry script for ``job_file`` (cwd will be the output directory)."""

    def job_options(self) -> dict[str, Any]:
        return {}

    async def build(self, source: str, params: dict[str, Any], out_dir: Path) -> BackendBuild:
        # The child runs with cwd=out_dir, so paths handed to it must not be relative to ours.
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "model.py").write_text(source, encoding="utf-8")
        job_file = out_dir / "job.json"
        job_file.write_text(json.dumps({"params": params, **self.job_options()}), encoding="utf-8")
        result_file = out_dir / "result.json"
        result_file.unlink(missing_ok=True)

        run = await self.executor.run(
            self.command(job_file), out_dir, timeout_s=self.timeout_s, memory_mb=self.memory_mb
        )
        if run.timed_out:
            return BackendBuild(log=run.output, error=f"build exceeded the {self.timeout_s:.0f}s time limit")
        if not result_file.is_file():
            tail = "\n".join(run.output.strip().splitlines()[-25:])
            return BackendBuild(log=run.output, error=f"build process exited with code {run.returncode}:\n{tail}")

        result = json.loads(result_file.read_text(encoding="utf-8"))
        files = [OutputFile(**f) for f in result.get("files", []) if (out_dir / f["name"]).is_file()]
        return BackendBuild(
            files=files,
            log=run.output,
            error=None if result.get("ok") else (result.get("error") or "build failed without an error message"),
            info=result.get("info", {}),
        )
