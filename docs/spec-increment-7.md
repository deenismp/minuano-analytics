# Spec — minuano increment 7: deployed

**Status:** complete — verified live 2026-07-27
**Created:** 2026-07-27
**Revision history:** v1.0

> **Written after the work, not before it — and that is a deviation worth naming.** Increment 7
> began as pure execution of increment 6's runbook, which is the one case the "spec before build"
> rule does not cover. It stopped being execution the moment the first deploy failed: three
> defects surfaced that needed code changes. This file records what was actually done and what it
> cost, so the increment leaves the same artifact behind as the other six.

---

## Goal

Stop having a deploy *runbook* and start having a *deployment*. The collector runs on Cloud Run,
writing to a real GCS bucket, reachable from the public internet, with no key material anywhere.

The point is not the URL. It is that a document nobody has executed is a hypothesis, and that
every layer below it — the port handling, the credential path, the IAM story, the health
endpoint — had only ever been tested against itself.

## Non-goals

No custom domain. No rate limiting or write key (still the real exposure of a public endpoint,
still a v1 conversation). No `s3://` or `az://`. No dashboard. No autoscaling policy beyond a
`--max-instances` cost cap.

---

## Steps

### Step 1 — Project, APIs, and a keyless identity

- [x] `run`, `cloudbuild` **and `artifactregistry`** enabled. The third is not optional and the
      runbook had omitted it: `gcloud` normally offers to enable it mid-deploy, but `--quiet`
      declines that prompt and the build fails instead.
- [x] Service account `minuano-collector`, granted `roles/storage.objectCreator` on the bucket
      and nothing else. No key created at any point.

**Done when:** the service has an identity that can create objects and cannot delete them.

### Step 2 — Build with the cloud backend actually compiled in

- [x] `cloudbuild.yaml`, passing `--build-arg MINUANO_EXTRAS=gcp`
- [x] Deploy by image rather than `--source .`

**Why this step exists at all:** `gcloud run deploy --source .` cannot set a Docker build ARG.
`--set-build-env-vars` reaches Google Cloud buildpacks only, and a Dockerfile build ignores it
without warning — so the image shipped with no cloud backend and the container died at boot.
See error.md, TRAP-13.

**Done when:** the deployed image contains `gcsfs`.
**Evidence:** revision 00003 boots; revisions 00001 and 00002 did not.

### Step 3 — Let least privilege actually be least

- [x] `writer.preflight()` asserts the write and treats removing its probe as best-effort

The probe wrote *and deleted*, so it required `storage.objects.delete`, and the deploy 403'd
under `objectCreator`. Widening the role was the wrong fix: raw is append-only by invariant and
`/collect` is public and unauthenticated, so delete on the bucket means a compromised endpoint can
erase the raw store. See error.md, TRAP-15.

**Done when:** the collector boots against a bucket where it cannot delete.
**Evidence:** `/health` returns `ok` live under create-only IAM — that response *is* the preflight
having succeeded.

### Step 4 — A health endpoint the platform will let you reach

- [x] The same handler serves `/healthz` **and** `/health`
- [x] `check_output.py` asserts they answer identically, so the alias cannot be pruned later

**Cloud Run reserves `/healthz`.** Its frontend answers with a branded HTML 404, the container
never sees the request, and nothing reaches Cloud Logging — so a healthy service looks dead and
the tool you would debug with is silent. The runbook's verification step told you to curl exactly
that path. See error.md, TRAP-14.

**Done when:** `curl $URL/health` returns the health payload from the live service.

### Step 5 — Verify the whole chain against the real thing

- [x] `POST /collect` → `events/dt=…`
- [x] `GET /collect?e=<base64>` (the GTM `sendPixel` route) → `events/dt=…`
- [x] a malformed payload → `bad/dt=…` with its validation errors, **200 not 4xx**
- [x] a bare `GET /collect` with no `e` → `bad/dt=…` with `"GET /collect called without the `e`
      parameter"`, also not rejected
- [x] a `token`-suffixed param stored as `<REDACTED>`, campaign object intact
- [x] all of it read back out of the bucket

---

## Validation contract

| Check | Rule | Bar | Result |
|---|---|---|---|
| Boots | the service reaches Ready against a `gs://` sink | FAIL | ✅ rev 00004 |
| Reachable | the health payload is retrievable from outside | FAIL | ✅ via `/health` |
| Non-lossy | invalid input is stored, never rejected | FAIL | ✅ both bad paths |
| Redaction | secret-shaped params are `<REDACTED>` in the bucket | FAIL | ✅ |
| Least privilege | the collector's identity cannot delete | FAIL | ✅ `objectCreator` only |
| No key | no service-account key exists for this deployment | FAIL | ✅ none created |

## What this increment did not prove

- **Nothing has driven a real browser at the live endpoint.** The snippet's cookie behaviour under
  real `SameSite`/ITP rules is still unexercised — the standing gap from `validation/README.md`.
- **No load.** One-event-at-a-time curls prove correctness, not behaviour under traffic.
- **Scale-to-zero drains have not been observed under concurrency.** Cloud Run's ~10s
  SIGTERM→SIGKILL window is the tightest of any host, and `MINUANO_FLUSH_MAX_SECONDS=5` leaves
  little room; a shutdown mid-flush at volume is untested.
- **Reading the bucket back with DuckDB** over `gs://` is still a separate untested path.

## Decisions

Recorded in full in `PROJECT.md` — three entries dated 2026-07-27: the build-config decision, the
best-effort-cleanup decision, and the `/health` alias.

## Cost of this increment

Three failed revisions and four Cloud Build runs, all inside the free tier. Every defect found was
in the deployment path, not the collector's logic — which is the argument for having done it
before writing a dashboard, not after.
