# syntax=docker/dockerfile:1.7
#
# Docker image for `splitsmith serve` (hosted mode).
#
# Single-stage on purpose: the slim runtime (everything outside
# ``[dev]``) is already small enough that a multi-stage split mostly
# adds complexity. If the image grows past a few hundred MB it's
# worth revisiting with a uv build cache + a slim distroless final
# stage; for now the priority is a single-file ``docker compose up``
# experience for contributors.
#
# What ships:
# - Python 3.11 (matches the wheel's ``requires-python``).
# - uv for dependency install (faster + lockfile-aware).
# - ``[project]`` deps + ``[project.optional-dependencies].hosted``:
#   the core local-mode set plus the runtime hosted-mode deps
#   (SQLAlchemy + alembic + asyncpg + aiosqlite + python-ulid +
#   boto3). The dev group (torch / transformers / panns / mypy / ruff
#   / moto / etc.) stays out -- the slim ONNX runtime + scikit-learn
#   cover voter B + C inference paths.
# - ffmpeg + ffprobe via the OS package manager so trim / shot-detect
#   workers can still execute when a real shooter / match is uploaded.

FROM python:3.11-slim-bookworm

# System deps: ffmpeg for trim / probe (workers still need it once
# real jobs run); curl for healthchecks. ``--no-install-recommends``
# keeps the image tight. Build tools intentionally omitted -- every
# wheel in the slim runtime ships a manylinux artifact.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Install uv -- pinned to the same version range the dev environment uses.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

# Non-root user. Avoids container processes running as root, and
# keeps any future volume mounts owned correctly without a chown.
RUN groupadd --system splitsmith \
 && useradd --system --gid splitsmith --home-dir /home/splitsmith --create-home splitsmith

WORKDIR /app

# Copy lock + project metadata first so the dependency install
# layer caches across source-only changes.
COPY pyproject.toml uv.lock README.md ./
COPY alembic.ini ./
COPY alembic ./alembic

# Install dependencies only. ``--no-install-project`` skips the
# splitsmith package itself, so this layer is invalidated only when
# ``pyproject.toml`` / ``uv.lock`` change -- a source-only edit reuses
# the wheel install on the next build. ``--extra hosted`` pulls the
# hosted-mode runtime deps (alembic + sqlalchemy + asyncpg + boto3 +
# ...). Without it the container would boot with the local-mode wheel
# only and ``splitsmith serve`` would crash on the first alembic call.
RUN uv sync --frozen --no-dev --extra hosted --no-install-project

# Now bring in the source and install the package itself into the
# already-warm venv. ``--frozen`` ensures the build still fails if
# the lockfile and pyproject have drifted.
COPY src ./src
RUN uv sync --frozen --no-dev --extra hosted

# Hand the working directory to the non-root user so anything the
# server writes (logs, the optional ``~/.splitsmith`` config) lands
# in a writable home.
RUN chown -R splitsmith:splitsmith /app
USER splitsmith

# Pin the config dir so the model cache (``<config_dir>/models``) lands
# at a fixed, baked-in path that the build step below and the runtime
# both agree on -- independent of the platform default, which differs
# between the build user and any future runtime user/home.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPLITSMITH_CONFIG_DIR=/home/splitsmith/.splitsmith

# Bake the slim ONNX model artifacts (~450 MB: CLAP + PANN + text
# embeddings) into the image. This is the whole point of running a
# persistent worker fleet: a cold worker loads models from local disk
# instead of paying a ~450 MB download on first detection (doc 04 --
# "model artifacts live in a baked-in Docker layer"). With models
# present, ``_maybe_submit_model_download`` no-ops at boot, so neither
# the API nor a worker ever fetches at runtime.
#
# Gated behind ``BAKE_MODELS`` so offline / network-restricted builds
# (e.g. some CI) can opt out with ``--build-arg BAKE_MODELS=0`` and
# fall back to the runtime-download path. Default on.
ARG BAKE_MODELS=1
RUN if [ "$BAKE_MODELS" = "1" ]; then \
        splitsmith fetch-models; \
    else \
        echo "BAKE_MODELS=0 -- skipping model bake; runtime will download on first detection"; \
    fi

EXPOSE 5174

# ``serve`` sets SPLITSMITH_MODE=hosted itself; the compose file
# layers in SPLITSMITH_DATABASE_URL + S3 credentials.
ENTRYPOINT ["splitsmith", "serve"]
CMD ["--host", "0.0.0.0", "--port", "5174"]
