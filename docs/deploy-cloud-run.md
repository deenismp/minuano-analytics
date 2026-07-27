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
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
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

No key is created at any point. That is the whole argument for this host.

## 3. Deploy

```bash
gcloud run deploy minuano-collector \
    --source . \
    --service-account="minuano-collector@$PROJECT.iam.gserviceaccount.com" \
    --set-env-vars="MINUANO_SINK_URI=gs://$BUCKET/raw,MINUANO_FLUSH_MAX_SECONDS=5,MINUANO_CORS_ORIGINS=https://your-site.example" \
    --allow-unauthenticated \
    --min-instances=0 \
    --project="$PROJECT" --region="$REGION" --quiet
```

Notes on the flags that matter:

- **`--source .`** builds from the `Dockerfile`. `.gcloudignore` keeps credentials, collected
  data and repository furniture out of the upload — check it before the first deploy.
- **`--allow-unauthenticated` is required here.** A tracking endpoint that browsers post to cannot
  be authenticated. It is also the exposure: anyone with the URL can write. See §6.
- **`--min-instances=0`** is scale-to-zero. An idle demo costs nothing.
- The image needs the GCP backend compiled in. If the build does not pick up the extra, pass it:
  `--build-env-vars=MINUANO_EXTRAS=gcp`, or set `ARG MINUANO_EXTRAS="gcp"` as the Dockerfile
  default for a GCP-only deployment.

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

curl -s "$URL/healthz"
```

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

## 5. The shutdown window

Cloud Run gives roughly **10 seconds** between `SIGTERM` and `SIGKILL` — the tightest of any
common host. The collector drains on SIGTERM, so `MINUANO_FLUSH_MAX_SECONDS` must stay
comfortably under that. The default of 5 is fine; raising it to batch more aggressively trades
directly against how much a scale-down can lose.

This matters more here than elsewhere precisely because scale-to-zero means shutdowns are routine
rather than exceptional.

## 6. Guardrails

`/collect` is unauthenticated by necessity, so the URL is the credential. Before sharing it:

- **A 30-day lifecycle delete rule on the bucket** — the storage cap. Apply it at creation.
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
