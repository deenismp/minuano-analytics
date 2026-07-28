# Event reference

Every field minuano collects, what it means, and what it looks like. Generated against
[`schema/event.v0.json`](../schema/event.v0.json), which is the contract — if this document and
the schema ever disagree, the schema is right.

**Schema version `0`.** It can still change. Fields planned for v1 are listed at the end, marked
as such, and are **not** collected today.

---

## The shape of an event

```json
{
  "schema_version": "0",
  "event_name": "page_view",
  "event_timestamp": "2026-07-28T03:40:27.512Z",
  "ingested_at": "2026-07-28T03:40:27.998Z",
  "anonymous_id": "mms43yg9xoei7wqv6",
  "session_id": "1785210026",
  "user_id": null,
  "page": {
    "url": "https://www.example.com/pricing?utm_source=google",
    "path": "/pricing",
    "title": "Pricing",
    "referrer": "https://www.google.com/"
  },
  "campaign": {
    "source": "google", "medium": "cpc", "campaign": "spring2026",
    "content": null, "term": null, "attribution": "first_touch"
  },
  "device": {
    "platform": "web", "user_agent": "Mozilla/5.0 (iPhone; …)",
    "language": "pt-BR", "screen_width": 375, "screen_height": 812
  },
  "params": { "gclid": "EAIaIQob…", "plan": "pro", "seats": 5 }
}
```

Every row is one event. Sessions, channels and user journeys are **derived later** from these
rows — nothing in this document is computed at collection time except `ingested_at`.

---

## Top-level fields

| Field | Type | Required | Example | Meaning |
|---|---|:--:|---|---|
| `schema_version` | string, always `"0"` | ✅ | `"0"` | Which contract this row was written against. Lets a future v1 live beside v0 in the same store. |
| `event_name` | string, `^[a-z][a-z0-9_]{0,39}$` | ✅ | `page_view`, `add_to_cart` | What happened. Lowercase, digits, underscores, ≤40 chars. Anything else is ignored by the snippet with a console warning. |
| `event_timestamp` | string, RFC 3339 | ✅ | `2026-07-28T03:40:27.512Z` | When it happened, **from the client's clock**. Can be wrong — a skewed device is normal. |
| `ingested_at` | string, RFC 3339 | server-set | `2026-07-28T03:40:27.998Z` | When the collector received it. **The only server-derived field**, and it overwrites anything the client sends. The partition key is its UTC date. |
| `anonymous_id` | string, 8–64 chars | ✅ | `mms43yg9xoei7wqv6` | The device/browser. Cookie `_mnu_id`, 2 years. Stable across pages and sessions. |
| `session_id` | string, 8–64 chars | ✅ | `"1785210026"` | Unix seconds at session start, as a string. Cookie `_mnu_ses` with a 30-minute sliding expiry — the cookie's lifetime *is* the rule. |
| `user_id` | string or `null`, ≤128 | — | `"u_88213"` | Your own identifier, once the visitor is known. Set via `minuanoConfig.userId`. Never invented by minuano. |

## `page`

| Field | Type | Example | Meaning |
|---|---|---|---|
| `page.url` | string | `https://ex.com/pricing?utm_source=google` | Full URL including query string. **Stored verbatim** — see the warning below. |
| `page.path` | string | `/pricing` | Path only. The field to group by; it does not vary with query strings. |
| `page.title` | string | `Pricing` | `document.title` at fire time. |
| `page.referrer` | string or `null` | `https://www.google.com/` | `document.referrer` — the page that linked here. `null` on direct visits and new tabs. |

> **`page.url` is stored as-is, including the query string.** If your site puts a token in a URL
> — magic-link login, password reset, invite links — that value lands in raw storage, which is
> append-only. Redaction only inspects `params`. Strip such parameters before the tag fires, or
> keep those routes out of tracking.

**On `referrer` as "the previous page":** it is the browser's own answer, so it is correct for
ordinary link navigation and empty for direct visits or a new tab. It is **not** updated by
single-page-app route changes, because it is fixed at document load — an SPA needs a GTM History
Change trigger calling `minuano.track()`.

## `campaign`

All five UTM fields are read from the landing URL and then **persisted in cookies**, so they stay
attached to later pageviews that carry no UTMs of their own.

| Field | Type | Example | Meaning |
|---|---|---|---|
| `campaign.source` | string or `null`, ≤250 | `google` | `utm_source`. |
| `campaign.medium` | string or `null`, ≤250 | `cpc` | `utm_medium`. |
| `campaign.campaign` | string or `null`, ≤250 | `spring2026` | `utm_campaign`. |
| `campaign.content` | string or `null`, ≤250 | `banner_a` | `utm_content`. |
| `campaign.term` | string or `null`, ≤250 | `cfp+course` | `utm_term`. |
| `campaign.attribution` | `first_touch` \| `last_touch` | `first_touch` | **Which model this row carries.** A visitor's first-ever event is `first_touch` (cookie `_mnu_ft`, 2 years, never overwritten); everything after is `last_touch` (cookie `_mnu_lt`, 180 days). |

`attribution` exists so neither model has to be reconstructed by ordering events. First-touch
analysis is `WHERE campaign.attribution = 'first_touch'`, not a window function.

## `device`

| Field | Type | Example | Meaning |
|---|---|---|---|
| `device.platform` | `web` \| `android` \| `ios` \| `server` | `web` | Always `web` today; the others exist for the SDKs the contract was shaped around. |
| `device.user_agent` | string or `null` | `Mozilla/5.0 (iPhone; …)` | Read from the **payload**, never from the request socket — so a server-side relay reports the visitor, not the relay. |
| `device.language` | string or `null` | `pt-BR` | `navigator.language`. |
| `device.screen_width` | integer or `null` | `375` | CSS pixels. |
| `device.screen_height` | integer or `null` | `812` | CSS pixels. |

## `params`

Free-form key/value pairs. **Max 25 keys. Values must be string, number, boolean or `null`** —
one level, no nesting. A nested object is dropped by the snippet with a console warning, because
nested structures make columnar queries painful.

| Example key | Type | Meaning |
|---|---|---|
| `gclid` | string | Google click id, captured automatically on `page_view` when present. |
| `fbclid` | string | Meta click id, same. |
| `plan` | string | Yours. Anything your event needs. |
| `seats` | number | Yours. |

> **Redaction.** Any key that normalises to contain `token`, `apikey`, `sessionid`, `secret` or
> `password` has its value replaced with `<REDACTED>` before storage — replaced, never deleted, so
> you can still see the field was sent. Matching ignores punctuation and case, so `x-api-key`,
> `API Key` and `X_API_KEY` are all caught. This applies at any nesting depth and on every storage
> path, including payloads that failed to parse.

---

## Events

### Reserved — do not send these yourself

| Event | Sent by | Meaning |
|---|---|---|
| `page_view` | the snippet, automatically on load | A page was viewed. Carries `gclid`/`fbclid` in `params` when present. |
| `session_start` | *derived downstream* | The first event of a session. Not collected — the batch pass derives it. |
| `first_visit` | *derived downstream* | The visitor's first-ever event. Not collected — derived. |

Sending a reserved name yourself would collide with the derived ones and double-count.

### Custom events

Anything matching `^[a-z][a-z0-9_]{0,39}$`. There is no registry to update and no code to change —
`event_name` plus `params` **is** the extension point.

```js
minuano.track('add_to_cart', { sku: 'ABC-1', price: 149.9, currency: 'BRL' });
minuano.track('signup_completed', { plan: 'pro', trial: true });
```

Suggested shape, not enforced: `object_verb` in the past tense (`checkout_started`,
`video_completed`). Consistency here is what makes the data queryable a year later.

---

## Derived downstream, not collected

These do not exist in a raw row. They are computed by the SQL in [`sql/`](../sql/) over immutable
files, so a rule change is a re-run, not a re-collection.

| Derived | From | Notes |
|---|---|---|
| `session_start` / `first_visit` | event ordering per visitor | The events the collector deliberately never sends. |
| `channel` | `campaign.source` + `medium` + `campaign` | Paid Search, Organic Social, Referral, Direct, AI Assistant… an ordered CASE. |
| `event_date` | `event_timestamp` | **Not the same as the `dt` partition**, which is the *ingest* date. Filter on `dt` to prune files, `event_date` to answer a question, and pad `dt` by ±1 day. |
| session duration, entry/exit page, pages per session | events grouped by `session_id` | |

---

## Planned for v1 — not collected today

Listed so you can design around them, and so nobody assumes they exist.

| Field | Type | Why it is not here yet |
|---|---|---|
| `page.previous_path` | string or `null` | The reliable in-site previous page. `page.referrer` is empty on new tabs and never updates on SPA routes, so the snippet would persist the last tracked URL in a cookie instead. **Next planned change.** |
| `campaign.click_id` | string or `null` | `gclid`/`fbclid` live in `params` today because `campaign` is closed (`additionalProperties: false`). Promoting them is a schema bump. |
| iOS attribution | object | Apple's AdServices returns a token exchanged for **numeric** campaign and ad-group ids. There is no `utm_source`, so it does not fit `campaign` and needs its own object. Android's Play Install Referrer *is* UTM-shaped and fits unchanged. |
| per-event `params` contracts | — | `params` has no type discipline beyond "flat scalar". A contract per `event_name` is the obvious v1 conversation. |
| `consent` | object | Consent state at collection time. There is no cookieless mode in v0. |
| `event_id` | string (uuid) | Client-generated id for exactly-once dedupe on retries. Today a retried beacon can duplicate. |

Changing any of the above means `schema_version` becomes `"1"`. Both versions can coexist in the
same store — that is what the field is for.
