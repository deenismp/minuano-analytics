"""Make cloud credentials available where there is no filesystem to put them on.

Railway, Fly, Cloud Run and friends hand you environment variables, not files. Google's client
libraries want a file path. This bridges the two: if `GOOGLE_APPLICATION_CREDENTIALS_JSON` holds
the raw key, write it to a private temp file once at boot and point
`GOOGLE_APPLICATION_CREDENTIALS` at it.

AWS and Azure need none of this -- boto3 and azure-identity read environment variables directly.

The key's contents are never logged, never echoed, and the file is created 0600 before anything
is written to it.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile

ENV_JSON = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
ENV_PATH = "GOOGLE_APPLICATION_CREDENTIALS"


def bootstrap() -> str | None:
    """Return a short, non-sensitive description of what was set up, or None if nothing was.

    Idempotent: an already-valid `GOOGLE_APPLICATION_CREDENTIALS` path wins, so mounting a key
    file still works and this never overwrites a deliberate choice.
    """
    existing = os.getenv(ENV_PATH)
    if existing and os.path.exists(existing):
        return "credentials file already provided"

    raw = os.getenv(ENV_JSON, "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Say that it is malformed. Do NOT include the value in the message.
        raise ValueError(f"{ENV_JSON} is set but is not valid JSON ({exc.msg})") from None

    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    os.chmod(handle.name, stat.S_IRUSR | stat.S_IWUSR)  # 0600 before the write, not after
    with handle:
        json.dump(parsed, handle)

    os.environ[ENV_PATH] = handle.name
    # The client email is an identifier, not a secret -- it is what you paste into an IAM grant.
    # Nothing else from the key is surfaced.
    return f"credentials written from {ENV_JSON} for {parsed.get('client_email', 'unknown principal')}"
