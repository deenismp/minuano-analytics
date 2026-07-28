#!/usr/bin/env python3
"""Step 9 check -- does the derived layer remove what it should and keep what it must?

    uv run validation/checks/check_derived.py

Builds a hand-authored raw tree, runs analytics/compact.py over it, and reads the Parquet back.
Every case below is a specific way this job could destroy data rather than compact it -- which is
the risk that makes a derived layer worth testing at all. Raw is recoverable; a derived layer that
silently drops rows looks exactly like one that works.

What this does NOT prove: that Parquet written here is readable by Athena or Spark. That needs
those engines, and is listed in validation/README.md.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from analytics.compact import build  # noqa: E402

WORK = ROOT / "validation" / "output" / "derived-data"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def event(**over) -> dict:
    base = {
        "schema_version": "0",
        "event_name": "page_view",
        "event_timestamp": "2026-07-28T12:00:00Z",
        "ingested_at": "2026-07-28T12:00:01Z",
        "anonymous_id": "anon_00000001",
        "session_id": "1785500000",
    }
    base.update(over)
    return base


def write_raw(root: Path, rows: list[dict]) -> None:
    part = root / "events" / "dt=2026-07-28"
    part.mkdir(parents=True, exist_ok=True)
    # One event per file, which is what the collector actually produces at low traffic -- the
    # small-file shape this job exists to fix.
    for i, row in enumerate(rows):
        (part / f"testrun-{i:06d}.ndjson").write_text(json.dumps(row) + "\n", encoding="utf-8")


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    raw, dest = WORK / "raw", WORK / "derived"

    rows = [
        # One event delivered twice -- same id, two arrivals. Exactly one must survive, and it
        # must be the earliest-ingested copy.
        event(event_id="dup_0000000001", ingested_at="2026-07-28T12:00:01Z", anonymous_id="anon_dup_first"),
        event(event_id="dup_0000000001", ingested_at="2026-07-28T12:00:09Z", anonymous_id="anon_dup_second"),
        # Two DISTINCT events, neither carrying an id -- the pre-increment-8 shape, and still the
        # majority of live traffic while cached snippets roll over. Both must survive.
        event(anonymous_id="anon_noid_0001"),
        event(anonymous_id="anon_noid_0002"),
        # A believable clock: kept, and partitioned by its own date.
        event(event_id="trust_000000001", event_timestamp="2026-07-27T23:00:00Z",
              ingested_at="2026-07-27T23:00:02Z", anonymous_id="anon_trusted01"),
        # A clock 166 days out -- the real shape observed in production. Must be kept, flagged,
        # and partitioned by ingested_at rather than fabricating a February partition.
        event(event_id="skewed_00000001", event_timestamp="2026-02-11T19:20:04Z",
              ingested_at="2026-07-28T12:00:03Z", anonymous_id="anon_skewed001"),
    ]
    write_raw(raw, rows)

    stats = build(str(raw), str(dest))
    con = duckdb.connect()
    glob = f"{dest}/events/**/*.parquet"
    rel = con.sql(f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    rel.create_view("d")

    n = con.sql("SELECT count(*) FROM d").fetchone()[0]
    check(n == 5, "one duplicate delivery removed, everything else kept",
          f"6 raw rows in, {n} out (expected 5)")

    kept = [r[0] for r in con.sql(
        "SELECT anonymous_id FROM d WHERE event_id = 'dup_0000000001'").fetchall()]
    check(kept == ["anon_dup_first"], "the earliest-ingested copy is the one that survives",
          f"kept={kept}")

    noid = con.sql("SELECT count(*) FROM d WHERE event_id IS NULL").fetchone()[0]
    check(noid == 2, "events with NO event_id are not collapsed into one",
          f"{noid} of 2 survived — a PARTITION BY over NULL would leave 1")

    trusted = con.sql(
        "SELECT event_date FROM d WHERE event_id = 'trust_000000001'").fetchone()[0]
    check(str(trusted) == "2026-07-27", "a believable clock partitions by its own date",
          f"event_date={trusted}")

    row = con.sql("""SELECT event_time_trusted, CAST(event_date AS VARCHAR),
                            CAST(event_timestamp AS VARCHAR)
                     FROM d WHERE event_id = 'skewed_00000001'""").fetchone()
    check(row[0] is False, "a 166-day-out clock is flagged, not silently accepted",
          f"event_time_trusted={row[0]}")
    check(row[1] == "2026-07-28", "an untrusted clock partitions by ingested_at, not its own date",
          f"event_date={row[1]} (its own timestamp says 2026-02-11)")
    check(row[2].startswith("2026-02-11"), "the original event_timestamp is preserved verbatim",
          f"event_timestamp={row[2]} — clamping it would destroy the evidence of skew")

    # The failure this is really guarding: COPY ... OVERWRITE_OR_IGNORE leaves the previous run's
    # files in place, so every rebuild doubles the data -- introduced by the job whose whole
    # purpose is removing duplicates, and invisible until someone counts.
    build(str(raw), str(dest))
    build(str(raw), str(dest))
    again = con.sql(f"SELECT count(*) FROM read_parquet('{glob}', hive_partitioning=true)").fetchone()[0]
    check(again == n, "three rebuilds produce the same row count, not three times the rows",
          f"after 3 runs: {again} rows (expected {n})")

    check(stats["files"] < len(rows), "many small raw objects become fewer, larger ones",
          f"{len(rows)} raw files → {stats['files']} parquet file(s)")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
