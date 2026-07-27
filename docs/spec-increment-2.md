# Spec — minuano increment 2: S3 writer and the container

**Status:** complete — all three steps verified 2026-07-27
**Created:** 2026-07-27
**Revision history:** v1.0
**Workflow:** 2 of 3

---

## Goal

The same collector, writing NDJSON to S3 instead of a local directory, running in a container
that flushes its buffer on SIGTERM. Two instances sharing one prefix must not overwrite each
other — a claim increment 1 made by construction and never demonstrated.

## Non-goals

Athena queries (→ increment 3). Any deployment manifest — ECS task definitions, Kubernetes,
Helm — remains out of scope for v0 entirely. No autoscaling, no load testing.

---

## Steps

### Step 1 — S3 writer and sink selection

- [x] Extract the buffering into a base class; `LocalNDJSONWriter` and `S3NDJSONWriter` differ
      only in how one batch is put
- [x] `MINUANO_SINK=s3` with `MINUANO_S3_BUCKET` and `MINUANO_S3_PREFIX`
- [x] Key layout `s3://<bucket>/<prefix>/<stream>/dt=YYYY-MM-DD/<instance_id>-<seq>.ndjson`,
      matching the local layout exactly so increment 3 reads either
- [x] `boto3` added to dependencies (approved in the original dependency ask)
- [x] Fail fast at boot on a missing bucket, rather than at the first flush

**Done when:** both sinks produce byte-identical NDJSON for the same events, and the S3 key
layout matches the local path layout.
**Evidence:** `validation/output/step4-s3-writer-check.txt` — a recording stub client captures
every `put_object`; assertions on key layout, partition date, sequence uniqueness, and a
byte-for-byte diff of the local file against the S3 body.
**Agent:** main-thread.

> The stub proves the writer, not AWS. A real-bucket run is required before this is trusted in
> production, and is listed as unproven in `validation/README.md` until it happens.

### Step 2 — Container

- [x] `Dockerfile` — slim base, non-root user, no build toolchain in the final image
- [x] `docker-compose.yml` — local sink, `./data` mounted, port published
- [x] `HEALTHCHECK` against `/healthz`
- [x] SIGTERM inside the container drains the buffer to the mounted volume

**Done when:** `docker compose up`, an event posted from the host, `docker compose stop`, and the
event is on the host filesystem — proving the container's shutdown hook ran.
**Evidence:** `validation/output/step5-container-check.txt` — compose up, healthcheck reaching
healthy, POST, `stop`, and the NDJSON line read back from the mounted volume.
**Agent:** main-thread.

### Step 3 — Two instances, one prefix

- [x] Run two collectors concurrently against the same data directory
- [x] Both write, neither truncates or overwrites the other

**Done when:** every event from both instances is present, in files whose names carry distinct
instance ids.
**Evidence:** `validation/output/step5-container-check.txt`, second section — line count equals
the total sent across both instances, and two distinct `instance_id` prefixes appear in the
filenames.
**Agent:** main-thread.

---

## Validation contract

**Source of truth:** the events the harness sent, and the byte content of the local file when
comparing sinks. Never the collector's own counters.

| Check | Rule | Bar |
|---|---|---|
| Sink parity | S3 body == local file bytes for the same events | FAIL |
| Key layout | `<prefix>/<stream>/dt=YYYY-MM-DD/<instance>-<seq>.ndjson` | FAIL |
| Partition | `dt=` equals the UTC date of `ingested_at` | FAIL |
| Sequence | no key reused within an instance | FAIL |
| Boot | `MINUANO_SINK=s3` with no bucket fails at startup, not at first flush | FAIL |
| Container drain | the event is on the mounted volume after `docker compose stop` | FAIL |
| Concurrency | total lines == total sent; ≥2 distinct instance ids in filenames | FAIL |

---

## Decisions

| Decision | Why | Alternatives rejected |
|---|---|---|
| Buffering extracted into a base class | The rule is no abstraction until a second implementation needs one. It now does — local and S3 differ only in how a batch is put. | Duplicating the buffer logic: two places to fix a flush bug. |
| S3 client is injectable | Lets the writer be proved without AWS credentials, with no test-only dependency. | `moto`: a dependency added purely for tests. |
| One `put_object` per flush, never multipart | A flush is bounded by `MINUANO_FLUSH_MAX_EVENTS`; batches are kilobytes. Multipart is complexity with no payload to justify it. | Multipart: solves a problem this workload does not have. |
| Fail at boot on a missing bucket | A misconfigured sink that only surfaces at the first flush loses the events buffered before it. | Lazy validation: quieter, and lossy. |
