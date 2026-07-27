-- the reference platform default channel group, as an ordered CASE.
--
-- Rules transcribed from Google's "Default channel group" documentation
-- (the platform's channel-group documentation), not from memory. A channel classifier that is
-- subtly wrong is worse than none, because it looks right.
--
-- ORDER IS THE ALGORITHM. `cpm` matches both the paid-medium regex and the Display rule; it is
-- Display only because the paid branches require a search or social source and are tested first.
-- Reordering this CASE changes the numbers without changing any rule.
--
-- Two honest gaps, both listed in validation/README.md:
--   * the reference platform matches source against a large managed list of search and social sites. This is a seed
--     list of the common ones -- an unlisted search engine falls through to Referral or Direct.
--   * Channels driven by Google Ads metadata rather than source/medium (ad network type, campaign
--     type) cannot be reproduced from UTMs alone. Paid Search here means "paid medium from a known
--     search source", which is the reference platform's non-Google rule.
--
-- The `(direct)` / `(none)` sentinels are applied HERE, at enrichment. Raw keeps its NULLs.

CREATE OR REPLACE MACRO channel_group(src, med, camp) AS (
    CASE
        -- Cross-network: campaign name signals it, regardless of source or medium.
        WHEN lower(coalesce(camp, '')) LIKE '%cross-network%'
            THEN 'Cross-network'

        -- Paid Search: known search source AND a paid medium.
        WHEN regexp_matches(lower(coalesce(src, '')),
                 '^(google|bing|yahoo|duckduckgo|baidu|yandex|ecosia|ask|aol|brave)')
             AND regexp_matches(lower(coalesce(med, '')), '^(.*cp.*|ppc|retargeting|paid.*)$')
            THEN 'Paid Search'

        -- Paid Social: known social source AND a paid medium.
        WHEN regexp_matches(lower(coalesce(src, '')),
                 '(facebook|instagram|linkedin|twitter|x\.com|tiktok|pinterest|reddit|threads|snapchat|whatsapp)')
             AND regexp_matches(lower(coalesce(med, '')), '^(.*cp.*|ppc|retargeting|paid.*)$')
            THEN 'Paid Social'

        -- Display: medium says so. Tested after the paid branches, so `cpm` from a social source
        -- is Paid Social and `cpm` from anywhere else is Display.
        WHEN lower(coalesce(med, '')) IN ('display', 'banner', 'expandable', 'interstitial', 'cpm')
            THEN 'Display'

        -- Email: either field says email, in any of its four spellings.
        WHEN regexp_matches(lower(coalesce(src, '')), '^(email|e-mail|e_mail|e mail)$')
             OR regexp_matches(lower(coalesce(med, '')), '^(email|e-mail|e_mail|e mail)$')
            THEN 'Email'

        -- Organic Search: known search source, or the medium is literally `organic`.
        WHEN regexp_matches(lower(coalesce(src, '')),
                 '^(google|bing|yahoo|duckduckgo|baidu|yandex|ecosia|ask|aol|brave)')
             OR lower(coalesce(med, '')) = 'organic'
            THEN 'Organic Search'

        -- Organic Social: known social source, or a social medium.
        WHEN regexp_matches(lower(coalesce(src, '')),
                 '(facebook|instagram|linkedin|twitter|x\.com|tiktok|pinterest|reddit|threads|snapchat|whatsapp)')
             OR lower(coalesce(med, '')) IN
                 ('social', 'social-network', 'social-media', 'sm', 'social network', 'social media')
            THEN 'Organic Social'

        -- Referral.
        WHEN lower(coalesce(med, '')) IN ('referral', 'app', 'link')
            THEN 'Referral'

        -- Direct: no source and no medium. Raw NULLs become the reference platform's sentinels here.
        WHEN coalesce(src, '(direct)') = '(direct)'
             AND coalesce(med, '(none)') IN ('(not set)', '(none)')
            THEN 'Direct'

        -- Anything with UTMs that matched nothing above. the reference platform calls this Unassigned, and a rising
        -- Unassigned count is the signal that the seed lists above need extending.
        ELSE 'Unassigned'
    END
);
