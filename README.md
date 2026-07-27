# minuano

[![checks](https://github.com/deenismp/minuano-analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/deenismp/minuano-analytics/actions/workflows/ci.yml)
[![licence](https://img.shields.io/badge/licence-Apache--2.0-blue.svg)](LICENSE)

Open source user behavior tracking with campaign attribution that works the same way on web,
Android, and iOS.

*Minuano* is the cold wind that crosses the pampa in southern Brazil after a front passes.

> **Status: pre-alpha.** v0 runs end to end from `docker compose` — collector, browser snippet, a
> sink that reaches AWS, GCP or Azure through one URI, and a query layer that derives sessions and
> the reference platform channel groups. Nothing here has served production traffic, no cloud backend has actually
> been written to, and the event schema is version `0`, which means it can change.

## Why

Campaign attribution breaks at the platform boundary.

Someone clicks your ad, reads two pages on the web, installs your app the next day, and converts
on their phone a week later. The web side saw a `utm_source`. The app side saw an install. Nothing
joins the campaign across them, so the channel that actually earned the conversion gets credited to
"direct" — or to whatever the last platform to see the user happened to call itself.

It stays broken because each platform hands you a different shape:

- **Web** gives you URL parameters and a cookie you control.
- **Android** gives you a referrer string from the Play Install Referrer API — UTM-shaped, readable
  once, at first launch.
- **iOS** gives you an AdServices attribution token you exchange with Apple, and what comes back is
  numeric campaign and ad-group IDs from Apple Search Ads. There is no `utm_source` at all.

Three formats, three identity models, three moments in time. Reconciling them is normally either
manual SQL that nobody trusts, or a paid attribution vendor.

## What minuano does about it

**One event contract, every platform.** Campaign source, medium and name live on the event itself,
tagged first-touch or last-touch as observed — so attribution never has to be reconstructed
afterwards by guessing at session ordering.

**Collection stores what it saw; nothing else.** Raw UTMs land exactly as observed, and channel
grouping runs as a separate pass over immutable files. Get the classification wrong and you re-run
it; you don't re-collect a month of traffic.

**Sessions and channels follow the reference platform's published rules**, so the numbers mean what people already
expect them to mean — without the data leaving infrastructure you control.

Being straight about the hard part: the event contract handles web and Android today, and **iOS
attribution does not fit it yet** — Apple's numeric Search Ads IDs have no home in a
`source/medium/campaign` object. That's a known schema-v1 problem, written up in
[`PROJECT.md`](PROJECT.md) rather than glossed over. Mobile SDKs are the goal the contract was
shaped around; neither one is built.

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

Docker is the only prerequisite — no Python, no uv, no Node on the host.

```bash
docker compose --profile demo up --build
open "http://localhost:8080/demo/demo.html?utm_source=newsletter&utm_medium=email"
```

Click the buttons on the demo page, then read the events back:

```bash
docker compose run --rm analytics     # sessions, channels, data-quality
docker compose stop                   # SIGTERM drains the buffer before the container exits
```

Raw events land in `./data/events/dt=<today>/`. Anything that failed validation is in
`./data/bad/` with its errors attached — nothing is ever dropped.

<details>
<summary>Running it without Docker</summary>

```bash
uv sync
uv run uvicorn collector.app:app --reload      # terminal 1
python3 -m http.server 8080                     # terminal 2
uv run analytics/run.py
```
</details>

## Storage — one URI, any cloud

Where events go is a single variable, and its scheme picks the backend:

| `MINUANO_SINK_URI` | Backend | Needs |
|---|---|---|
| `file:///data` | local disk (default) | nothing |
| `s3://bucket/raw` | AWS S3, and anything S3-compatible | `MINUANO_EXTRAS=aws` |
| `gs://bucket/raw` | Google Cloud Storage | `MINUANO_EXTRAS=gcp` |
| `az://container/raw` | Azure Blob / ADLS Gen2 | `MINUANO_EXTRAS=azure` |

```bash
MINUANO_EXTRAS=aws MINUANO_SINK_URI=s3://my-bucket/raw docker compose up --build
```

There is one writer. `fsspec` resolves every scheme to the same interface, so local disk and three
clouds are one code path — and the base image carries no cloud SDK at all until an extra asks for
one. Credentials are never configured by minuano: each backend uses its own cloud's chain, so
instance roles, workload identity and managed identity all work as intended. A URI whose backend
is not installed fails at **boot**, naming the extra to install, rather than at the first flush
after events have already been buffered.

The layout is identical on every backend, so a reader never has to care which produced it:

```
<sink>/<events|bad>/dt=YYYY-MM-DD/<instance_id>-<seq>.ndjson
```

`dt` is the **ingest** date, not the event date — a closed partition is never reorganised, and a
skewed client clock cannot write into a past day. Downstream jobs should pad ±1 day.

Everything else is environment variables too:

| Variable | Default | Meaning |
|---|---|---|
| `MINUANO_SINK_URI` | `file://./data` | where events land; the scheme picks the backend |
| `MINUANO_FLUSH_MAX_EVENTS` | `100` | flush when this many events are buffered |
| `MINUANO_FLUSH_MAX_SECONDS` | `5` | flush at least this often |
| `MINUANO_MAX_BODY_BYTES` | `1048576` | larger bodies get the one and only 4xx |
| `MINUANO_CORS_ORIGINS` | `*` | comma-separated; set this in production |
| `MINUANO_INSTANCE_ID` | random | appears in every object name, so instances never collide |

### Querying what you collected

```bash
docker compose run --rm analytics            # over the collected data
uv run analytics/run.py --data-dir <path>    # or on the host
```

DuckDB reads the NDJSON where it sits — no load step, no service, views recreated on every run.
The SQL in [`sql/`](sql/) derives the two things collection deliberately does not: **sessions**
(attribution taken at the session's *first* event, the reference platform's rule) and **channel grouping** (the reference platform's
default channel group as an ordered CASE). It is written to run on Athena unchanged once the sink
points at a bucket — only the glob path differs.

The report includes an `ingest partition vs event date` breakdown, which exists to keep one
tradeoff visible: `dt` is the *ingest* date, so `WHERE dt = '<past date>'` silently returns nothing
for events that happened then. Filter on `dt` to prune files, on `event_date` to answer a question,
and pad `dt` by ±1 day when the question is about `event_date`.

Tests: six suites, 110 checks — see [`validation/README.md`](validation/README.md), including the
list of what they do *not* prove. The two that matter most: the snippet has never run in a real
browser, and no cloud backend has ever been written to.

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
| 2 | object-store writer, Dockerfile, docker-compose | ✅ |
| 3 | query layer, sessions, the reference platform channel grouping (DuckDB, local) | ✅ |
| 4 | one URI-based sink across AWS/GCP/Azure, docker as the entry point | ✅ |
| later | a real bucket on each cloud · Athena · GTM Custom Template · server-side GTM tag · dashboard · Android and iOS SDKs | |

The dashboard is deliberately last. It does not get built until real data has been sitting in
storage for a week and been queried with Athena.

## Contributing

Opinions are as welcome as code — the schema is version `0`, so the design is still genuinely
open. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to run and test it, and
[`PROJECT.md`](PROJECT.md) for why things are the way they are, including the alternatives that
were rejected. Arguing with a decision-log entry is the most useful issue you can file.

All six suites run in CI on every pull request, on Linux.

## License

Apache 2.0. Snowplow's architecture informed this project's design; none of its licensed server
code is used.
