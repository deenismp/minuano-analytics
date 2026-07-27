"""Buffered NDJSON writer.

Layout, identical on every backend:

    <sink_uri>/<stream>/dt=YYYY-MM-DD/<instance_id>-<seq>.ndjson

`stream` is `events` or `bad`. `dt` is the UTC date of `ingested_at`, **not** of
`event_timestamp` -- the collector must never reorganise a closed partition, and a client clock
skewed by hours would otherwise write into a past day. Downstream jobs pad +/-1 day
(ingestion-patterns, Boundary-File Padding).

Every flush writes a *new* object rather than appending to an existing one, and `instance_id` is
in the name, so two containers sharing a prefix cannot collide.

There is one writer. fsspec resolves `file://`, `s3://`, `gs://`, `az://` and `memory://` to the
same interface, so local disk and three clouds are one code path -- the abstraction is fsspec's,
not ours. The only backend-specific behaviour left is the temp-file-then-rename on local disk,
which object stores do not need because a PUT is already atomic.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict

import fsspec


class BufferedNDJSONWriter:
    """Buffers events in memory and writes each partition as one object per flush."""

    def __init__(self, sink_uri: str, instance_id: str, flush_max_events: int, flush_max_seconds: float) -> None:
        self._instance_id = instance_id
        self._flush_max_events = flush_max_events
        self._flush_max_seconds = flush_max_seconds
        self._buffer: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._seq = 0
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task | None = None

        # Resolved once, at construction, so a broken destination fails at boot.
        self._fs, self._root = fsspec.core.url_to_fs(sink_uri)
        self._root = self._root.rstrip("/")
        protocols = self._fs.protocol if isinstance(self._fs.protocol, tuple) else (self._fs.protocol,)
        self._is_local = "file" in protocols or "local" in protocols

    @property
    def location(self) -> str:
        return f"{self._fs.protocol if isinstance(self._fs.protocol, str) else self._fs.protocol[0]}://{self._root}"

    @property
    def buffered(self) -> int:
        return sum(len(lines) for lines in self._buffer.values())

    def _key(self, stream: str, dt: str, seq: int) -> str:
        return f"{stream}/dt={dt}/{self._instance_id}-{seq:06d}.ndjson"

    def _put(self, stream: str, dt: str, seq: int, body: str) -> str:
        path = f"{self._root}/{self._key(stream, dt, seq)}"
        payload = body.encode("utf-8")

        if self._is_local:
            # Write beside the target and rename, so a reader never sees a partial file.
            # Object stores get this for free: a PUT is atomic and only appears when complete.
            self._fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
            tmp = f"{path}.tmp"
            with self._fs.open(tmp, "wb") as handle:
                handle.write(payload)
            self._fs.mv(tmp, path)
        else:
            with self._fs.open(path, "wb") as handle:
                handle.write(payload)
        return path

    async def append(self, stream: str, dt: str, record: dict) -> None:
        async with self._lock:
            self._buffer[(stream, dt)].append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            over_limit = sum(len(v) for v in self._buffer.values()) >= self._flush_max_events
        if over_limit:
            await self.flush()

    async def flush(self) -> list[str]:
        """Write every buffered partition as its own object. Returns the paths written."""
        async with self._lock:
            if not self._buffer:
                return []
            pending, self._buffer = self._buffer, defaultdict(list)
            start_seq = self._seq
            self._seq += len(pending)

        written = []
        for offset, ((stream, dt), lines) in enumerate(sorted(pending.items())):
            body = "\n".join(lines) + "\n"
            # fsspec's filesystems are synchronous; keep the event loop free while one PUT runs.
            written.append(await asyncio.to_thread(self._put, stream, dt, start_seq + offset, body))
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


def make_writer(cfg) -> BufferedNDJSONWriter:
    return BufferedNDJSONWriter(
        sink_uri=cfg.sink_uri,
        instance_id=cfg.instance_id,
        flush_max_events=cfg.flush_max_events,
        flush_max_seconds=cfg.flush_max_seconds,
    )
