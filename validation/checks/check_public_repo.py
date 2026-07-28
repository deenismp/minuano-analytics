#!/usr/bin/env python3
"""This repository is PUBLIC. Fail before anything private reaches it.

    uv run validation/checks/check_public_repo.py

Every rule below exists because the thing it checks for was actually committed to this repo and
had to be removed afterwards -- twice by rewriting published history. Removal after the fact is
not a fix: a force-push leaves the old objects fetchable by SHA, and GitHub will not garbage
collect them on request.

The rule this encodes: **a public repo carries what a user or a contributor needs, and nothing
else.** Working material -- the development record, generated artefacts, anything naming live
infrastructure -- stays on disk and gitignored. When in doubt, it does not go in.

This runs in CI, so it is the layer that does not depend on anyone remembering.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


# Files that are working material, not product. Each was tracked at some point and removed.
NEVER_TRACK = [
    ("error.md", "the trap register -- development record, not product"),
    ("docs/spec-increment-", "increment specs -- how a slice was scoped is working material"),
    ("docs/gtm-tag-inline.html", "generated, and stamped with a live endpoint URL"),
    ("refs/", "a reading list is not a deliverable"),
    # Never tracked, so this one is a guard rather than a post-mortem. Permission rules name the
    # real project, region and service; `settings.json` is the file a contributor would most
    # plausibly commit "to help the team", and it is the wrong tree for it.
    (".claude/", "Claude Code workspace config -- names live infrastructure, and is not product"),
]

# Credential-shaped names. The ignore files also cover these; this catches a `git add -f`.
CREDENTIAL_NAMES = re.compile(
    r"(^|/)("
    r"credentials.*\.json|client_secret.*\.json|.*service[_-]?account.*\.json|.*key\.json"
    r"|application_default_credentials\.json|.*secret.*\.json|\.env(\..*)?$"
    r"|.*\.(pem|p12|p8|key)|id_rsa.*|id_ed25519.*"
    r")$", re.I)

# Content that must never appear in a tracked file.
CONTENT_RULES = [
    # A real deployed hostname. Placeholders like `<your-service>.up.railway.app` are fine --
    # the `<` is what distinguishes documentation from disclosure.
    (re.compile(r"https://[a-z0-9][a-z0-9.\-]*\.(run\.app|up\.railway\.app|fly\.dev|onrender\.com)"),
     "a live deployment URL",
     "on an unauthenticated collector the URL is the credential; use a <placeholder>"),
    (re.compile(r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}"), "an AWS access key id", "rotate it now"),
    (re.compile(r"sk-ant-[A-Za-z0-9\-]{20,}|sk-[A-Za-z0-9]{32,}"), "an API key", "rotate it now"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"), "a GitHub token", "rotate it now"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key", "rotate it now"),
    (re.compile(r'"private_key"\s*:'), "a service-account key body", "rotate it now"),
    (re.compile(r"postgres(ql)?://[^\s:]+:[^\s@]+@|mongodb(\+srv)?://[^\s:]+:[^\s@]+@"),
     "a database URL with credentials", "rotate it now"),
    (re.compile(r"hooks\.slack\.com/services/|discord\.com/api/webhooks/"), "a webhook URL", "rotate it"),
    # Infrastructure identifiers. Not secret, but a public repo should read as a template --
    # and a bucket name plus a public write endpoint is more than either alone. The repo's own
    # name on github.com is excluded: it is unavoidable and discloses nothing.
    (re.compile(r"\bminuano-demo-raw\b|\b333209675368\b"
                r"|(?<!github\.com/deenismp/)\bminuano-analytics\b(?!/actions)"),
     "a real infrastructure identifier",
     "use $BUCKET / $PROJECT placeholders so the runbook reads as a template"),
]

SELF = "validation/checks/check_public_repo.py"


def main() -> int:
    files = tracked_files()
    print(f"{len(files)} tracked files\n")

    for needle, why in NEVER_TRACK:
        hits = [f for f in files if f.startswith(needle) or f == needle]
        check(not hits, f"not tracked: {needle}", ", ".join(hits) if hits else why)

    cred = [f for f in files if CREDENTIAL_NAMES.search(f)]
    check(not cred, "no credential-shaped filename is tracked",
          ", ".join(cred) if cred else "checked every tracked path")

    for pattern, what, advice in CONTENT_RULES:
        hits = []
        for rel in files:
            if rel == SELF:
                continue  # this file necessarily contains the patterns it looks for
            path = ROOT / rel
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                hits.append(f"{rel}:{line}")
        check(not hits, f"no tracked file contains {what}",
              f"{', '.join(hits[:6])}{' …' if len(hits) > 6 else ''} — {advice}" if hits else advice)

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        print("\nThis repository is public. Removing a file after it is pushed does NOT undo the\n"
              "disclosure -- the blob stays fetchable by SHA. Fix it before committing.")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
