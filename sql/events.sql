-- The raw event stream, read where it lies.
--
-- No load step: DuckDB reads the NDJSON the collector wrote. `hive_partitioning` turns the
-- `dt=YYYY-MM-DD` directory into a `dt` column, and `union_by_name` tolerates events that omit
-- optional objects entirely.
--
-- `dt` is the INGEST date. `event_date` is the event's own date, and the two differ whenever a
-- client's clock is skewed, a mobile SDK flushes a queue late, or a batch is replayed. Filter on
-- `dt` to prune files; filter on `event_date` to answer a question about when something happened.
-- Doing the second with the first is the silent-wrong-answer bug this layout is designed around.
--
-- This file used to say "pad +/-1 day on `dt`" as though that made a padded query correct. The
-- first day of production data falsified it: an event arrived **166 days** behind its ingest, and
-- no fixed pad catches that. A client clock is attacker-controlled input in all but name -- there
-- is no upper bound to pick.
--
-- So the honest rule, and the one that replaces it:
--
--   * padding `dt` is a PERFORMANCE optimisation with a known, measurable loss -- not a
--     correctness guarantee. On day one +/-1 day would have captured 99.96% of events.
--   * `health_clock_skew` in `sql/health.sql` measures that loss for YOUR data. Read it before
--     trusting any padded `event_date` aggregate; if its outer buckets are non-empty, the pad is
--     dropping rows and the padded number is quietly low.
--   * when completeness actually matters -- billing, reconciliation, anything published -- scan
--     without a `dt` filter, or read a layer partitioned by `event_date` instead.

-- The column list is DECLARED, not inferred, and it mirrors schema/event.v0.json.
--
-- Inference reads what happens to be in the files, so a dataset where no event carries a `page`
-- object produces no `page` column and every downstream query fails to compile -- which is
-- exactly what a server-side-only or freshly-started deployment looks like. Declaring the schema
-- also stops column types drifting between runs as the data changes.
CREATE OR REPLACE VIEW events AS
SELECT
    *,
    CAST(event_timestamp AS DATE) AS event_date,
FROM read_json(
    getvariable('events_glob'),
    format = 'newline_delimited',
    hive_partitioning = true,
    columns = {
        schema_version: 'VARCHAR',
        -- Optional and client-minted. NULL for every event collected before 2026-07-28, and for
        -- any install still serving a cached snippet -- so a dedup on it must tolerate NULL
        -- rather than treat it as a key.
        event_id: 'VARCHAR',
        event_name: 'VARCHAR',
        event_timestamp: 'TIMESTAMP',
        ingested_at: 'TIMESTAMP',
        anonymous_id: 'VARCHAR',
        session_id: 'VARCHAR',
        user_id: 'VARCHAR',
        page: 'STRUCT(url VARCHAR, path VARCHAR, title VARCHAR, referrer VARCHAR)',
        campaign: 'STRUCT("source" VARCHAR, medium VARCHAR, "campaign" VARCHAR, content VARCHAR, term VARCHAR, attribution VARCHAR)',
        device: 'STRUCT(platform VARCHAR, user_agent VARCHAR, "language" VARCHAR, screen_width BIGINT, screen_height BIGINT)',
        params: 'JSON'
    }
);
