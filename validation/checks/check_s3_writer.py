#!/usr/bin/env python3
"""Increment 2, step 1 -- does the S3 writer put the right bytes at the right key?

Drives both writers directly with the same events and compares them. The S3 client is a
recording stub, so this needs no AWS credentials and no test-only dependency.

    uv run validation/checks/check_s3_writer.py

This proves the writer, NOT AWS. A real-bucket run is still required -- see
validation/README.md.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from collector import config  # noqa: E402
from collector.writer import make_writer  # noqa: E402

LOCAL_DIR = ROOT / "validation" / "output" / "sink-parity"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


class RecordingS3Client:
    """Stands in for `boto3.client('s3')`, recording what would have been put."""

    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, **kwargs) -> dict:
        self.puts.append(kwargs)
        return {"ETag": f'"{len(self.puts)}"'}


def events(n: int, dt: str) -> list[dict]:
    return [{"schema_version": "0", "event_name": "page_view", "event_timestamp": f"{dt}T12:00:0{i}Z",
             "ingested_at": f"{dt}T12:00:0{i}.000Z", "anonymous_id": f"anon_000000{i}",
             "session_id": "1785500000"} for i in range(n)]


def with_env(**overrides):
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update({k: v for k, v in overrides.items() if v is not None})
    for k, v in overrides.items():
        if v is None:
            os.environ.pop(k, None)
    return saved


async def run() -> None:
    if LOCAL_DIR.exists():
        shutil.rmtree(LOCAL_DIR)

    dt, other_dt = "2026-07-27", "2026-07-28"
    batch, second_day = events(3, dt), events(2, other_dt)

    # --- S3 sink ---------------------------------------------------------------------
    with_env(MINUANO_SINK="s3", MINUANO_S3_BUCKET="minuano-test", MINUANO_S3_PREFIX="raw",
             MINUANO_INSTANCE_ID="s3instance1", MINUANO_FLUSH_MAX_EVENTS="10000")
    stub = RecordingS3Client()
    s3 = make_writer(config.load(), client=stub)
    for event in batch:
        await s3.append("events", dt, event)
    await s3.append("bad", dt, {"errors": ["nope"], "payload": {}})
    for event in second_day:
        await s3.append("events", other_dt, event)
    await s3.flush()
    for event in events(1, dt):
        await s3.append("events", dt, event)
    await s3.flush()

    keys = [put["Key"] for put in stub.puts]
    check(len(stub.puts) == 4, "one put_object per (stream, partition) per flush",
          f"puts={len(stub.puts)} keys={keys}")
    check(all(k.startswith("raw/") for k in keys), "prefix applied to every key", f"keys={keys}")
    check(any(k.startswith("raw/events/dt=2026-07-27/") for k in keys) and
          any(k.startswith("raw/bad/dt=2026-07-27/") for k in keys) and
          any(k.startswith("raw/events/dt=2026-07-28/") for k in keys),
          "stream and partition are both in the key path")
    check(all("s3instance1-" in k and k.endswith(".ndjson") for k in keys),
          "instance id and extension in every filename", f"keys={keys}")
    check(len(set(keys)) == len(keys), "no key reused across flushes", f"keys={keys}")
    check(all(put["ContentType"] == "application/x-ndjson" for put in stub.puts),
          "content type is application/x-ndjson")

    s3_body = next(put["Body"] for put in stub.puts if put["Key"].startswith("raw/events/dt=2026-07-27/"))

    # --- local sink, same events -----------------------------------------------------
    with_env(MINUANO_SINK="local", MINUANO_DATA_DIR=str(LOCAL_DIR), MINUANO_S3_BUCKET=None,
             MINUANO_INSTANCE_ID="s3instance1", MINUANO_FLUSH_MAX_EVENTS="10000")
    local = make_writer(config.load())
    for event in batch:
        await local.append("events", dt, event)
    await local.flush()

    local_file = next((LOCAL_DIR / "events" / f"dt={dt}").glob("*.ndjson"))
    local_body = local_file.read_bytes()
    check(local_body == s3_body, "sink parity: identical bytes from both writers",
          f"local={len(local_body)}B s3={len(s3_body)}B")
    check(local_file.name == "s3instance1-000000.ndjson",
          "local filename matches the S3 key's final segment", f"name={local_file.name}")
    check(not list(LOCAL_DIR.rglob("*.tmp")), "no .tmp file left behind after the atomic rename")

    # --- boot-time configuration -----------------------------------------------------
    with_env(MINUANO_SINK="s3", MINUANO_S3_BUCKET=None)
    try:
        config.load()
        check(False, "MINUANO_SINK=s3 without a bucket fails at boot", "no error raised")
    except ValueError as exc:
        check("MINUANO_S3_BUCKET" in str(exc), "MINUANO_SINK=s3 without a bucket fails at boot", str(exc))

    with_env(MINUANO_SINK="gcs", MINUANO_S3_BUCKET=None)
    try:
        config.load()
        check(False, "an unknown sink fails at boot", "no error raised")
    except ValueError as exc:
        check("must be" in str(exc), "an unknown sink fails at boot", str(exc))


def main() -> int:
    asyncio.run(run())
    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
