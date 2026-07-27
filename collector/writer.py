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

        # Set when a flush fails, so the failure is visible on /healthz instead of only in a log
        # line someone has to be watching for.
        self.last_error: str | None = None

    @property
    def location(self) -> str:
        return f"{self._fs.protocol if isinstance(self._fs.protocol, str) else self._fs.protocol[0]}://{self._root}"

    @property
    def buffered(self) -> int:
        return sum(len(lines) for lines in self._buffer.values())

    def preflight(self) -> None:
        """Prove the sink is writable before accepting a single event.

        Without this, an unwritable sink is discovered at the first flush -- by which time the
        collector has already answered 2xx to real traffic that it is about to lose. The most
        common cause is the boring one: on Linux a bind-mounted host directory is owned by the
        host user, and this container deliberately runs as an unprivileged uid.

        Only the *write* is asserted. Removing the probe afterwards is best-effort on purpose --
        see the note below.
        """
        probe = f"{self._root}/.minuano-writable-{self._instance_id}"
        try:
            if self._is_local:
                self._fs.makedirs(self._root, exist_ok=True)
            with self._fs.open(probe, "wb") as handle:
                handle.write(b"ok\n")
        except Exception as exc:
            raise RuntimeError(
                f"sink {self.location} is not writable: {type(exc).__name__}: {exc}\n"
                "If this is a bind mount on Linux, the container's uid does not own the host "
                "directory. Run with: MINUANO_UID=$(id -u) MINUANO_GID=$(id -g) docker compose up"
            ) from exc

        # Cleanup is deliberately not part of the assertion. Raw is append-only, so the
        # collector's identity should not be *able* to delete -- on GCS the least-privilege role
        # for this workload, roles/storage.objectCreator, grants create without delete, and a
        # required delete here would force every deployment to widen that to objectAdmin. That
        # would hand a public, unauthenticated endpoint the power to erase the raw store, which
        # is a far worse trade than the object left behind.
        #
        # The probe key carries the instance id, so it is never overwritten and never collides;
        # a bucket lifecycle rule reclaims it. It sits beside the `events/` and `bad/` prefixes,
        # not inside them, so no downstream glob ever sees it.
        try:
            self._fs.rm(probe)
        except Exception:
            pass

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
        """Write every buffered partition as its own object. Returns the paths written.

        A batch that fails to write goes **back** into the buffer rather than being dropped, so a
        transient object-store error costs a retry instead of the events. Taking the buffer and
        then losing it on an exception is the same silent-loss failure the collector's never-reject
        rule exists to prevent.
        """
        async with self._lock:
            if not self._buffer:
                return []
            pending, self._buffer = self._buffer, defaultdict(list)
            start_seq = self._seq
            self._seq += len(pending)

        written: list[str] = []
        failed: dict[tuple[str, str], list[str]] = {}
        for offset, ((stream, dt), lines) in enumerate(sorted(pending.items())):
            body = "\n".join(lines) + "\n"
            try:
                # fsspec's filesystems are synchronous; keep the event loop free during a PUT.
                written.append(await asyncio.to_thread(self._put, stream, dt, start_seq + offset, body))
            except Exception as exc:
                failed[(stream, dt)] = lines
                self.last_error = f"{type(exc).__name__}: {exc}"

        if failed:
            async with self._lock:
                for key, lines in failed.items():
                    self._buffer[key] = lines + self._buffer[key]
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
