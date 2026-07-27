# minuano — what exists now, and why it is that way

**Updated:** 2026-07-27
**Status:** increment 1 in progress
**Repo:** `github.com/deenismp/minuano-analytics` (local directory is still `open-tracking` — rename pending)

> Every delivery updates this file. A delivery is not done until the component row, the status,
> and the decision log reflect it.

---

## Components

| Component | Path | Status | Notes |
|---|---|---|---|
| Event contract | `schema/event.v0.json` | ✅ committed | JSON Schema draft 2020-12, version `0` |
| Working agreement | `CLAUDE.md` | ✅ committed | invariants live here |
| Increment 1 spec | `docs/spec-increment-1.md` | ✅ committed | checkboxes tracked in-file |
| Collector | `collector/` | 🔨 increment 1 | FastAPI, local writer |
| Validation harness | `validation/` | 🔨 increment 1 | fixtures + output checks |
| Browser snippet | `snippet/minuano.js` | 🔨 increment 1 | zero deps, <2KB minified |
| GTM install doc | `docs/install-gtm.md` | 🔨 increment 1 | Custom HTML tag |
| S3 writer | — | ⬜ increment 2 | |
| Container | `Dockerfile`, `docker-compose.yml` | ⬜ increment 2 | |
| Athena queries | — | ⬜ increment 3 | rung 4 of the ladder in `refs/refs.md` |
| Reference pack | `refs/refs.md` | ✅ committed | pre-existing research |

## Glossary

| Term | Meaning here |
|---|---|
| **event** | one row; the only unit the collector knows about |
| **good / bad** | an event that passed schema validation vs one that failed; both are stored |
| **first touch** | the campaign values observed on a visitor's first ever visit, persisted in a cookie |
| **last touch** | the most recent non-empty campaign values observed, persisted in a cookie |
| **ingest date** | UTC date of `ingested_at`; the partition key. Not the event date |
| **instance id** | random id generated at collector boot; makes output filenames unique per container |
| **increment** | one shippable slice with its own spec file; increments 1–3 make up v0 |

## Decision log

| Date | Decision |
|---|---|
| 2026-07-27 | **Collector validates but never rejects.** Always 2xx; valid → `data/events/dt=…`, invalid → `data/bad/dt=…` with the error list attached to the original payload.<br>*Why + alternatives rejected:* Snowplow's collector writes any payload to the raw stream regardless of validity and forks good from bad downstream in Enrich, which is what keeps that pipeline non-lossy. minuano has no Enrich stage, so the fork moves to collect. Rejected (a) validate-and-4xx — a snippet bug then silently destroys a day of traffic, which is anti-pattern #4 in `Personal/02-…/anti-patterns.md`; (b) no validation at all — truest to Snowplow, but leaves no feedback loop while the snippet is being written.<br>*Verified by:* Snowplow docs, "the Collector tries to write any payload to the raw stream, no matter its content, and no matter whether it is valid."<br>*Provenance:* web research 2026-07-27 (Snowplow fundamentals + failed-events docs); main thread, no sub-agent. |
| 2026-07-27 | **Partition raw by ingest date, not event date.**<br>*Why + alternatives rejected:* the collector must never reorganize a closed partition, and a client clock skewed by hours would otherwise write into a past day. Downstream pads ±1 day (ingestion-patterns Pattern 7, Boundary-File Padding). Rejected event-date partitioning: convenient for Athena, but makes raw mutable.<br>*Verified by:* `Personal/02-data-engineering-patterns/ingestion-patterns.md` §5, §7.<br>*Provenance:* main thread. |
| 2026-07-27 | **Nothing is derived from the request socket except `ingested_at`.**<br>*Why + alternatives rejected:* server-side GTM and future server SDKs relay on a visitor's behalf; deriving IP/user-agent/language from the socket would stamp every relayed event with the relay's identity, which is the standard failure mode of server-side tracking. Rejected socket-derived UA/IP: breaks server-side GTM before it is even built.<br>*Verified by:* GTM server-side template docs (`sendHttpRequest` relays; the visitor's context must travel in the payload).<br>*Provenance:* web research 2026-07-27; main thread. |
| 2026-07-27 | **`params` values matching `token$` / `apikey` / `sessionid` are replaced with `<REDACTED>`, not dropped.**<br>*Why + alternatives rejected:* `params` is a free-form dict on a public endpoint — the exact shape behind the 51K plaintext-token leak in anti-pattern #7. Anti-pattern #6 says redact by replacement, never deletion of context. Rejected dropping the key: loses the fact that the field was ever sent.<br>*Verified by:* `Personal/02-…/anti-patterns.md` §6, §7.<br>*Provenance:* main thread. |
| 2026-07-27 | **Session rules copied verbatim from the reference platform:** 30 minutes of inactivity, no midnight reset, no split on a new campaign, `session_id` = unix seconds at session start.<br>*Why + alternatives rejected:* keeps minuano's derived sessions directly comparable to the reference platform's, which is the benchmark this project is measured against. Midnight reset and campaign-split are Universal Analytics behaviors that the reference platform dropped. Rejected inventing a rollover rule: no upside, breaks comparability.<br>*Verified by:* the reference platform "[About] sessions" support doc — "a session ends or times out after 30 minutes of user inactivity"; `session_start` carries the attribution (gclid, UTM, referrer).<br>*Provenance:* web research 2026-07-27 (the platform's sessions documentation); main thread. |
| 2026-07-27 | **Apache 2.0.**<br>*Why + alternatives rejected:* it is the license Snowplow kept for its trackers, and it lets companies embed the future mobile SDKs without a legal review — adoption of the SDK layer is the whole wedge. Snowplow's server components are under SLULA (no highly-available production use, no competing product), so their architecture may be studied but their code may not be copied. Rejected AGPL: protects against a SaaS competitor that does not exist yet, at the cost of SDK adoption today.<br>*Verified by:* Snowplow Limited Use License FAQ.<br>*Provenance:* web research 2026-07-27; main thread. |
| 2026-07-27 | **Custom events get no machinery.**<br>*Why + alternatives rejected:* `event_name` + `params` in `schema/event.v0.json` are already the extension point; only the snippet API (`minuano.track()`) was missing. Rejected a plugin/registry/config layer: abstraction with no second implementation to justify it. The queue stub (`window.minuano = window.minuano \|\| []`) is the one piece that cannot be retrofitted, because adding it later silently drops early events for every already-installed snippet.<br>*Provenance:* main thread. |
| 2026-07-27 | **Increment 1 targets local disk only; S3 and the container move to increment 2.**<br>*Why + alternatives rejected:* the time budget was multi-day, which by the work-loop rules means splitting rather than scoping one long workflow. Local-disk-only reaches a runnable end-to-end loop with no AWS credentials involved.<br>*Provenance:* `/start-workflow` scoping pass 2026-07-27. |

## Known deltas for schema v1

- **iOS attribution does not fit `campaign`.** Apple's AdServices returns an attribution *token*
  exchanged for numeric campaign/ad-group ids from Apple Search Ads — there is no `utm_source`.
  Android's Play Install Referrer *is* UTM-shaped and fits the current object unchanged.
- **`params` has no type discipline beyond scalar.** Fine for v0; a per-event-name parameter
  contract is the obvious v1 conversation.
