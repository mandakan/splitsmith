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
# from a clean checkout. node_modules is regenerated here via pnpm -- the
# same package manager CI and the wheel-publish job use, so the image and
# the wheel build the SPA identically. The repo's node_modules is kept out
# of the build context by .dockerignore.
FROM ${NODE_IMAGE} AS spa
WORKDIR /spa
# pnpm is pinned to match the rest of the repo (root packageManager).
RUN corepack enable && corepack prepare pnpm@10.30.3 --activate
# Lockfile-first so ``pnpm install`` caches across SPA source-only edits.
COPY src/splitsmith/ui_static/package.json src/splitsmith/ui_static/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY src/splitsmith/ui_static/ ./
RUN pnpm run build

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

# ffmpeg + ffprobe from BtbN/FFmpeg-Builds, replacing the Debian ``ffmpeg``
# package, which drags ~300 MB of codec/dev libraries into the runtime image.
# These binaries link only glibc + libgcc_s (both already in the base image)
# and carry every codec we use compiled in: drawtext, libx264, prores, aac,
# and h264_nvenc for Dockerfile.gpu.
#
# The `-shared` variant, not the static one: static duplicates every codec
# library into BOTH binaries (281 MB for the pair), while shared is 0.7 MB of
# binaries against one 171 MB set of libav*.so -- ~110 MB off the image. The
# libs go in their own directory with an ld.so.conf.d entry rather than an
# LD_LIBRARY_PATH, so nothing has to be exported into every subprocess the
# app spawns. BtbN's shared binaries carry a malformed RPATH (`-Wl:../lib`),
# which is why the loader has to be told where the libs are at all.
#
# Previously John Van Sickle's static builds, off an unversioned personal
# host. That URL failed the v0.32.0 release twice: it answered the runner
# HTTP 200 with a body that was not an xz archive, so ``curl -f`` passed it
# through and tar died on garbage. The single-arch GPU job pulling the same
# URL succeeded both times, which points at the two concurrent arch legs of
# this job being served an error page. Two things fix that class of failure:
# the source is now a GitHub release asset, and the sha256 below is checked
# BEFORE extraction, so a wrong body fails as a checksum mismatch naming the
# file rather than as a confusing tar error.
#
# Pinned to one immutable autobuild tag. The two arches carry DIFFERENT build
# revisions within a tag (arm64 lags amd64 by an hour or so), which is why the
# filename is spelled out per arch instead of composed from one revision. To
# bump, pick a tag from https://github.com/BtbN/FFmpeg-Builds/releases and take
# both names and digests from
#   gh api repos/BtbN/FFmpeg-Builds/releases/tags/<tag> \
#     --jq '.assets[] | select(.name|test("linux(64|arm64)-gpl-shared\\.tar\\.xz")) | "\(.name) \(.digest)"'
# Keep Dockerfile.gpu's copy of this block in step.
#
# ``TARGETARCH`` is provided automatically by buildx (amd64 / arm64).
ARG TARGETARCH
ARG FFMPEG_BUILD=autobuild-2026-08-14-13-16
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils; \
    rm -rf /var/lib/apt/lists/*; \
    case "${TARGETARCH:-amd64}" in \
        amd64) ff_file=ffmpeg-N-126134-gc48230eb86-linux64-gpl-shared.tar.xz; \
               ff_sha=b3bb57f31b7e5ad4a80f9557f5a81b0ef455c86e53c3219299754fb1bb9267ed ;; \
        arm64) ff_file=ffmpeg-N-126133-gead4378652-linuxarm64-gpl-shared.tar.xz; \
               ff_sha=ed55c70d198d0ad70e40bc2c98b6b2a45b3c4f6c477d37f4f80c22a55193288c ;; \
        *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 \
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_BUILD}/${ff_file}" \
        -o /tmp/ffmpeg.tar.xz; \
    echo "${ff_sha}  /tmp/ffmpeg.tar.xz" | sha256sum -c -; \
    mkdir -p /tmp/ffmpeg; \
    tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1; \
    install -m0755 /tmp/ffmpeg/bin/ffmpeg /tmp/ffmpeg/bin/ffprobe /usr/local/bin/; \
    mkdir -p /usr/local/lib/ffmpeg; \
    cp -a /tmp/ffmpeg/lib/*.so* /usr/local/lib/ffmpeg/; \
    echo /usr/local/lib/ffmpeg > /etc/ld.so.conf.d/zz-ffmpeg.conf; \
    ldconfig; \
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
# the compose healthcheck. ffmpeg/ffprobe come from the builder (below)
# -- no apt ffmpeg package, no codec libs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
 && rm -rf /var/lib/apt/lists/*

# ffmpeg + ffprobe from the builder: the two binaries, the libav* set they
# link against, and the ld.so.conf.d entry that points the loader at it.
# ``ldconfig`` here is not optional -- the cache is per-image, so without it
# both binaries fail at exec with a missing libavcodec.
COPY --from=builder /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/
COPY --from=builder /usr/local/lib/ffmpeg /usr/local/lib/ffmpeg
COPY --from=builder /etc/ld.so.conf.d/zz-ffmpeg.conf /etc/ld.so.conf.d/zz-ffmpeg.conf
RUN ldconfig && ffmpeg -version | head -1 && ffprobe -version | head -1

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

# Playwright's Chromium (issue #683 amendment): overlay_raster
# .ChromiumRasterizer needs the headless-shell browser Playwright's own
# package never vendors -- the same one-time step CI takes (see
# .github/workflows/ci.yml's "Install Chromium for the overlay
# rasterizer"). ``playwright install``'s default browser cache
# (``~/.cache/ms-playwright``) would land under /root here, since this
# runs before ``USER splitsmith`` below and root has no other home to
# write to -- unreadable once the process actually runs as the non-root
# ``splitsmith`` user. ``PLAYWRIGHT_BROWSERS_PATH`` is pinned to a
# location outside any user's home instead, exported as a build-time
# ``ENV`` (not just this one ``RUN``'s shell) so the same path is what
# ``ChromiumRasterizer.__enter__`` resolves at runtime too -- installing
# to one path and looking in another is the exact silent-miss failure
# mode a pinned env var exists to rule out. ``--with-deps`` apt-get
# installs the OS libraries Chromium needs to run at all, which this
# slim base image does not carry; ``--only-shell`` fetches the
# headless-shell build ``overlay_raster.CHROMIUM_CHANNEL`` launches
# (260M vs the full browser's 377M, verified byte-identical screenshot
# output -- see the amendment's "Dependency change, stated plainly").
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN apt-get update \
 && /app/.venv/bin/playwright install --with-deps chromium --only-shell \
 && rm -rf /var/lib/apt/lists/* \
 && chown -R splitsmith:splitsmith /opt/ms-playwright

# Alembic migrations: ``splitsmith serve`` runs ``alembic upgrade head`` on
# boot (unless --skip-migrations) with cwd=/app (the repo root in the
# editable layout), so alembic.ini + the versions tree must live at /app.
COPY --chown=splitsmith:splitsmith alembic.ini ./
COPY --chown=splitsmith:splitsmith alembic ./alembic

# Baked models -> the runtime config dir's models/ cache.
COPY --from=builder --chown=splitsmith:splitsmith /opt/splitsmith /home/splitsmith/.splitsmith

# Agent state dir. ``splitsmith agent`` (self-hosted worker) persists
# agent.json here (default SPLITSMITH_AGENT_STATE_DIR=/data). Pre-create it
# owned by the runtime user so the documented named-volume mount
# (``-v splitsmith-agent:/data``) inherits splitsmith ownership: a fresh
# named volume adopts the ownership of the image directory it covers. Without
# this the mount point auto-creates root-owned and the non-root user cannot
# write agent.json - the agent would exchange (and burn) its one-time
# registration token, then crash on the write.
RUN mkdir -p /data && chown splitsmith:splitsmith /data

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
