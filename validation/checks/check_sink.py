#!/usr/bin/env python3
"""Increment 4 -- one writer, any backend. Does the sink behave the same everywhere?

`memory://` is an fsspec filesystem built in to the base install, and it is *not* the local
branch, so it exercises the object-store code path -- no temp file, no rename -- with no cloud
SDK and no credentials. Comparing it byte-for-byte against `file://` is the closest thing to a
cloud test that can run offline.

    uv run validation/checks/check_sink.py

What this does NOT prove is that any real cloud works: see validation/README.md.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import fsspec  # noqa: E402

from collector import config  # noqa: E402
from collector.writer import make_writer  # noqa: E402

LOCAL_DIR = ROOT / "validation" / "output" / "sink-parity"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         └─ {detail}" if detail else ""))


def events(n: int, dt: str) -> list[dict]:
    return [{"schema_version": "0", "event_name": "page_view", "event_timestamp": f"{dt}T12:00:0{i}Z",
             "ingested_at": f"{dt}T12:00:0{i}.000Z", "anonymous_id": f"anon_000000{i}",
             "session_id": "1785500000"} for i in range(n)]


def with_env(**overrides) -> None:
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


async def write_all(uri: str):
    """Same events, same order, same instance id -- whatever the backend."""
    with_env(MINUANO_SINK_URI=uri, MINUANO_INSTANCE_ID="sink00000001", MINUANO_FLUSH_MAX_EVENTS="10000")
    writer = make_writer(config.load())
    dt, other_dt = "2026-07-27", "2026-07-28"
    for event in events(3, dt):
        await writer.append("events", dt, event)
    await writer.append("bad", dt, {"errors": ["nope"], "payload": {}})
    for event in events(2, other_dt):
        await writer.append("events", other_dt, event)
    first = await writer.flush()
    for event in events(1, dt):
        await writer.append("events", dt, event)
    second = await writer.flush()
    return writer, first + second


async def run() -> None:
    if LOCAL_DIR.exists():
        shutil.rmtree(LOCAL_DIR)

    local_writer, local_paths = await write_all(f"file://{LOCAL_DIR}")
    memory_writer, memory_paths = await write_all("memory://minuano-sink-test")

    def keys(paths: list[str], root: str) -> list[str]:
        return sorted(p.split(root.rstrip("/") + "/", 1)[-1] for p in paths)

    local_keys = keys(local_paths, str(LOCAL_DIR))
    memory_keys = keys(memory_paths, "/minuano-sink-test")

    check(local_keys == memory_keys, "key layout is identical on both backends",
          f"local={local_keys}\n            memory={memory_keys}")
    check(len(local_keys) == 4, "one object per (stream, partition) per flush", f"objects={len(local_keys)}")
    check(all(k.startswith(("events/dt=", "bad/dt=")) for k in local_keys),
          "stream and partition are both in the key path", f"keys={local_keys}")
    check(all("sink00000001-" in k and k.endswith(".ndjson") for k in local_keys),
          "instance id and extension in every object name")
    check(len(set(local_keys)) == len(local_keys), "no key reused across flushes")

    memory_fs = fsspec.filesystem("memory")
    parity = []
    for key in local_keys:
        local_bytes = (LOCAL_DIR / key).read_bytes()
        memory_bytes = memory_fs.cat_file(f"/minuano-sink-test/{key}")
        parity.append(local_bytes == memory_bytes)
    check(all(parity), "sink parity: byte-identical output from both backends",
          f"{sum(parity)}/{len(parity)} objects match")

    check(not list(LOCAL_DIR.rglob("*.tmp")), "local writes leave no .tmp behind after the rename")
    check(local_writer.location.startswith("file://") and memory_writer.location.startswith("memory://"),
          "each writer reports its own backend",
          f"{local_writer.location} | {memory_writer.location}")

    # --- boot guards ------------------------------------------------------------------
    # Pick a scheme whose backend is genuinely absent, rather than hardcoding `gs://`. Hardcoding
    # made this check depend on what happened to be installed: green in CI, which installs no
    # extras, and red on any machine where someone had run the cloud-sink check with
    # `--extra gcp`. A check that goes red for a reason unrelated to what it tests is worse than
    # no check -- it teaches you to skim past a failing line.
    absent = [(scheme, extra, module)
              for scheme, extra, module in (("gs", "gcp", "gcsfs"), ("s3", "aws", "s3fs"), ("az", "azure", "adlfs"))
              if importlib.util.find_spec(module) is None]
    if not absent:
        print("[SKIP] 'backend not installed' cannot be exercised: every cloud extra is present here")
    else:
        scheme, extra, _ = absent[0]
        with_env(MINUANO_SINK_URI=f"{scheme}://bucket/raw")
        try:
            config.load()
            check(False, "a URI whose backend is not installed fails at boot", "no error raised")
        except ValueError as exc:
            check(f"minuano-collector[{extra}]" in str(exc),
                  "a URI whose backend is not installed fails at boot, naming the extra", str(exc))

    with_env(MINUANO_SINK_URI="nonsense://bucket/raw")
    try:
        config.load()
        check(False, "an unknown scheme fails at boot", "no error raised")
    except ValueError as exc:
        check("unknown scheme" in str(exc) or "not installed" in str(exc),
              "an unknown scheme fails at boot", str(exc))

    # --- preflight: an unwritable sink must be refused before any traffic is accepted -----
    locked = ROOT / "validation" / "output" / "unwritable"
    if locked.exists():
        os.chmod(locked, 0o700)
        shutil.rmtree(locked)
    locked.mkdir(parents=True)
    os.chmod(locked, 0o500)  # readable and traversable, not writable -- a Linux bind mount
    try:
        with_env(MINUANO_SINK_URI=f"file://{locked}/raw", MINUANO_INSTANCE_ID="preflight001")
        writer = make_writer(config.load())
        try:
            writer.preflight()
            check(False, "an unwritable sink is refused at startup", "preflight did not raise")
        except RuntimeError as exc:
            check("not writable" in str(exc) and "MINUANO_UID" in str(exc),
                  "an unwritable sink is refused at startup, with the fix in the message",
                  str(exc).splitlines()[0])
    finally:
        os.chmod(locked, 0o700)
        shutil.rmtree(locked)

    # A batch that fails to write goes back into the buffer instead of being dropped.
    with_env(MINUANO_SINK_URI="memory://retain-test", MINUANO_INSTANCE_ID="retain000001",
             MINUANO_FLUSH_MAX_EVENTS="10000")
    writer = make_writer(config.load())
    await writer.append("events", "2026-07-27", events(1, "2026-07-27")[0])

    def explode(*args, **kwargs):
        raise OSError("simulated object-store failure")

    writer._put = explode
    written = await writer.flush()
    check(written == [] and writer.buffered == 1,
          "a failed flush returns the events to the buffer rather than dropping them",
          f"written={written} buffered={writer.buffered}")
    check(writer.last_error and "simulated" in writer.last_error,
          "the failure is recorded for /healthz to report", writer.last_error or "<none>")

    with_env(MINUANO_SINK_URI=None, MINUANO_INSTANCE_ID=None, MINUANO_FLUSH_MAX_EVENTS=None)
    check(config.load().sink_uri == "file://./data", "the default sink is local ./data",
          f"default={config.load().sink_uri}")

    # --- the credential bridge ---------------------------------------------------------
    # These 25 lines are the only place in the project that touches private key material, and
    # until now they had no coverage at all: on a machine with ambient credentials
    # GOOGLE_APPLICATION_CREDENTIALS_JSON is unset, bootstrap() returns None immediately, and
    # every "passing" run exercised nothing. Needs no bucket and no real key.
    from collector import credentials  # noqa: PLC0415 -- kept local to this section

    # Assembled rather than written as literals, so this fixture does not itself trip
    # check_public_repo.py -- which greps tracked files for exactly these shapes. Weakening that
    # check to whitelist the test directory would have been the wrong trade: a real key could hide
    # there just as easily.
    _pem = "-----BEGIN " + "PRIVATE KEY" + "-----\nNOTAREALKEY\n-----END " + "PRIVATE KEY" + "-----\n"
    FAKE = {"type": "service_account", "client_email": "probe@example.iam.gserviceaccount.com",
            "private" + "_key": _pem}
    saved = {k: os.environ.get(k) for k in (credentials.ENV_JSON, credentials.ENV_PATH)}
    try:
        with_env(**{credentials.ENV_PATH: None, credentials.ENV_JSON: json.dumps(FAKE)})
        summary = credentials.bootstrap()
        written = os.environ.get(credentials.ENV_PATH, "")
        mode = stat.S_IMODE(os.stat(written).st_mode) if written and os.path.exists(written) else None

        check(mode == 0o600, "the credential file is written 0600", f"mode={oct(mode) if mode else None}")
        check(json.loads(Path(written).read_text()) == FAKE,
              "the key round-trips to the file the SDK will read")
        # The whole point of the module: the summary is logged, so it must carry the identifier
        # and nothing else. A leak here goes straight to stdout on every boot.
        check("NOTAREALKEY" not in (summary or "") and "private_key" not in (summary or ""),
              "no key material appears in what bootstrap() returns", summary or "<none>")
        check("probe@example.iam.gserviceaccount.com" in (summary or ""),
              "the summary names the principal, which is what an IAM grant needs")

        # A JSON *string* rather than an object -- the most common PaaS paste mistake. It must
        # fail before a file is created, and the error must name the variable at fault.
        with_env(**{credentials.ENV_PATH: None, credentials.ENV_JSON: json.dumps(json.dumps(FAKE))})
        try:
            credentials.bootstrap()
            check(False, "a double-encoded key is refused before any file is written", "no error raised")
        except ValueError as exc:
            check(credentials.ENV_JSON in str(exc) and credentials.ENV_PATH not in os.environ,
                  "a double-encoded key is refused before any file is written", str(exc)[:90])

        with_env(**{credentials.ENV_PATH: None, credentials.ENV_JSON: '{"broken'})
        try:
            credentials.bootstrap()
            check(False, "malformed JSON fails at boot", "no error raised")
        except ValueError as exc:
            check("broken" not in str(exc), "malformed JSON fails at boot without echoing the value",
                  str(exc)[:90])

        with_env(**{credentials.ENV_JSON: None, credentials.ENV_PATH: None})
        check(credentials.bootstrap() is None, "no credentials configured is not an error")
    finally:
        with_env(**saved)


def main() -> int:
    asyncio.run(run())
    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print("FAILED: " + ", ".join(failed) if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
