# minuano

Open source user behavior tracking with campaign attribution that works the same way on web,
Android, and iOS.

*Minuano* is the cold wind that crosses the pampa in southern Brazil after a front passes.

> **Status: pre-alpha.** v0 runs end to end locally — collector, browser snippet, container, and a
> query layer that derives sessions and the reference platform channel groups. Nothing here has served production
> traffic, the S3 sink has never talked to AWS, and the event schema is version `0`, which means it
> can change.

## Why

Cross-platform campaign attribution is where open source analytics is weakest.

| Tool | Mobile SDKs | Campaign attribution | License |
|---|---|---|---|
| **Matomo** | real Android + iOS SDKs | Android reports install source, not campaigns; iOS needs explicit tracker config | GPL (SDKs BSD-3) |
| **PostHog** | good | weak marketing channel modeling | MIT |
| **Snowplow** | good | strong, but you build the modeling | trackers Apache 2.0; server components limited-use |
| **Plausible / Umami** | none | basic UTM capture | AGPL / MIT |

Nobody does best-in-class cross-platform campaign attribution in open source. That is the gap.

## Design

The data model is the reference platform's, on purpose.

1. **Every row is an event, not a session.** Sessions are derived downstream.
2. **Collection does no enrichment.** the reference platform leaves `traffic_source` empty in its intraday streaming
   tables and resolves attribution in a later pass; minuano stores raw UTMs exactly as observed and
   leaves channel grouping to a batch job.
3. **Event parameters are flat, one level, capped at 25.** Nested objects wreck Athena queries.

Two consequences worth stating up front:

- **The collector never drops an event.** It always returns 2xx. Valid events land in
  `data/events/dt=…`; invalid ones land in `data/bad/dt=…` with their validation errors attached,
  so they can be fixed and replayed. Snowplow's collector takes the same position — write
  everything, fork good from bad, stay non-lossy.
- **`ingested_at` is the only server-derived field.** Nothing is read off the request socket, which
  is what makes server-side relays (server-side GTM, future server SDKs) work correctly rather than
  labelling every event with the relay's identity.

## Quick start

```bash
uv sync

# terminal 1 -- the collector, writing to ./data
uv run uvicorn collector.app:app --reload

# terminal 2 -- serve the demo page
python3 -m http.server 8080

open "http://localhost:8080/demo/demo.html?utm_source=newsletter&utm_medium=email"
```

Click a button on the demo page, then look in `data/events/dt=<today>/`. Anything that failed
validation is in `data/bad/` with its errors attached — nothing is ever dropped.

Or in a container, which is the same collector writing to a mounted `./data`:

```bash
docker compose up --build
curl -X POST localhost:8000/collect -d '{"schema_version":"0","event_name":"page_view",
  "event_timestamp":"2026-07-27T12:00:00Z","anonymous_id":"anon_00000001","session_id":"1785500000"}'
docker compose stop     # SIGTERM drains the buffer to ./data before the container exits
```

To write to S3 instead, set `MINUANO_SINK=s3` and `MINUANO_S3_BUCKET`; the collector refuses to
boot without a bucket rather than discovering it at the first flush. Both sinks emit byte-identical
NDJSON under the same key layout, so a reader does not care which one produced it:

```
<root>/<events|bad>/dt=YYYY-MM-DD/<instance_id>-<seq>.ndjson
```

`dt` is the **ingest** date, not the event date — a closed partition is never reorganised, and a
skewed client clock cannot write into a past day. Downstream jobs should pad ±1 day.

Configuration is environment variables only:

| Variable | Default | Meaning |
|---|---|---|
| `MINUANO_SINK` | `local` | `local` or `s3` |
| `MINUANO_DATA_DIR` | `./data` | where NDJSON lands, local sink only |
| `MINUANO_S3_BUCKET` | — | required when `MINUANO_SINK=s3`; checked at boot |
| `MINUANO_S3_PREFIX` | `raw` | key prefix inside the bucket |
| `MINUANO_FLUSH_MAX_EVENTS` | `100` | flush when this many events are buffered |
| `MINUANO_FLUSH_MAX_SECONDS` | `5` | flush at least this often |
| `MINUANO_MAX_BODY_BYTES` | `1048576` | larger bodies get the one and only 4xx |
| `MINUANO_CORS_ORIGINS` | `*` | comma-separated; set this in production |
| `MINUANO_INSTANCE_ID` | random | appears in every filename, so instances never collide |

### Querying what you collected

```bash
uv run analytics/run.py                      # over ./data
uv run analytics/run.py --data-dir <path>
```

DuckDB reads the NDJSON where it sits — no load step, no service, views recreated on every run.
The SQL in [`sql/`](sql/) derives the two things collection deliberately does not: **sessions**
(attribution taken at the session's *first* event, the reference platform's rule) and **channel grouping** (the reference platform's
default channel group as an ordered CASE). It is written to run on Athena unchanged once the S3
sink is pointed at a bucket — only the glob path differs.

The report includes an `ingest partition vs event date` breakdown, which exists to keep one
tradeoff visible: `dt` is the *ingest* date, so `WHERE dt = '<past date>'` silently returns nothing
for events that happened then. Filter on `dt` to prune files, on `event_date` to answer a question,
and pad `dt` by ±1 day when the question is about `event_date`.

Tests: six suites, 95 checks — see [`validation/README.md`](validation/README.md), including the
list of what they do *not* prove. The two that matter most: the snippet has never run in a real
browser, and the S3 writer has never talked to AWS.

## The event contract

[`schema/event.v0.json`](schema/event.v0.json) — JSON Schema draft 2020-12. Required:
`schema_version`, `event_name`, `event_timestamp`, `anonymous_id`, `session_id`. Optional
`page`, `campaign`, `device`, `params` objects.

Custom events need no configuration: any `event_name` matching `^[a-z][a-z0-9_]{0,39}$` is valid,
with up to 25 flat scalar `params`. `page_view`, `session_start` and `first_visit` are reserved.

## Install

Increment 1 supports a script tag and a GTM Custom HTML tag — see
[`docs/install-gtm.md`](docs/install-gtm.md). A client-side GTM Custom Template for the Community
Gallery and a server-side GTM tag template are planned; the `GET /collect?e=<base64>` path exists
precisely so the sandboxed-template route works, since GTM's `sendPixel` API is GET only.

## Roadmap

| Increment | Contents | Status |
|---|---|---|
| 1 | contract, collector on local disk, browser snippet, GTM Custom HTML install | ✅ |
| 2 | S3 writer, Dockerfile, docker-compose | ✅ |
| 3 | query layer, sessions, the reference platform channel grouping (DuckDB, local) | ✅ |
| later | real S3 bucket + Athena · GTM Custom Template · server-side GTM tag · dashboard · Android and iOS SDKs | |

The dashboard is deliberately last. It does not get built until real data has been sitting in
storage for a week and been queried with Athena.

## License

Apache 2.0. Snowplow's architecture informed this project's design; none of its licensed server
code is used.
