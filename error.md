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

## TRAP-5 — Base64 GET payloads land in access logs and hit proxy length caps

**Date:** 2026-07-27 · **Status:** OPEN, accepted for v0

The `GET /collect?e=` path exists because GTM's sandboxed `sendPixel` is GET-only. Two costs come
with it: the full event body appears in every access log along the path, and URLs above roughly
2KB are truncated or refused by some proxies and CDNs.

**Accepted for v0** because `page_view` payloads are small. Revisit when custom events start
carrying fat `params` — that is when a payload will silently exceed the cap.
