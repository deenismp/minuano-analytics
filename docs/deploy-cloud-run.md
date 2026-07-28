# Deploying the collector to Cloud Run, writing to Google Cloud Storage

> One worked example. [`deploy.md`](deploy.md) has the platform-agnostic contract — read that
> first if you are on Railway, Render, Fly, ECS or Kubernetes.

The reason to pick Cloud Run over a generic PaaS is not price — both are free at this volume.
It is that **there is no key**. The service gets its own identity, `gcsfs` picks it up through
Application Default Credentials, and there is nothing to create, store in a variable, rotate, or
accidentally commit. Every other host needs a service-account JSON somewhere.

Prerequisite: the `gcloud` CLI. `brew install --cask google-cloud-sdk`, then `gcloud auth login`.

---

## Commands that need your explicit go-ahead

Three of the steps below change account state and are not run automatically — enabling APIs,
granting IAM, and anything that deletes. They are written out so you can read the blast radius
first. Everything else is reversible.

## 1. Project and APIs

```bash
export PROJECT=minuano-analytics
export REGION=southamerica-east1          # same region as the bucket; do not split them
export BUCKET=minuano-demo-raw

# Enables billing-eligible APIs — run deliberately, not as a reflex.
# artifactregistry is not optional: it stores the built image. gcloud normally offers to enable
# it mid-deploy, but --quiet declines that prompt and the build fails instead.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    --project="$PROJECT" --quiet
```

## 2. A service account for the collector — no key

```bash
gcloud iam service-accounts create minuano-collector \
    --display-name="minuano collector" --project="$PROJECT" --quiet
```

Then grant it write access to the bucket and nothing else. **`objectCreator`, not `objectAdmin`**:
the collector only ever PUTs new keys — it never reads and never deletes, so read or delete
permission is blast radius with no purpose.

```bash
# IAM change — read it before running.
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
    --member="serviceAccount:minuano-collector@$PROJECT.iam.gserviceaccount.com" \
    --role="roles/storage.objectCreator" \
    --project="$PROJECT" --quiet
```

`objectCreator` grants create **without delete**, which is what the append-only invariant wants:
a public, unauthenticated `/collect` should not be able to erase the raw store even if abused.
The collector's boot probe is written to cooperate with that — it asserts the write and treats
removing the probe as best-effort, so it does not need `objectAdmin`. See error.md, TRAP-15.

No key is created at any point. That is the whole argument for this host.

## 3. Build, then deploy — two steps, and the reason matters

**`gcloud run deploy --source .` will not work here.** The image needs the GCP backend compiled
in via the `MINUANO_EXTRAS` build ARG, and a source deploy has no way to set one:
`--set-build-env-vars` reaches Google Cloud buildpacks only and is silently ignored by a
Dockerfile build. The result is an image with no cloud backend, which fails at boot with
`needs the 'gs' backend, which is not installed`. This is not hypothetical — it is how the first
deploy of this service failed (error.md, TRAP-13).

So build explicitly with `cloudbuild.yaml`, which passes the arg:

```bash
gcloud builds submit --config cloudbuild.yaml \
    --substitutions=_EXTRAS=gcp \
    --project="$PROJECT" --region="$REGION" --quiet
```

Then deploy the image it pushed:

```bash
IMAGE="$REGION-docker.pkg.dev/$PROJECT/cloud-run-source-deploy/minuano-collector:latest"

gcloud run deploy minuano-collector \
    --image="$IMAGE" \
    --service-account="minuano-collector@$PROJECT.iam.gserviceaccount.com" \
    --set-env-vars="MINUANO_SINK_URI=gs://$BUCKET/raw,MINUANO_FLUSH_MAX_SECONDS=5,MINUANO_CORS_ORIGINS=https://your-site.example" \
    --allow-unauthenticated \
    --min-instances=0 --max-instances=3 \
    --project="$PROJECT" --region="$REGION" --quiet
```

Notes on the flags that matter:

- **`.gcloudignore`** keeps credentials, collected data and repository furniture out of the
  upload — check it before the first build.
- **`--allow-unauthenticated` is required here.** A tracking endpoint that browsers post to cannot
  be authenticated. It is also the exposure: anyone with the URL can write. See §6.
- **`--min-instances=0`** is scale-to-zero. An idle demo costs nothing.
- **`--max-instances`** is the compute-side cost cap from §6. Set it on the first deploy, not
  after the first surprise.

**Do not set `GOOGLE_APPLICATION_CREDENTIALS_JSON`.** The service identity supersedes it, and
setting it would reintroduce exactly the key this host exists to avoid.

### `$PORT`

Cloud Run injects `PORT` (8080 by default) and the container binds it. Nothing to configure —
unlike Railway, there is no separate Target Port to keep in sync. The `HEALTHCHECK` in the
Dockerfile also reads `$PORT`, though Cloud Run uses its own probing and ignores it.

## 4. Verify

```bash
URL=$(gcloud run services describe minuano-collector \
        --project="$PROJECT" --region="$REGION" --format="value(status.url)")

curl -s "$URL/health"
```

> **Use `/health`, not `/healthz`, on Cloud Run.** `/healthz` is a reserved path: Google's
> frontend answers it itself with a branded HTML 404, the container never receives the request,
> and nothing is written to Cloud Logging. A perfectly healthy service looks dead. The collector
> serves the identical payload on both paths for exactly this reason (error.md, TRAP-14).
> Docker's `HEALTHCHECK` and Railway's probe still use `/healthz` and are unaffected — they dial
> `127.0.0.1` inside the container, below the frontend.

`{"status":"ok"}` means the process is up **and** the bucket is writable — the collector probes
the sink at boot and refuses to start otherwise. A revision that fails to start with
`sink … is not writable` in the logs is the IAM binding in §2, not the code.

`"status":"degraded"` with a `last_error` means events are being accepted and held in memory
because flushes are failing. Nothing is lost yet; fix it before the instance is recycled.

```bash
curl -X POST "$URL/collect" -H 'Content-Type: application/json' \
  -d '{"schema_version":"0","event_name":"page_view","event_timestamp":"2026-07-27T12:00:00Z",
       "anonymous_id":"anon_00000001","session_id":"1785500000"}'

gcloud storage ls "gs://$BUCKET/raw/events/" --project="$PROJECT"
```

Allow one flush interval (`MINUANO_FLUSH_MAX_SECONDS`) before expecting the object to appear.

You will also see `raw/.minuano-writable-<instance>` objects beside `events/` and `bad/`. Those
are the boot probes from §2 — the collector cannot delete them under `objectCreator`, by design,
and the lifecycle rule reclaims them. They sit outside `events/` and `bad/`, so no downstream
glob reads them.

**Verified end to end on 2026-07-27** against this exact configuration: a POST event and a
base64 `GET /collect?e=` event both landed in `raw/events/dt=…` with a `token`-suffixed param
stored as `<REDACTED>`; a malformed payload and a bare `GET /collect` both landed in `raw/bad/…`
with their validation errors attached, and neither was rejected.

## 5. The shutdown window

Cloud Run gives roughly **10 seconds** between `SIGTERM` and `SIGKILL` — the tightest of any
common host. The collector drains on SIGTERM, so `MINUANO_FLUSH_MAX_SECONDS` must stay
comfortably under that. The default of 5 is fine; raising it to batch more aggressively trades
directly against how much a scale-down can lose.

This matters more here than elsewhere precisely because scale-to-zero means shutdowns are routine
rather than exceptional.

## 6. Guardrails

`/collect` is unauthenticated by necessity, so the URL is the credential. Before sharing it:

- **A lifecycle delete rule on the bucket** — the storage cap, and the only automatic bound on
  what an abused public endpoint can accumulate. Apply it at creation. Pick the age deliberately:
  a demo wants 30 days, but anything being compared against the reference platform wants to outlive the comparison —
  this deployment runs **400 days**, matching the reference platform's 14-month ceiling for user-level data. Getting
  this wrong is not retroactively fixable; data deleted on day 31 is gone.

  ```bash
  printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":400}}]}' > /tmp/lifecycle.json
  gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=/tmp/lifecycle.json --project="$PROJECT"
  ```
- **`--max-instances`** — bound the compute side. Cloud Run's free tier is 2M requests/month, and
  a cap turns a flood into throttling instead of a bill.
- **Budget alert** (notifies, does not cap) and a **Cloud Monitoring alert on request count** —
  the latter is what actually catches abuse, because cost data lags up to a day.

## 7. What this does not cover

- **No rate limiting or write key.** A v1 conversation, and the real exposure of a public endpoint.
- **No custom domain.** A Cloud Run domain mapping, unrelated to the collector.
- **Reading the data back with DuckDB over `gs://`** is a separate untested path — it needs
  DuckDB's own `httpfs` extension and its own credentials. Writing works; reading directly from
  the bucket has not been exercised.
