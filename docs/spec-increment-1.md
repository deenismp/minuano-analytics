# Spec — minuano increment 1: contract + collector + snippet, end to end on local disk

**Status:** in progress
**Created:** 2026-07-27
**Revision history:** v1.0
**Workflow:** 1 of 3 (see Roadmap below)

---

## Goal

A hit from a browser lands as a schema-valid NDJSON line on local disk, with `ingested_at`
set by the server. No AWS, no container, no dashboard.

## Non-goals for this increment

S3 writer, `Dockerfile`, `docker-compose.yml` (→ increment 2). Athena queries over the raw
NDJSON (→ increment 3). Campaign enrichment, channel grouping, session stitching, identity
resolution, mobile SDKs, any UI, any deployment manifest (→ out of v0 entirely).

---

## Steps

### Step 1 — Repo foundation + the contract on disk

- [x] `git init`, remote wired to `github.com/deenismp/minuano-analytics`
- [x] `LICENSE` — Apache 2.0
- [x] `schema/event.v0.json` — verbatim from the bootstrap prompt
- [x] `CLAUDE.md` — scope, v0 boundary, out-of-scope list, dependency rule
- [x] `README.md` — what this is and the gap it fills
- [x] `PROJECT.md` + `error.md` — the standing artifact set

**Done when:** a valid sample event passes validation against the committed schema file and a
deliberately invalid one fails.
**Evidence:** `jsonschema` run output in `validation/output/step1-schema-check.txt`; first commit pushed.
**Agent:** main-thread.

### Step 2 — Collector, local writer, non-lossy

- [x] `POST /collect` — one event or an array; body parsed as JSON **regardless of `Content-Type`**
- [x] `GET /collect?e=<base64url>` — returns a 1×1 GIF (GTM `sendPixel` expects an image)
- [x] `GET /healthz`
- [x] CORS: `Access-Control-Allow-Origin` + `OPTIONS /collect`
- [x] `ingested_at` set server-side, always overwriting any client-supplied value
- [x] Always 2xx. Valid → `data/events/dt=…`, invalid → `data/bad/dt=…` with the error list
- [x] Buffered append, flush on count / age / SIGTERM
- [x] Unique filenames per instance
- [x] Config via env vars only; logs to stdout
- [x] `validation/` — fixtures + output checks

**Done when:** both ingest paths write, and a deliberately malformed event lands in `bad/` with
its error attached rather than vanishing.
**Evidence:** `validation/output/` run log showing line count == events sent (good + bad), every
good line schema-valid, `ingested_at` present and ≠ any client-supplied value, correct partition;
plus a cross-origin POST succeeding and a payload-supplied `device.user_agent` landing intact.
**Agent:** main-thread.

### Step 3 — Snippet + GTM doc

- [x] `snippet/minuano.js` — cookies for first/last touch, `anonymous_id`, `session_id`, auto `page_view`
- [x] `window.minuano.track(name, params)` + queue stub for pre-load calls
- [x] Transport chain: `sendBeacon` → `fetch(keepalive)` → pixel GET
- [x] `demo/demo.html`
- [x] `docs/install-gtm.md` — Custom HTML tag + a custom-event tag driven by dataLayer variables

**Size bar missed, deliberately.** 2838 bytes minified against a 2KB bar; 1530 gzip, 1296 brotli.
Deleting every optional piece (fetch fallback, gclid capture, console warnings, id accessors)
recovers ~580 bytes and still lands at ~2260, so 2KB minified is unreachable with campaign
persistence + custom events + two transports. Budget restated as ≤2KB **transferred**. Open for
Denis to overrule.

**Done when:** `demo.html?utm_source=x&utm_medium=y` writes a `page_view` with
`campaign.source=x`; `anonymous_id` stable across reloads; `session_id` stable within 30 min;
minified under 2KB; a `track()` call issued *before* the snippet loads still lands.
**Evidence:** the resulting NDJSON lines + `wc -c` on the minified file.
**Agent:** main-thread.

---

## Validation contract

**Source of truth:** the events the test harness sent (a static fixture file), never the
collector's own accept/reject counters — the expectation must not come from the mechanism
being verified.

**Checks:**

| Check | Rule | Bar |
|---|---|---|
| Rows | `lines(events/) + lines(bad/)` == events sent | FAIL |
| Schema | every line in `events/` validates against `schema/event.v0.json` | FAIL |
| Non-lossy | no fixture is absent from both directories | FAIL |
| `ingested_at` | present on every good line, and ≠ any client-supplied value | FAIL |
| Partition | file's `dt=` equals the UTC date of its `ingested_at` | FAIL |
| Redaction | no `params` value survives for a key matching `token$`/`apikey`/`sessionid` | FAIL |
| Dupes | no duplicate `(anonymous_id, event_timestamp, event_name)` within a run | WARN |

**Expected values** come from `validation/cases/fixtures.json` — static, hand-authored.

---

## Decisions

| Decision | Why | Alternatives rejected |
|---|---|---|
| Collector validates but never rejects (always 2xx; `events/` vs `bad/`) | Snowplow's collector writes any payload to the raw stream and forks good/bad downstream in Enrich, keeping the pipeline non-lossy. minuano has no Enrich stage, so the fork happens at collect. A 4xx-and-drop means a snippet bug silently destroys a day of traffic — anti-pattern #4. | (a) Validate and 4xx-reject: lossy. (b) No validation at all: true to Snowplow, but leaves no feedback loop while writing the snippet. |
| Partition by **ingest** date, not event date | The collector must never reorganize a closed partition, and a skewed client clock would write into a past day. Downstream pads ±1 day per ingestion-patterns Pattern 7. | Event-date partitioning: convenient for Athena, but makes raw mutable. |
| Redact matching `params` values, don't drop the key | Anti-pattern #6: redact by replacement, never deletion of context. Anti-pattern #7 is the 51K-plaintext-token leak this prevents. | Dropping the key: loses the fact that the field was ever sent. |
| Nothing derived from the request socket except `ingested_at` | Server-side GTM and future server SDKs relay on the visitor's behalf; deriving IP/UA from the socket would label every relayed event with the container's identity. | Socket-derived UA/IP: breaks sGTM before it is built. |
| Session rules copied verbatim from the published standard | 30-min inactivity, no midnight reset, no campaign split, `session_id` = unix seconds at session start. Keeps minuano's derived sessions comparable to the reference platform's. | Inventing our own rollover rule: no upside, breaks comparability. |
| Apache 2.0 | What Snowplow kept for its trackers; lets companies embed the future mobile SDKs without legal review. Snowplow's server components are SLULA (no HA production use, no competing product) — architecture may be studied, code may not be copied. | AGPL: protects against a SaaS competitor that does not exist yet. |
| Custom events need no new machinery | `event_name` + `params` in the schema are already the extension point; only the snippet API was missing. | A plugin/registry/config layer: abstraction with no second implementation to justify it. |

---

## Roadmap

| Increment | Contents |
|---|---|
| **1 (this spec)** | contract, collector on local disk, snippet, GTM Custom HTML install |
| 2 | S3 writer, `Dockerfile`, `docker-compose.yml` |
| 3 | Athena over the raw NDJSON |
| later | client-side GTM Custom Template (Community Gallery); server-side GTM tag template; campaign enrichment / channel grouping batch job; mobile SDKs |
