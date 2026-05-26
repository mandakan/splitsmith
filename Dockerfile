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
# - The core dependency set: no ``[dev]`` extras, so torch /
#   transformers / panns-inference stay out of the hosted-mode
#   container. The slim ONNX runtime + scikit-learn cover voter
#   B + C inference paths.
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
# the ~500 MB wheel install on the next build.
RUN uv sync --frozen --no-dev --no-install-project

# Now bring in the source and install the package itself into the
# already-warm venv. ``--frozen`` ensures the build still fails if
# the lockfile and pyproject have drifted.
COPY src ./src
RUN uv sync --frozen --no-dev

# Hand the working directory to the non-root user so anything the
# server writes (logs, the optional ``~/.splitsmith`` config) lands
# in a writable home.
RUN chown -R splitsmith:splitsmith /app
USER splitsmith

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 5174

# ``serve`` sets SPLITSMITH_MODE=hosted itself; the compose file
# layers in SPLITSMITH_DATABASE_URL + S3 credentials.
ENTRYPOINT ["splitsmith", "serve"]
CMD ["--host", "0.0.0.0", "--port", "5174"]
