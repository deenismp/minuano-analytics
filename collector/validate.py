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


def _looks_secret(key: Any) -> bool:
    """Match the pattern against a *normalised* key, so punctuation cannot smuggle one past.

    `x-api-key` is the most common on-the-wire spelling of the very thing `apikey` is trying to
    catch, and it used to sail straight through -- the raw pattern sees `api-key`, which is
    neither `apikey` nor `api_key`. Stripping non-alphanumerics first makes `x-api-key`,
    `API Key`, `api.key` and `X_API_KEY` all collapse onto a spelling the list already covers.
    """
    return bool(SECRET_KEY_PATTERNS.search(re.sub(r"[^a-z0-9]", "", str(key).lower())))


def _redact_in_place(node: Any) -> None:
    """Walk dicts and lists to any depth, replacing secret-shaped values."""
    if isinstance(node, dict):
        for key, value in node.items():
            if _looks_secret(key) and value not in (None, "", {}, []):
                node[key] = REDACTED
            else:
                _redact_in_place(value)
    elif isinstance(node, list):
        for item in node:
            _redact_in_place(item)


# Secret-shaped assignments inside a string we could not parse: `"token": "x"`, `apikey=x`.
# Deliberately greedy about key shape and conservative about value terminators.
_RAW_SECRET = re.compile(
    r"""(?P<key>[A-Za-z0-9_.\-]*"""
    r"""(?:token|apikey|api[_.\-]key|sessionid|session[_.\-]id|secret|password)"""
    r"""[A-Za-z0-9_.\-]*)"""
    r"""(?P<sep>"?\s*[:=]\s*"?)"""
    r"""(?P<value>[^"'&,}\s]+)""",
    re.I | re.X,
)


def redact_text(text: str) -> str:
    """Redact secret-shaped assignments in a string that is not parseable as an event.

    A body that fails to parse is still stored -- that is the non-lossy guarantee -- and a
    truncated beacon is exactly how a live key ends up in `bad/`. Storing it verbatim made the
    unparseable path the one route with no redaction at all.
    """
    return _RAW_SECRET.sub(lambda m: f"{m.group('key')}{m.group('sep')}{REDACTED}", text)


def redact(event: Any) -> Any:
    """Replace secret-shaped values in place. Replacement, never deletion.

    Deleting the key would hide the fact that the field was ever sent; anti-pattern #6 says
    redact by replacement so the context survives.

    Recurses, and accepts any payload shape rather than only a dict with a dict `params`.
    Both limits were real holes: `params.auth.token` was stored in plaintext because the walk
    stopped at the top level, and a payload that was a bare string or a nested list skipped
    redaction entirely because it never reached this function's dict branch.
    """
    if isinstance(event, str):
        return redact_text(event)
    if isinstance(event, dict):
        params = event.get("params")
        if params is not None:
            _redact_in_place(params)
        return event
    _redact_in_place(event)
    return event
