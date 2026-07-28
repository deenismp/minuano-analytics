# minuano — Claude working agreement

`minuano` is an open source user behavior tracking pipeline. The name is the cold wind that
crosses the pampa in southern Brazil after a front passes.

**Long-term target:** web, Android, and iOS, with source/medium/campaign attribution that works
consistently across all three.

**The gap it fills:** cross-platform campaign attribution. Matomo has real Android and iOS SDKs,
but its Android acquisition reports show install source only, not campaigns, and iOS campaigns
only work if the tracker is explicitly configured. PostHog has good mobile SDKs but weak
marketing channel modeling. Snowplow has the right architecture but its server components carry
a limited-use license. Nobody does best-in-class cross-platform campaign attribution in open source.

---

## Design principles

Copied deliberately from the reference platform's model.

1. **Every row is an event, not a session.** Sessions are derived downstream.
2. **Collection does no enrichment.** the reference platform leaves `traffic_source` empty in its intraday streaming
   tables and resolves attribution in a later processing pass. The collector stores raw UTMs
   exactly as observed; channel grouping happens in the batch pass in `sql/`, never at collect.
3. **Event parameters are flat, one level, capped.** Nested objects wreck Athena queries.

## Invariants — do not break these without a decision-log entry in `PROJECT.md`

- **`ingested_at` is set by the collector and never trusted from the client.** It is the one and
  only field derived server-side.
- **Nothing else is derived from the request socket.** Not IP, not user agent, not language, not
  platform — all of those are read from the payload. Server-side GTM and future server SDKs relay
  on a visitor's behalf; socket-derived values would label every relayed event with the relay's
  identity.
- **The collector never rejects an event.** It always returns 2xx. Valid events land in
  `data/events/dt=…`, invalid ones in `data/bad/dt=…` with the validation errors attached to the
  original payload. A dropped event is unanswerable forever.
- **Storage is one URI (`MINUANO_SINK_URI`), one writer, any backend.** Do not add a
  per-cloud writer or a second storage variable; fsspec is the abstraction and it is enough.
- **Raw is append-only, partitioned by ingest date.** Never reorganize a closed partition. A
  skewed client clock must not be able to write into a past day. Downstream jobs pad ±1 day.
- **`params` values matching `token$` / `apikey` / `sessionid` are replaced with `<REDACTED>`,
  never dropped.** Redact by replacement so the field's existence stays visible.
- **`campaign.attribution` distinguishes first-touch from last-touch at the event level**, so it
  never has to be reconstructed from session ordering.
- **Session rules are the reference platform's, verbatim:** 30 minutes of inactivity, no midnight reset, no split on
  a new campaign, `session_id` = unix seconds at session start.
- **Reserved event names:** `page_view`, `session_start`, `first_visit`. Custom events must not
  use them — the batch job will derive them.

## v0 scope

Web only. No mobile SDKs. No dashboard. No auth. No database.

1. Collector with two ingest paths — `POST /collect` (one event or an array) and
   `GET /collect?e=<base64-encoded JSON>`. The GET path is not optional: GTM custom templates run
   in sandboxed JavaScript where `sendPixel` is the outbound data API, and it is GET only.
2. Browser snippet — reads UTMs, persists first-touch and last-touch, generates and persists
   `anonymous_id` and `session_id`, fires `page_view`, exposes `minuano.track()`. Under 2KB
   minified, zero dependencies. **Cookies, not localStorage**, because GTM sandboxed templates can
   read and write cookies — one storage model across both install methods.
3. Local dev path — the collector writes to a local directory by default; the same code path
   reaches S3, GCS or Azure Blob by changing `MINUANO_SINK_URI`.
4. `docs/install-gtm.md` — copy-paste GTM Custom HTML tag. Documentation only, no template code.

Container basics: stateless, config via env vars only, logs to stdout, `/healthz`, flush buffered
events on SIGTERM, unique object keys per instance so two containers never overwrite each other.

**Explicitly out of scope:** a real GTM Custom Template for the Community Gallery, a server-side
GTM tag template, session stitching, identity resolution, any UI or dashboard, any deployment
manifests (ECS task definitions, Kubernetes, Helm).

The dashboard is last, not first. It does not get built until real data has been sitting in
storage for a week and been queried. Postgres is the right serving store for it when that day
comes — it is not the right store for querying raw events, and that distinction is why DuckDB is
in `sql/` and Postgres is nowhere.

## How to work here

- **Ask before adding any dependency.** Current set: `fastapi`, `uvicorn`, `jsonschema`, `fsspec`,
  `duckdb`. Cloud backends are extras, never base deps: `aws` → `s3fs`, `gcp` → `gcsfs`,
  `azure` → `adlfs`. The snippet has zero dependencies, permanently.
- **No abstraction layers, plugin systems, or config frameworks** until a second implementation
  actually needs one. Custom events need no machinery — `event_name` + `params` in the schema are
  already the extension point.
- **Smallest shippable increments.** Run it end to end before polishing anything.
- Boring and obvious beats clever.
- Work is specced on disk before it is built — see `docs/spec-increment-*.md`.
- End every response with one concrete next action.

## Licensing

Apache 2.0. Snowplow's architecture may be studied freely; its server components are under the
Snowplow Limited Use License (no highly-available production use, no competing product), so their
code must not be copied. Snowplow's *trackers* are Apache 2.0.

## Reference material

The research pack (`refs/refs.md`) was removed from the repo on 2026-07-27 — it was working notes,
not a deliverable, and a public repo is the wrong home for a reading list. The conclusions that
mattered are already in `PROJECT.md`'s decision log, which records each one with its source.

When a design question needs an external source, cite it in the decision-log entry rather than
reintroducing a links file that nobody maintains.
