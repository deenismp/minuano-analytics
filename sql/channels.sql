-- the reference platform default channel group, as an ordered CASE.
--
-- Rules transcribed from Google's "Default channel group" documentation
-- (the platform's channel-group documentation), then corrected against 7.4M real events from a
-- production the reference platform property by diffing this macro's output against the reference platform's own
-- `reference_channel_field.reference_campaign_field.default_channel_group`. The first
-- version agreed on 92.9% of events; every disagreement worth fixing is fixed below.
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
--   * `google`/`cpc` that the reference platform calls **Display** -- it knows the Google Ads network type; we see
--     only source and medium, and correctly call it Paid Search.
--   * campaigns the reference platform calls **Cross-network** without the string appearing in the campaign name --
--     again Google Ads campaign-type metadata.
-- Both are structural, not seed-list gaps. Do not "fix" them by special-casing google/cpc.
--
-- The `(direct)` / `(none)` sentinels are applied HERE, at enrichment. Raw keeps its NULLs.

CREATE OR REPLACE MACRO channel_group(src, med, camp) AS (
    CASE
        -- Cross-network: campaign name signals it, regardless of source or medium.
        WHEN lower(coalesce(camp, '')) LIKE '%cross-network%'
            THEN 'Cross-network'

        -- ---- paid branches: a paid medium plus a source we can classify -------------------
        WHEN regexp_matches(lower(coalesce(med, '')), '^(.*cp.*|ppc|retargeting|paid.*)$')
             AND (regexp_matches(lower(coalesce(src, '')),
                     '(amazon|ebay|shopify|etsy|walmart|mercadolivre|mercadolibre|shopee|alibaba|aliexpress)')
                  OR lower(coalesce(med, '')) LIKE '%shopping%')
            THEN 'Paid Shopping'

        WHEN regexp_matches(lower(coalesce(med, '')), '^(.*cp.*|ppc|retargeting|paid.*)$')
             AND regexp_matches(lower(coalesce(src, '')),
                     '(google|bing|yahoo|duckduckgo|baidu|yandex|ecosia|msn|ask\.com|aol|brave|qwant|startpage|naver|seznam)')
            THEN 'Paid Search'

        WHEN regexp_matches(lower(coalesce(med, '')), '^(.*cp.*|ppc|retargeting|paid.*)$')
             AND (regexp_matches(lower(coalesce(src, '')),
                     '(facebook|instagram|linkedin|twitter|x\.com|tiktok|pinterest|reddit|threads|snapchat|whatsapp|telegram|kwai|tumblr|quora)')
                  OR lower(coalesce(src, '')) IN ('fb', 'ig', 'meta', 'x', 'li', 'tt', 'wpp'))
            THEN 'Paid Social'

        WHEN regexp_matches(lower(coalesce(med, '')), '^(.*cp.*|ppc|retargeting|paid.*)$')
             AND (regexp_matches(lower(coalesce(src, '')), '(youtube|vimeo|twitch|dailymotion)')
                  OR lower(coalesce(src, '')) IN ('yt'))
            THEN 'Paid Video'

        -- Display: medium says so. After the paid branches, so `cpm` from a known social source
        -- is Paid Social and `cpm` from anywhere else is Display.
        WHEN lower(coalesce(med, '')) IN ('display', 'banner', 'expandable', 'interstitial', 'cpm')
            THEN 'Display'

        -- Paid Other: a paid medium we could not attribute to a known platform. Better than
        -- Unassigned -- it at least keeps paid traffic out of the organic numbers.
        WHEN regexp_matches(lower(coalesce(med, '')), '^(.*cp.*|ppc|retargeting|paid.*)$')
            THEN 'Paid Other'

        -- ---- organic branches ---------------------------------------------------------------
        WHEN regexp_matches(lower(coalesce(src, '')),
                 '(amazon|ebay|shopify|etsy|walmart|mercadolivre|mercadolibre|shopee|alibaba|aliexpress)')
             OR lower(coalesce(med, '')) LIKE '%shopping%'
            THEN 'Organic Shopping'

        WHEN regexp_matches(lower(coalesce(src, '')),
                 '(facebook|instagram|linkedin|twitter|x\.com|tiktok|pinterest|reddit|threads|snapchat|whatsapp|telegram|kwai|tumblr|quora)')
             OR lower(coalesce(src, '')) IN ('fb', 'ig', 'meta', 'x', 'li', 'tt', 'wpp')
             OR lower(coalesce(med, '')) IN
                 ('social', 'social-network', 'social-media', 'sm', 'social network', 'social media')
            THEN 'Organic Social'

        WHEN regexp_matches(lower(coalesce(src, '')), '(youtube|vimeo|twitch|dailymotion)')
             OR lower(coalesce(src, '')) IN ('yt')
             OR regexp_matches(lower(coalesce(med, '')), '^(video|.*video.*)$')
            THEN 'Organic Video'

        WHEN regexp_matches(lower(coalesce(src, '')),
                 '(google|bing|yahoo|duckduckgo|baidu|yandex|ecosia|msn|ask\.com|aol|brave|qwant|startpage|naver|seznam)')
             OR lower(coalesce(med, '')) = 'organic'
            THEN 'Organic Search'

        -- AI Assistant: the reference platform added this channel as LLM referrals became material. In the sample
        -- it was 5,598 events that we were sending to Unassigned.
        WHEN regexp_matches(lower(coalesce(src, '')),
                 '(chatgpt|openai|perplexity|copilot|gemini|claude\.ai|anthropic)')
             OR regexp_matches(lower(coalesce(med, '')), '(ai.?assistant|ai.?chat)')
            THEN 'AI Assistant'

        -- ---- everything else ------------------------------------------------------------------
        WHEN regexp_matches(lower(coalesce(src, '')), '^(email|e-mail|e_mail|e mail)$')
             OR regexp_matches(lower(coalesce(med, '')), '^(email|e-mail|e_mail|e mail|newsletter)$')
            THEN 'Email'

        WHEN lower(coalesce(med, '')) = 'affiliate'
            THEN 'Affiliates'

        WHEN lower(coalesce(med, '')) = 'audio'
            THEN 'Audio'

        WHEN lower(coalesce(src, '')) = 'sms' OR lower(coalesce(med, '')) = 'sms'
            THEN 'SMS'

        WHEN regexp_matches(lower(coalesce(med, '')), '(mobile|notification|push)')
            THEN 'Mobile Push Notifications'

        WHEN lower(coalesce(med, '')) IN ('referral', 'app', 'link')
            THEN 'Referral'

        -- Direct: no source and no medium. Raw NULLs become the reference platform's sentinels here.
        WHEN coalesce(src, '(direct)') = '(direct)'
             AND coalesce(med, '(none)') IN ('(not set)', '(none)')
            THEN 'Direct'

        -- Anything that matched nothing above. the reference platform calls this Unassigned too, and a rising
        -- Unassigned count is the signal that the source lists need extending -- which is exactly
        -- how the 2026-07-27 corrections were found.
        ELSE 'Unassigned'
    END
);
