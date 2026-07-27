"""Configuration. Environment variables only -- the container is stateless.

Every setting has a working default so `uvicorn collector.app:app` runs with no environment
at all, which is the local dev path.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Config:
    # Where raw events land. `local` is the dev path; `s3` is the deployed one.
    sink: str = field(default_factory=lambda: os.getenv("MINUANO_SINK", "local"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("MINUANO_DATA_DIR", "./data")))
    s3_bucket: str = field(default_factory=lambda: os.getenv("MINUANO_S3_BUCKET", ""))
    s3_prefix: str = field(default_factory=lambda: os.getenv("MINUANO_S3_PREFIX", "raw"))

    # Buffering. Flush when either bound is hit, and always on shutdown.
    flush_max_events: int = field(default_factory=lambda: _env_int("MINUANO_FLUSH_MAX_EVENTS", 100))
    flush_max_seconds: float = field(default_factory=lambda: float(_env_int("MINUANO_FLUSH_MAX_SECONDS", 5)))

    # A body larger than this is refused with 413 -- the one and only non-2xx response.
    max_body_bytes: int = field(default_factory=lambda: _env_int("MINUANO_MAX_BODY_BYTES", 1_048_576))

    # Browsers post cross-origin from the customer's domain. Server-side relays do not need this.
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o.strip() for o in os.getenv("MINUANO_CORS_ORIGINS", "*").split(",") if o.strip())
    )

    # Makes output filenames unique per running container, so two instances writing to the
    # same prefix can never overwrite each other.
    instance_id: str = field(default_factory=lambda: os.getenv("MINUANO_INSTANCE_ID") or uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        if self.sink not in ("local", "s3"):
            raise ValueError(f"MINUANO_SINK={self.sink!r} must be 'local' or 's3'")
        # Fail at boot, not at the first flush: a sink that only reveals itself as
        # misconfigured on flush has already buffered -- and then loses -- real events.
        if self.sink == "s3" and not self.s3_bucket:
            raise ValueError("MINUANO_SINK=s3 requires MINUANO_S3_BUCKET")


def load() -> Config:
    return Config()
