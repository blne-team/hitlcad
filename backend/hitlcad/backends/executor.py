"""Process execution for untrusted model code.

``LocalExecutor`` applies resource limits, a scrubbed environment, a wall-clock deadline and
process-group kill. Time is bounded by the deadline alone: a CPU-time limit counts every thread, so it
would kill parallel work (OCP's and Blender's meshers) long before the deadline. It does not isolate the filesystem or network; swap in a container or
bubblewrap executor for anything beyond local single-user use.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import resource
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

LOG_LIMIT = 64_000


@dataclass
class ExecResult:
    returncode: int | None
    output: str
    timed_out: bool
    duration_s: float


class Executor(Protocol):
    async def run(
        self, argv: list[str], cwd: Path, *, timeout_s: float, memory_mb: int | None = None
    ) -> ExecResult: ...


class LocalExecutor:
    def __init__(self, extra_env: dict[str, str] | None = None) -> None:
        self.extra_env = extra_env or {}

    def _env(self, cwd: Path) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(cwd),
            "TMPDIR": str(cwd),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            # glibc reserves a 64 MB arena per thread; with many cores that exhausts RLIMIT_AS and
            # OCP's parallel mesher deadlocks instead of failing.
            "MALLOC_ARENA_MAX": "2",
        }
        for key in ("DISPLAY", "XDG_RUNTIME_DIR", "LD_LIBRARY_PATH"):
            if key in os.environ:
                env[key] = os.environ[key]
        env.update(self.extra_env)
        return env

    async def run(self, argv: list[str], cwd: Path, *, timeout_s: float, memory_mb: int | None = None) -> ExecResult:
        def limits() -> None:
            if memory_mb:
                cap = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            resource.setrlimit(resource.RLIMIT_FSIZE, (512 << 20, 512 << 20))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self._env(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=limits,
            start_new_session=True,
        )
        timed_out = False
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            out, _ = await proc.communicate()
        except asyncio.CancelledError:  # the build was cancelled (a shutdown): leave no process behind
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await asyncio.shield(proc.wait())
            raise
        text = out.decode("utf-8", errors="replace")
        if len(text) > LOG_LIMIT:
            text = "…[truncated]…\n" + text[-LOG_LIMIT:]
        return ExecResult(proc.returncode, text, timed_out, time.monotonic() - started)
