-- Events that failed validation are kept, not dropped. A pipeline that cannot see its own
-- rejects is one that lies about its coverage.
--
-- Separate from events.sql because DuckDB resolves the glob when the view is CREATED, not when
-- it is queried: on a clean run there are no rejects, and a `dt=*` pattern matching no files is
-- an error. The runner executes this file only when there is something to read, which is why
-- `bad_events` may legitimately not exist.

CREATE OR REPLACE VIEW bad_events AS
SELECT *
FROM read_json(
    getvariable('bad_glob'),
    format = 'newline_delimited',
    hive_partitioning = true,
    union_by_name = true
);
