# Deploying the collector to Railway, writing to Google Cloud Storage

> One worked example. [`deploy.md`](deploy.md) has the platform-agnostic contract — read that
> first if you are on Render, Fly, Cloud Run, ECS or Kubernetes.

Railway runs the container; GCS holds the events. Railway has an ephemeral filesystem, so the
local sink would lose everything on each redeploy — the object store is not optional here.

Nothing in this document needs `gcloud` installed. Everything is either the Railway UI or the
Google Cloud console.

---

## 1. The bucket

```
Name        your-bucket
Location    a single region, not multi-region (co-locate with your users; multi-region is a
            reflex that costs more and buys nothing here)
Access      uniform bucket-level, public access prevention on
Lifecycle   Delete objects older than 30 days
```

**Create the lifecycle rule at the same time as the bucket, not later.** `/collect` is an
unauthenticated write endpoint — it has to be, that is what a tracking endpoint is — so anyone
with the URL can put objects in your bucket. The lifecycle rule is the storage cap. Request
volume is the part it does not bound; see §5.

## 2. The service account

One account, one role, one bucket:

```
Role      roles/storage.objectCreator      ← not objectAdmin. The collector only ever PUTs new
                                             keys; it never reads and never deletes
Scope     the bucket, not the project
```

Create a JSON key. **Do not put it in the repo** — `.gitignore` and `.dockerignore` here match
the `<project>-<12 hex>.json` shape so a stray key is ignored on arrival, but an ignored file is
still one `git add -f` or one backup tool away from being somewhere it should not be.

> This is a deliberate deviation from `gcp-data-engineering.md`, which says never to store a
> service-account key and to use Workload Identity Federation instead. Railway cannot federate a
> GCP identity, so the choice is a scoped key or a different host. Cloud Run would remove the key
> entirely by giving the service its own identity — worth revisiting if this ever matters more
> than convenience.

## 3. Railway

**Connect the repo first.** `git push` deploys **nothing** if the GitHub repo is not connected in
the Railway UI — the push updates GitHub and Railway never notices. Either connect it under
**New → GitHub Repo**, or deploy explicitly with `railway up`. This has cost hours before.

### Variables

The collector's own settings are the same on every host and in every cloud — only the sink URI
changes scheme:

| Variable | Value |
|---|---|
| `MINUANO_SINK_URI` | `s3://<bucket>/raw`, `gs://<bucket>/raw`, or `az://<container>/raw` |
| `MINUANO_CORS_ORIGINS` | your site's origin — not `*` in production |
| `MINUANO_FLUSH_MAX_SECONDS` | `5` |
| `PORT` | `8000` |

**Credentials depend on the cloud, not on Railway.** Railway cannot mint a cloud identity, so this
is the tier-2 case in [`deploy.md`](deploy.md#credentials) — set your cloud's own standard
variables and nothing minuano-specific:

| Sink | Add these |
|---|---|
| `s3://` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` |
| `s3://` on R2 / MinIO / Backblaze | the three above, plus `AWS_ENDPOINT_URL` |
| `gs://` | `GOOGLE_APPLICATION_CREDENTIALS_JSON` — the entire key JSON as one string |
| `az://` | `AZURE_STORAGE_ACCOUNT_NAME` + `AZURE_STORAGE_ACCOUNT_KEY`, or a connection string |

Whichever you use, it must be a **runtime variable, never a build ARG.** Railway echoes expanded
`RUN` commands into the plaintext build log, which is exactly how a GitHub PAT leaked once. For
`gs://`, the collector writes the value to a `0600` temp file at boot and never logs it; AWS and
Azure SDKs read their variables directly and need no bridge.

Setting any variable triggers a redeploy.

### Build

The image needs the GCP backend compiled in:

```
MINUANO_EXTRAS = gcp
```

as a build argument (this one is safe to be a build arg — it is the string `gcp`).

### Networking

**Set Target Port to 8000.** The container reads `$PORT` and binds it; Railway's edge proxy needs
to be told separately where to send traffic. Skip this and the deploy reports success while the
URL returns 502. Setting `PORT=8000` *and* Target Port 8000 makes all three values agree, so no
injected port can mismatch.

Then generate a domain.

## 4. Verify

```bash
curl -s https://<your-service>.up.railway.app/healthz
```

`{"status":"ok", ...}` means the process is up **and** the sink is writable — the collector runs a
preflight probe against the bucket at boot and refuses to start if it cannot write. A container
that will not start with `sink … is not writable` in the logs is IAM, not code.

`"status":"degraded"` with a `last_error` means events are being accepted and held in memory
because flushes are failing. Nothing is lost yet; fix it before the process restarts.

Then send one event and look in the bucket:

```bash
curl -X POST https://<your-service>.up.railway.app/collect \
  -H 'Content-Type: application/json' \
  -d '{"schema_version":"0","event_name":"page_view","event_timestamp":"2026-07-27T12:00:00Z",
       "anonymous_id":"anon_00000001","session_id":"1785500000"}'
```

It appears under `raw/events/dt=<today>/` within `MINUANO_FLUSH_MAX_SECONDS`. If it is missing,
check `raw/bad/dt=<today>/` before assuming it was dropped — the collector never drops anything.

Useful log commands, from `railway-deploy-gotchas.md`:

```bash
railway logs -d -n 40            # recent runtime, one-shot. A bare `railway logs | tail` hangs
railway logs -b --filter error   # -b is the last SUCCESSFUL build, not the failed one
```

## 5. Cost guardrails

Three layers, all free, none of which take more than a couple of minutes. Set them **before** the
URL goes anywhere.

**Budget alert** — Billing → Budgets & alerts. Thresholds at 50/90/100% of something small like
$5. Understand what this does: it **notifies, it does not cap**. Nothing stops at 100% unless you
wire a function that disables billing on the project, which is a blunt instrument.

**Request-count alert** — Monitoring → Alerting, on the Cloud Storage `api/request_count` metric.
This is the one that actually catches abuse: **cost data lags up to a day**, so a budget alert
tells you about a flood tomorrow. Request rate tells you in minutes.

**Daily number** — link BigQuery billing export (Billing → Billing export), then a scheduled
query that emails a one-line daily total. The export only fills forward, so link it now even if
you write the query later.

For scale: at these volumes the GCS bill is fractions of a cent — one `put_object` per flush per
partition, and a flush with an empty buffer writes nothing at all. The guardrails exist for the
tail, not the expected case.

## 6. What this does not cover

- **No rate limiting or write key.** `/collect` accepts anything from anyone. That is correct for
  a tracking endpoint and a real exposure for a public one. A v1 conversation.
- **No custom domain, no CDN.** Both are Railway UI steps, neither affects the collector.
- **`s3://` and `az://` are unproven.** Only `gs://` has been run against a real bucket
  (`validation/checks/check_cloud_sink.py`). Same writer, different backend — the untested part
  is credentials and IAM, not logic.
