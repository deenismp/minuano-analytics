# minuano — error register

What broke, why, and what it taught. Status-tagged. Entries are added the moment something is
learned, not at the end of a session.

**Status tags:** `OPEN` · `FIXED` · `MITIGATED` · `WONTFIX` · `TRAP` (never broke, but future-you
will walk into it)

---

## TRAP-1 — The event schema was never committed, despite being described as committed

**Date:** 2026-07-27 · **Status:** FIXED

The bootstrap brief said *"This is already committed at `schema/event.v0.json`. Read it before
writing any code."* It was not. The working directory held only `refs/refs.md` and was not a git
repo; `github.com/deenismp/minuano-analytics` had been created the same day with zero commits.

**Taught:** verify the repo state before trusting a brief's claim about what is on disk. Costs one
`ls`. The schema JSON was reproduced in the brief, so the fix was to write the file — but a session
that had "read" a hallucinated file would have built against imagined field names.

## TRAP-2 — Validating at the collector will tempt you into rejecting

**Date:** 2026-07-27 · **Status:** MITIGATED by design

The natural implementation of "validate against the schema" is 422-on-invalid. That makes the
pipeline lossy: a snippet bug or a schema tightening silently destroys traffic that can never be
recovered, and you find out from a dashboard gap weeks later.

**Mitigation:** the collector always returns 2xx and forks — valid to `data/events/dt=…`, invalid
to `data/bad/dt=…` with the errors attached to the original payload. If you are reading this
because you are about to add a 4xx path, read the decision-log entry in `PROJECT.md` first.

## TRAP-3 — Deriving anything from the request socket breaks server-side GTM before it exists

**Date:** 2026-07-27 · **Status:** MITIGATED by design

It is very natural to fill `device.user_agent` from the `User-Agent` header and geo from the
source IP. Do that, and every event relayed through a server-side GTM container — or any future
server SDK — is stamped with the container's identity instead of the visitor's.

**Mitigation:** `ingested_at` is the only server-derived field. Everything else comes from the
payload. This is an invariant in `CLAUDE.md`.

## TRAP-4 — `params` is a free-form dict on a public endpoint

**Date:** 2026-07-27 · **Status:** MITIGATED

This is the exact shape that produced a 51K plaintext-token leak in a previous project
(`Personal/02-data-engineering-patterns/anti-patterns.md` §7). Anyone can POST anything into
`params`, and a well-meaning developer will eventually put a session token in there.

**Mitigation:** values whose key matches `token$` / `apikey` / `sessionid` are replaced with
`<REDACTED>` at collect. Suffix-anchored patterns, not an exact-name allowlist — an allowlist is
what failed last time. Redaction is by replacement, never deletion, so the field's existence stays
visible.

## TRAP-6 — A clean SIGTERM shutdown does *not* exit 0

**Date:** 2026-07-27 · **Status:** FIXED (in the check, not the code)

The first version of `check_output.py` asserted `returncode == 0` after SIGTERM and failed, which
looked like the shutdown hook was broken. It was not: uvicorn restores the default signal handler
and re-raises the captured signal once the lifespan shutdown has run
(`uvicorn/server.py`, `capture_signals`), so the process dies *by* SIGTERM and reports `-15`.

**Taught:** exit 0 after SIGTERM would actually mean the signal was swallowed. Assert on the
observable effect — the drain log line and the file on disk — not on the exit code. Verified by
reading uvicorn's source rather than guessing, which is what turned a "bug" into a corrected test
in one step.

## TRAP-7 — The S3 writer has never spoken to AWS

**Date:** 2026-07-27 · **Status:** OPEN

`check_s3_writer` injects a recording stub, so key layout and byte content are proved and nothing
else is. Credentials, IAM, bucket policy, region routing, throttling, retries, and same-key
overwrite behaviour are all unexercised. The stub cannot fail the way AWS fails.

**Do this before trusting the S3 sink:** run the collector against a real bucket with
`MINUANO_SINK=s3`, post the fixture set, and confirm the objects and their contents in S3.

## TRAP-8 — DuckDB resolves a glob when the view is CREATED, not when it is queried

**Date:** 2026-07-27 · **Status:** FIXED

`CREATE VIEW bad_events AS SELECT * FROM read_json('.../bad/dt=*/*.ndjson')` fails outright with
`IO Error: No files found that match the pattern` on any run where nothing was rejected. Views are
not lazy about their globs.

**Fix:** `bad_events` lives in its own `sql/bad_events.sql`, executed by the runner only when a
reject file actually exists. So `bad_events` may legitimately not exist, and callers must handle a
`CatalogException` rather than assuming the view is there.

**Taught:** a healthy pipeline produces no bad rows, so this failure only shows up on a *clean*
run — the run you are least likely to be testing.

## TRAP-9 — `WHERE dt = '<date>'` is a silently wrong way to ask about event dates

**Date:** 2026-07-27 · **Status:** MITIGATED by design, but it will still catch someone

`dt` is the **ingest** date. `event_date` is when the event happened. In the increment 3 fixtures,
eleven events spanning three event-dates all sit in a single `dt=2026-07-27` partition, so
`WHERE dt = '2026-07-26'` returns **0 rows** while `WHERE event_date = '2026-07-26'` returns 3.

No error, no warning, just a smaller number than the truth — the worst failure shape there is.

**Rule:** filter on `dt` to prune files, on `event_date` to answer a question. When the question is
about `event_date`, pad `dt` by ±1 day (Boundary-File Padding). This is asserted in
`check_analytics.py` so the tradeoff stays visible rather than becoming folklore.

## TRAP-5 — Base64 GET payloads land in access logs and hit proxy length caps

**Date:** 2026-07-27 · **Status:** OPEN, accepted for v0

The `GET /collect?e=` path exists because GTM's sandboxed `sendPixel` is GET-only. Two costs come
with it: the full event body appears in every access log along the path, and URLs above roughly
2KB are truncated or refused by some proxies and CDNs.

**Accepted for v0** because `page_view` payloads are small. Revisit when custom events start
carrying fat `params` — that is when a payload will silently exceed the cap.
