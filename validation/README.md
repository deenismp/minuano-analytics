# Validation

Is the **output** right, not just the code.

```
cases/      static, hand-authored expectations
checks/     the runners
output/     evidence from the last run (gitignored)
```

## Running

```bash
uv run validation/checks/check_schema.py     # the contract accepts and rejects what it should
uv run validation/checks/check_output.py     # what the collector actually wrote to disk
uv run validation/checks/check_snippet.py    # what the snippet actually sends (needs node)
uv run validation/checks/check_sink.py       # sink parity across backends (no cloud needed)
uv run validation/checks/check_container.py  # the container, and two instances on one prefix (needs docker)
uv run validation/checks/check_analytics.py  # sessions and channel grouping over collected events
```

110 checks total: 14 + 22 + 16 + 11 + 19 + 28.

Each writes its console output to `validation/output/step*-*.txt`. All three exit non-zero on
failure, so they compose into a pre-commit or CI step later.

## What each one proves

| Check | Proves |
|---|---|
| `check_schema` | every fixture in `cases/fixtures.json` validates or fails as hand-authored; secret-shaped `params` values are replaced while their keys survive |
| `check_output` | rows in == rows on disk; good lines are schema-valid; `ingested_at` overwrote the client's value; `dt=` matches the UTC date of `ingested_at`; bad rows carry both their errors and the original payload; a cross-origin `text/plain` POST is accepted; a payload-supplied `user_agent` is not replaced by the socket's; the buffer drains on SIGTERM |
| `check_snippet` | UTMs are read from the URL and persisted to a page without them; the first event of a new visitor is `first_touch` and the rest are `last_touch`; `anonymous_id` survives a session boundary; 31 minutes of inactivity starts a new `session_id`; a `track()` call made before load still lands; a nested param is dropped and the valid ones kept |
| `check_sink` | `file://` and `memory://` produce byte-identical objects at identical keys — and `memory://` is not the local branch, so the object-store code path is exercised with no cloud SDK; one object per (stream, partition) per flush; no key reused; a URI whose backend is missing fails at boot **naming the extra to install**; an unknown scheme fails at boot |
| `check_container` | the image builds and runs as a non-root user; the container's own `HEALTHCHECK` reports healthy; `docker compose stop` drains the buffer to the host volume; logs are one JSON object per line on stdout; `docker compose run --rm analytics` runs the same SQL in the container as on the host; the demo profile serves the page and the snippet; **two instances writing to one prefix lose nothing and do not overwrite each other** |
| `check_analytics` | every collected event is visible to the query layer; three event-dates land in one ingest partition and filtering on `dt` for a past event-date returns 0 rows while `event_date` returns 3; sessions match the hand-authored shape; **attribution comes from the session's first event, not its last**; sessions re-derived from the 30-minute gap match the client's count; all nine fixture sessions classify into the expected the reference platform channel with none falling through to Unassigned |

The expectations live in `cases/fixtures.json` and are hand-written. They are never generated
from the collector's own output — an expectation derived from the mechanism being verified
proves nothing.

## What these do **not** prove

- **The snippet has not run in a browser.** `check_snippet` drives it through a DOM shim
  (`checks/snippet_runtime.mjs`). Real cookie semantics — `SameSite`, `Secure`, ITP's 7-day cap on
  script-written cookies, subdomain scoping — are not exercised. Neither is a real
  `navigator.sendBeacon`, only a stand-in with the same contract. **Open the demo page in a real
  browser before trusting any of it.**
- **No cloud has ever been written to.** `memory://` exercises the object-store branch, but it
  cannot fail the way a cloud fails: credentials, IAM, bucket policies, region routing, throttling,
  retries, and what a PUT does when the key already exists are all unexercised. `s3fs` / `gcsfs` /
  `adlfs` have never even been imported here — only the boot guard that reports them missing has
  been tested. **A real-bucket run per cloud is required before any of the three is trusted.**
- **DuckDB has only ever read local files.** `analytics/run.py` accepts a cloud URI and hands it
  straight to DuckDB, which needs its own extension (`httpfs`, `azure`) and its own credentials —
  a completely separate path from the writer's, sharing only the URI string. Untested.
- **No load.** Buffer behaviour under sustained traffic, and what a flush costs at volume, is
  unmeasured. Concurrency is proved for two instances at six events, which is a collision test,
  not a load test.
- **Neither flush trigger is tested.** Every harness sets the size and time bounds so high they
  never trip; only the SIGTERM drain is proved. A run with realistic bounds is missing.
- **The 413 path is untested.** An oversized body is the one non-2xx response and no fixture
  exercises it.
- **The container is proved on one platform.** macOS, Docker Desktop, arm64. The non-root user
  writing to a bind mount is exactly the thing that behaves differently on Linux hosts, where uid
  10001 may not own the host directory.
- **The channel classifier is checked against nine hand-picked sessions, not against the reference platform.** Every
  fixture was written to exercise a branch, so passing means the CASE does what the doc says — not
  that minuano and the reference platform would agree on real traffic. The honest test is running both over the same
  property and diffing. Two known approximations: the search/social source lists are a seed list
  rather than the reference platform's managed one, and channels driven by Google Ads metadata (ad network type,
  campaign type) cannot be reproduced from UTMs at all.
- **The SQL has never run on Athena.** It is written to port — one path change — but Trino and
  DuckDB disagree on enough (struct access, `arg_min`, `date_diff` argument order, regex flavour)
  that "ports cleanly" is a claim, not a result, until it runs there.
- **Nine sessions is not a dataset.** Nothing here says anything about query cost, partition
  pruning at volume, or whether the ±1 day boundary padding is sufficient in practice.
- **Timezones.** Every fixture lands on one UTC day. A run crossing midnight UTC, or a client with
  a badly skewed clock, is not covered.
