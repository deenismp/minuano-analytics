"""Buffered NDJSON writers.

Layout, identical on both sinks:

    <root>/<stream>/dt=YYYY-MM-DD/<instance_id>-<seq>.ndjson

`stream` is `events` or `bad`. `dt` is the UTC date of `ingested_at`, **not** of
`event_timestamp` -- the collector must never reorganise a closed partition, and a client
clock skewed by hours would otherwise write into a past day. Downstream jobs pad +/-1 day
(ingestion-patterns, Boundary-File Padding).

Every flush writes a *new* object rather than appending to an existing one, and `instance_id`
is in the name, so two containers sharing a prefix cannot collide.

The two sinks differ only in `_put`. That is the whole reason there is a base class here --
the second implementation exists, so the abstraction has earned itself.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path


class BufferedNDJSONWriter:
    def __init__(self, instance_id: str, flush_max_events: int, flush_max_seconds: float) -> None:
        self._instance_id = instance_id
        self._flush_max_events = flush_max_events
        self._flush_max_seconds = flush_max_seconds
        self._buffer: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._seq = 0
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task | None = None

    # --- subclass contract ---------------------------------------------------------------
    def _put(self, stream: str, dt: str, seq: int, body: str) -> str:
        """Persist one batch. Returns a human-readable location for the log line."""
        raise NotImplementedError

    def _key(self, stream: str, dt: str, seq: int) -> str:
        return f"{stream}/dt={dt}/{self._instance_id}-{seq:06d}.ndjson"

    # --- buffering -----------------------------------------------------------------------
    @property
    def buffered(self) -> int:
        return sum(len(lines) for lines in self._buffer.values())

    async def append(self, stream: str, dt: str, record: dict) -> None:
        async with self._lock:
            self._buffer[(stream, dt)].append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            over_limit = sum(len(v) for v in self._buffer.values()) >= self._flush_max_events
        if over_limit:
            await self.flush()

    async def flush(self) -> list[str]:
        """Write every buffered partition as its own object. Returns the locations written."""
        async with self._lock:
            if not self._buffer:
                return []
            pending, self._buffer = self._buffer, defaultdict(list)
            start_seq = self._seq
            self._seq += len(pending)

        written = []
        for offset, ((stream, dt), lines) in enumerate(sorted(pending.items())):
            body = "\n".join(lines) + "\n"
            written.append(self._put(stream, dt, start_seq + offset, body))
        return written

    async def start(self) -> None:
        """Flush on an interval as well as on a count threshold."""

        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._flush_max_seconds)
                await self.flush()

        self._flusher = asyncio.create_task(_loop())

    async def stop(self) -> list[str]:
        """Cancel the interval flusher and drain the buffer. This is the SIGTERM path."""
        if self._flusher is not None:
            self._flusher.cancel()
            try:
                await self._flusher
            except asyncio.CancelledError:
                pass
            self._flusher = None
        return await self.flush()


class LocalNDJSONWriter(BufferedNDJSONWriter):
    def __init__(self, data_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._data_dir = Path(data_dir)

    def _put(self, stream: str, dt: str, seq: int, body: str) -> str:
        final = self._data_dir / self._key(stream, dt, seq)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.with_suffix(".ndjson.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, final)  # atomic: a reader never sees a partial file
        return str(final)


class S3NDJSONWriter(BufferedNDJSONWriter):
    """One `put_object` per flush. A batch is bounded by the flush threshold and is
    kilobytes, so multipart would be complexity without a payload to justify it.

    `client` is injectable so the writer can be proved without AWS credentials and without a
    test-only dependency.
    """

    def __init__(self, bucket: str, prefix: str = "", client=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        if client is None:
            import boto3  # imported lazily so the local path needs no AWS SDK at runtime

            client = boto3.client("s3")
        self._client = client

    def _put(self, stream: str, dt: str, seq: int, body: str) -> str:
        key = f"{self._prefix}/{self._key(stream, dt, seq)}" if self._prefix else self._key(stream, dt, seq)
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        return f"s3://{self._bucket}/{key}"


def make_writer(cfg, client=None) -> BufferedNDJSONWriter:
    common = {
        "instance_id": cfg.instance_id,
        "flush_max_events": cfg.flush_max_events,
        "flush_max_seconds": cfg.flush_max_seconds,
    }
    if cfg.sink == "s3":
        return S3NDJSONWriter(bucket=cfg.s3_bucket, prefix=cfg.s3_prefix, client=client, **common)
    return LocalNDJSONWriter(data_dir=cfg.data_dir, **common)
