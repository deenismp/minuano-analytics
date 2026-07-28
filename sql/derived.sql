-- The derived layer: raw, made answerable. Reads `events`; writes nothing.
--
-- Raw stays exactly as the collector wrote it -- append-only, partitioned by ingest date, never
-- reorganised. That invariant is why this is a SEPARATE layer rather than a compaction of raw in
-- place. Everything here is a reproducible projection: delete the whole derived tree and one run
-- rebuilds it byte for byte, because the only input is raw.
--
-- Three problems get fixed here, and none of them could be fixed at collect:
--
--   1. DUPLICATE DELIVERIES. `event_id` (increment 8) identifies one event delivered twice.
--   2. UNTRUSTWORTHY CLOCKS. `event_timestamp` is client-supplied on a public endpoint.
--   3. FILE COUNT. Raw is ~1.9 events per object; that is invisible to DuckDB and fatal to Athena.
--      Compaction is the writer's job, not this view's -- see analytics/compact.py.

-- How far apart `event_timestamp` and `ingested_at` may be before the client's clock is not
-- believed. One day is deliberately generous: measured on 10,572 real events, 99.4% land within
-- five minutes, and the next bucket out is already at six days.
CREATE OR REPLACE MACRO skew_limit_seconds() AS 86400;

CREATE OR REPLACE VIEW derived_events AS
SELECT
    * EXCLUDE (event_date),

    epoch(event_timestamp) - epoch(ingested_at)                    AS skew_seconds,

    -- Whether the client's own clock is believable for this row. Carried as a column rather than
    -- applied silently, so a query can exclude untrusted rows, count them, or ignore the question
    -- -- and so the fact that a decision was made is visible in the data.
    abs(epoch(event_timestamp) - epoch(ingested_at))
        <= skew_limit_seconds()                                    AS event_time_trusted,

    -- The timestamp to actually order and partition by. A clock we do not trust for attribution
    -- is not one we should trust for partitioning either, so it falls back to `ingested_at` --
    -- which is server-set and the one field a client cannot influence.
    --
    -- This is the fix for the backdating problem (error.md TRAP-20): `sessions.sql` resolves a
    -- whole session's source/medium/campaign with `arg_min(..., event_timestamp)`, so an event
    -- claiming an impossible timestamp rewrites the attribution of the session it lands in.
    -- Ordering by this column instead removes that lever. The raw `event_timestamp` is kept
    -- above, untouched -- clamping at collect would destroy the evidence and blind
    -- `health_clock_skew`.
    CASE WHEN abs(epoch(event_timestamp) - epoch(ingested_at)) <= skew_limit_seconds()
         THEN event_timestamp ELSE ingested_at END                 AS event_time,

    CAST(CASE WHEN abs(epoch(event_timestamp) - epoch(ingested_at)) <= skew_limit_seconds()
              THEN event_timestamp ELSE ingested_at END AS DATE)   AS event_date,

FROM events
-- Deduplicate on `event_id`, keeping the earliest-ingested copy.
--
-- The `IS NULL` branch is load-bearing and not a formality. `event_id` is optional: every event
-- collected before 2026-07-28 has none, and so does every install still serving a cached snippet
-- (coverage was 2.7% an hour after the GTM publish). Without that branch, `PARTITION BY event_id`
-- treats all NULLs as one group and collapses 97% of the dataset to a single row.
QUALIFY event_id IS NULL
     OR row_number() OVER (PARTITION BY event_id ORDER BY ingested_at) = 1;
