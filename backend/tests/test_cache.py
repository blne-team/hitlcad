"""The cache layer: entries kept by the content of what they depend on, the least recently used let go first."""

from __future__ import annotations

import time
from pathlib import Path

from hitlcad.kernel.cache import CacheDb
from hitlcad.kernel.lru import MemoryLru


def test_a_value_is_kept_by_what_it_depends_on_and_the_code_that_computes_it(tmp_path: Path) -> None:
    db = CacheDb(tmp_path, 1 << 20)
    results, later = db.cache("results", "code 1"), db.cache("results", "code 2")
    assert results.key("part", 1) != results.key("part", 2) != later.key("part", 1)
    assert results.key("part", 1) != db.cache("other", "code 1").key("part", 1)
    computed: list[int] = []
    for _ in range(2):
        assert results.get_or(results.key("part", 1), lambda: computed.append(1) or {"area": 3.0}) == {"area": 3.0}
    assert computed == [1]  # the second read is the first's
    again = CacheDb(tmp_path, 1 << 20).cache("results", "code 1")  # a server started again: from the disk
    assert again.get(again.key("part", 1)) == {"area": 3.0} and again.get(again.key("part", 2)) is None


def test_entries_are_let_go_least_recently_used_first_once_over_budget(tmp_path: Path) -> None:
    db = CacheDb(tmp_path, 2500)
    parts = db.cache("parts")
    kept = []
    for n in range(3):
        staging = parts.staging(parts.key(n))
        (staging / "part.stl").write_bytes(b"x" * 1000)
        kept.append(parts.commit(parts.key(n), staging, {"n": n}))
        time.sleep(0.01)
    # three 1000-byte entries in 2500 bytes: the oldest went, its files with it
    assert parts.files(parts.key(0)) is None and not kept[0].exists()
    assert parts.files(parts.key(1)) == (kept[1], {"n": 1})
    time.sleep(0.01)
    staging = parts.staging(parts.key(3))  # 1 was just read: 2 is now the least recently used
    (staging / "part.stl").write_bytes(b"x" * 1000)
    parts.commit(parts.key(3), staging)
    assert parts.files(parts.key(1)) is not None and parts.files(parts.key(2)) is None


def test_a_directory_being_read_is_never_replaced(tmp_path: Path) -> None:
    scenes = CacheDb(tmp_path, 1 << 20).cache("scenes")
    key = scenes.key("model.step")
    first, second = scenes.staging(key), scenes.staging(key)  # two requests for the same scene at once
    (first / "scene.json").write_text("first", encoding="utf-8")
    (second / "scene.json").write_text("second", encoding="utf-8")
    kept = scenes.commit(key, first)
    assert scenes.commit(key, second) == kept and (kept / "scene.json").read_text(encoding="utf-8") == "first"
    assert not second.exists()


def test_values_in_memory_are_let_go_by_their_measure() -> None:
    kept: MemoryLru[str, bytes] = MemoryLru(250, size=len)
    for key in "abc":
        kept.put(key, b"x" * 100)
    assert kept.get("a") is None and kept.get("b") is not None and kept.held == 200
    kept.put("d", b"x" * 300)  # larger than all it may hold: not kept, nothing let go for it
    assert kept.get("d") is None and len(kept) == 2
