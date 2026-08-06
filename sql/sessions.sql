-- Sessions, derived. Collection never wrote one -- every row it stored is an event.
--
-- Attribution is taken at session START, via arg_min over the event timestamp. The published rule: "the
-- session_start event carries the information that determines the attribution of the session,
-- such as the gclid, UTM parameters, and referrer." Using the last event's campaign instead would
-- re-attribute a session to whatever the visitor happened to click most recently, which is wrong
-- in precisely the case attribution is being asked about.
--
-- arg_min SKIPS NULLs: each column independently resolves to the earliest NON-NULL value in the
-- session, not the value on the literal first event. That is deliberate and long-standing for the
-- UTM columns (a campaign arriving on the second pageview still attributes the session), and the
-- increment-11 columns (`click_id`, `referrer`) inherit the same semantics on purpose -- mixing
-- strict-first-event and first-non-null in one view would make the precedence rules depend on
-- which column you happened to read.
--
-- Source, medium and campaign pass through `infer_traffic_source` (sql/channels.sql): a session
-- with no UTMs and no click id falls back to its session-start referrer -- engine hosts become
-- `<engine>/organic`, other external hosts `<host>/referral`, and hosts suffix-matching the
-- deployment's own `MINUANO_INTERNAL_DOMAINS` stay direct rather than becoming self-referrals.
-- `attribution_from` records which rule fired ('click_id' | 'utm' | 'referrer' | 'none'), so an
-- inferred source is never mistaken for a tagged one.

CREATE OR REPLACE VIEW sessions AS
WITH at_start AS (
    SELECT
        anonymous_id,
        session_id,
        min(event_timestamp)                                   AS session_start,
        max(event_timestamp)                                   AS session_end,
        date_diff('second', min(event_timestamp), max(event_timestamp)) AS duration_seconds,
        count(*)                                               AS events,
        count(*) FILTER (WHERE event_name = 'page_view')       AS page_views,
        arg_min(page.path, event_timestamp)                    AS entry_path,
        arg_max(page.path, event_timestamp)                    AS exit_path,
        -- The property may span subdomains; entry_path alone merges `/` across all of them.
        referrer_host(arg_min(page.url, event_timestamp))      AS entry_hostname,
        arg_min(page.referrer, event_timestamp)                AS referrer,
        arg_min(campaign.source, event_timestamp)              AS utm_source,
        arg_min(campaign.medium, event_timestamp)              AS utm_medium,
        arg_min(campaign.campaign, event_timestamp)            AS utm_campaign,
        arg_min(campaign.attribution, event_timestamp)         AS attribution,
        arg_min(params->>'gclid', event_timestamp)             AS click_id,
        CAST(min(event_timestamp) AS DATE)                     AS session_date,
    FROM events
    GROUP BY anonymous_id, session_id
),
inferred AS (
    SELECT
        *,
        infer_traffic_source(utm_source, utm_medium, utm_campaign, click_id, referrer,
                             getvariable('internal_domains')) AS i
    FROM at_start
)
SELECT
    anonymous_id,
    session_id,
    session_start,
    session_end,
    duration_seconds,
    events,
    page_views,
    entry_path,
    exit_path,
    entry_hostname,
    referrer,
    i['source']                                            AS source,
    i['medium']                                            AS medium,
    i['campaign']                                          AS campaign_name,
    i['attribution_from']                                  AS attribution_from,
    attribution,
    channel_group(i['source'], i['medium'], i['campaign']) AS channel,
    session_date,
FROM inferred;


-- The same sessions, worked out again from scratch -- 30 minutes of inactivity per visitor,
-- the published rule -- without looking at the client's session_id.
--
-- This is a data-quality test, not a replacement. The client knows things the warehouse does not
-- (a tab left open, a cookie cleared), so its session_id stays authoritative. But if these two
-- counts diverge, the snippet's cookie logic has broken -- and without a browser in the loop,
-- this is the only end-to-end check on it that exists.
CREATE OR REPLACE VIEW derived_sessions AS
WITH gaps AS (
    SELECT
        anonymous_id,
        session_id,
        event_timestamp,
        coalesce(
            date_diff('minute',
                lag(event_timestamp) OVER (PARTITION BY anonymous_id ORDER BY event_timestamp),
                event_timestamp),
            9999
        ) AS minutes_since_previous
    FROM events
),
numbered AS (
    SELECT
        *,
        sum(CASE WHEN minutes_since_previous >= 30 THEN 1 ELSE 0 END)
            OVER (PARTITION BY anonymous_id ORDER BY event_timestamp
                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS derived_session_number
    FROM gaps
)
SELECT
    anonymous_id,
    derived_session_number,
    min(session_id)         AS client_session_id,
    count(DISTINCT session_id) AS distinct_client_session_ids,
    min(event_timestamp)    AS session_start,
    count(*)                AS events,
FROM numbered
GROUP BY anonymous_id, derived_session_number;
