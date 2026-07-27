# Spec — minuano increment 4: portable object storage, and docker as the entry point

**Status:** complete — both steps verified 2026-07-27
**Created:** 2026-07-27
**Revision history:** v1.0

---

## Goal

Two things:

1. **One sink, any cloud.** `MINUANO_SINK_URI` replaces the sink/bucket/prefix trio. Local disk,
   S3, GCS and Azure Blob become the same code path, differing only in a URI scheme.
2. **Docker is how you run it.** The collector, the demo page, and the analytics report all run
   from `docker compose`, so a fresh clone needs Docker and nothing else.

## Non-goals

No Kubernetes, ECS, or Helm manifests — still out of scope for v0. No cloud-specific auth helpers;
each backend uses its SDK's own credential chain, which is the point of not writing our own.

---

## Steps

### Step 1 — One URI-based sink

- [x] `fsspec` in the base install; `s3fs` / `gcsfs` / `adlfs` as the `aws` / `gcp` / `azure` extras
- [x] `boto3` stops being a direct dependency — it arrives via `s3fs` when the AWS extra is installed
- [x] `LocalNDJSONWriter` and `S3NDJSONWriter` collapse into one `ObjectStoreWriter`
- [x] `MINUANO_SINK_URI` is the only storage knob. Default `file://./data`
- [x] Boot fails with an actionable message when the URI's backend is not installed
      (`gs://…` without the `gcp` extra says which extra to install)
- [x] Local writes keep the temp-file-then-rename; object stores do not need it, a PUT is atomic

**Done when:** the same events written to `file://` and to an object store are byte-identical, and
the key layout is unchanged from increment 2.
**Evidence:** `validation/output/check_sink.txt`. `memory://` — an fsspec filesystem built
in to the base install — stands in for the object-store path, so the non-local branch is exercised
with no cloud SDK and no credentials.
**Agent:** main-thread.

### Step 2 — Docker as the entry point

- [x] Image carries `sql/` and `analytics/` as well as the collector, so one image runs both
- [x] `docker compose up` → collector
- [x] `docker compose --profile demo up` → collector + a static server for `demo/demo.html`
- [x] `docker compose run --rm analytics` → the report over collected data
- [x] Cloud credentials pass through by mount and environment, never baked into the image

**Done when:** a clone with only Docker installed can collect an event from a browser and read the
report back, without `uv`, Python, or Node on the host.
**Evidence:** `validation/output/step5-container-check.txt`, which now also covers the analytics and demo profiles.
**Agent:** main-thread.

---

## Validation contract

| Check | Rule | Bar |
|---|---|---|
| Sink parity | `file://` and `memory://` produce identical bytes at identical keys | FAIL |
| Key layout | unchanged from increment 2: `<stream>/dt=…/<instance>-<seq>.ndjson` | FAIL |
| Missing backend | a URI whose backend is not installed fails at boot, naming the extra | FAIL |
| Unchanged behaviour | every increment 1–3 check still passes against the new sink | FAIL |
| Compose: collector | `docker compose up` collects and drains on stop | FAIL |
| Compose: analytics | `docker compose run --rm analytics` reports over the collected data | FAIL |
| Compose: demo | the demo profile serves `demo.html` and the snippet | FAIL |

---

## Decisions

| Decision | Why | Alternatives rejected |
|---|---|---|
| `fsspec`, not three SDK writers | The abstraction is fsspec's, not ours — local and every cloud become one code path, so this is *less* code than increment 2, not more. Extras keep the base install light: `pip install minuano[gcp]` pulls only what that cloud needs. DuckDB reads the same URIs, so the query layer ports with the sink. | **Three native writers** (boto3 + google-cloud-storage + azure-storage-blob): three heavy dependencies and three code paths to keep in sync, buying better native error handling that one small PUT per flush does not need. **S3-compatible + endpoint override**: 4 lines and zero dependencies, but Azure Blob has no S3-compatible API, so it answers two clouds of three. |
| One `MINUANO_SINK_URI`, clean break | `MINUANO_SINK` + `MINUANO_S3_BUCKET` + `MINUANO_S3_PREFIX` + `MINUANO_DATA_DIR` were four knobs describing one destination. A URI is one. Nothing is deployed anywhere, so there is no migration to preserve. | Keeping the old variables as aliases: config sprawl, and two ways to say the same thing that can disagree. |
| Fail at boot on a missing backend | `gs://` without `gcsfs` otherwise fails at the first flush, which is after events have been buffered and are then lost. | A lazy import at flush time. |
| Credentials via the SDK's own chain | Each backend already implements its cloud's credential resolution — instance roles, workload identity, managed identity. Writing our own would be worse and would need maintaining. | A `MINUANO_*_KEY` set of variables: encourages long-lived static keys, exactly what the cloud-native chains exist to avoid. |
