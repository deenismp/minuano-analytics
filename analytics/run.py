#!/usr/bin/env python3
"""Run the query layer over collected events and print a report.

    uv run analytics/run.py [--data-dir ./data]

Nothing is loaded or copied: DuckDB reads the NDJSON the collector wrote. The views are
recreated on every run, so there is no staleness question to answer.

The same SQL is meant to run on Athena once the S3 sink is in use -- only the path in
`events_glob` changes.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

# Order matters: `sessions` calls the macro that `channels` defines.
SQL_FILES = ("events.sql", "channels.sql", "sessions.sql")


def connect(location: str | Path) -> duckdb.DuckDBPyConnection:
    """`location` is the sink: a local path, or the same URI the collector writes to.

    Cloud URIs are handed to DuckDB as-is, which needs its own extension for the scheme
    (`httpfs` for s3/gs, `azure` for az) and its own credentials. That path is untested --
    see validation/README.md.
    """
    root = str(location)
    if root.startswith("file://"):
        root = root[len("file://"):]
    root = root.rstrip("/")
    is_local = "://" not in root

    con = duckdb.connect()
    con.execute(f"SET VARIABLE events_glob = '{root}/events/dt=*/*.ndjson'")
    con.execute(f"SET VARIABLE bad_glob = '{root}/bad/dt=*/*.ndjson'")
    for name in SQL_FILES:
        con.execute((SQL_DIR / name).read_text(encoding="utf-8"))

    # DuckDB resolves a glob when the view is created, and a pattern matching no files is an
    # error -- so on a run with no rejected events, `bad_events` is simply not defined.
    if not is_local or any((Path(root) / "bad").rglob("*.ndjson")):
        try:
            con.execute((SQL_DIR / "bad_events.sql").read_text(encoding="utf-8"))
        except duckdb.Error:
            pass  # no rejected events to read
    return con


def show(con: duckdb.DuckDBPyConnection, title: str, sql: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))
    try:
        print(con.sql(sql))
    except duckdb.Error as exc:
        print(f"   (no data: {str(exc).splitlines()[0]})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.getenv("MINUANO_SINK_URI") or str(ROOT / "data"),
                        help="local path or sink URI (default: $MINUANO_SINK_URI, else ./data)")
    args = parser.parse_args()

    print(f"minuano analytics — {args.data_dir}")
    try:
        con = connect(args.data_dir)
    except duckdb.Error as exc:
        if "No files found" in str(exc):
            print("\nNo events collected yet. Send one, then run this again:\n"
                  "  curl -X POST localhost:8000/collect -d '{\"schema_version\":\"0\",\n"
                  "    \"event_name\":\"page_view\",\"event_timestamp\":\"2026-01-01T00:00:00Z\",\n"
                  "    \"anonymous_id\":\"anon_00000001\",\"session_id\":\"1785500000\"}'")
            return 0
        raise

    show(con, "volume", """
        SELECT count(*) AS events,
               count(DISTINCT anonymous_id) AS visitors,
               count(DISTINCT session_id) AS sessions,
               min(event_timestamp) AS first_event,
               max(event_timestamp) AS last_event
        FROM events
    """)

    # dt is the ingest date; event_date is when it happened. They are not the same column and
    # this report shows both on purpose.
    show(con, "ingest partition vs event date", """
        SELECT dt AS ingest_partition, event_date, count(*) AS events
        FROM events GROUP BY 1, 2 ORDER BY 1, 2
    """)

    show(con, "sessions by channel", """
        SELECT channel, count(*) AS sessions, sum(page_views) AS page_views,
               round(avg(duration_seconds), 1) AS avg_seconds
        FROM sessions GROUP BY 1 ORDER BY sessions DESC, channel
    """)

    show(con, "acquisition detail", """
        SELECT channel,
               coalesce(source, '(direct)') AS source,
               coalesce(medium, '(none)') AS medium,
               coalesce(campaign_name, '(not set)') AS campaign,
               count(*) AS sessions
        FROM sessions GROUP BY 1, 2, 3, 4 ORDER BY sessions DESC, channel
    """)

    show(con, "entry pages", """
        SELECT entry_path, count(*) AS sessions, sum(page_views) AS page_views
        FROM sessions WHERE entry_path IS NOT NULL
        GROUP BY 1 ORDER BY sessions DESC LIMIT 10
    """)

    show(con, "events by name", """
        SELECT event_name, count(*) AS events, count(DISTINCT anonymous_id) AS visitors
        FROM events GROUP BY 1 ORDER BY events DESC
    """)

    # A divergence here means the snippet's cookie logic has drifted, not that the SQL is wrong.
    show(con, "data quality — client sessions vs sessions re-derived from the 30-minute rule", """
        SELECT (SELECT count(*) FROM sessions) AS client_assigned,
               (SELECT count(*) FROM derived_sessions) AS derived_from_gap,
               (SELECT count(*) FROM sessions)
                 - (SELECT count(*) FROM derived_sessions) AS difference
    """)

    show(con, "rejected events", """
        SELECT dt, count(*) AS bad_rows, list_distinct(flatten(list(errors)))[1:3] AS sample_errors
        FROM bad_events GROUP BY 1 ORDER BY 1
    """)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
