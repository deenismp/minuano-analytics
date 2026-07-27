"""Buffered NDJSON writer.

Layout:

    <data_dir>/<stream>/dt=YYYY-MM-DD/<instance_id>-<seq>.ndjson

`stream` is `events` or `bad`. `dt` is the UTC date of `ingested_at`, **not** of
`event_timestamp` -- the collector must never reorganise a closed partition, and a client
clock skewed by hours would otherwise write into a past day. Downstream jobs pad +/-1 day
(ingestion-patterns, Boundary-File Padding).

Every flush creates a *new* file rather than appending to an existing one, and the file is
written to `.tmp` then renamed. A reader therefore never sees a partial file, and two
instances sharing a prefix cannot collide because `instance_id` is in the name.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path


class LocalNDJSONWriter:
    def __init__(self, data_dir: Path, instance_id: str, flush_max_events: int, flush_max_seconds: float) -> None:
        self._data_dir = Path(data_dir)
        self._instance_id = instance_id
        self._flush_max_events = flush_max_events
        self._flush_max_seconds = flush_max_seconds
        self._buffer: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._seq = 0
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task | None = None

    @property
    def buffered(self) -> int:
        return sum(len(lines) for lines in self._buffer.values())

    async def append(self, stream: str, dt: str, record: dict) -> None:
        async with self._lock:
            self._buffer[(stream, dt)].append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            over_limit = sum(len(v) for v in self._buffer.values()) >= self._flush_max_events
        if over_limit:
            await self.flush()

    async def flush(self) -> list[Path]:
        """Write every buffered partition to its own file. Returns the paths written."""
        async with self._lock:
            if not self._buffer:
                return []
            pending, self._buffer = self._buffer, defaultdict(list)
            start_seq = self._seq
            self._seq += len(pending)

        written = []
        for offset, ((stream, dt), lines) in enumerate(sorted(pending.items())):
            directory = self._data_dir / stream / f"dt={dt}"
            directory.mkdir(parents=True, exist_ok=True)
            final = directory / f"{self._instance_id}-{start_seq + offset:06d}.ndjson"
            tmp = final.with_suffix(".ndjson.tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, final)  # atomic: a reader never sees a partial file
            written.append(final)
        return written

    async def start(self) -> None:
        """Flush buffered events on an interval as well as on a count threshold."""

        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._flush_max_seconds)
                await self.flush()

        self._flusher = asyncio.create_task(_loop())

    async def stop(self) -> list[Path]:
        """Cancel the interval flusher and drain the buffer. Called on SIGTERM."""
        if self._flusher is not None:
            self._flusher.cancel()
            try:
                await self._flusher
            except asyncio.CancelledError:
                pass
            self._flusher = None
        return await self.flush()
