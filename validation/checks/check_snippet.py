#!/usr/bin/env python3
"""Step 3 check -- does the snippet produce the events the contract expects?

Starts a collector, runs snippet/minuano.js through the DOM shim in snippet_runtime.mjs
(four page loads, one cookie jar), then reads the collector's output back off disk.

    uv run validation/checks/check_snippet.py

What this does NOT prove is listed in validation/README.md -- chiefly that it is a shim, not
a browser.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.getenv("MINUANO_SNIPPET_PORT", "8788"))
BASE = f"http://127.0.0.1:{PORT}"
DATA_DIR = ROOT / "validation" / "output" / "snippet-data"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def main() -> int:
    if DATA_DIR.exists():
        for path in sorted(DATA_DIR.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()

    env = {**os.environ, "MINUANO_SINK_URI": f"file://{DATA_DIR}",
           "MINUANO_FLUSH_MAX_EVENTS": "10000", "MINUANO_FLUSH_MAX_SECONDS": "3600",
           "MINUANO_INSTANCE_ID": "snippetrun01"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "collector.app:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)

    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=5)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("collector did not become healthy")

        runtime = subprocess.run(
            ["node", "validation/checks/snippet_runtime.mjs", f"{BASE}/collect"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        print(runtime.stdout.strip() or runtime.stderr.strip())
        check(runtime.returncode == 0, "snippet ran without throwing", runtime.stderr.strip()[-300:])
        check("dropped param \"cart\"" in runtime.stderr or "dropped param \"cart\"" in runtime.stdout,
              "snippet warned about the contract-breaking param before sending",
              "params are flat scalars only")
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()

    events = []
    for file in sorted((DATA_DIR / "events").rglob("*.ndjson")):
        events += [json.loads(line) for line in file.read_text().splitlines() if line.strip()]
    events.sort(key=lambda e: e["event_timestamp"])
    bad = list((DATA_DIR / "bad").rglob("*.ndjson"))

    print()
    check(not bad, "nothing the snippet sent landed in bad/", f"bad files={[str(b) for b in bad]}")
    check(len(events) == 5, "five events landed: four page_views plus the queued custom event",
          f"got {[e['event_name'] for e in events]}")
    if len(events) != 5:
        return 1

    first, second, third, custom, fourth = events

    check(first["campaign"].get("source") == "newsletter" and first["campaign"].get("medium") == "email",
          "page_view carries the UTMs from the URL", f"campaign={first['campaign']}")
    check(first["campaign"].get("attribution") == "first_touch",
          "a brand-new visitor's first event is tagged first_touch")
    check(first["params"].get("gclid") == "CjwK123", "gclid captured into params",
          f"params={first['params']}")

    check(second["campaign"].get("source") == "newsletter",
          "campaign persists to a page with no UTMs on the URL", f"campaign={second['campaign']}")
    check(second["campaign"].get("attribution") == "last_touch",
          "subsequent events are tagged last_touch")

    ids = {e["anonymous_id"] for e in events}
    check(len(ids) == 1, "anonymous_id is stable across page loads and across the session boundary",
          f"ids={ids}")

    check(custom["event_name"] == "signup_completed",
          "a track() call made before the snippet loaded still landed", f"name={custom['event_name']}")
    check(custom["params"] == {"plan": "pro", "seats": 3},
          "the contract-breaking param was dropped, the valid ones kept", f"params={custom['params']}")

    same_session = {e["session_id"] for e in (first, second, third, custom)}
    check(len(same_session) == 1, "session_id is stable within the inactivity window",
          f"session_ids={same_session}")
    check(fourth["session_id"] not in same_session,
          "31 minutes of inactivity starts a new session (the 30-minute rule)",
          f"before={same_session.pop()} after={fourth['session_id']}")
    check(fourth["session_id"].isdigit() and len(fourth["session_id"]) >= 8,
          "session_id is unix seconds at session start", f"session_id={fourth['session_id']}")

    check(third["page"]["path"] == "/signup" and third["device"]["platform"] == "web",
          "page and device context populated from the payload", f"page={third['page']['path']}")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
