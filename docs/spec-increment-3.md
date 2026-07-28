# Spec — minuano increment 3: the query layer, sessions, and channel grouping

**Status:** complete — all three steps verified 2026-07-27
**Created:** 2026-07-27
**Revision history:** v1.0
**Workflow:** 3 of 3 (completes v0)

---

## Goal

Turn raw events into the two things a hosted analytics product gives you and collection deliberately does not: **sessions**
and **channel grouping**. Locally, with DuckDB reading the NDJSON where it sits.

This is the pass that answers the question the whole storage layout was a bet on — *is
partition-by-ingest-date queryable, and what does it cost you?*

## Non-goals

No dashboard. No Athena (that arrives when the S3 sink does; the SQL is written to port). No
identity resolution or cross-device stitching. No incremental/materialised tables — views over the
raw files, recomputed on every run, because at this volume anything else is premature.

---

## Steps

### Step 1 — Query layer over the raw NDJSON

- [x] `sql/events.sql` — a view over `<data_dir>/events/dt=*/*.ndjson`, hive-partitioned so `dt`
      is a column
- [x] `analytics/run.py` — loads the SQL files in order and prints a report
- [x] Demonstrate the ingest-date tradeoff explicitly: events whose `event_timestamp` spans three
      days all land in **one** `dt` partition

**Done when:** the view reads every event written by the collector, `dt` is queryable as a column,
and a query filtered on *event* date returns rows that a query filtered on `dt` does not.
**Evidence:** `validation/output/step7-analytics-check.txt`.
**Agent:** main-thread.

### Step 2 — Session derivation

- [x] `sql/sessions.sql` — one row per `(anonymous_id, session_id)`: start, end, event and
      page-view counts, entry and exit path, and the campaign **as of session start**
- [x] A re-derivation of session boundaries from the 30-minute inactivity gap, independent of the
      client's `session_id`, compared against it as a data-quality test

**Done when:** the session table matches the hand-authored expectations, and the independently
derived session count equals the client-assigned one.
**Evidence:** same file. A mismatch between derived and client sessions is the signal that the
snippet's cookie logic has broken — it is the only end-to-end check on it we can run without a
browser.
**Agent:** main-thread.

### Step 3 — Channel grouping

- [x] `sql/channels.sql` — a `channel_group(source, medium, campaign)` macro implementing the standard
      default channel group as an **ordered** CASE
- [x] Rules taken verbatim from Google's documentation, not from memory, including the paid-medium
      regex `^(.*cp.*|ppc|retargeting|paid.*)$`
- [x] `(direct)` / `(none)` sentinels applied here, at enrichment — raw keeps NULLs

**Done when:** every fixture session is classified into the channel its hand-authored expectation
names.
**Evidence:** same file, per-session expected-vs-actual table.
**Agent:** main-thread.

---

## Validation contract

**Source of truth:** `validation/cases/analytics-fixtures.json` — hand-authored events with
hand-authored expected sessions and channels. The events are POSTed through the real collector, so
the file layout under test is the one the collector actually produces, not one the test wrote.

| Check | Rule | Bar |
|---|---|---|
| Read-through | every event posted is visible in the `events` view | FAIL |
| Partition column | `dt` is queryable and equals the ingest date for all rows | FAIL |
| Ingest-vs-event date | events spanning 3 event-dates occupy 1 `dt` partition; filtering on `dt` for a past event date returns 0 rows while filtering on event date returns the expected rows | FAIL |
| Session count | one row per `(anonymous_id, session_id)`, matching the fixture count | FAIL |
| Session attribution | campaign fields equal the values at session start, not at session end | FAIL |
| Derived == client | sessions re-derived from the 30-minute gap equal the client-assigned count | FAIL |
| Channel | every session's channel equals its hand-authored expectation | FAIL |

---

## Decisions

| Decision | Why | Alternatives rejected |
|---|---|---|
| DuckDB, not Postgres | The queries are analytical scans over event data — the workload columnar engines exist for, and the reason hosted analytics products export to a warehouse rather than Cloud SQL. DuckDB also reads the NDJSON in place, so there is no loader to build, and its SQL ports to Athena with a path change. | **Postgres**: needs an ingest step that does not exist, and either a new loader job or a collector that writes to a database — abandoning the append-only-files architecture. Right answer later for the *dashboard serving layer*, wrong one for querying raw events. **Stdlib Python**: no dependency, but the logic is throwaway and ports to nothing. **MinIO + Trino**: closest to the Athena target, two containers, far too heavy for a project with no data yet. |
| Views, recomputed every run | No state, no incremental logic, no staleness bugs. At this volume the whole dataset is a rounding error. | Materialised tables: premature, and adds a "is it stale?" question that does not need answering yet. |
| Channel rules from Google's doc, not from training | This is the project's whole differentiator. A channel classifier that is subtly wrong is worse than none, because it looks right. | Writing the CASE from memory. |
| Session attribution taken at session **start** | The published rule: the session-start event carries the information that determines the attribution of the session. Using the last event's campaign would re-attribute a session to whatever the visitor clicked last. | `arg_max`: silently wrong in exactly the case attribution matters. |
| Re-derive sessions as a DQ test, not as the source | The client assigns `session_id`; the batch job checking it independently is what catches a broken snippet. Replacing it would throw away the client's knowledge of what a "session" felt like. | Deriving sessions server-side only: loses the client signal, and every server-side or relayed event would fabricate its own session. |
