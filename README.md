# minuano

Open source user behavior tracking with campaign attribution that works the same way on web,
Android, and iOS.

*Minuano* is the cold wind that crosses the pampa in southern Brazil after a front passes.

> **Status: pre-alpha.** Increment 1 — collector, snippet, local disk. Nothing here is
> production-ready and the event schema is version `0`, which means it can change.

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

| Increment | Contents |
|---|---|
| 1 | contract, collector on local disk, browser snippet, GTM Custom HTML install |
| 2 | S3 writer, Dockerfile, docker-compose |
| 3 | Athena over the raw NDJSON |
| later | GTM Custom Template · server-side GTM tag · campaign enrichment and channel grouping · Android and iOS SDKs |

The dashboard is deliberately last. It does not get built until real data has been sitting in
storage for a week and been queried with Athena.

## License

Apache 2.0. Snowplow's architecture informed this project's design; none of its licensed server
code is used.
