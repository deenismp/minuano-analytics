"""Make cloud credentials available where there is no filesystem to put them on.

Railway, Fly, Render and friends hand you environment variables, not files. Google's client
libraries want a file path. This bridges the two: if `GOOGLE_APPLICATION_CREDENTIALS_JSON` holds
the raw key, write it to a private temp file once at boot and point
`GOOGLE_APPLICATION_CREDENTIALS` at it.

AWS and Azure need none of this -- boto3 and azure-identity read environment variables directly.
That is the whole reason this module is GCP-only: it is not a preference, it is that Google's
Application Default Credentials takes a *path* while the other two take strings.

Prefer a workload identity over any of this. On Cloud Run, GKE, ECS, EKS or AKS the container can
be given its own identity and this module does nothing at all -- which is the best outcome,
because a key that is never created cannot leak, expire, or need rotating.

The key's contents are never logged, never echoed, and never included in an error message. The
file is created 0600 before anything is written to it, and removed at exit.
"""

from __future__ import annotations

import atexit
import json
import os
import stat
import tempfile

ENV_JSON = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
ENV_PATH = "GOOGLE_APPLICATION_CREDENTIALS"

# Enough to tell a service-account key from some other valid JSON. `private_key` is deliberately
# NOT read -- only its presence is tested, and it is never bound to a name.
REQUIRED_KEYS = ("client_email", "private_key")


def _looks_like_service_account(parsed: object) -> bool:
    return isinstance(parsed, dict) and all(k in parsed for k in REQUIRED_KEYS)


def bootstrap() -> str | None:
    """Return a short, non-sensitive description of what was set up, or None if nothing was.

    Idempotent: an already-usable `GOOGLE_APPLICATION_CREDENTIALS` path wins, so mounting a key
    file still works and this never overwrites a deliberate choice.
    """
    existing = os.getenv(ENV_PATH)
    # `exists()` alone was not enough: a directory, or a failed secret mount that left an empty
    # stub, would win over a perfectly good ENV_JSON and then surface as an opaque auth error
    # from deep inside gcsfs, hundreds of lines from the actual cause.
    if existing and os.path.isfile(existing) and os.path.getsize(existing) > 0:
        return "credentials file already provided"

    raw = os.getenv(ENV_JSON, "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Say that it is malformed and where. Do NOT include the value: `exc.msg` is a description
        # like "Expecting ',' delimiter" and is safe, but `exc.doc` holds the whole key, so the
        # chained exception is suppressed with `from None` to keep it out of the traceback.
        raise ValueError(f"{ENV_JSON} is set but is not valid JSON ({exc.msg})") from None

    # Validate BEFORE creating the file. Otherwise a double-encoded value -- a JSON *string*
    # rather than an object, which is the most common paste mistake on a PaaS -- would write a
    # useless credential file, point ENV_PATH at it, and only then fail with a bare AttributeError
    # that never names the variable at fault.
    if not _looks_like_service_account(parsed):
        raise ValueError(
            f"{ENV_JSON} is valid JSON but is not a service-account key object "
            f"(expected an object containing {' and '.join(REQUIRED_KEYS)}). "
            "A common cause is pasting the key double-encoded, as a JSON string."
        )

    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    with handle:
        # fchmod on the open descriptor, not chmod on the path: no window in which the name could
        # be swapped. NamedTemporaryFile already creates at 0600 via mkstemp, so this is
        # belt-and-braces -- but it is the same line count as the version that is not.
        os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR)
        json.dump(parsed, handle)

    os.environ[ENV_PATH] = handle.name

    # Bound the answer to "how many copies of my key are on this disk". In a container this is
    # academic -- one file, ephemeral filesystem -- but `check_cloud_sink.py` calls bootstrap() in
    # the parent process on every run, so on a developer laptop the copies otherwise accumulate in
    # $TMPDIR with no lifecycle. Will not fire on SIGTERM (uvicorn re-raises it), which is fine:
    # containers are ephemeral, and the check script exits normally.
    atexit.register(_cleanup, handle.name)

    # The client email is an identifier, not a secret -- it is what you paste into an IAM grant.
    # Nothing else from the key is surfaced, and `private_key` is never bound to a variable.
    return f"credentials written from {ENV_JSON} for {parsed.get('client_email', 'unknown principal')}"


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
