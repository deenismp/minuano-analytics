#!/usr/bin/env python3
"""Step 1 check -- does the committed schema accept and reject what it should?

Runs every fixture in validation/cases/fixtures.json against schema/event.v0.json and
compares the outcome to the fixture's hand-authored `expect`. This proves the contract,
not the collector; the collector's output is checked by check_output.py.

    uv run validation/checks/check_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from collector.validate import redact, redact_text, validate  # noqa: E402

FIXTURES = ROOT / "validation" / "cases" / "fixtures.json"


def main() -> int:
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))["fixtures"]
    failures = []

    print(f"schema:   {(ROOT / 'schema' / 'event.v0.json').relative_to(ROOT)}")
    print(f"fixtures: {len(fixtures)}\n")

    for fixture in fixtures:
        name, expect = fixture["name"], fixture["expect"]
        errors = validate(fixture["event"])
        actual = "bad" if errors else "good"
        ok = actual == expect

        print(f"[{'PASS' if ok else 'FAIL'}] {name:<44} expect={expect:<5} actual={actual}")
        if errors:
            print(f"         └─ {errors[0]}")
        if not ok:
            failures.append(name)

    # Redaction is a separate promise from validation: secret-shaped values must be replaced,
    # and the key must survive the replacement.
    print()
    for fixture in fixtures:
        expected_keys = fixture.get("assert", {}).get("redacted_params")
        if not expected_keys:
            continue
        params = redact(json.loads(json.dumps(fixture["event"])))["params"]
        for key in expected_keys:
            ok = key in params and params[key] == "<REDACTED>"
            print(f"[{'PASS' if ok else 'FAIL'}] redaction {fixture['name']}/{key}")
            if not ok:
                failures.append(f"{fixture['name']}/{key}")
        kept = [k for k in params if k not in expected_keys]
        print(f"         └─ non-secret params kept: {kept}")

    # --- redaction bypasses, all four found live on 2026-07-28 -------------------------
    # Each of these put a secret into the sink in plaintext. The fixtures above did not catch
    # them because every fixture is a well-formed dict with a flat `params` -- the exact shape
    # the old implementation handled.
    bypasses = [
        ("nested params object",
         lambda: redact({"params": {"auth": {"token": "S"}}})["params"]["auth"]["token"]),
        ("hyphenated key name (x-api-key)",
         lambda: redact({"params": {"x-api-key": "S"}})["params"]["x-api-key"]),
        ("secret inside a list value",
         lambda: redact({"params": {"items": [{"apikey": "S"}]}})["params"]["items"][0]["apikey"]),
        ("payload that is a list, not a dict",
         lambda: redact([{"params": {"apikey": "S"}}])[0]["params"]["apikey"]),
    ]
    for name, probe in bypasses:
        try:
            ok = probe() == "<REDACTED>"
        except Exception as exc:  # a shape change should fail loudly, not silently pass
            ok, name = False, f"{name} (raised {type(exc).__name__})"
        print(f"[{'PASS' if ok else 'FAIL'}] redaction bypass closed: {name}")
        if not ok:
            failures.append(f"bypass/{name}")

    raw = redact_text('{"params":{"apikey":"S","plan":"keep me",')
    ok = "S" not in raw.replace("<REDACTED>", "") and "keep me" in raw
    print(f"[{'PASS' if ok else 'FAIL'}] redaction bypass closed: unparseable body (payload_raw)")
    print(f"         └─ {raw}")
    if not ok:
        failures.append("bypass/payload_raw")

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'ALL CHECKS PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
