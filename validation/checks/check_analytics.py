#!/usr/bin/env python3
"""Increment 3 -- do sessions and channel grouping come out right?

Posts hand-authored events through the real collector, then runs the SQL over what actually
landed on disk. Expectations come from validation/cases/analytics-fixtures.json and are
hand-written; nothing here is derived from the SQL it is checking.

    uv run validation/checks/check_analytics.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from analytics.run import connect  # noqa: E402

PORT = os.getenv("MINUANO_ANALYTICS_PORT", "8794")
DATA_DIR = ROOT / "validation" / "output" / "analytics-data"
CASES = json.loads((ROOT / "validation" / "cases" / "analytics-fixtures.json").read_text())

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def collect_fixtures() -> None:
    """Send every fixture event through the collector, then SIGTERM so the buffer drains."""
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)

    env = {**os.environ, "MINUANO_SINK_URI": f"file://{DATA_DIR}", "MINUANO_INSTANCE_ID": "analytics001",
           "MINUANO_FLUSH_MAX_EVENTS": "10000", "MINUANO_FLUSH_MAX_SECONDS": "3600"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "collector.app:app", "--port", PORT, "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=5)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("collector did not become healthy")

        request = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/collect", method="POST",
            data=json.dumps(CASES["events"]).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read())
        check(body["accepted"] == CASES["expected"]["events"] and body["rejected"] == 0,
              "every fixture event was accepted by the collector", f"response={body}")

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()


def main() -> int:
    collect_fixtures()
    expected = CASES["expected"]
    con = connect(DATA_DIR)

    # --- step 1: the query layer reads what the collector wrote --------------------------
    volume = con.sql("""
        SELECT count(*) AS events, count(DISTINCT anonymous_id) AS visitors,
               count(DISTINCT session_id) AS sessions, count(DISTINCT event_date) AS event_dates,
               count(DISTINCT dt) AS ingest_partitions
        FROM events
    """).fetchone()
    events, visitors, sessions, event_dates, partitions = volume

    check(events == expected["events"], "every event written is visible in the view",
          f"expected={expected['events']} actual={events}")
    check(visitors == expected["visitors"], "visitor count", f"actual={visitors}")
    check(event_dates == expected["event_dates"] and partitions == expected["ingest_partitions"],
          "three event dates land in one ingest partition — the tradeoff, made visible",
          f"event_dates={event_dates} ingest_partitions={partitions}")

    # The point of the previous check, stated as the bug it prevents.
    naive = con.sql("SELECT count(*) FROM events WHERE dt = '2026-07-26'").fetchone()[0]
    correct = con.sql("SELECT count(*) FROM events WHERE event_date = DATE '2026-07-26'").fetchone()[0]
    check(naive == 0 and correct == 3,
          "filtering on dt for a past event date silently returns nothing; event_date is correct",
          f"WHERE dt='2026-07-26' -> {naive} rows;  WHERE event_date='2026-07-26' -> {correct} rows")

    # --- step 2: sessions ----------------------------------------------------------------
    check(sessions == expected["sessions"], "session count", f"actual={sessions}")

    rows = {r[0]: r for r in con.sql("""
        SELECT session_id, anonymous_id, channel, events, entry_path, source, medium
        FROM sessions
    """).fetchall()}
    for case in CASES["sessions"]:
        row = rows.get(case["session_id"])
        ok = (row is not None and row[1] == case["anonymous_id"]
              and row[3] == case["events"] and row[4] == case["entry_path"])
        check(ok, f"session {case['session_id']} shape ({case['anonymous_id']})",
              f"expected events={case['events']} entry={case['entry_path']} | actual={row[3:5] if row else None}")

    # Attribution is taken at session start, not at session end. The second session of visitor A
    # begins on /checkout but must still carry the campaign the visitor originally arrived on.
    second = rows["1785112700"]
    check(second[5] == "google" and second[6] == "cpc",
          "session attribution comes from the session's first event",
          f"source={second[5]} medium={second[6]} entry={second[4]}")

    derived, client = con.sql("""
        SELECT (SELECT count(*) FROM derived_sessions), (SELECT count(*) FROM sessions)
    """).fetchone()
    check(derived == client,
          "sessions re-derived from the 30-minute gap match the client's session_id count",
          f"derived={derived} client={client}")

    # --- step 3: channel grouping ---------------------------------------------------------
    for case in CASES["sessions"]:
        row = rows.get(case["session_id"])
        actual = row[2] if row else None
        check(actual == case["channel"], f"channel: {case['why']}",
              f"expected={case['channel']} actual={actual}")

    by_channel = dict(con.sql("SELECT channel, count(*) FROM sessions GROUP BY 1").fetchall())
    check(by_channel == expected["channels"], "channel totals match the hand-authored expectation",
          f"actual={by_channel}")

    check(con.sql("SELECT count(*) FROM sessions WHERE channel = 'Unassigned'").fetchone()[0] == 0,
          "no fixture fell through to Unassigned")

    # --- the classifier itself, branch by branch ---------------------------------------
    # The fixtures above exercise 8 channels using 5 distinct sources. `channels.sql` has 20
    # branches, and its header says "ORDER IS THE ALGORITHM" -- yet a mutation that hoisted
    # Referral above the organic branches (reintroducing a defect worth 115k events on real
    # traffic) still passed every check here. A comment is not a test. See error.md, TRAP-19.
    #
    # These call the macro directly, so they need no fixture events. The order-sensitive pairs
    # are the point: each one is a case where two branches both match and only position decides.
    cases = [
        # (source, medium, campaign, expected)               # why this row exists
        ("google", "cpc", None, "Paid Search"),
        ("google", "organic", None, "Organic Search"),
        ("google", "cpm", None, "Paid Search"),              # order: paid beats Display
        ("zzz", "cpm", None, "Display"),                     # order: Display is the fallback for cpm
        ("facebook", "cpc", None, "Paid Social"),            # order: paid social beats organic
        ("facebook", "referral", None, "Organic Social"),
        ("youtube", "referral", None, "Organic Video"),      # order: video beats Referral
        ("youtube", "cpc", None, "Paid Video"),              # order: paid video beats Display
        ("news.ycombinator.com", "referral", None, "Referral"),
        ("newsletter", "email", None, "Email"),
        ("amazon", "cpc", None, "Paid Shopping"),
        ("amazon", "referral", None, "Organic Shopping"),
        ("chatgpt.com", "referral", None, "AI Assistant"),
        ("partner", "affiliate", None, "Affiliates"),
        ("spotify", "audio", None, "Audio"),
        ("carrier", "sms", None, "SMS"),
        ("app", "push", None, "Mobile Push Notifications"),
        (None, None, None, "Direct"),
        # regressions, each one a real misclassification found on 2026-07-28
        ("netflix.com", "referral", None, "Referral"),       # `x\.com` matched as a substring
        ("wix.com", "referral", None, "Referral"),
        ("x.com", "referral", None, "Organic Social"),       # ...while the real one still works
        ("www.docs.google.com", "referral", None, "Referral"),  # engine-product test was ^-anchored
        ("fb ", "cpc", None, "Paid Social"),                 # untrimmed UTMs defeated exact lists
    ]
    wrong = []
    for src, med, camp, expected in cases:
        got = con.execute("SELECT channel_group(?, ?, ?)", [src, med, camp]).fetchone()[0]
        if got != expected:
            wrong.append(f"{src}/{med} -> {got} (want {expected})")
    check(not wrong, f"channel_group classifies all {len(cases)} branch and ordering cases",
          "; ".join(wrong) if wrong else f"{len(cases)} cases, including 5 order-sensitive pairs")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
