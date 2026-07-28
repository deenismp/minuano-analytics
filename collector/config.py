"""Configuration. Environment variables only -- the container is stateless.

Every setting has a working default, so `uvicorn collector.app:app` runs with no environment at
all and writes to ./data. That is the local dev path.

Storage is one knob. `MINUANO_SINK_URI` says where events go, and its scheme says which cloud:

    file://./data                 local disk (default)
    s3://bucket/raw               AWS S3            -- needs the `aws` extra
    gs://bucket/raw               Google Cloud       -- needs the `gcp` extra
    az://container/raw            Azure Blob / ADLS  -- needs the `azure` extra
    memory://test                 in-process, used by the validation harness

Credentials are never configured here. Each fsspec backend resolves them through its own cloud's
chain -- instance roles, workload identity, managed identity -- which is why we do not hand-roll
a set of MINUANO_*_KEY variables that would only encourage long-lived static keys.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Scheme -> the extra that provides it. Used to turn "backend missing" into an instruction.
BACKEND_EXTRAS = {
    "s3": "aws",
    "s3a": "aws",
    "gs": "gcp",
    "gcs": "gcp",
    "az": "azure",
    "abfs": "azure",
    "abfss": "azure",
    "adl": "azure",
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    """Separate from `_env_int` because a value like `0.5` is legitimate here and `int()` is not.

    `flush_max_seconds` was typed float, documented in seconds, and parsed with `int()` wrapped in
    `float()` -- so `MINUANO_FLUSH_MAX_SECONDS=0.5` died with `invalid literal for int()`. That
    error surfaced from `CFG = config.load()` at module import, before any logging exists, so the
    operator saw a bare traceback naming `int` for a setting nothing describes as an integer.
    This is the knob most likely to be tuned, because it is the one that governs output file size.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Config:
    # Where raw events land. One URI, any backend. See the module docstring.
    sink_uri: str = field(default_factory=lambda: os.getenv("MINUANO_SINK_URI", "file://./data"))

    # Buffering. Flush when either bound is hit, and always on shutdown.
    flush_max_events: int = field(default_factory=lambda: _env_int("MINUANO_FLUSH_MAX_EVENTS", 100))
    flush_max_seconds: float = field(default_factory=lambda: _env_float("MINUANO_FLUSH_MAX_SECONDS", 5.0))

    # A body larger than this is refused with 413 -- the one and only non-2xx response.
    max_body_bytes: int = field(default_factory=lambda: _env_int("MINUANO_MAX_BODY_BYTES", 1_048_576))

    # Browsers post cross-origin from the customer's domain. Server-side relays do not need this.
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o.strip() for o in os.getenv("MINUANO_CORS_ORIGINS", "*").split(",") if o.strip())
    )

    # Makes output object names unique per running container, so two instances writing to the
    # same prefix can never overwrite each other.
    instance_id: str = field(default_factory=lambda: os.getenv("MINUANO_INSTANCE_ID") or uuid.uuid4().hex[:12])

    @property
    def scheme(self) -> str:
        return urlsplit(self.sink_uri).scheme or "file"

    def __post_init__(self) -> None:
        # Fail at boot, not at the first flush. A sink that only reveals itself as unusable on
        # flush has already buffered -- and then loses -- real events.
        import fsspec

        try:
            fsspec.get_filesystem_class(self.scheme)
        except ImportError as exc:
            extra = BACKEND_EXTRAS.get(self.scheme)
            hint = f"install it with: pip install 'minuano-collector[{extra}]'" if extra else str(exc)
            raise ValueError(
                f"MINUANO_SINK_URI={self.sink_uri!r} needs the {self.scheme!r} backend, which is not installed. {hint}"
            ) from exc
        except ValueError as exc:
            raise ValueError(f"MINUANO_SINK_URI={self.sink_uri!r} has an unknown scheme {self.scheme!r}") from exc


def load() -> Config:
    return Config()
