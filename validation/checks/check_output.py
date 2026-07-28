#!/usr/bin/env python3
"""Step 2 check -- is what actually landed on disk correct?

Starts a real collector on a spare port, drives both ingest paths, sends SIGTERM, then reads
the output directory back and checks it against `validation/cases/fixtures.json`. The
expectation comes from the static fixture file, never from the collector's own counters --
the expectation must not come from the mechanism being verified.

    uv run validation/checks/check_output.py

Buffering is deliberately configured so that nothing flushes on a size or time bound: the
whole run is proved by the SIGTERM drain.
"""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from collector.validate import validate  # noqa: E402

PORT = int(os.getenv("MINUANO_TEST_PORT", "8787"))
BASE = f"http://127.0.0.1:{PORT}"
DATA_DIR = ROOT / "validation" / "output" / "data"
FIXTURES = json.loads((ROOT / "validation" / "cases" / "fixtures.json").read_text())["fixtures"]

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def request(method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def wait_for_health(proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"collector exited early with code {proc.returncode}")
        try:
            status, _, body = request("GET", "/healthz")
            if status == 200:
                print(f"collector up: {json.loads(body)}\n")
                return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("collector did not become healthy in time")


def read_stream(stream: str) -> list[dict]:
    directory = DATA_DIR / stream
    records = []
    for file in sorted(directory.rglob("*.ndjson")):
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                record["_file"] = str(file.relative_to(DATA_DIR))
                records.append(record)
    return records


def check_cors_rules() -> None:
    """CORS policy is parsed here, not in a browser -- so assert the parse.

    A subdomain wildcard cannot go in the response header (it must echo the caller's exact
    origin), so `https://*.example.com` compiles to a regex. Getting that regex slightly wrong
    either locks out the customer's own site or opens the endpoint to any origin, and neither
    shows up until it is live.
    """
    import re as _re

    from collector.app import _cors_rules

    rules = _cors_rules(("https://example.com", "https://*.example.com"))
    rx = _re.compile(rules["allow_origin_regex"])
    cases = [("https://www.example.com", True), ("https://a.b.example.com", True),
             ("https://example.com", False),          # apex is the exact entry, not the regex
             ("https://evil.com", False),
             ("https://example.com.evil.com", False),  # the one that matters
             ("http://www.example.com", False)]       # scheme is part of the origin
    wrong = [f"{o}->{bool(rx.fullmatch(o))}" for o, want in cases if bool(rx.fullmatch(o)) != want]
    check(not wrong, "CORS subdomain wildcard matches subdomains and nothing else",
          "; ".join(wrong) if wrong else f"{len(cases)} origins, incl. example.com.evil.com rejected")
    check(_cors_rules(("*",))["allow_origins"] == ["*"], "a bare * still means allow everything")


def main() -> int:
    check_cors_rules()
    if DATA_DIR.exists():
        for path in sorted(DATA_DIR.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()

    env = {
        **os.environ,
        "MINUANO_SINK_URI": f"file://{DATA_DIR}",
        "MINUANO_FLUSH_MAX_EVENTS": "10000",   # never trips
        "MINUANO_FLUSH_MAX_SECONDS": "3600",   # never trips
        "MINUANO_INSTANCE_ID": "testrun00001",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "collector.app:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    try:
        wait_for_health(proc)

        # --- drive both ingest paths -------------------------------------------------
        first, rest = FIXTURES[0], FIXTURES[1:]

        encoded = base64.urlsafe_b64encode(json.dumps(first["event"]).encode()).decode().rstrip("=")
        status, headers, body = request("GET", f"/collect?e={encoded}")
        check(status == 200 and headers.get("content-type") == "image/gif",
              "GET /collect returns a 1x1 GIF", f"status={status} content-type={headers.get('content-type')}")
        check(len(body) < 100 and body[:3] == b"GIF", "GET /collect body is GIF bytes", f"{len(body)} bytes")

        payload = json.dumps([f["event"] for f in rest]).encode()
        status, headers, body = request(
            "POST", "/collect", payload,
            {"Content-Type": "text/plain;charset=UTF-8", "Origin": "https://example.com"},
        )
        check(status == 200, "POST /collect accepts an array sent as text/plain", f"status={status}")
        check(headers.get("access-control-allow-origin") in ("*", "https://example.com"),
              "POST /collect answers cross-origin",
              f"access-control-allow-origin={headers.get('access-control-allow-origin')!r}")

        status, headers, _ = request(
            "OPTIONS", "/collect", None,
            {"Origin": "https://example.com", "Access-Control-Request-Method": "POST"},
        )
        check(status in (200, 204), "OPTIONS /collect preflight succeeds", f"status={status}")

        status, _, body = request("POST", "/collect", b"this is not json{{",
                                  {"Content-Type": "application/json"})
        check(status == 202, "unparseable body is accepted, not rejected", f"status={status}")

        # Nothing should be on disk yet: no size or time bound was configured to trip.
        check(not list(DATA_DIR.rglob("*.ndjson")), "nothing written before shutdown (events are buffered)")
        _, _, body = request("GET", "/healthz")
        check(json.loads(body)["buffered"] == len(FIXTURES) + 1,
              "healthz reports the full buffer", f"buffered={json.loads(body)['buffered']}")

        # `/health` is not decoration: Cloud Run's frontend reserves `/healthz` and answers it
        # itself with an HTML 404, so on that platform `/health` is the only externally reachable
        # health path. Deleting it as a duplicate would break the Cloud Run runbook.
        alias_status, _, alias_body = request("GET", "/health")
        check(alias_status == 200 and json.loads(alias_body)["status"] == json.loads(body)["status"],
              "/health answers identically to /healthz (Cloud Run reserves /healthz)",
              f"status={alias_status} body={alias_body[:80]}")

        # --- SIGTERM is the flush ------------------------------------------------------
        proc.send_signal(signal.SIGTERM)
        stdout, _ = proc.communicate(timeout=20)

        # uvicorn restores the default handler and re-raises the captured signal once the
        # lifespan shutdown has run (uvicorn/server.py, `capture_signals`), so dying *by*
        # SIGTERM is the correct outcome -- exit 0 would mean the signal was swallowed.
        check(proc.returncode in (0, -signal.SIGTERM), "collector exited on SIGTERM, not on a crash",
              f"exit={proc.returncode}")
        check("collector stopped, buffer drained" in stdout,
              "the shutdown hook ran and drained the buffer",
              next((line for line in stdout.splitlines() if "buffer drained" in line), "<no drain log line>"))
    finally:
        if proc.poll() is None:
            proc.kill()

    # --- read back what landed ---------------------------------------------------------
    good, bad = read_stream("events"), read_stream("bad")
    sent = len(FIXTURES) + 1  # fixtures + the unparseable body
    expected_good = sum(1 for f in FIXTURES if f["expect"] == "good")

    print()
    check(len(good) + len(bad) == sent, "rows: every event sent is on disk (good + bad)",
          f"sent={sent} good={len(good)} bad={len(bad)}")
    check(len(good) == expected_good, "good count matches the fixtures' hand-authored expectation",
          f"expected={expected_good} actual={len(good)}")

    schema_failures = [g.get("event_name") for g in good if validate({k: v for k, v in g.items() if k != "_file"})]
    check(not schema_failures, "schema: every line in events/ validates", f"failures={schema_failures}")

    # Non-lossy: every fixture is accounted for, in one stream or the other.
    landed_ids = {g.get("anonymous_id") for g in good}
    landed_ids |= {(b.get("payload") or {}).get("anonymous_id") for b in bad if isinstance(b.get("payload"), dict)}
    missing = [f["name"] for f in FIXTURES if f["event"].get("anonymous_id") not in landed_ids]
    check(not missing, "non-lossy: no fixture vanished", f"missing={missing}")

    check(all(g.get("ingested_at") for g in good), "ingested_at present on every good line")
    check(all(g.get("ingested_at") != "1999-01-01T00:00:00Z" for g in good),
          "ingested_at overwrote the client-supplied value",
          "fixture client_supplied_ingested_at_is_overwritten sent 1999-01-01T00:00:00Z")

    wrong_partition = [r["_file"] for r in good + bad if f"dt={r['ingested_at'][:10]}" not in r["_file"]]
    check(not wrong_partition, "partition: dt= matches the UTC date of ingested_at",
          f"mismatched={wrong_partition}")

    secrets = [g for g in good if any(
        str(v).startswith(("eyJ", "sk-live", "one-time")) for v in (g.get("params") or {}).values())]
    check(not secrets, "redaction: no secret-shaped value survived to disk")
    redacted = [g for g in good if (g.get("params") or {}).get("auth_token") == "<REDACTED>"]
    check(len(redacted) == 1, "redaction: the key survived, only the value was replaced",
          f"params={redacted[0].get('params') if redacted else None}")

    relayed = [g for g in good if (g.get("device") or {}).get("platform") == "server"]
    check(bool(relayed) and relayed[0]["device"]["user_agent"].startswith("RelayedAgent/"),
          "payload-supplied user_agent survived (nothing derived from the socket)",
          f"user_agent={relayed[0]['device']['user_agent'] if relayed else None!r}")

    check(all("errors" in b and b["errors"] for b in bad), "every bad row carries its validation errors")
    check(all(("payload" in b) or ("payload_raw" in b) for b in bad), "every bad row carries the original payload")

    dupes = [key for key, n in Counter(
        (g.get("anonymous_id"), g.get("event_timestamp"), g.get("event_name")) for g in good).items() if n > 1]
    if dupes:
        print(f"[WARN] duplicate (anonymous_id, event_timestamp, event_name): {dupes}")

    files = sorted(str(p.relative_to(DATA_DIR)) for p in DATA_DIR.rglob("*.ndjson"))
    print(f"\nfiles written ({len(files)}):")
    for file in files:
        print(f"  {file}")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
