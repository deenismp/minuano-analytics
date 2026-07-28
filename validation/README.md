# Validation

Is the **output** right, not just the code.

```
cases/      static, hand-authored expectations
checks/     the runners
output/     evidence from the last run (gitignored)
```

## Running

```bash
uv run validation/checks/check_public_repo.py  # nothing private is tracked (this repo is public)
uv run validation/checks/check_schema.py     # the contract accepts and rejects what it should
uv run validation/checks/check_output.py     # what the collector actually wrote to disk
uv run validation/checks/check_snippet.py    # what the snippet actually sends (needs node)
uv run validation/checks/check_sink.py       # sink parity across backends (no cloud needed)
uv run validation/checks/check_container.py  # the container, and two instances on one prefix (needs docker)
uv run validation/checks/check_analytics.py  # sessions and channel grouping over collected events
MINUANO_SINK_URI=gs://bucket/raw \\
  uv run --extra gcp validation/checks/check_cloud_sink.py   # a REAL bucket (skips without one)
```

117 checks total: 17 + 23 + 16 + 14 + 19 + 28. All of them run in CI on Linux on every pull
request. `check_cloud_sink.py` adds 9 more, and runs only where a real bucket and credentials
exist — 126 all told.

Each writes its console output to `validation/output/step*-*.txt`. All three exit non-zero on
failure, so they compose into a pre-commit or CI step later.

## What each one proves

| Check | Proves |
|---|---|
| `check_schema` | every fixture in `cases/fixtures.json` validates or fails as hand-authored; secret-shaped `params` values are replaced while their keys survive |
| `check_output` | rows in == rows on disk; good lines are schema-valid; `ingested_at` overwrote the client's value; `dt=` matches the UTC date of `ingested_at`; bad rows carry both their errors and the original payload; a cross-origin `text/plain` POST is accepted; a payload-supplied `user_agent` is not replaced by the socket's; the buffer drains on SIGTERM |
| `check_snippet` | UTMs are read from the URL and persisted to a page without them; the first event of a new visitor is `first_touch` and the rest are `last_touch`; `anonymous_id` survives a session boundary; 31 minutes of inactivity starts a new `session_id`; a `track()` call made before load still lands; a nested param is dropped and the valid ones kept |
| `check_sink` | an unwritable sink is refused **at startup** rather than at first flush; a failed flush returns its events to the buffer instead of dropping them; `file://` and `memory://` produce byte-identical objects at identical keys — and `memory://` is not the local branch, so the object-store code path is exercised with no cloud SDK; one object per (stream, partition) per flush; no key reused; a URI whose backend is missing fails at boot **naming the extra to install**; an unknown scheme fails at boot |
| `check_container` | the image builds and runs as a non-root user; the container's own `HEALTHCHECK` reports healthy; `docker compose stop` drains the buffer to the host volume; logs are one JSON object per line on stdout; `docker compose run --rm analytics` runs the same SQL in the container as on the host; the demo profile serves the page and the snippet; **two instances writing to one prefix lose nothing and do not overwrite each other** |
| `check_analytics` | every collected event is visible to the query layer; three event-dates land in one ingest partition and filtering on `dt` for a past event-date returns 0 rows while `event_date` returns 3; sessions match the hand-authored shape; **attribution comes from the session's first event, not its last**; sessions re-derived from the 30-minute gap match the client's count; all nine fixture sessions classify into the expected channel with none falling through to Unassigned |

**The session rule is separately validated against real traffic.** Re-deriving session boundaries
from a 30-minute inactivity gap over 33,687 real events reproduced an established analytics
platform's own session count to within 0.08% (4,901 vs 4,905), with 99.8% of users matching
exactly. That validates the *rule*; `sql/sessions.sql`'s implementation of it is still covered only
by the fixtures below, because the comparison was re-implemented in the warehouse to run where the
data is.

The expectations live in `cases/fixtures.json` and are hand-written. They are never generated
from the collector's own output — an expectation derived from the mechanism being verified
proves nothing.

## What these do **not** prove

- **The snippet has not run in a browser.** `check_snippet` drives it through a DOM shim
  (`checks/snippet_runtime.mjs`). Real cookie semantics — `SameSite`, `Secure`, ITP's 7-day cap on
  script-written cookies, subdomain scoping — are not exercised. Neither is a real
  `navigator.sendBeacon`, only a stand-in with the same contract. **Open the demo page in a real
  browser before trusting any of it.**
- ~~**No cloud has ever been written to.**~~ **Closed for GCS 2026-07-27** — `check_cloud_sink.py`
  ran the collector against a real bucket: probe object written at preflight, fixtures accepted,
  SIGTERM drained to the cloud, events read back with `ingested_at` and redaction intact. **`s3://`
  and `az://` are still unproven** — same writer, different backend, so the risk is credentials and
  IAM rather than logic. Run the same check against each before trusting it. Throttling, retries,
  and same-key overwrite remain unexercised on every backend.
- **DuckDB has only ever read local files.** `analytics/run.py` accepts a cloud URI and hands it
  straight to DuckDB, which needs its own extension (`httpfs`, `azure`) and its own credentials —
  a completely separate path from the writer's, sharing only the URI string. Untested.
- ~~**Nothing has ever been deployed.**~~ **Closed 2026-07-27** — the collector runs on Cloud Run
  against a real GCS bucket, verified live: POST and base64-GET events landed in
  `events/dt=…` with redaction intact, a malformed payload and a bare `GET /collect` landed in
  `bad/…` with their errors, none rejected. Deploying found three defects the whole local suite
  could not — a build argument a source deploy cannot pass, a health path the platform reserves,
  and a least-privilege role the boot probe had outgrown. **A platform you have not deployed to is
  not a small gap**, the same lesson CI-on-Linux taught when it found a data-loss bug on run one.
- **No load.** Buffer behaviour under sustained traffic, and what a flush costs at volume, is
  unmeasured. Concurrency is proved for two instances at six events, which is a collision test,
  not a load test.
- **Neither flush trigger is tested.** Every harness sets the size and time bounds so high they
  never trip; only the SIGTERM drain is proved. A run with realistic bounds is missing.
- **The 413 path is untested.** An oversized body is the one non-2xx response and no fixture
  exercises it.
- ~~**The container is proved on one platform.**~~ **Closed 2026-07-27.** CI runs the container
  suite on ubuntu-latest x86 as well as macOS arm64 — and found a data-loss bug on its first run
  Still unproven: any other architecture, and rootless Docker/Podman.
- **The channel classifier agrees with an established analytics platform on ≥99.6% of 7.8M real
  events across two independent properties** — 99.6% on one (from 92.9%) and 99.7% on another
  (from 97.6%), measured 2026-07-27 by diffing this macro against that platform's own default
  channel group over the same source/medium pairs. Two properties is what makes it a generalisation
  rather than an overfit. The residual is:
  - **Structural, unfixable from UTMs:** paid search traffic the reference classifies as *Display*,
    and campaigns it calls *Cross-network* without the words appearing anywhere. Both need ad-platform
    metadata — ad network type and campaign type — that a UTM does not carry. ~26k events, 0.35%.
  - **Deliberate divergence, not a defect:** `search.brave.com` we call Organic Search where the
    reference says Referral, and LLM referrers we call AI Assistant where the reference is
    inconsistent. Its managed list is behind the traffic here; copying a stale list would be worse
    than being explicit.
  - **Residual substring over-matching:** `pinterest.lightning.force.com` becomes Organic Social
    because `pinterest` matches as a substring. The Google-property class (`docs.`, `mail.`,
    `accounts.`, `gemini.`) is now handled by `is_engine_product()`. What remains needs a managed
    host list, which is the thing a seed list deliberately is not.
  A table-driven case list guards every branch and each order-sensitive pair; the real sample is what found the gaps.
- **The SQL has never run on Athena.** It is written to port — one path change — but Trino and
  DuckDB disagree on enough (struct access, `arg_min`, `date_diff` argument order, regex flavour)
  that "ports cleanly" is a claim, not a result, until it runs there.
- **Nine sessions is not a dataset.** Nothing here says anything about query cost, partition
  pruning at volume, or whether the ±1 day boundary padding is sufficient in practice.
- **Timezones.** Every fixture lands on one UTC day. A run crossing midnight UTC, or a client with
  a badly skewed clock, is not covered.
