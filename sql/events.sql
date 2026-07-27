-- The raw event stream, read where it lies.
--
-- No load step: DuckDB reads the NDJSON the collector wrote. `hive_partitioning` turns the
-- `dt=YYYY-MM-DD` directory into a `dt` column, and `union_by_name` tolerates events that omit
-- optional objects entirely.
--
-- `dt` is the INGEST date. `event_date` is the event's own date, and the two differ whenever a
-- client's clock is skewed, a mobile SDK flushes a queue late, or a batch is replayed. Filter on
-- `dt` to prune files; filter on `event_date` to answer a question about when something happened.
-- Doing the second with the first is the silent-wrong-answer bug this layout is designed around
-- (Boundary-File Padding: pad +/-1 day on `dt` when the question is about `event_date`).

CREATE OR REPLACE VIEW events AS
SELECT
    * EXCLUDE (event_timestamp, ingested_at),
    CAST(event_timestamp AS TIMESTAMP)              AS event_timestamp,
    CAST(ingested_at AS TIMESTAMP)                  AS ingested_at,
    CAST(CAST(event_timestamp AS TIMESTAMP) AS DATE) AS event_date,
FROM read_json(
    getvariable('events_glob'),
    format = 'newline_delimited',
    hive_partitioning = true,
    union_by_name = true
);
