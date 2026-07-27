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
    # Where raw events land. `local` is the only sink in increment 1; the S3 writer arrives
    # in increment 2 and will be selected here.
    sink: str = field(default_factory=lambda: os.getenv("MINUANO_SINK", "local"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("MINUANO_DATA_DIR", "./data")))

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
        if self.sink != "local":
            raise ValueError(f"MINUANO_SINK={self.sink!r} is not supported yet; increment 1 is local-only")


def load() -> Config:
    return Config()
