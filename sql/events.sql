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
