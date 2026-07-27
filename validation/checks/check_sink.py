#!/usr/bin/env python3
"""Increment 4 -- one writer, any backend. Does the sink behave the same everywhere?

`memory://` is an fsspec filesystem built in to the base install, and it is *not* the local
branch, so it exercises the object-store code path -- no temp file, no rename -- with no cloud
SDK and no credentials. Comparing it byte-for-byte against `file://` is the closest thing to a
cloud test that can run offline.

    uv run validation/checks/check_sink.py

What this does NOT prove is that any real cloud works: see validation/README.md.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import fsspec  # noqa: E402

from collector import config  # noqa: E402
from collector.writer import make_writer  # noqa: E402

LOCAL_DIR = ROOT / "validation" / "output" / "sink-parity"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def events(n: int, dt: str) -> list[dict]:
    return [{"schema_version": "0", "event_name": "page_view", "event_timestamp": f"{dt}T12:00:0{i}Z",
             "ingested_at": f"{dt}T12:00:0{i}.000Z", "anonymous_id": f"anon_000000{i}",
             "session_id": "1785500000"} for i in range(n)]


def with_env(**overrides) -> None:
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


async def write_all(uri: str):
    """Same events, same order, same instance id -- whatever the backend."""
    with_env(MINUANO_SINK_URI=uri, MINUANO_INSTANCE_ID="sink00000001", MINUANO_FLUSH_MAX_EVENTS="10000")
    writer = make_writer(config.load())
    dt, other_dt = "2026-07-27", "2026-07-28"
    for event in events(3, dt):
        await writer.append("events", dt, event)
    await writer.append("bad", dt, {"errors": ["nope"], "payload": {}})
    for event in events(2, other_dt):
        await writer.append("events", other_dt, event)
    first = await writer.flush()
    for event in events(1, dt):
        await writer.append("events", dt, event)
    second = await writer.flush()
    return writer, first + second


async def run() -> None:
    if LOCAL_DIR.exists():
        shutil.rmtree(LOCAL_DIR)

    local_writer, local_paths = await write_all(f"file://{LOCAL_DIR}")
    memory_writer, memory_paths = await write_all("memory://minuano-sink-test")

    def keys(paths: list[str], root: str) -> list[str]:
        return sorted(p.split(root.rstrip("/") + "/", 1)[-1] for p in paths)

    local_keys = keys(local_paths, str(LOCAL_DIR))
    memory_keys = keys(memory_paths, "/minuano-sink-test")

    check(local_keys == memory_keys, "key layout is identical on both backends",
          f"local={local_keys}\n            memory={memory_keys}")
    check(len(local_keys) == 4, "one object per (stream, partition) per flush", f"objects={len(local_keys)}")
    check(all(k.startswith(("events/dt=", "bad/dt=")) for k in local_keys),
          "stream and partition are both in the key path", f"keys={local_keys}")
    check(all("sink00000001-" in k and k.endswith(".ndjson") for k in local_keys),
          "instance id and extension in every object name")
    check(len(set(local_keys)) == len(local_keys), "no key reused across flushes")

    memory_fs = fsspec.filesystem("memory")
    parity = []
    for key in local_keys:
        local_bytes = (LOCAL_DIR / key).read_bytes()
        memory_bytes = memory_fs.cat_file(f"/minuano-sink-test/{key}")
        parity.append(local_bytes == memory_bytes)
    check(all(parity), "sink parity: byte-identical output from both backends",
          f"{sum(parity)}/{len(parity)} objects match")

    check(not list(LOCAL_DIR.rglob("*.tmp")), "local writes leave no .tmp behind after the rename")
    check(local_writer.location.startswith("file://") and memory_writer.location.startswith("memory://"),
          "each writer reports its own backend",
          f"{local_writer.location} | {memory_writer.location}")

    # --- boot guards ------------------------------------------------------------------
    with_env(MINUANO_SINK_URI="gs://bucket/raw")
    try:
        config.load()
        check(False, "a URI whose backend is not installed fails at boot", "no error raised")
    except ValueError as exc:
        check("minuano-collector[gcp]" in str(exc),
              "a URI whose backend is not installed fails at boot, naming the extra", str(exc))

    with_env(MINUANO_SINK_URI="nonsense://bucket/raw")
    try:
        config.load()
        check(False, "an unknown scheme fails at boot", "no error raised")
    except ValueError as exc:
        check("unknown scheme" in str(exc) or "not installed" in str(exc),
              "an unknown scheme fails at boot", str(exc))

    with_env(MINUANO_SINK_URI=None, MINUANO_INSTANCE_ID=None, MINUANO_FLUSH_MAX_EVENTS=None)
    check(config.load().sink_uri == "file://./data", "the default sink is local ./data",
          f"default={config.load().sink_uri}")


def main() -> int:
    asyncio.run(run())
    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
