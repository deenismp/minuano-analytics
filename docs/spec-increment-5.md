# Spec — minuano increment 5: make it contributable

**Status:** built 2026-07-27 — awaiting the first CI run to confirm
**Created:** 2026-07-27
**Revision history:** v1.0

---

## Goal

A stranger can read the repo, form an opinion, and open a pull request that verifies itself.

Today every check runs on one laptop. That is the thing blocking contribution: a PR from someone
else would have to be validated by hand, on macOS, by the maintainer.

## Non-goals

No release automation, no publishing to PyPI, no coverage gates, no linters added as blocking
checks. Those are worth having when there are contributors; adding them before there are any is
ceremony.

---

## Steps

### Step 1 — CI that runs everything, on Linux

- [x] `.github/workflows/ci.yml` — every suite on every push and pull request
- [x] Ubuntu x86 runners, which is **not** the platform any of this has been proved on
- [x] Two jobs: the fast suites, and the docker one that needs a daemon
- [x] The snippet suite needs Node; the container suite needs Docker. Both are on the runner

**Done when:** all six suites pass in CI on Linux, and a failing check fails the run.
**Evidence:** a green run on the default branch, linked from the README badge.
**Agent:** main-thread.

> This closes a documented gap. `validation/README.md` says the container is *"proved on one
> platform — macOS, Docker Desktop, arm64"* and specifically flags that a non-root user writing to
> a bind mount behaves differently on Linux, where uid 10001 may not own the host directory. CI is
> the first time that runs anywhere else, so it is a real test, not a formality.

### Step 2 — The files a contributor looks for

- [x] `CONTRIBUTING.md` — how to run it, how to test it, what review will ask about
- [x] `CODE_OF_CONDUCT.md`
- [x] `.github/ISSUE_TEMPLATE/` — bug report and feature request
- [x] `.github/pull_request_template.md` — including "which check proves this"
- [x] README badge

**Done when:** the templates render on GitHub and `CONTRIBUTING.md` describes a path that works
from a clean clone.
**Agent:** main-thread.

---

## Validation contract

| Check | Rule | Bar |
|---|---|---|
| CI passes | all six suites green on ubuntu-latest | FAIL |
| CI fails loudly | a deliberately broken check fails the workflow | FAIL |
| Clean clone | the CONTRIBUTING path works with only Docker, or only uv + node | FAIL |
| Templates | issue and PR templates render on GitHub | WARN |

---

## Decisions

| Decision | Why | Alternatives rejected |
|---|---|---|
| CI runs the existing suites, no new test framework | There are already 110 checks that assert on real output. Wrapping them in pytest would add a dependency and change nothing about what is verified. | Porting to pytest: churn, a new dependency, same assertions. |
| Two jobs, not one | The docker job is minutes; the rest are seconds. Splitting means a contributor sees the fast feedback first. | One job: every contributor waits for the slowest thing. |
| No linter as a blocking check | Nobody has contributed yet. A style gate before a first contributor is a barrier with no upside. | Adding ruff/black as required: ceremony ahead of need. |
| No coverage gate | These checks assert on output, not lines. A coverage number would measure the wrong thing and invite gaming. | Codecov: a number that goes up while proving less. |
