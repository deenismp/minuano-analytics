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

Set the standard variables your cloud's SDK already reads — `AWS_ACCESS_KEY_ID`,
`AZURE_STORAGE_*`, and so on. Nothing minuano-specific.

**GCP is the exception**, because Google's libraries want a file path and a PaaS gives you
strings. Put the whole key JSON in `GOOGLE_APPLICATION_CREDENTIALS_JSON` and the collector writes
it to a `0600` temp file at boot, never logging it.

If your platform *can* give the workload a cloud identity — Cloud Run service accounts, EKS/GKE
workload identity, ECS task roles — use that instead and set no credentials at all. A key you
never create is a key that cannot leak.

## Verifying a deploy anywhere

```bash
curl -s https://<your-host>/healthz
```

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
