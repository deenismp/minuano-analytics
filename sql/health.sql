-- How much of the rest of this report should you not trust?
--
-- Every view here exists because the first day of real traffic produced a number that looked
-- clean and was not. A report that shows only its headline figures is not neutral -- it actively
-- hides the fact that some of them are wrong, because wrong input averages in silently and the
-- output still renders.
--
-- These are diagnostics, not filters. Nothing here removes a row from `events` or `sessions`.
-- Deciding what to exclude is a downstream decision that needs a human; this layer's only job is
-- to make the decision *available* instead of invisible.

-- 1. Clock skew ----------------------------------------------------------------------------
--
-- `event_timestamp` is the client's clock and `ingested_at` is ours, so the gap is skew plus
-- flight time. This matters far beyond curiosity: `sql/events.sql` tells you to pad `dt` by
-- +/-1 day when asking an `event_date` question, and the first day of production data contained
-- an event **166 days** behind its ingest -- which that pad misses completely.
--
-- Read this before trusting any `event_date` aggregate. If the outer buckets are non-empty, the
-- +/-1 day pad is not enough and the honest answer is a full scan.
CREATE OR REPLACE VIEW health_clock_skew AS
SELECT
    CASE
        WHEN skew_seconds < -86400 THEN 'client >1d behind (pad misses these)'
        WHEN skew_seconds <   -300 THEN 'client 5m-1d behind'
        WHEN skew_seconds <=   300 THEN 'within 5 min'
        WHEN skew_seconds <= 86400 THEN 'client 5m-1d ahead'
        ELSE                            'client >1d ahead (pad misses these)'
    END                                          AS bucket,
    count(*)                                     AS events,
    round(min(skew_seconds) / 86400.0, 2)        AS min_days,
    round(max(skew_seconds) / 86400.0, 2)        AS max_days
FROM (SELECT epoch(event_timestamp) - epoch(ingested_at) AS skew_seconds FROM events)
GROUP BY 1;

-- 2. event_id coverage and duplicate rate --------------------------------------------------
--
-- Two questions in one view, and the first has to be answered before the second means anything.
--
-- COVERAGE is the snippet rollout gauge. `event_id` is optional, so events without one are not
-- errors -- they are events from a browser or GTM container still serving a cached snippet.
-- While coverage is climbing, the deploy is still propagating.
--
-- DUPLICATES are only measurable within the covered slice. A duplicate here is one event
-- *delivered* twice (a sendBeacon retry, a proxy replay, a re-flushed batch) -- NOT a tag firing
-- twice, which builds two events and mints two ids. Reporting a duplicate rate over the whole
-- dataset while coverage is partial would understate it in exact proportion to the rollout.
CREATE OR REPLACE VIEW health_event_id AS
SELECT
    count(*)                                                        AS events,
    count(event_id)                                                 AS with_event_id,
    round(100.0 * count(event_id) / nullif(count(*), 0), 1)         AS coverage_pct,
    count(event_id) - count(DISTINCT event_id)                      AS duplicate_deliveries,
    round(100.0 * (count(event_id) - count(DISTINCT event_id))
          / nullif(count(event_id), 0), 2)                          AS duplicate_pct_of_covered
FROM events;

-- 3. Tagging health ------------------------------------------------------------------------
--
-- The defect that cost ~180 sessions on day one: a campaign name is set but `utm_medium` is
-- empty, so paid traffic classifies as Organic Search or Organic Video.
--
-- This is NOT a bug in `sql/channels.sql`. Given the same empty medium, the reference platform
-- classifies these identically -- the rules were transcribed from its documentation precisely so
-- they would. The defect is upstream, in how the campaign was tagged, and the only thing this
-- pipeline can do about it is refuse to let the number look clean.
CREATE OR REPLACE VIEW health_tagging AS
SELECT
    channel,
    coalesce(source, '(direct)')       AS source,
    coalesce(campaign_name, '(not set)') AS campaign,
    count(*)                           AS sessions
FROM sessions
WHERE campaign_name IS NOT NULL
  AND (medium IS NULL OR medium = '')
GROUP BY 1, 2, 3;

-- 4. Entry paths that behave like a machine ------------------------------------------------
--
-- The signal is CONCENTRATION, not any one session. The first draft of this view flagged every
-- session with one page view and zero dwell, which is tautological -- a single-pageview session
-- has zero duration by construction -- and it duly flagged 1,657 of 2,542 sessions on real data.
-- A diagnostic that fires on 65% of traffic is noise, and worse than none, because it trains you
-- to skip the whole section.
--
-- What is actually anomalous is a *path* that almost never gets a second page view across many
-- sessions. Real pages have a mix; a health-check endpoint or a scripted hit does not. Day one
-- had `/render` at 100 sessions and exactly 100 page views.
--
-- Still a suspect list, not a filter. A genuinely one-page landing page looks like this too, so
-- the thresholds only decide what is worth a human glance -- nothing is excluded from `sessions`.
CREATE OR REPLACE VIEW health_robotic_paths AS
SELECT
    entry_path,
    count(*)                                                            AS sessions,
    count(*) FILTER (page_views = 1)                                    AS single_hit_sessions,
    round(100.0 * count(*) FILTER (page_views = 1) / count(*), 1)       AS single_hit_pct,
    count(DISTINCT anonymous_id)                                        AS visitors
FROM sessions
WHERE entry_path IS NOT NULL
GROUP BY 1
-- 20 sessions is enough that "everyone bounced" stops being chance; 95% leaves room for the
-- occasional real second pageview on an otherwise machine-driven path.
HAVING count(*) >= 20
   AND 100.0 * count(*) FILTER (page_views = 1) / count(*) >= 95;

-- 5. Your own tag debugging, counted as traffic ---------------------------------------------
--
-- Separated from the view above because this one is not a heuristic. A `preview`-shaped source
-- or `medium = test` is unambiguously not a visitor, and it should never be argued about.
CREATE OR REPLACE VIEW health_preview_traffic AS
SELECT
    coalesce(source, '(direct)') AS source,
    coalesce(medium, '(none)')   AS medium,
    count(*)                     AS sessions
FROM sessions
WHERE lower(coalesce(source, '')) LIKE '%preview%'
   OR lower(coalesce(medium, '')) IN ('test', 'preview')
GROUP BY 1, 2;
