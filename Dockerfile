# syntax=docker/dockerfile:1.7
#
# Multi-stage image for `splitsmith serve` (hosted mode).
#
# Why multi-stage: the single-stage build shipped uv + a 951 MB
# `chown -R /app` dup layer + ~280 MB of ui_static/node_modules, all of
# which the runtime never touches. Splitting builder from runtime keeps
# the final image to {venv + ffmpeg + baked models + base} only.
#
# Layout:
# - spa: a Node stage that builds ``src/splitsmith/ui_static`` into dist/.
#   This makes the image self-contained from a clean git checkout -- dist/
#   is gitignored, so a host-prebuilt dist/ is no longer required (e.g. a
#   Railway / Cloud build straight from the repo).
# - builder: installs the venv (deps + the splitsmith package, editable),
#   overlays the SPA dist from the ``spa`` stage, and bakes the slim ONNX
#   models.
# - runtime: a clean base + ffmpeg, with the venv, the (slim) source tree,
#   alembic migrations, and baked models copied in. No uv, no SPA build
#   inputs, no chown dup layer.
#
# Why editable (not a built wheel): ``splitsmith serve`` resolves the
# alembic config dir as ``Path(cli.__file__).parent.parent.parent`` -- the
# repo root in the ``src/splitsmith/...`` layout. A non-editable install
# moves the package into site-packages and breaks that path math (alembic
# then runs with no script_location). Editable keeps cli.py at
# /app/src/splitsmith/cli.py so the repo-root assumption holds. The
# The builder drops the ui_static TS source after copying ``src`` and
# overlays the dist built in the ``spa`` stage, so only ui_static/dist (the
# built SPA) ships in the runtime image -- the same lean result as before,
# now without depending on a host-prebuilt dist.
#
# CRITICAL invariant: both stages use the SAME base image so the venv's
# interpreter path (pyvenv.cfg -> /usr/local/bin/python3.11) stays valid
# after the copy. ``UV_PYTHON_DOWNLOADS=never`` forces uv to build the venv
# against that base-image Python rather than a uv-managed one that would
# not exist in the runtime stage.
#
# What ships at runtime:
# - Python 3.11 (matches the wheel's ``requires-python``).
# - ``[project]`` deps + ``[project.optional-dependencies].hosted`` (the
#   slim ONNX runtime + scikit-learn + SQLAlchemy/alembic/asyncpg/boto3/
#   procrastinate). The dev group (torch / transformers / panns / mypy /
#   ruff / moto) stays out.
# - ffmpeg + ffprobe for trim / probe.
# - Baked CLAP + PANN + text-embedding artifacts (~450 MB) so neither the
#   API nor a worker downloads models at runtime (doc 04).

ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

# --------------------------------------------------------------------------
# SPA build (Node)
# --------------------------------------------------------------------------
# Builds the React SPA into dist/ so the runtime image is self-contained
# from a clean checkout. node_modules is regenerated here via ``npm ci``
# (the repo's node_modules + the alternate pnpm lockfile are kept out of the
# build context by .dockerignore).
FROM ${NODE_IMAGE} AS spa
WORKDIR /spa
# Lockfile-first so ``npm ci`` caches across SPA source-only edits.
COPY src/splitsmith/ui_static/package.json src/splitsmith/ui_static/package-lock.json ./
RUN npm ci
COPY src/splitsmith/ui_static/ ./
RUN npm run build

# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

# Use the base-image Python; never let uv fetch a managed interpreter (it
# would bake an interpreter path the runtime stage can't satisfy). Copy
# link mode so the venv holds real files, not hardlinks into uv's cache
# (hardlinks don't survive the cross-stage COPY).
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

# Static ffmpeg + ffprobe (John Van Sickle release builds -- fully
# statically linked, no runtime shared libs). Replaces the Debian
# ``ffmpeg`` package, which drags ~300 MB of codec/dev libraries into
# the runtime image; the static binaries are ~80 MB and self-contained.
# ``TARGETARCH`` is provided automatically by buildx (amd64 / arm64).
# The release URL tracks the latest stable ffmpeg; it isn't strictly
# version-pinned, which is acceptable for a CLI we only shell out to.
ARG TARGETARCH
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils; \
    rm -rf /var/lib/apt/lists/*; \
    case "${TARGETARCH:-amd64}" in \
        amd64) ff_arch=amd64 ;; \
        arm64) ff_arch=arm64 ;; \
        *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${ff_arch}-static.tar.xz" \
        -o /tmp/ffmpeg.tar.xz; \
    mkdir -p /tmp/ffmpeg; \
    tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1; \
    install -m0755 /tmp/ffmpeg/ffmpeg /tmp/ffmpeg/ffprobe /usr/local/bin/; \
    rm -rf /tmp/ffmpeg /tmp/ffmpeg.tar.xz; \
    /usr/local/bin/ffmpeg -version | head -1; \
    /usr/local/bin/ffprobe -version | head -1

WORKDIR /app

# Dependency layer first so it caches across source-only edits. Metadata
# only -- ``--no-install-project`` skips the splitsmith package itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra hosted --no-install-project

# Now the source + the editable project install.
COPY src ./src
# Replace the ui_static tree with just the dist built in the ``spa`` stage:
# drop the TS source / configs / any stale host dist that rode in via the
# context, then overlay the freshly-built SPA. The runtime image therefore
# carries only ui_static/dist regardless of what was (or wasn't) prebuilt on
# the host -- so ``docker build`` works from a clean git checkout.
RUN rm -rf src/splitsmith/ui_static
COPY --from=spa /spa/dist ./src/splitsmith/ui_static/dist
RUN uv sync --frozen --no-dev --extra hosted

# Slim the venv before it's copied to the runtime stage: drop bundled
# test suites + __pycache__. NOTE: do NOT ``strip`` the native .so files
# -- the prebuilt scientific wheels (numpy/scipy OpenBLAS) carry an ELF
# layout that strip corrupts ("load command not page-aligned"), breaking
# numpy import. The big libs (llvmlite, openblas) stay as shipped.
RUN find /app/.venv -type d -name '__pycache__' -prune -exec rm -rf {} + ; \
    find /app/.venv -type d -name 'tests' -prune -exec rm -rf {} + ; \
    find /app/.venv -type d -name 'test' -prune -exec rm -rf {} + ; \
    SP="$(/app/.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"; \
    rm -rf "${SP}/sklearn/datasets/data" "${SP}/sklearn/datasets/descr" \
           "${SP}/sklearn/datasets/images"

# Bake the slim ONNX model artifacts into a staging dir we copy into the
# runtime stage. ``SPLITSMITH_CONFIG_DIR`` drives the cache location
# (<config_dir>/models). Gated behind BAKE_MODELS so offline / network-
# restricted builds opt out with ``--build-arg BAKE_MODELS=0``.
ENV SPLITSMITH_CONFIG_DIR=/opt/splitsmith
ARG BAKE_MODELS=1
RUN if [ "$BAKE_MODELS" = "1" ]; then \
        /app/.venv/bin/splitsmith fetch-models; \
    else \
        echo "BAKE_MODELS=0 -- skipping model bake; runtime will download on first detection"; \
        mkdir -p /opt/splitsmith/models; \
    fi

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# Runtime system deps only: ca-certificates for outbound TLS, curl for
# the compose healthcheck. ffmpeg/ffprobe come as static binaries from
# the builder (below) -- no apt ffmpeg package, no codec libs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
 && rm -rf /var/lib/apt/lists/*

# Static ffmpeg + ffprobe from the builder (self-contained, no lib deps).
COPY --from=builder /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/

# Non-root user; --create-home so the baked model cache + any runtime
# writes (logs) land in a writable home.
RUN groupadd --system splitsmith \
 && useradd --system --gid splitsmith --home-dir /home/splitsmith --create-home splitsmith

WORKDIR /app

# The venv carries all deps + the editable link to /app/src. Copy both to
# the SAME paths they were built at: the venv so interpreter shebangs +
# the editable .pth resolve, and the source tree the .pth points at.
COPY --from=builder --chown=splitsmith:splitsmith /app/.venv /app/.venv
COPY --from=builder --chown=splitsmith:splitsmith /app/src /app/src

# Alembic migrations: ``splitsmith serve`` runs ``alembic upgrade head`` on
# boot (unless --skip-migrations) with cwd=/app (the repo root in the
# editable layout), so alembic.ini + the versions tree must live at /app.
COPY --chown=splitsmith:splitsmith alembic.ini ./
COPY --chown=splitsmith:splitsmith alembic ./alembic

# Baked models -> the runtime config dir's models/ cache.
COPY --from=builder --chown=splitsmith:splitsmith /opt/splitsmith /home/splitsmith/.splitsmith

USER splitsmith

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPLITSMITH_CONFIG_DIR=/home/splitsmith/.splitsmith

EXPOSE 5174

# ENTRYPOINT is just the CLI so the same image runs both roles: the API
# (default CMD = ``serve ...``) and the worker fleet (compose overrides
# ``command: ["worker"]``). Both subcommands set SPLITSMITH_MODE=hosted
# themselves; the compose file layers in SPLITSMITH_DATABASE_URL + S3 creds.
ENTRYPOINT ["splitsmith"]
CMD ["serve", "--host", "0.0.0.0", "--port", "5174"]
