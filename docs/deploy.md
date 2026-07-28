# Deploying the collector anywhere

minuano is a stateless container. It runs on Railway, Render, Fly.io, Koyeb, Cloud Run, ECS,
Kubernetes, or a VM — nothing in it assumes a particular host or cloud.

Rather than shipping a config file per platform, here is the contract. If your platform satisfies
it, the collector works; if it doesn't, this tells you exactly which part will break.

---

## What the platform must provide

| Requirement | Why | What breaks without it |
|---|---|---|
| **Sets `$PORT`, or lets you fix it at 8000** | The container binds whatever `$PORT` says | Nothing listens where the router expects; usually a 502 with a healthy-looking deploy |
| **Sends `SIGTERM` before killing** | SIGTERM is the flush — it drains the buffer | Buffered events are lost on every deploy and scale-down |
| **A shutdown grace period longer than your flush interval** | See below — this is the one people get wrong | Silent partial loss on shutdown |
| **Environment variables** | All configuration, including credentials | Nothing is configurable |
| **Egress to your object store** | The sink is remote | Collector refuses to start; the preflight probe catches it at boot |
| **A durable sink — not the container filesystem** | Almost every PaaS filesystem is ephemeral | Every redeploy silently erases collected events |

That's it. No database, no queue, no shared volume, no sidecar.

## The grace period is the part people get wrong

The collector holds events in memory for at most `MINUANO_FLUSH_MAX_SECONDS`, then writes them.
On shutdown it drains whatever it's holding. **Your flush interval must be comfortably shorter
than the platform's shutdown grace period**, or a scale-down kills the process mid-drain.

| Platform | Default grace | Notes |
|---|---|---|
| Railway | configurable — `stop_grace_period` | this repo's compose sets 20s |
| Cloud Run | ~10s | short; keep the flush interval well under it |
| Fly.io | configurable — `kill_timeout` | |
| ECS | configurable — `stopTimeout` | |
| Kubernetes | 30s — `terminationGracePeriodSeconds` | |

The default `MINUANO_FLUSH_MAX_SECONDS=5` is safe everywhere in that table. If you raise it to
batch more aggressively, raise the grace period with it — and remember that the interval is also
your **worst-case loss window** if the process is killed without a signal at all.

## Ephemeral filesystems

This is the failure that looks like nothing is wrong. Most PaaS filesystems are wiped on every
deploy, so the default `MINUANO_SINK_URI=file:///data` will collect events happily and lose them
at the next push, with no error anywhere.

Two ways out:

- **An object store** — `s3://`, `gs://` or `az://`. Recommended, and it's what makes two
  instances behind a load balancer work: every object name carries the instance id, so they
  cannot overwrite each other.
- **A persistent volume**, if your platform offers one (Railway volumes, Fly volumes, a
  Kubernetes PVC). Simpler, but it ties the collector to one machine, which gives up the
  statelessness the rest of the design is built on.

## Credentials

**minuano defines no credential variables of its own.** There is no `MINUANO_S3_KEY`. Each fsspec
backend resolves credentials through its own cloud's standard chain, which is what makes one
writer work across every host in this document — and it means the best configuration is usually
no configuration.

### Tier 1 — the platform can give the container an identity. Set nothing.

If your host can attach a cloud identity to a running container, use it and configure no
credentials at all. A key you never create cannot leak, expire, or need rotating.

| Where the container runs | Mechanism | Sink it authenticates |
|---|---|---|
| AWS ECS / Fargate | task role (`taskRoleArn`) | `s3://` |
| AWS EC2, or Docker on an EC2 host | instance profile | `s3://` |
| AWS EKS | IRSA, or EKS Pod Identity | `s3://` |
| Google Cloud Run | service identity (`--service-account`) | `gs://` |
| Google GKE | Workload Identity | `gs://` |
| Azure Container Apps / Container Instances | managed identity | `az://` |
| Azure AKS | Workload Identity | `az://` |
| Any of the above, cross-cloud | Workload Identity Federation | any |

This is what [`deploy-cloud-run.md`](deploy-cloud-run.md) uses, and why no key exists anywhere in
that deployment.

### Tier 2 — no identity service. Then, and only then, set variables.

Railway, Render, Fly.io, Koyeb, Heroku, a plain VM, `docker run` on your laptop — none of these can
mint a cloud identity, so the container needs actual credentials. Use the standard variables; the
SDKs already read them.

| Sink | Variables to set |
|---|---|
| `s3://` on AWS | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` (plus `AWS_SESSION_TOKEN` if they are temporary) |
| `s3://` on R2 / MinIO / Backblaze / Wasabi | the same three, plus `AWS_ENDPOINT_URL` pointing at the provider |
| `gs://` | `GOOGLE_APPLICATION_CREDENTIALS_JSON` — the whole key JSON as a string. See below |
| `gs://` with a mounted key file | `GOOGLE_APPLICATION_CREDENTIALS` — a path, as normal |
| `az://` | `AZURE_STORAGE_ACCOUNT_NAME` + `AZURE_STORAGE_ACCOUNT_KEY`, or `AZURE_STORAGE_CONNECTION_STRING`, or `AZURE_STORAGE_SAS_TOKEN` |
| `az://` via a service principal | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET` |

Scope the credential to *create objects in one prefix* and nothing else. Raw is append-only, so the
collector never needs read or delete — and on a public endpoint, a key that cannot delete is the
difference between vandalism and destruction. See error.md, TRAP-15.

### Why one cloud needs code and the other two do not

`collector/credentials.py` handles GCP and only GCP. That is not a preference, it is the shape of
the problem:

- **AWS and Azure take strings.** `boto3` and `azure-identity` read their credentials straight out
  of environment variables. A PaaS hands you environment variables. Nothing to bridge.
- **Google takes a path.** Application Default Credentials looks for
  `GOOGLE_APPLICATION_CREDENTIALS`, and that variable must contain a **filesystem path to a key
  file** — there is no supported way to hand Google's libraries the JSON inline. A PaaS gives you
  strings and no filesystem to put a key in, so this is the one combination with no native answer.

So the bridge is ~30 lines that do exactly one thing: if `GOOGLE_APPLICATION_CREDENTIALS_JSON`
holds the raw key, write it to a `0600` temp file at boot — permissions set *before* the write, not
after — and point the standard variable at it. The contents are never logged; the only thing echoed
is the `client_email`, which is an identifier you paste into an IAM grant, not a secret.

It is idempotent and deliberately loses to an explicit choice: if `GOOGLE_APPLICATION_CREDENTIALS`
already points at a real file, that wins and the bridge does nothing. Mounting a secret still works.

The reason it is not a general "credential provider" abstraction is that there is nothing to
abstract — two of the three clouds need zero code, and building a plugin layer for one case is the
kind of machinery this project's working agreement rules out until a second implementation actually
needs it.

> **Untested paths.** `gs://` is proved against a real bucket. **`s3://` and `az://` have never been
> written to**, so their credential handling is reasoned-about rather than exercised — see
> error.md, TRAP-7, which includes the one command that closes the gap for each.

## Verifying a deploy anywhere

```bash
curl -s https://<your-host>/health     # /healthz also works on most hosts — see the note
```

> The collector serves the same payload at `/healthz` and `/health`. Prefer **`/health`** for
> external checks: **Google Cloud Run reserves `/healthz`** and answers it from its own frontend
> with an HTML 404, so the request never reaches the container and never appears in the logs — a
> healthy service looks dead. Container-internal probes (Docker's `HEALTHCHECK`, Railway's) are
> unaffected because they dial `127.0.0.1`, below any platform frontend. See error.md, TRAP-14.

`{"status":"ok"}` means the process is up **and** the sink is writable — the collector probes it
at boot and refuses to start otherwise. `"status":"degraded"` with a `last_error` means events are
being accepted and held in memory because flushes are failing: nothing is lost yet, but fix it
before the process restarts.

Then post one event and confirm it lands in your sink within the flush interval. If it isn't in
`events/`, look in `bad/` before assuming it was dropped — the collector never drops anything.

## Platform-specific runbooks

- [Cloud Run + GCS](deploy-cloud-run.md) — the only host here where **no key exists at all**; the
  service gets its own identity. Tightest shutdown window of any common host (~10s), which matters
  because scale-to-zero makes shutdowns routine.
- [Railway + GCS](deploy-railway.md) — including the two traps that cost hours: `git push` deploys
  nothing unless the repo is connected in the Railway UI, and an unset **Target Port** produces a
  "successful" deploy that returns 502.

Contributions covering other platforms are welcome; the shape of that Railway document is a
reasonable template.

## What is deliberately not here

No Kubernetes manifests, Helm chart, ECS task definition, or Terraform. The container is the
interface, and everyone's deployment layer has house rules that a generic manifest gets wrong.
