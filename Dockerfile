# Build stage -- resolve dependencies from the lockfile into a self-contained venv.
FROM python:3.13-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, so a source-only change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY collector/ ./collector/
COPY schema/ ./schema/
RUN uv sync --frozen


# Runtime stage -- no build toolchain, no uv, no package manager.
FROM python:3.13-slim

# Unbuffered so stdout logs reach the container runtime immediately, which matters when
# the process is being SIGTERM'd and you want to see the drain line.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MINUANO_DATA_DIR=/data

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin minuano \
    && mkdir -p /data && chown minuano:minuano /data

WORKDIR /app
COPY --from=build --chown=minuano:minuano /app /app

USER minuano
EXPOSE 8000
VOLUME ["/data"]

# No curl in a slim image, and no reason to add one -- python is already here.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"

# Exec form, so uvicorn is PID 1 and receives SIGTERM directly. That signal is the flush:
# uvicorn turns it into a lifespan shutdown, which drains the buffer.
STOPSIGNAL SIGTERM
CMD ["uvicorn", "collector.app:app", "--host", "0.0.0.0", "--port", "8000"]
