-- Sessions, derived. Collection never wrote one -- every row it stored is an event.
--
-- Attribution is taken at session START, via arg_min over the event timestamp. The published rule: "the
-- session_start event carries the information that determines the attribution of the session,
-- such as the gclid, UTM parameters, and referrer." Using the last event's campaign instead would
-- re-attribute a session to whatever the visitor happened to click most recently, which is wrong
-- in precisely the case attribution is being asked about.

CREATE OR REPLACE VIEW sessions AS
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
    arg_min(page.referrer, event_timestamp)                AS referrer,
    arg_min(campaign.source, event_timestamp)              AS source,
    arg_min(campaign.medium, event_timestamp)              AS medium,
    arg_min(campaign.campaign, event_timestamp)            AS campaign_name,
    arg_min(campaign.attribution, event_timestamp)         AS attribution,
    channel_group(
        arg_min(campaign.source, event_timestamp),
        arg_min(campaign.medium, event_timestamp),
        arg_min(campaign.campaign, event_timestamp)
    )                                                      AS channel,
    CAST(min(event_timestamp) AS DATE)                     AS session_date,
FROM events
GROUP BY anonymous_id, session_id;


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
