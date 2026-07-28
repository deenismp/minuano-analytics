# minuano — error register

What broke, why, and what it taught. Status-tagged. Entries are added the moment something is
learned, not at the end of a session.

**Status tags:** `OPEN` · `FIXED` · `MITIGATED` · `WONTFIX` · `TRAP` (never broke, but future-you
will walk into it)

---

## TRAP-1 — The event schema was never committed, despite being described as committed

**Date:** 2026-07-27 · **Status:** FIXED

The bootstrap brief said *"This is already committed at `schema/event.v0.json`. Read it before
writing any code."* It was not. The working directory held only the research notes and was not a git
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

## TRAP-7 — No cloud had ever been written to

**Date:** 2026-07-27 · **Status:** FIXED for GCS; still OPEN for S3 and Azure

Everything proving the sink ran against `file://` and `memory://`. Neither can fail the way a
cloud fails — credentials, IAM, region routing, throttling — and `s3fs`/`gcsfs`/`adlfs` had never
even been imported.

**Closed for GCS on 2026-07-27** by `validation/checks/check_cloud_sink.py`, which runs the real
collector against a real bucket: preflight wrote a probe object, the fixture set was accepted,
SIGTERM drained to the cloud, and every event was read back out with `ingested_at` and the
`<REDACTED>` param intact. 9/9.

**Still unproven: `s3://` and `az://`.** They share the writer and differ only in the fsspec
backend, so the risk is credentials and IAM rather than logic — but that is exactly the part
`memory://` cannot exercise. Run the same check against each:

```bash
MINUANO_SINK_URI=s3://<bucket>/raw uv run --extra aws validation/checks/check_cloud_sink.py
```

**Taught:** the preflight probe added for BUG-1 turned out to double as the credential test — if
the collector boots against a cloud URI at all, IAM is already proven.

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

## BUG-1 — On Linux the container accepted events with a 200 and then silently lost them

**Date:** 2026-07-27 · **Status:** FIXED · **Found by:** the first CI run, on ubuntu-latest

`validation/README.md` had predicted this in writing: *"the non-root user writing to a bind mount
is exactly the thing that behaves differently on Linux hosts, where uid 10001 may not own the host
directory."* The first run off macOS proved it.

The host directory is owned by the invoking user. The container runs as uid 10001. The write at
flush time raised `PermissionError`, `flush()` had already taken the buffer, and the exception
vanished inside the lifespan shutdown — so `docker compose stop` reported success, no drain log
line appeared, and the event was gone. Docker Desktop on macOS maps ownership to the calling user,
which hid it completely.

**Severity:** the collector's central promise is that it never drops an event. It answered 200 and
then dropped one. Silent loss behind a clean exit is the same failure shape as anti-pattern #4.

**Three fixes, because one was not enough:**

1. `writer.preflight()` writes and deletes a probe object at startup. An unwritable sink now
   refuses to boot, naming the fix (`MINUANO_UID=$(id -u) MINUANO_GID=$(id -g)`), instead of
   being discovered after traffic has been accepted.
2. `flush()` puts a failed batch **back** into the buffer instead of dropping it, so a transient
   object-store error costs a retry rather than the events. This bug existed independently of
   permissions and would have bitten on the first S3 blip.
3. `docker-compose.yml` takes `MINUANO_UID`/`MINUANO_GID`, and shutdown logs at `error` level with
   the remaining count when the buffer is not empty. `/healthz` reports `degraded` plus
   `last_error`.

**Taught:** the gap register was right, and writing it down is what made the fix a five-minute job
instead of a mystery. A platform you have not run on is not a small gap — CI on a second OS found
a data-loss bug on its first execution.

## TRAP-12 — A HEALTHCHECK pinned to a literal port lies when the platform assigns one

**Date:** 2026-07-27 · **Status:** FIXED

The `CMD` was fixed to read `$PORT` for Railway, but the `HEALTHCHECK` next to it still probed
`127.0.0.1:8000`. A container told to listen on any other port would serve correctly and report
unhealthy forever — and orchestrators restart unhealthy containers, so a working deploy would
crash-loop.

Surfaced by the `gcp-deploy` skill's note that **Cloud Run injects `PORT=8080` by default**, so
this was one deploy away from being real rather than theoretical.

**Fix:** the healthcheck reads `$PORT` with the same 8000 default as the CMD. Cloud Run ignores
Docker's HEALTHCHECK and probes itself, so the damage would have landed on Railway, Fly, ECS or
plain `docker run -e PORT=…` instead.

**Taught:** when one line starts reading an environment variable, grep for the literal it replaced.
The pair had to agree and nothing enforced it.

## TRAP-10 — An inferred schema disappears when the data is sparse

**Date:** 2026-07-27 · **Status:** FIXED

`sql/sessions.sql` failed to compile with `Binder Error: Referenced table "page" not found!` — not
because the SQL was wrong, but because the dataset it ran against was a single minimal event with
no `page` object. DuckDB's JSON inference builds columns from what is actually in the files, so a
field no event carries simply does not exist, and every query referencing it fails.

This is what a server-side-only deployment, or a freshly started one, looks like. It passed on the
fixture dataset because those events happen to be rich.

**Fix:** `sql/events.sql` declares its columns explicitly, mirroring `schema/event.v0.json`.
Inference is convenient and non-deterministic; the schema is the contract, so read against it.

**Taught:** a query that works on your test data and breaks on sparse data has not been tested,
it has been flattered. `union_by_name` does not help — it reconciles columns *across* files and
does nothing when no file has the field at all.

## TRAP-11 — A test that never cleans its output directory passes exactly once

**Date:** 2026-07-27 · **Status:** FIXED

`check_container.py` asserted "nothing written before shutdown" and "exactly one event drained",
against a host directory it never cleared. It passed on the first run and failed on every run
after, reading the previous run's files.

**Taught:** every harness here starts by deleting its own output directory. The bug is invisible
on the run you write the test on, which is the run you trust most.

## TRAP-13 — `gcloud run deploy --source` cannot pass a Docker build ARG

**Date:** 2026-07-27 · **Status:** FIXED · **Found by:** the first real Cloud Run deploy

`--set-build-env-vars` / `--build-env-vars` look like they set build arguments. They do not: they
only reach **Google Cloud buildpacks**. A Dockerfile build ignores them completely, with no
warning — the build succeeds and produces a subtly wrong image.

`MINUANO_EXTRAS` is a build `ARG`, so the deployed image contained no cloud backend at all and the
container died at boot:

```
ValueError: MINUANO_SINK_URI='gs://…' needs the 'gs' backend, which is not installed
```

The runbook had actively recommended the wrong flag, so following it exactly reproduced the bug.

**Fix:** `cloudbuild.yaml`, which passes `--build-arg` explicitly, then
`gcloud run deploy --image` against what it pushed. Two commands instead of one, and deterministic.

**Taught:** the failure was *loud* only because the collector validates its sink at boot. With
lazy backend resolution this image would have deployed green, served 200s, and lost every event at
the first flush — BUG-1 all over again, in a place no test could reach. The fail-at-boot decision
paid for itself the first time it met a real platform.

## TRAP-14 — Cloud Run reserves `/healthz`; the container never sees it

**Date:** 2026-07-27 · **Status:** FIXED

`curl https://<service>.run.app/healthz` returns a Google-branded **HTML 404**. The service is
healthy. The request never reaches the container, and **nothing appears in Cloud Logging** — the
one signal you would use to investigate is also absent.

Proven, not guessed: every other path (`/health`, `/healthzz`, `/Healthz`, `/readyz`, `/livez`,
`/`) returned *our* FastAPI JSON 404 and was logged. Only `/healthz` was missing from the request
log. `/healthz/` returned a 307 from our own app redirecting to `/healthz`, which then vanished —
so the route was registered and reachable, and exactly one path was being swallowed upstream.

This was one runbook step away from wasting an afternoon: `docs/deploy-cloud-run.md` told you to
verify the deploy by curling `/healthz`, which on Cloud Run *always* fails.

**Fix:** the same handler is registered at `/healthz` **and** `/health`, and `check_output.py`
asserts they answer identically so the alias cannot be removed as a duplicate. Docker's
`HEALTHCHECK` and Railway are unaffected — they dial `127.0.0.1` inside the container, below the
frontend — so the alias is only load-bearing for external probes on Cloud Run.

**Taught:** when a platform returns *its own* error page rather than your framework's, stop
debugging your app. Compare content-type and trace headers across two paths; ours answered
`application/json` with `x-cloud-trace-context`, the intercepted one answered `text/html` with
neither. That two-request diff located it faster than any log search could have.

## TRAP-15 — `objectCreator` is the right role, and the preflight probe broke it

**Date:** 2026-07-27 · **Status:** FIXED

The runbook argued for `roles/storage.objectCreator` over `objectAdmin` on the grounds that *"the
collector only ever PUTs new keys — it never reads and never deletes."* That stopped being true
when BUG-1's fix added `writer.preflight()`, which writes a probe object **and deletes it**. The
deploy failed at boot with a 403 on `storage.objects.delete`.

The tempting fix is to widen the role. That is the wrong direction: raw is append-only by
invariant, and `/collect` is a public unauthenticated endpoint, so granting its identity delete on
the bucket means a compromised endpoint can erase the raw store.

**Fix:** the write is asserted; the cleanup is best-effort and its failure is swallowed. The probe
key carries the instance id, so it never collides and never needs an overwrite (which in GCS would
itself require delete). One ~3-byte object is left per cold start and the bucket's 30-day
lifecycle rule reclaims it.

**Taught:** least-privilege IAM is a *design constraint on the code*, not a deployment detail
bolted on afterwards. A permission the code does not need is a permission the code must not
require — and the argument for the narrow role should be re-read whenever the code gains a new
operation, because that comment in the runbook was true when written and false three commits later.

## TRAP-16 — A check that asserts a dependency is *absent* is environment-dependent

**Date:** 2026-07-27 · **Status:** FIXED

`check_sink.py` proved that `gs://` fails at boot when the `gcp` extra is missing, by hardcoding
`gs://`. It passes in CI, which installs no extras — and fails on any machine where someone once
ran `uv run --extra gcp validation/checks/check_cloud_sink.py`, which leaves `gcsfs` in `.venv`.
This session started with that check already red for a reason that had nothing to do with the code.

**Fix:** pick the first backend that is genuinely absent (`gs`/`s3`/`az`) at runtime, and print an
explicit `[SKIP]` if all three are installed.

**Taught:** a red check nobody can explain is worse than a missing one — it trains you to skim past
failures. Assertions about what is *not* installed must read the environment, not assume it.

## TRAP-17 — The cloud check's most important assertion could not fail

**Date:** 2026-07-27 · **Status:** FIXED

`check_cloud_sink.py` polled the collector until it answered, then reported:

```python
check(True, "collector booted, so preflight wrote a probe object to the cloud")
```

`check(True, …)` — unconditional. The poll loop above it exits two ways: the collector answered,
or the 30-second deadline passed without the process dying. On the second path it still printed
**PASS**, and the run then died on the next request with `Connection refused`.

That assertion is the whole point of the file. It is the one that claims real credentials, real
IAM and a real bucket all work — the thing `memory://` cannot prove and the reason TRAP-7 was
closed. It was reporting success on a run where the collector never came up.

Surfaced by running the check on a machine with no Application Default Credentials, so the
collector booted, failed its sink preflight, and never served.

**Fix:** track `booted` and assert on it, with a detail line naming the usual cause (missing
credentials). Return non-zero rather than continuing into assertions that cannot mean anything.

**Taught:** this is TRAP-6 and TRAP-11 a third time — assert on the observable effect, not on
having reached a line of code. Worth grepping a harness for `check(True` and `assert True`: any
literal-true assertion is either documentation pretending to be a test, or a hole exactly this
shape. The failure mode is silent and permanent, because a check that always passes never asks
to be looked at.

## BUG-2 — Four of five storage paths wrote secrets to the sink in plaintext

**Date:** 2026-07-28 · **Status:** FIXED · **Found by:** a security review of the live service

The invariant says secret-shaped `params` values are replaced with `<REDACTED>`. It held for
exactly one shape: a well-formed dict with a flat `params`. Verified live against a running
collector — five probe secrets sent, **five landed in plaintext on disk**:

| Payload | Why it bypassed |
|---|---|
| `params: {auth: {token: …}}` | `redact()` walked only the top level of `params` |
| `[[{"params": {"apikey": …}}]]` | `_collect` unwraps one list level; the inner list is not a dict, so `_store` stored it before `redact()` was reached |
| `"apikey=…"` (bare JSON string) | same non-dict branch |
| `{"params":{"apikey":…},` (truncated) | `_store_unparseable` stored `payload_raw` verbatim, never redacted |
| `params: {"x-api-key": …}` | the pattern covers `apikey` and `api_key` but not the hyphenated spelling — the most common one on the wire |

The `x-api-key` miss is the sharpest: the denylist names that exact secret and let the usual
spelling through. A control key `TOKEN` in the same request *was* redacted, which is what makes
the gap so easy to miss — the feature demonstrably works.

**Severity:** this is TRAP-4 reproducing itself. That trap exists because an allowlist leaked 51K
plaintext tokens in a previous project; the fix was a suffix-anchored denylist, and the denylist
then had four holes. The endpoint is public and unauthenticated, and the sink is append-only
under `objectCreator` — so a secret that lands there **cannot be deleted**, only waited out.

**Fix:** redaction is now a property of *storage*, not of event shape. `redact()` recurses through
dicts and lists to any depth and accepts any payload type; keys are normalised
(`re.sub(r"[^a-z0-9]", "", key.lower())`) before matching, so `x-api-key`, `API Key` and `api.key`
all collapse onto spellings the pattern already covers; and `redact_text()` scrubs secret-shaped
assignments out of unparseable bodies. Every storage path calls one of them.

**Taught:** fixtures written alongside an implementation test the shape its author imagined. All
three redaction fixtures were flat dicts, so 100% of them passed against an implementation with
four holes. When a check protects an invariant, the cases have to come from the *threat*, not from
the happy path — `check_schema.py` now asserts each bypass explicitly.

## BUG-3 — "ORDER IS THE ALGORITHM" was enforced by a comment, and `x.com` matched `netflix.com`

**Date:** 2026-07-28 · **Status:** FIXED · **Found by:** a review of the sample-derived range

Two defects in `sql/channels.sql`, both silent by construction — neither can ever raise an error.

**1. Nothing tested the CASE ordering.** The header says *"Reordering this CASE changes the numbers
without changing any rule."* A mutation that made the Organic Video branch unreachable — exactly
the 115k-event defect a previous session had fixed — still passed **28/28**. The fixtures use five
distinct sources and reach eight of twenty branches; every branch added by the sample-driven
rewrite (Cross-network, Paid/Organic Shopping, Paid/Organic Video, AI Assistant, Affiliates, Audio,
SMS, Mobile Push) had **zero coverage**. `validation/README.md` meanwhile claimed *"Nine
hand-authored fixtures still guard each branch"*, which was false and was the sentence that would
stop anyone from checking.

That mattered more than usual: the justifying sample is not in the repo and cannot be re-run, so
the fixture set was the only durable record of the rewrite — and it did not record it.

**2. `x\.com` sat in the substring regex, not the exact-match list.** Verified:
`netflix.com`, `wix.com`, `citrix.com` and `phoenix.com` all classified as **Organic Social**. The
header explicitly designs against this — *"Short names (`fb`, `ig`, `x`) match EXACTLY, because a
substring `ig` would match almost anything"* — and then one member was filed in the wrong half.
Worse than the known `pinterest.lightning.force.com` case, because these are ordinary referrers.

Also fixed: `is_engine_product()` was anchored with `^`, so `www.docs.google.com` bypassed it and
became Organic Search; the product list missed `news`/`maps`/`cloud`/`scholar`/`analytics`; and
nothing was `trim()`ed, so `'fb '` fell through every exact-match list.

**Fix:** a table-driven case list in `check_analytics.py` — 23 rows covering every branch plus the
five order-sensitive pairs, calling the macro directly so it needs no fixture events. **Proven by
mutation:** removing `youtube` from the video branches, disabling the Organic Video branch, and
re-introducing the `x\.com` substring each now turn the suite red. Before, all three passed.

**Taught:** when a file's own comment says a property is load-bearing, that is the property most
worth asserting — the comment is evidence the author knew it was fragile and stopped one step
short. And a test suite that grew alongside a classifier tests the branches its author was
thinking about, which is never the ones that break.

## TRAP-18 — Reviewing only the current session's diff leaves the riskiest code unread

**Date:** 2026-07-28 · **Status:** noted

The first code review of this project was scoped to one session's diff. That silently excluded
`collector/credentials.py` (53 lines handling private key material, shipped straight to a live
deployment) and the rewrite of `sql/channels.sql` from sample data that is not in the repo and
cannot be re-run.

**Taught:** scope a review by *risk*, not by recency. Code derived from a careful analysis
inherits the analysis's credibility without having earned it — and code that has never been read
by anyone but its author is the code most worth reading, regardless of when it was written.

## TRAP-5 — Base64 GET payloads land in access logs and hit proxy length caps

**Date:** 2026-07-27 · **Status:** OPEN, accepted for v0

The `GET /collect?e=` path exists because GTM's sandboxed `sendPixel` is GET-only. Two costs come
with it: the full event body appears in every access log along the path, and URLs above roughly
2KB are truncated or refused by some proxies and CDNs.

**Accepted for v0** because `page_view` payloads are small. Revisit when custom events start
carrying fat `params` — that is when a payload will silently exceed the cap.
