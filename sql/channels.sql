-- The standard default channel group, as an ordered CASE.
--
-- Rules transcribed from the reference platform's published "default channel group" documentation,
-- then corrected against 7.8M real events from TWO independent production properties, by diffing
-- this macro against that platform's own resolved default channel group over the same
-- inputs. Agreement went 92.9% -> 99.6% on the first property and 97.6% -> 99.7% on the second,
-- which is what says the corrections generalise rather than overfitting one traffic mix.
--
-- Two divergences from the reference are DELIBERATE. `search.brave.com` we call Organic Search
-- where it says Referral; LLM referrers we call AI Assistant where it is inconsistent. In both
-- cases its managed list looks behind the traffic, and copying a stale list is worse than being
-- explicit.
--
-- ORDER IS THE ALGORITHM. `cpm` matches both the paid-medium regex and the Display rule; it is
-- Display only because the paid branches are tested first. `youtube`/`referral` is Organic Video
-- only because video is tested before Referral. Reordering this CASE changes the numbers without
-- changing any rule.
--
-- Source matching is deliberately in two parts. Domains match as SUBSTRINGS, because real traffic
-- arrives as `br.search.yahoo.com` and `ntp.msn.com`, and an anchored `^yahoo` silently sends both
-- to Referral. Short names (`fb`, `ig`, `x`) match EXACTLY, because a substring `ig` would match
-- almost anything.
--
-- What cannot be reproduced from UTMs, confirmed against real data:
--   * paid search traffic the reference calls **Display** -- it knows the ad-network type; we see
--     only source and medium, and correctly call it Paid Search.
--   * campaigns it calls **Cross-network** without the string appearing in the campaign name --
--     again ad-platform campaign-type metadata.
-- Both are structural, not seed-list gaps. Do not "fix" them by special-casing google/cpc.
--
-- The `(direct)` / `(none)` sentinels are applied HERE, at enrichment. Raw keeps its NULLs.

-- A source is only a search engine if it is not one of the engine operator's OTHER products.
-- `docs.google.com`, `mail.google.com`, `accounts.google.com` and `gemini.google.com` all contain
-- "google" and none of them are search; the reference calls every one of them Referral. This cost 12k events
-- across two properties before it was measured. Kept as a separate macro so both the Paid Search
-- and Organic Search branches use the identical test.
CREATE OR REPLACE MACRO is_engine_product(src) AS (
    regexp_matches(trim(lower(coalesce(src, ''))),
        '(^|\.|//)(accounts|mail|docs|keep|drive|calendar|gemini|notebooklm|sites|groups|translate|photos|meet|chat|classroom|myaccount|support|play|store|business|partner|script|news|maps|books|scholar|analytics|cloud|developers|firebase|console|admin|forms|contacts|earth|domains|adsense)\.')
    OR regexp_matches(trim(lower(coalesce(src, ''))), '(googleusercontent|googleadservices|googletagmanager)')
);

-- ---- shared source lists -------------------------------------------------------------------
--
-- ONE copy of each list, used by the paid branch, the organic branch, and referrer inference.
-- Before increment 11 these regexes were inlined twice per list inside the CASE; a correction
-- applied to one copy and not the other is exactly the drift the extraction exists to prevent.

CREATE OR REPLACE MACRO is_paid_medium(med) AS (
    regexp_matches(trim(lower(coalesce(med, ''))), '^(.*cp.*|ppc|retargeting|paid.*)$')
);

CREATE OR REPLACE MACRO is_shopping_source(src) AS (
    regexp_matches(trim(lower(coalesce(src, ''))),
        '(amazon|ebay|shopify|etsy|walmart|mercadolivre|mercadolibre|shopee|alibaba|aliexpress)')
);

CREATE OR REPLACE MACRO is_search_source(src) AS (
    regexp_matches(trim(lower(coalesce(src, ''))),
        '(google|bing|yahoo|duckduckgo|baidu|yandex|ecosia|msn|ask\.com|aol|brave|qwant|startpage|naver|seznam)')
    AND NOT is_engine_product(src)
);

-- Short names (`fb`, `ig`, `x`) match EXACTLY, never as substrings -- `x\.com` in the substring
-- half classified `netflix.com` as Organic Social (error.md BUG-3).
CREATE OR REPLACE MACRO is_social_source(src) AS (
    regexp_matches(trim(lower(coalesce(src, ''))),
        '(facebook|instagram|linkedin|twitter|tiktok|pinterest|reddit|threads|snapchat|whatsapp|telegram|kwai|tumblr|quora)')
    OR trim(lower(coalesce(src, ''))) IN ('fb', 'ig', 'meta', 'x', 'x.com', 'li', 'tt', 'wpp')
);

CREATE OR REPLACE MACRO is_video_source(src) AS (
    regexp_matches(trim(lower(coalesce(src, ''))), '(youtube|vimeo|twitch|dailymotion)')
    OR trim(lower(coalesce(src, ''))) IN ('yt')
);

CREATE OR REPLACE MACRO channel_group(src, med, camp) AS (
    CASE
        -- Cross-network: campaign name signals it, regardless of source or medium.
        WHEN trim(lower(coalesce(camp, ''))) LIKE '%cross-network%'
            THEN 'Cross-network'

        -- ---- paid branches: a paid medium plus a source we can classify -------------------
        WHEN is_paid_medium(med)
             AND (is_shopping_source(src) OR trim(lower(coalesce(med, ''))) LIKE '%shopping%')
            THEN 'Paid Shopping'

        WHEN is_paid_medium(med) AND is_search_source(src)
            THEN 'Paid Search'

        WHEN is_paid_medium(med) AND is_social_source(src)
            THEN 'Paid Social'

        WHEN is_paid_medium(med) AND is_video_source(src)
            THEN 'Paid Video'

        -- Display: medium says so. After the paid branches, so `cpm` from a known social source
        -- is Paid Social and `cpm` from anywhere else is Display.
        WHEN trim(lower(coalesce(med, ''))) IN ('display', 'banner', 'expandable', 'interstitial', 'cpm')
            THEN 'Display'

        -- Paid Other: a paid medium we could not attribute to a known platform. Better than
        -- Unassigned -- it at least keeps paid traffic out of the organic numbers.
        WHEN is_paid_medium(med)
            THEN 'Paid Other'

        -- ---- organic branches ---------------------------------------------------------------
        WHEN is_shopping_source(src) OR trim(lower(coalesce(med, ''))) LIKE '%shopping%'
            THEN 'Organic Shopping'

        WHEN is_social_source(src)
             OR trim(lower(coalesce(med, ''))) IN
                 ('social', 'social-network', 'social-media', 'sm', 'social network', 'social media')
            THEN 'Organic Social'

        WHEN is_video_source(src)
             OR regexp_matches(trim(lower(coalesce(med, ''))), '^(video|.*video.*)$')
            THEN 'Organic Video'

        WHEN is_search_source(src)
             OR trim(lower(coalesce(med, ''))) = 'organic'
            THEN 'Organic Search'

        -- AI Assistant: the reference added this channel as LLM referrals became material. In the sample
        -- it was 5,598 events that we were sending to Unassigned.
        WHEN regexp_matches(trim(lower(coalesce(src, ''))),
                 '(chatgpt|openai|perplexity|copilot|gemini|claude\.ai|anthropic)')
             OR regexp_matches(trim(lower(coalesce(med, ''))), '(ai.?assistant|ai.?chat)')
            THEN 'AI Assistant'

        -- ---- everything else ------------------------------------------------------------------
        WHEN regexp_matches(trim(lower(coalesce(src, ''))), '^(email|e-mail|e_mail|e mail)$')
             OR regexp_matches(trim(lower(coalesce(med, ''))), '^(email|e-mail|e_mail|e mail|newsletter)$')
            THEN 'Email'

        WHEN trim(lower(coalesce(med, ''))) = 'affiliate'
            THEN 'Affiliates'

        WHEN trim(lower(coalesce(med, ''))) = 'audio'
            THEN 'Audio'

        WHEN trim(lower(coalesce(src, ''))) = 'sms' OR trim(lower(coalesce(med, ''))) = 'sms'
            THEN 'SMS'

        WHEN regexp_matches(trim(lower(coalesce(med, ''))), '(mobile|notification|push)')
            THEN 'Mobile Push Notifications'

        WHEN trim(lower(coalesce(med, ''))) IN ('referral', 'app', 'link')
            THEN 'Referral'

        -- Direct: no source and no medium. Raw NULLs become the standard sentinels here.
        WHEN coalesce(src, '(direct)') = '(direct)'
             AND coalesce(med, '(none)') IN ('(not set)', '(none)')
            THEN 'Direct'

        -- Anything that matched nothing above. The reference calls this Unassigned too, and a rising
        -- Unassigned count is the signal that the source lists need extending -- which is exactly
        -- how the 2026-07-27 corrections were found.
        ELSE 'Unassigned'
    END
);

-- ---- referrer-based source inference (increment 11) ----------------------------------------
--
-- 16.7% of real sessions classified Direct while carrying a search or social referrer, because
-- classification read only the UTMs. The reference platform resolves this in processing, and so
-- do we: the collector stores the referrer verbatim; this macro reads it at query time.
--
-- Precedence, transcribed from the reference platform's documentation (fetched 2026-08-05),
-- not recalled:
--   1. an auto-tagging click id wins over manual UTMs for source/medium -- but a UTM campaign
--      name still populates the campaign field;
--   2. otherwise any UTM parameter present means ALL values come from UTMs, verbatim;
--   3. otherwise the referrer: a known search engine's hostname -> that engine / organic;
--      any other external site -> its hostname / referral. "Search-engine referral data is
--      processed by comparing the hostname ... to a list of known search engines."
--   4. otherwise direct: "a session is processed as direct traffic when no information about
--      the referral source is available."
--
-- Self-referrals are excluded per the reference's own rule -- "Analytics will not identify
-- traffic as referral when the referring website matched the same domain of the current page or
-- any of its subdomains." We cannot compute "same registrable domain" portably in SQL (`.com.br`
-- defeats last-two-labels), so the deployment declares its own domains instead:
-- `MINUANO_INTERNAL_DOMAINS`, a comma-separated list, suffix-matched so one entry covers every
-- subdomain. Empty list = no exclusion.
--
-- `attribution_from` says which rule fired: 'click_id' | 'utm' | 'referrer' | 'none'. Inferred
-- attribution is never disguised as tagged -- the same ethos as `event_time_trusted` in the
-- derived layer.
--
-- Known simplifications, on purpose:
--   * only the Google click id is recognised; other platforms' click ids carry no documented
--     source/medium contract we could transcribe.
--   * app referrers (`android-app://`, reversed-domain hosts like `com.<vendor>.<app>`) are
--     referral, never organic -- `com.google.android.gm` contains "google" and is a mail app,
--     not a search.

CREATE OR REPLACE MACRO referrer_host(ref) AS (
    lower(coalesce(regexp_extract(coalesce(ref, ''), '^[a-zA-Z][a-zA-Z0-9+.-]*://([^/:?#]+)', 1), ''))
);

CREATE OR REPLACE MACRO is_internal_host(host, internal_csv) AS (
    len(list_filter(
        string_split(lower(coalesce(internal_csv, '')), ','),
        d -> length(trim(d)) > 0
             AND (host = trim(d) OR host LIKE '%.' || trim(d))
    )) > 0
);

-- The canonical short name the reference reports for an engine referrer: `www.google.com.br`
-- and `br.search.yahoo.com` become `google` and `yahoo`, not the full hostname.
CREATE OR REPLACE MACRO engine_name(host) AS (
    regexp_extract(host,
        '(google|bing|yahoo|duckduckgo|baidu|yandex|ecosia|msn|ask\.com|aol|brave|qwant|startpage|naver|seznam)', 1)
);

CREATE OR REPLACE MACRO infer_traffic_source(src, med, camp, click_id, ref, internal_csv) AS (
    CASE
        WHEN length(trim(coalesce(click_id, ''))) > 0
            THEN {'source': 'google', 'medium': 'cpc', 'campaign': camp,
                  'attribution_from': 'click_id'}
        WHEN coalesce(nullif(trim(coalesce(src, '')), ''),
                      nullif(trim(coalesce(med, '')), ''),
                      nullif(trim(coalesce(camp, '')), '')) IS NOT NULL
            THEN {'source': src, 'medium': med, 'campaign': camp,
                  'attribution_from': 'utm'}
        WHEN length(referrer_host(ref)) > 0
             AND NOT is_internal_host(referrer_host(ref), internal_csv)
            THEN CASE
                WHEN coalesce(ref, '') ILIKE 'android-app://%' OR referrer_host(ref) LIKE 'com.%'
                    THEN {'source': referrer_host(ref), 'medium': 'referral',
                          'campaign': CAST(NULL AS VARCHAR), 'attribution_from': 'referrer'}
                WHEN is_search_source(referrer_host(ref))
                    THEN {'source': engine_name(referrer_host(ref)), 'medium': 'organic',
                          'campaign': CAST(NULL AS VARCHAR), 'attribution_from': 'referrer'}
                ELSE {'source': regexp_replace(referrer_host(ref), '^www\.', ''),
                      'medium': 'referral',
                      'campaign': CAST(NULL AS VARCHAR), 'attribution_from': 'referrer'}
            END
        ELSE {'source': CAST(NULL AS VARCHAR), 'medium': CAST(NULL AS VARCHAR),
              'campaign': CAST(NULL AS VARCHAR), 'attribution_from': 'none'}
    END
);
