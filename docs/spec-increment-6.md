# Spec — minuano increment 6: deployable

**Status:** complete — verified 2026-07-27
**Created:** 2026-07-27
**Revision history:** v1.0

---

## Goal

The container runs on a PaaS that assigns its own port and provides credentials as environment
variables rather than files. Concretely: Railway, writing to the GCS bucket that increment 4's
sink already proved.

## Non-goals

No ECS/Kubernetes/Helm manifests — still out of scope. No autoscaling policy, no CDN, no custom
domain. The monitoring guardrails are documented as commands to run, not built as code.

---

## Steps

### Step 1 — Bind the port the platform assigns

- [x] `CMD` reads `$PORT`, defaulting to 8000
- [x] uvicorn stays PID 1 so SIGTERM still drains the buffer

**Done when:** `PORT=9001 docker run` serves on 9001, and `docker compose stop` still drains.
**Evidence:** `check_container.py`, which already asserts the drain, run with a non-default port.
**Agent:** main-thread.

> This is a real bug for any PaaS, not a Railway quirk. `fastmcp-deploy-on-railway.md` names it:
> *"Hardcode port 8000 → Read `os.environ.get("PORT", "8000")` first; Railway injects."* Its
> companion pitfall is that skipping **Target Port** in Networking means the deploy "succeeds" and
> the URL returns 502.

### Step 2 — Credentials from the environment, not the filesystem

- [x] `GOOGLE_APPLICATION_CREDENTIALS_JSON` holding the raw key is written to a private temp file
      at boot, and `GOOGLE_APPLICATION_CREDENTIALS` is pointed at it
- [x] The key's contents are never logged, and the file is `0600`
- [x] AWS and Azure need nothing: their SDKs already read environment variables

**Done when:** the collector reaches a `gs://` bucket with only an environment variable set — no
key on disk.
**Evidence:** `check_cloud_sink.py` run with the key supplied as JSON in the environment.
**Agent:** main-thread.

### Step 3 — `railway.toml` and a deploy runbook

- [x] `railway.toml` — Dockerfile builder, `ON_FAILURE` restart with 3 retries, `/healthz`
- [x] `docs/deploy-railway.md` — the git-connected trap, the Target Port step, the variables to
      set, and the three cost guardrails

**Done when:** someone with the repo and a Railway account can follow it end to end.
**Agent:** main-thread.

---

## Validation contract

| Check | Rule | Bar |
|---|---|---|
| Port | the container serves on `$PORT` when set, 8000 when not | FAIL |
| Signals | SIGTERM still drains after the CMD change | FAIL |
| Credentials from env | a `gs://` sink works with no key file on disk | FAIL |
| No leakage | the key JSON appears in no log line, and the temp file is `0600` | FAIL |

---

## Decisions

| Decision | Why | Alternatives rejected |
|---|---|---|
| `sh -c exec uvicorn …` rather than an entrypoint script | `exec` replaces the shell, so uvicorn is still PID 1 and receives SIGTERM directly — which is what drains the buffer. A wrapper script that forgets `exec` silently breaks the flush, and the container would still look healthy. | A shell entrypoint without `exec`: the shell becomes PID 1, uvicorn never sees the signal. |
| Key material via `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Railway has no filesystem to mount a key into. Writing it to a `0600` temp file at boot is the same pattern already used in `pingu-chat`, so it is one shape to remember rather than two. | Baking the key into the image: it ends up in a public registry layer. A build `ARG`: Railway echoes expanded `RUN` commands into the plaintext build log — that is how a PAT leaked once already. |
| Monitoring documented, not coded | Budget alerts, request-count alerts and billing export are account configuration. Code in this repo cannot create them for someone else's project, and pretending otherwise would produce a script nobody can run. | A setup script: only works for one account and rots. |
