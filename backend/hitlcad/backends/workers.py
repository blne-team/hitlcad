"""A backend's build workers (`runtime.serve`), as a notebook server keeps its notebooks' kernels.

A worker is one design's, from its first job to its end: what it keeps between that design's builds (its
libraries loaded, what it exported) is never another design's, and what the design's code changed in a library
stays with that design. A backend keeps at most `size` workers; a job takes an idle worker of its design (the
one that last ran its part file, when it is idle: that is where its exports are kept), else the spare, else
replaces an idle one that ended, else starts one, else waits for a spare on its way, else replaces the idle worker
of another design used least recently, else waits. A worker that ends, overruns a limit, or ends a job holding more
than half its memory limit is replaced: its successor starts empty, and the part cache on disk brings back every
part it had built.

The spare is a worker of no design yet, its libraries loaded ahead of the next design's first job (`warm`): one is
started with the server, and again whenever a design takes it or starts a worker of its own, while a place is
free; none is started by replacing a design's worker. A server done with designs (`release`) stops their idle
workers, which frees their places for it.

A build holds a `Ticket`. When a newer build of the same design and configuration supersedes it, its jobs still
waiting for a worker end with `Superseded` at once, and its jobs running finish, so what they built reaches the
part cache for the build that replaced it.

A worker is the event loop's that started it (its pipes, and what waits on them): the workers belong to the
loop they run in, and the first use from another kills the ones of the loop before and starts afresh.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from hitlcad.execution.executor import ExecResult, Executor, Worker

#: Of its memory limit, what a worker may hold after a job and still be kept.
KEEP_SHARE = 0.5


class Superseded(Exception):
    """A newer build of the same design and configuration replaced this one."""


@dataclass(eq=False)
class Ticket:
    """A build's claim on the workers: whether a newer build replaced it, and what it builds (its spec's key)."""

    key: str = ""
    superseded: bool = False

    def check(self) -> None:
        """Go on only while no newer build replaced this one."""
        if self.superseded:
            raise Superseded()


@dataclass
class _Held:
    worker: Worker
    design: str | None  # None: started ahead of any job (`warm`)
    ran: set[str] = field(default_factory=set)  # its design's part files it has run


class Workers:
    def __init__(self, executor: Executor, size: int) -> None:
        self.executor = executor
        self.size = size
        self._idle: list[_Held] = []  # least recently used first
        self._held = 0  # workers started or starting, idle or not
        self._changed = asyncio.Condition()
        self._warming: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    def _here(self) -> None:
        loop = asyncio.get_running_loop()
        if loop is self._loop:
            return
        for held in self._idle:
            held.worker.kill()
        self._idle, self._held, self._warming, self._closed = [], 0, set(), False
        self._changed, self._loop = asyncio.Condition(), loop

    def warm(self, argv: list[str]) -> None:
        """Start the spare, unless one is ready or on its way, or no place is free."""
        self._here()
        if self._warming or self._held >= self.size or any(h.design is None for h in self._idle):
            return
        self._held += 1

        async def start() -> None:
            try:
                held = _Held(await self.executor.worker(argv), None)
            except BaseException:
                self._warming.discard(task)
                await self._release_slot()  # and the jobs waiting for it look again
                raise
            async with self._changed:
                self._warming.discard(task)
                self._idle.append(held)
                self._changed.notify_all()

        task = asyncio.get_running_loop().create_task(start())
        self._warming.add(task)

    async def wake(self) -> None:
        """Let the jobs waiting for a worker see that their build was superseded."""
        self._here()
        async with self._changed:
            self._changed.notify_all()

    async def run(self, ticket: Ticket, argv: list[str], design: str, file: str | None, job_file: Path, *,
                  timeout_s: float, memory_mb: int) -> ExecResult:  # fmt: skip
        """A job of `design` (for its part file `file`, if it is one) in one of its workers."""
        self._here()
        held = await self._take(ticket, argv, design, file)
        try:
            result = await held.worker.job(job_file, timeout_s=timeout_s, memory_mb=memory_mb)
            keep = not self._closed and held.worker.alive and held.worker.resident() <= KEEP_SHARE * (memory_mb << 20)
        except BaseException:
            await self._drop(held)
            raise
        if file:
            held.ran.add(file)
        if keep:
            async with self._changed:
                self._idle.append(held)
                self._changed.notify_all()
        else:
            await self._drop(held)
        return result

    async def _take(self, ticket: Ticket, argv: list[str], design: str, file: str | None) -> _Held:
        async with self._changed:
            while True:
                ticket.check()
                mine = [h for h in self._idle if h.design == design and h.worker.alive]
                held = next((h for h in mine if file in h.ran), None) or next(iter(mine), None)
                spare = next((h for h in self._idle if h.design is None and h.worker.alive), None)
                if held or spare:
                    taken = held or spare
                    self._idle.remove(taken)
                    taken.design = design
                    if spare is taken:
                        self.warm(argv)
                    return taken
                other = next((h for h in self._idle if not h.worker.alive), None)
                if other is None and self._held < self.size:
                    self._held += 1
                    break
                if other is None and self._warming:  # the spare on its way, rather than another design's worker
                    await self._changed.wait()
                    continue
                other = other or next((h for h in self._idle if h.design != design), None)
                if other is not None:  # one that ended, or the idle worker of another design: its slot is taken over
                    self._idle.remove(other)
                    break
                await self._changed.wait()
        try:
            if other is not None:
                await other.worker.stop()
            started = _Held(await self.executor.worker(argv), design)
        except BaseException:
            await self._release_slot()
            raise
        self.warm(argv)
        return started

    async def _drop(self, held: _Held) -> None:
        await held.worker.stop()
        await self._release_slot()

    async def _release_slot(self) -> None:
        async with self._changed:
            self._held -= 1
            self._changed.notify_all()

    async def release(self, designs: set[str]) -> None:
        """Stop the idle workers of `designs`, which a server is done with: their places are free for others."""
        self._here()
        async with self._changed:
            done = [h for h in self._idle if h.design in designs]
            self._idle = [h for h in self._idle if h.design not in designs]
        for held in done:
            await self._drop(held)

    async def close(self) -> None:
        """Stop the idle workers, and one still starting; one running a job stops once its job is done."""
        self._here()
        self._closed = True
        for task in self._warming:
            task.cancel()
        await asyncio.gather(*self._warming, return_exceptions=True)
        async with self._changed:
            idle, self._idle = self._idle, []
        for held in idle:
            await held.worker.stop()
        self._held -= len(idle)
