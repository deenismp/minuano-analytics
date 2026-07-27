"""minuano collector.

Two ingest paths, one behaviour:

    POST /collect            one event or an array of events, JSON body
    GET  /collect?e=<b64url> one base64url-encoded event, returns a 1x1 GIF

The GET path is not a convenience. GTM custom templates run in sandboxed JavaScript whose
outbound data API is `sendPixel`, and `sendPixel` is GET only.

Three rules hold on both paths:

1. `ingested_at` is set here and overwrites anything the client sent. It is the *only*
   server-derived field -- nothing is read off the request socket, because server-side GTM
   and future server SDKs relay on a visitor's behalf and socket-derived values would stamp
   every relayed event with the relay's identity.
2. Nothing is ever rejected. The response is always 2xx (except an oversized body). Valid
   events land in `data/events/`, invalid ones in `data/bad/` with their errors attached to
   the original payload, so they can be fixed and replayed.
3. Secret-shaped `params` values are replaced with `<REDACTED>` before anything is written.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, config, credentials
from .validate import redact, validate
from .writer import BufferedNDJSONWriter, make_writer

# Transparent 1x1 GIF. `sendPixel` sets an image src and expects image bytes back.
PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

CFG = config.load()


def log(level: str, msg: str, **fields: Any) -> None:
    """One JSON object per line on stdout. The container logs nowhere else."""
    record = {"ts": _now(), "level": level, "msg": msg, "instance": CFG.instance_id, **fields}
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), file=sys.stdout, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Must run before the writer resolves its filesystem, or the cloud backend authenticates
    # against nothing. Returns a description, never the key material.
    setup = credentials.bootstrap()
    if setup:
        log("info", "cloud credentials ready", detail=setup)

    writer = app.state.writer = make_writer(CFG)

    # Refuse to start rather than accept traffic we cannot store. An unwritable sink discovered
    # at the first flush has already been answered 2xx and the events are gone.
    try:
        writer.preflight()
    except RuntimeError as exc:
        log("error", "sink is not writable, refusing to start", sink_uri=CFG.sink_uri, detail=str(exc))
        raise

    await writer.start()
    log("info", "collector started", version=__version__, sink_uri=CFG.sink_uri, backend=CFG.scheme,
        flush_max_events=CFG.flush_max_events, flush_max_seconds=CFG.flush_max_seconds)
    yield
    # uvicorn turns SIGTERM into lifespan shutdown, so this is the SIGTERM flush.
    written = await writer.stop()
    if writer.buffered:
        # Loud, not silent: a clean-looking exit that left events behind is the worst outcome.
        log("error", "collector stopped with events still buffered", buffered=writer.buffered,
            last_error=writer.last_error, files_written=written)
    else:
        log("info", "collector stopped, buffer drained", files_written=written)


app = FastAPI(title="minuano collector", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CFG.cors_origins),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=86400,
)


async def _store(request: Request, payload: Any) -> tuple[int, int]:
    """Redact, stamp, validate, and route one event to `events` or `bad`. Never raises."""
    writer: BufferedNDJSONWriter = request.app.state.writer
    ingested_at = _now()
    dt = ingested_at[:10]

    if not isinstance(payload, dict):
        await writer.append("bad", dt, {
            "ingested_at": ingested_at,
            "collector_instance": CFG.instance_id,
            "errors": [f"event must be a JSON object, got {type(payload).__name__}"],
            "payload": payload,
        })
        return 0, 1

    redact(payload)
    as_received = copy.deepcopy(payload)  # kept verbatim on the bad row, post-redaction
    payload["ingested_at"] = ingested_at   # overwrites any client-supplied value

    errors = validate(payload)
    if errors:
        await writer.append("bad", dt, {
            "ingested_at": ingested_at,
            "collector_instance": CFG.instance_id,
            "errors": errors,
            "payload": as_received,
        })
        return 0, 1

    await writer.append("events", dt, payload)
    return 1, 0


async def _collect(request: Request, payload: Any) -> tuple[int, int]:
    events = payload if isinstance(payload, list) else [payload]
    accepted = rejected = 0
    for event in events:
        good, bad = await _store(request, event)
        accepted += good
        rejected += bad
    return accepted, rejected


async def _store_unparseable(request: Request, body: bytes, reason: str) -> None:
    """A body that is not JSON at all is still not thrown away."""
    writer: BufferedNDJSONWriter = request.app.state.writer
    ingested_at = _now()
    await writer.append("bad", ingested_at[:10], {
        "ingested_at": ingested_at,
        "collector_instance": CFG.instance_id,
        "errors": [reason],
        "payload_raw": body.decode("utf-8", errors="replace")[:4096],
    })


@app.get("/healthz")
async def healthz(request: Request) -> dict:
    writer = request.app.state.writer
    return {
        # `degraded` means events are being accepted but a flush has failed and they are being
        # held in memory. Still 200: the collector is up and losing nothing yet.
        "status": "degraded" if writer.last_error else "ok",
        "version": __version__,
        "instance": CFG.instance_id,
        "buffered": writer.buffered,
        "last_error": writer.last_error,
    }


@app.post("/collect")
async def collect_post(request: Request) -> Response:
    body = await request.body()
    if len(body) > CFG.max_body_bytes:
        # The only non-2xx in the service. An oversized body is an abuse guard, not a
        # validation failure -- there is nothing meaningful to store.
        log("warn", "body too large", bytes=len(body))
        return Response(status_code=413, content=json.dumps({"error": "payload too large"}),
                        media_type="application/json")

    # Parsed as JSON regardless of Content-Type: `navigator.sendBeacon` sends text/plain,
    # which is exactly what lets the browser skip the CORS preflight.
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        await _store_unparseable(request, body, f"body is not valid JSON: {exc}")
        log("warn", "unparseable body stored as bad row", bytes=len(body))
        return Response(status_code=202, content=json.dumps({"accepted": 0, "rejected": 1}),
                        media_type="application/json")

    accepted, rejected = await _collect(request, payload)
    log("info", "collect", path="post", accepted=accepted, rejected=rejected)
    return Response(status_code=200, content=json.dumps({"accepted": accepted, "rejected": rejected}),
                    media_type="application/json")


@app.get("/collect")
async def collect_get(request: Request, e: str = "") -> Response:
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, private", "Pragma": "no-cache"}

    if not e:
        await _store_unparseable(request, b"", "GET /collect called without the `e` parameter")
        log("warn", "GET /collect without payload")
        return Response(content=PIXEL, media_type="image/gif", headers=headers)

    try:
        # base64url, padding restored -- GTM's sandboxed encodeUriComponent output and
        # btoa-derived strings both arrive without it.
        raw = base64.urlsafe_b64decode(e + "=" * (-len(e) % 4))
        payload = json.loads(raw)
    except (binascii.Error, ValueError) as exc:
        await _store_unparseable(request, e.encode(), f"`e` is not base64url-encoded JSON: {exc}")
        log("warn", "undecodable GET payload stored as bad row", chars=len(e))
        return Response(content=PIXEL, media_type="image/gif", headers=headers)

    accepted, rejected = await _collect(request, payload)
    log("info", "collect", path="get", accepted=accepted, rejected=rejected)
    return Response(content=PIXEL, media_type="image/gif", headers=headers)
