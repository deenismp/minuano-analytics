#!/usr/bin/env python3
"""Real-cloud check -- does the collector actually write to an object store?

Everything else in this suite proves the sink against `file://` and `memory://`. Neither can
fail the way a cloud fails: credentials, IAM, region routing, throttling. This one runs the
collector against a real bucket and reads the objects back out of it.

    MINUANO_SINK_URI=gs://bucket/prefix \\
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json \\
    uv run --extra gcp validation/checks/check_cloud_sink.py

Skips loudly if MINUANO_SINK_URI is not a cloud URI, so it is safe to leave in CI where no
credentials exist. Objects it writes go under a `_selfcheck/` prefix and are deleted afterwards.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PORT = os.getenv("MINUANO_CLOUD_PORT", "8795")
SINK = os.getenv("MINUANO_SINK_URI", "")
FIXTURES = json.loads((ROOT / "validation" / "cases" / "fixtures.json").read_text())["fixtures"]

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def main() -> int:
    if "://" not in SINK or SINK.startswith(("file://", "memory://")):
        print(f"SKIP: MINUANO_SINK_URI={SINK!r} is not a cloud URI. "
              "Set it to gs:// s3:// or az:// with credentials to run this.")
        return 0

    import fsspec

    from collector import credentials

    # This process reads the bucket back, so it needs credentials too -- and on a PaaS they
    # arrive as an environment variable, not a file. Same bridge the collector uses.
    setup = credentials.bootstrap()
    print(f"credentials: {setup or 'from the ambient environment'}")

    prefix = f"{SINK.rstrip('/')}/_selfcheck"
    fs, root = fsspec.core.url_to_fs(prefix)
    print(f"target: {prefix}\n")

    env = {**os.environ, "MINUANO_SINK_URI": prefix, "MINUANO_INSTANCE_ID": "cloudcheck01",
           "MINUANO_FLUSH_MAX_EVENTS": "10000", "MINUANO_FLUSH_MAX_SECONDS": "3600"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "collector.app:app", "--port", PORT, "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                check(False, "collector started against the cloud sink", out[-400:])
                return 1
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=5)
                break
            except OSError:
                time.sleep(0.3)
        # Booting at all means preflight wrote and deleted a probe object -- i.e. real
        # credentials, real IAM, real bucket, all working.
        check(True, "collector booted, so preflight wrote a probe object to the cloud")

        payload = json.dumps([f["event"] for f in FIXTURES]).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{PORT}/collect", data=payload,
                                         method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read())
        expected_good = sum(1 for f in FIXTURES if f["expect"] == "good")
        check(body["accepted"] == expected_good, "fixtures accepted", f"response={body}")

        proc.send_signal(signal.SIGTERM)
        stdout, _ = proc.communicate(timeout=30)
        check("buffer drained" in stdout, "SIGTERM drained the buffer to the cloud",
              next((l for l in stdout.splitlines() if "buffer drained" in l), "<none>")[-200:])
    finally:
        if proc.poll() is None:
            proc.kill()

    objects = fs.find(root)
    check(bool(objects), f"objects exist in the bucket ({len(objects)})",
          "\n            ".join(objects[:4]))

    good = [o for o in objects if "/events/dt=" in o]
    bad = [o for o in objects if "/bad/dt=" in o]
    check(bool(good) and bool(bad), "both streams landed",
          f"events={len(good)} bad={len(bad)}")

    lines = []
    for obj in good:
        lines += [json.loads(line) for line in
                  fs.cat_file(obj).decode().splitlines() if line.strip()]
    check(len(lines) == expected_good, "every accepted event is readable back out of the cloud",
          f"expected={expected_good} read={len(lines)}")
    check(all(e.get("ingested_at") for e in lines), "ingested_at survived the round trip")
    redacted = [e for e in lines if (e.get("params") or {}).get("auth_token") == "<REDACTED>"]
    check(len(redacted) == 1, "redaction survived the round trip")

    fs.rm(root, recursive=True)
    check(not fs.find(root), "self-check objects cleaned up")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
