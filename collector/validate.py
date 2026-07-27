"""Schema validation for collected events.

The schema file is the contract. It is loaded from disk rather than mirrored into Python
classes, so there is exactly one definition of an event and it is the one that ships.

Validation never rejects an event. Callers use the returned error list to decide which
directory the event lands in -- `data/events/` or `data/bad/` -- and both are written.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "event.v0.json"

# Suffix-anchored, so a new upstream field like `magiclink_token` is caught without anyone
# remembering to add it. An exact-name allowlist is what leaked 51K plaintext tokens in a
# previous project -- see error.md TRAP-4.
SECRET_KEY_PATTERNS = re.compile(r"(token$|apikey|api_key|sessionid|session_id$|secret|password)", re.I)
REDACTED = "<REDACTED>"

# Derived downstream from the raw event stream, so custom events must not claim them.
RESERVED_EVENT_NAMES = frozenset({"page_view", "session_start", "first_visit"})

_format_checker = FormatChecker()


@_format_checker.checks("date-time", raises=ValueError)
def _is_rfc3339(value: Any) -> bool:
    """Check `date-time` with the standard library instead of taking a dependency.

    jsonschema treats `format` as an annotation unless a checker is registered, and its
    built-in date-time checker needs `rfc3339-validator`. Python 3.11+ `fromisoformat`
    accepts the trailing `Z`, which covers what browsers actually emit.
    """
    if not isinstance(value, str):
        return True
    datetime.fromisoformat(value)
    return True


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=_format_checker)


def validate(event: Any) -> list[str]:
    """Return a list of human-readable validation errors. Empty list means valid."""
    if not isinstance(event, dict):
        return [f"event must be a JSON object, got {type(event).__name__}"]
    errors = []
    for err in sorted(_validator().iter_errors(event), key=lambda e: list(e.path)):
        location = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{location}: {err.message}")
    return errors


def redact(event: Any) -> Any:
    """Replace secret-shaped `params` values in place. Replacement, never deletion.

    Deleting the key would hide the fact that the field was ever sent; anti-pattern #6 says
    redact by replacement so the context survives.
    """
    if not isinstance(event, dict):
        return event
    params = event.get("params")
    if not isinstance(params, dict):
        return event
    for key in list(params):
        if SECRET_KEY_PATTERNS.search(str(key)) and params[key] not in (None, ""):
            params[key] = REDACTED
    return event
