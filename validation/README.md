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
```

Each writes its console output to `validation/output/step*-*.txt`. All three exit non-zero on
failure, so they compose into a pre-commit or CI step later.

## What each one proves

| Check | Proves |
|---|---|
| `check_schema` | every fixture in `cases/fixtures.json` validates or fails as hand-authored; secret-shaped `params` values are replaced while their keys survive |
| `check_output` | rows in == rows on disk; good lines are schema-valid; `ingested_at` overwrote the client's value; `dt=` matches the UTC date of `ingested_at`; bad rows carry both their errors and the original payload; a cross-origin `text/plain` POST is accepted; a payload-supplied `user_agent` is not replaced by the socket's; the buffer drains on SIGTERM |
| `check_snippet` | UTMs are read from the URL and persisted to a page without them; the first event of a new visitor is `first_touch` and the rest are `last_touch`; `anonymous_id` survives a session boundary; 31 minutes of inactivity starts a new `session_id`; a `track()` call made before load still lands; a nested param is dropped and the valid ones kept |

The expectations live in `cases/fixtures.json` and are hand-written. They are never generated
from the collector's own output — an expectation derived from the mechanism being verified
proves nothing.

## What these do **not** prove

- **The snippet has not run in a browser.** `check_snippet` drives it through a DOM shim
  (`checks/snippet_runtime.mjs`). Real cookie semantics — `SameSite`, `Secure`, ITP's 7-day cap on
  script-written cookies, subdomain scoping — are not exercised. Neither is a real
  `navigator.sendBeacon`, only a stand-in with the same contract. **Open the demo page in a real
  browser before trusting any of it.**
- **No concurrency.** One instance, one request at a time. Two containers writing to one prefix is
  argued for by construction (`instance_id` in the filename), not demonstrated.
- **No load.** Buffer behaviour under sustained traffic, and what a flush costs at volume, is
  unmeasured.
- **Neither flush trigger is tested.** Both harnesses set the size and time bounds so high they
  never trip; only the SIGTERM drain is proved. A run with realistic bounds is missing.
- **The 413 path is untested.** An oversized body is the one non-2xx response and no fixture
  exercises it.
- **Nothing about S3.** The sink is local-only in increment 1. Key uniqueness, partition layout on
  a real bucket, and multipart behaviour are all increment 2.
- **Nothing about Athena.** Whether this NDJSON layout is actually queryable and cheap is the
  question increment 3 answers. It is the first real test of the partition-by-ingest-date decision.
- **Timezones.** Every fixture lands on one UTC day. A run crossing midnight UTC, or a client with
  a badly skewed clock, is not covered.
