#!/usr/bin/env python3
"""Increment 2, steps 2 and 3 -- does the container behave, and do two instances collide?

Step 2: build, start, wait for the healthcheck to report healthy, post an event, `compose
stop` (which is a SIGTERM plus a grace period), then read the event off the *host* volume --
proving the shutdown hook ran inside the container.

Step 3: run two collectors concurrently against one directory and confirm neither overwrites
the other. Increment 1 argued this by construction; this demonstrates it.

    uv run validation/checks/check_container.py

Requires docker. Skips with a clear message if the daemon is not reachable.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_DATA = ROOT / "validation" / "output" / "container-data"
CONCURRENCY_DATA = ROOT / "validation" / "output" / "concurrency-data"
PORT = os.getenv("MINUANO_CONTAINER_PORT", "8791")

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def compose(*args: str, env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, text=True,
                          capture_output=True, env={**os.environ, **(env or {})}, **kwargs)


def event(anonymous_id: str, name: str = "page_view") -> bytes:
    return json.dumps({
        "schema_version": "0", "event_name": name,
        "event_timestamp": "2026-07-27T12:00:00Z",
        "anonymous_id": anonymous_id, "session_id": "1785500000",
    }).encode()


def post(port: str, body: bytes) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/collect", data=body, method="POST",
                                 headers={"Content-Type": "text/plain;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def lines_in(directory: Path) -> list[dict]:
    records = []
    for file in sorted(directory.rglob("*.ndjson")):
        for line in file.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                record["_file"] = file.name
                records.append(record)
    return records


def container_checks() -> None:
    env = {"MINUANO_HOST_DATA": str(HOST_DATA), "MINUANO_PORT": PORT,
           "MINUANO_FLUSH_MAX_EVENTS": "10000", "MINUANO_FLUSH_MAX_SECONDS": "3600"}

    build = compose("build", env=env)
    check(build.returncode == 0, "image builds", build.stderr.strip().splitlines()[-1] if build.stderr else "")
    if build.returncode != 0:
        print(build.stderr[-2000:])
        return

    up = compose("up", "-d", env=env)
    check(up.returncode == 0, "compose up", up.stderr.strip().splitlines()[-1] if up.stderr else "")

    try:
        # The healthcheck is the container's own opinion of readiness, not ours.
        state, deadline = "", time.time() + 90
        while time.time() < deadline:
            ps = compose("ps", "--format", "json", env=env)
            rows = [json.loads(line) for line in ps.stdout.splitlines() if line.strip()]
            state = rows[0].get("Health", "") if rows else ""
            if state == "healthy":
                break
            time.sleep(2)
        check(state == "healthy", "container reports healthy via its own HEALTHCHECK", f"health={state!r}")

        check(post(PORT, event("anon_container1")) == 200, "event posted to the container")
        check(not list(HOST_DATA.rglob("*.ndjson")), "still buffered -- nothing on the volume yet")

        stop = compose("stop", env=env)
        check(stop.returncode == 0, "compose stop (SIGTERM + grace period)")

        landed = lines_in(HOST_DATA)
        check(len(landed) == 1 and landed[0]["anonymous_id"] == "anon_container1",
              "SIGTERM inside the container drained the buffer to the host volume",
              f"landed={[r['anonymous_id'] for r in landed]}")
        check(bool(landed) and landed[0].get("ingested_at", "").startswith("20"),
              "the line the container wrote is a complete, stamped event",
              landed[0].get("ingested_at") if landed else "")

        logs = compose("logs", "--no-color", env=env).stdout
        check("buffer drained" in logs, "the drain log line is on stdout",
              next((l.strip()[-140:] for l in logs.splitlines() if "buffer drained" in l), "<none>"))
        json_lines = [l for l in logs.splitlines() if l.partition("| ")[2].startswith("{")]
        parsed = [json.loads(l.partition("| ")[2]) for l in json_lines]
        check(bool(parsed) and all("ts" in r and "level" in r and "msg" in r for r in parsed),
              "logs are one JSON object per line on stdout",
              f"{len(parsed)} structured lines, e.g. msg={parsed[0]['msg']!r}" if parsed else "none parsed")

        whoami = compose("run", "--rm", "--entrypoint", "id", "collector", "-un", env=env)
        check(whoami.stdout.strip().endswith("minuano"), "container runs as a non-root user",
              f"id -un -> {whoami.stdout.strip()!r}")
    finally:
        compose("down", "-v", env=env)


def concurrency_checks() -> None:
    """Two instances, one directory. Neither may overwrite the other."""
    if CONCURRENCY_DATA.exists():
        shutil.rmtree(CONCURRENCY_DATA)

    procs = []
    for i, port in enumerate(("8792", "8793")):
        env = {**os.environ, "MINUANO_DATA_DIR": str(CONCURRENCY_DATA),
               "MINUANO_INSTANCE_ID": f"instance{i}xxxx",
               "MINUANO_FLUSH_MAX_EVENTS": "10000", "MINUANO_FLUSH_MAX_SECONDS": "3600"}
        procs.append((port, subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "collector.app:app", "--port", port, "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)))

    try:
        for port, _ in procs:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5)
                    break
                except OSError:
                    time.sleep(0.2)

        sent = 0
        for round_ in range(3):
            for index, (port, _) in enumerate(procs):
                post(port, event(f"anon_{index}_{round_}"))
                sent += 1

        for _, proc in procs:
            proc.send_signal(signal.SIGTERM)
        for _, proc in procs:
            proc.wait(timeout=20)
    finally:
        for _, proc in procs:
            if proc.poll() is None:
                proc.kill()

    landed = lines_in(CONCURRENCY_DATA)
    instances = {record["_file"].split("-")[0] for record in landed}
    check(len(landed) == sent, "two instances, one prefix: every event survived",
          f"sent={sent} landed={len(landed)}")
    check(len(instances) == 2, "each instance wrote under its own filename",
          f"instances={sorted(instances)}")
    check(len({r["anonymous_id"] for r in landed}) == sent, "no event was overwritten by the other instance")


def main() -> int:
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        print("SKIP: docker daemon is not reachable")
        return 0

    print("=== container ===")
    container_checks()
    print("\n=== two instances, one prefix ===")
    concurrency_checks()

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
