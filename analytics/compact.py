#!/usr/bin/env python3
"""Build the derived layer: raw NDJSON in, compacted Parquet out.

    uv run analytics/compact.py --source ./data --dest ./derived

Raw is never read for anything but input and never written to. The derived tree is disposable by
design -- delete it and one run rebuilds it identically, because raw is the only input.

**Full rebuild, on purpose.** Every run reads all of raw and rewrites every partition it produces.
An incremental job would need a watermark, and a watermark needs a bound on how late an event may
arrive -- which is exactly the assumption real traffic already falsified (an event arrived 166 days
behind its ingest). At the current volume a full rebuild is seconds. When that stops being true,
the fix is a windowed rebuild over `dt`, not a watermark, and it should be measured before it is
built rather than guessed at now.

Why Parquet, and why this exists at all: raw averages ~1.9 events per object because the collector
flushes every 5 seconds against sparse traffic, and buffering longer trades the small-file problem
for an event-loss risk on a platform that scales to zero. Compaction is the right place to fix it,
because it does not touch the append-only guarantee.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

# `derived` needs `events`; nothing here needs channels or sessions.
SQL_FILES = ("events.sql", "derived.sql")


def build(source: str, dest: str) -> dict:
    root = str(source)
    if root.startswith("file://"):
        root = root[len("file://"):]
    root = root.rstrip("/")

    con = duckdb.connect()
    con.execute(f"SET VARIABLE events_glob = '{root}/events/dt=*/*.ndjson'")
    con.execute(f"SET VARIABLE bad_glob = '{root}/bad/dt=*/*.ndjson'")
    for name in SQL_FILES:
        con.execute((SQL_DIR / name).read_text(encoding="utf-8"))

    before = con.sql("SELECT count(*) FROM events").fetchone()[0]
    after = con.sql("SELECT count(*) FROM derived_events").fetchone()[0]
    untrusted = con.sql(
        "SELECT count(*) FROM derived_events WHERE NOT event_time_trusted").fetchone()[0]

    out = str(dest).rstrip("/") + "/events"
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    # OVERWRITE, not OVERWRITE_OR_IGNORE: this job is a full rebuild, so an existing partition must
    # be replaced. OVERWRITE_OR_IGNORE leaves the old files in place and the next read sees every
    # event twice -- a silent duplication introduced by the very job that exists to remove them.
    con.execute(f"""
        COPY (SELECT * FROM derived_events ORDER BY event_time)
        TO '{out}'
        (FORMAT PARQUET, PARTITION_BY (event_date), OVERWRITE, FILENAME_PATTERN 'events_{{i}}',
         COMPRESSION zstd)
    """)

    files = sorted(Path(out).rglob("*.parquet"))
    return {
        "raw_events": before,
        "derived_events": after,
        "duplicates_removed": before - after,
        "untrusted_clock": untrusted,
        "partitions": len({f.parent for f in files}),
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(ROOT / "data"), help="raw root (local path)")
    parser.add_argument("--dest", default=str(ROOT / "derived"), help="derived root (local path)")
    args = parser.parse_args()

    # Cloud URIs are not supported here yet. DuckDB would need its own extension and its own
    # credentials for the write side, which is a different problem from the collector's fsspec
    # sink and is untested -- saying so is better than half-working.
    for label, value in (("source", args.source), ("dest", args.dest)):
        if "://" in value and not value.startswith("file://"):
            print(f"error: --{label} must be a local path; cloud sinks are not supported yet.\n"
                  f"       Sync raw down first:  gcloud storage rsync -r <uri> ./data", file=sys.stderr)
            return 2

    print(f"minuano compact — {args.source} → {args.dest}")
    stats = build(args.source, args.dest)

    print(f"\n  raw events         {stats['raw_events']:>10,}")
    print(f"  derived events     {stats['derived_events']:>10,}")
    print(f"  duplicates removed {stats['duplicates_removed']:>10,}")
    print(f"  untrusted clock    {stats['untrusted_clock']:>10,}  (partitioned by ingested_at)")
    print(f"\n  partitions         {stats['partitions']:>10,}")
    print(f"  files              {stats['files']:>10,}")
    print(f"  bytes              {stats['bytes']:>10,}")
    if stats["files"]:
        print(f"  events per file    {stats['derived_events'] / stats['files']:>10,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
