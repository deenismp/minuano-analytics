# Contributing to minuano

This is pre-alpha and the event schema is version `0`, which means it can still change. That makes
it a good time to argue about the design and a bad time to build anything on top of it.

Opinions are as welcome as code. If you think the event contract is wrong, or that channel
grouping belongs somewhere else, open an issue — that is more useful right now than a patch.

## Run it

Docker is the only prerequisite.

```bash
docker compose --profile demo up --build
open "http://localhost:8080/demo/demo.html?utm_source=newsletter&utm_medium=email"
docker compose run --rm analytics
```

Without Docker, you need [uv](https://docs.astral.sh/uv/) and Node 22:

```bash
uv sync
uv run uvicorn collector.app:app --reload
uv run analytics/run.py
```

## Test it

```bash
uv run validation/checks/check_schema.py     # the contract
uv run validation/checks/check_output.py     # what the collector wrote
uv run validation/checks/check_snippet.py    # what the snippet sends   (needs node)
uv run validation/checks/check_sink.py       # sink parity across backends
uv run validation/checks/check_analytics.py  # sessions and channel grouping
uv run validation/checks/check_container.py  # the container            (needs docker)
```

All six run in CI on every pull request. **Read
[`validation/README.md`](validation/README.md) before trusting any of them** — it lists what they
do *not* prove, which is the more useful half.

There is no pytest, no coverage gate, and no linter. The checks assert on real output — files on
disk, HTTP responses, query results — rather than on lines executed. If you add behaviour, add a
check that would fail without it, and put its expectation in `validation/cases/` as a static
hand-authored value. **Never generate an expectation from the thing being verified.**

## Read this before a non-trivial change

| File | What it is |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | the invariants. Breaking one needs a decision-log entry, not just a passing test |
| [`PROJECT.md`](PROJECT.md) | what exists and **why it is that way** — the decision log has the alternatives that were rejected |
| [`error.md`](error.md) | traps. Several exist because something looked broken and wasn't |
| [`docs/spec-increment-*.md`](docs/) | how each slice was scoped, with its evidence |

The invariants are the short version:

- **`ingested_at` is the only server-derived field.** Nothing else comes off the request socket, so
  server-side relays label events with the visitor's context rather than the relay's.
- **The collector never rejects an event.** Always 2xx; invalid events land in `bad/` with their
  errors attached. A dropped event is unanswerable forever.
- **Raw is append-only, partitioned by ingest date.** Never reorganise a closed partition.
- **Collection does no enrichment.** Channel grouping happens in `sql/`, never at collect.
- **Sessions follow the reference platform's rules verbatim** — 30 minutes of inactivity, no midnight reset, no split
  on a new campaign.

## What review will ask

- Which check proves this? If none does, that's the first conversation.
- Does it break an invariant? If it should, say why, in `PROJECT.md`.
- Does it add a dependency? Say what it buys and what was rejected. The base install is
  deliberately five packages, and cloud SDKs are extras.
- Does it add an abstraction? The rule is not until a second implementation needs one. Both
  abstractions here — the writer and the sink URI — were added the day a second case appeared.

## Scope

minuano is deliberately narrow. Currently out of scope: a dashboard, identity resolution, session
stitching, and deployment manifests. Mobile SDKs are the long-term goal and the reason the event
contract looks the way it does, but they are not started.

The one thing that is genuinely open: **nobody has run this in production.** The snippet has not
been tested in a real browser, and no cloud backend has been written to. If you do either, the
result is worth an issue whatever it says.

## Licence

Apache 2.0. By contributing you agree your contribution is licensed under it.
