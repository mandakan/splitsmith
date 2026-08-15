# Self-hosted workers: operator guide

This guide explains how to run a home Docker box as a compute worker for splitsmith.
The home worker runs detection and processing jobs, reducing Railway compute costs.
The server prefers the home worker over Railway when it is online.

## Prerequisites

- Docker installed on the home box.
- Outbound HTTPS from the home box to the splitsmith server (e.g., `my.splitsmith.app`).
- No inbound ports required - the agent opens a persistent outbound SSE connection.
- An admin account on the splitsmith server (email listed in `SPLITSMITH_ADMIN_EMAILS`).

## Getting the image

The image is published to GitHub Container Registry by the `Publish image (GHCR)`
workflow. Tags:

- `:edge` - rebuilt on every push to `main`; matches what staging runs.
- `:latest` and `:X.Y.Z` - published when a release is cut; `:latest` matches production.

Images are multi-arch (linux/amd64 and linux/arm64), so they run natively on x86
servers and ARM boxes.

**Option A - pull from registry (recommended):**

```bash
docker pull ghcr.io/mandakan/splitsmith:latest   # or :edge to match staging
```

Use that reference wherever `<IMAGE>` appears below. (The package must be public
for an unauthenticated pull; if the pull asks for credentials, the package is
still private - make it public in the GitHub package settings, one-time.)

**Option B - local build:**

```bash
git clone https://github.com/mandakan/splitsmith.git
cd splitsmith
docker build -t splitsmith:local .
```

Use `splitsmith:local` wherever `<IMAGE>` appears below.

The register dialog on the server shows a command with `SPLITSMITH_AGENT_IMAGE` as the
image tag. If you built locally, replace that tag with `splitsmith:local` before running
the command.

## Registering the worker

1. Sign in to the splitsmith UI with an admin account.
2. Go to **Admin > Workers** in the navigation.
3. Click **Register worker**, enter a name (e.g., "home-server"), and confirm.
4. The dialog shows a one-time registration token and a numbered sequence of
   copy-paste commands: build the image, start the agent, and check its logs.
   Each has its own copy button. Copy the start-agent command now - the token
   it contains is shown only once.

The commands in the dialog are the same ones described below; the dialog fills
in your server URL and the registration token for you.

## Running the agent

Run the command from the dialog, substituting the image tag if using a local build:

```bash
docker run -d \
  --restart unless-stopped \
  --name splitsmith-agent \
  -v splitsmith-agent:/data \
  -v splitsmith-models:/home/splitsmith/.splitsmith/models \
  <IMAGE> agent \
  --server-url https://my.splitsmith.app \
  --token <REGISTRATION_TOKEN>
```

The `-v splitsmith-agent:/data` flag mounts a named volume at the agent's state dir.
On first start, the agent exchanges the registration token for credentials and writes
`/data/agent.json`. That file persists across container restarts.

The second volume holds the detection models. The image ships without them -- they
are ~450 MB, and the first detection downloads them from `models.splitsmith.app`
into that directory, hash-verified. With the volume, that happens once and survives
container recreates; without it, every recreated container downloads them again.
To pre-warm rather than pay it on the first job:

```bash
docker run --rm -v splitsmith-models:/home/splitsmith/.splitsmith/models \
  <IMAGE> fetch-models
```

A box with no outbound internet needs an image built with the models baked in:
`docker build --build-arg BAKE_MODELS=1 .`

The container runs as a non-root user. `/data` is pre-created with that user's
ownership, so a named volume (as above) is writable out of the box. If you bind-mount
a host directory instead (`-v /some/host/dir:/data`), make it writable by uid 999, or
pass `--state-dir` to point at a path that is.

The `--token` flag is only needed on the first run. Once `agent.json` exists, the agent
uses it directly and `--token` is ignored.

### With Docker Compose

If you prefer a compose file over a raw `docker run`, the repo ships
`docker-compose.agent.yml` - a standalone file that runs just the agent (no
Postgres or object storage; those live on the server). Set the server URL and
first-run token via the environment:

```bash
SPLITSMITH_SERVER_URL=https://my.splitsmith.app \
SPLITSMITH_REGISTRATION_TOKEN=<REGISTRATION_TOKEN> \
docker compose -f docker-compose.agent.yml up -d
```

Drop `SPLITSMITH_REGISTRATION_TOKEN` on later runs once `agent.json` exists. Run
one copy per environment with distinct project names
(`docker compose -p splitsmith-agent-staging ...`) so their state volumes do not
collide.

## GPU acceleration (NVIDIA)

If the box has a discrete NVIDIA GPU, run the **GPU image** instead so the agent
offloads its two hot paths (issue #796):

- **ensemble detect** runs on the onnxruntime CUDA execution provider
  (~4.5x faster than CPU, with byte-identical detections);
- **audit-mode encode** uses ffmpeg `h264_nvenc` (measured 66.6s -> 14.7s on a
  4K60 stage), selected automatically once the agent confirms the GPU can
  actually encode.

Both are opportunistic: the same code falls back to CPU when the GPU is absent
or busy, so nothing fails closed. On registration the agent advertises what it
verified it can do (`capabilities.nvenc_h264`, `capabilities.cuda_ep`,
`capabilities.gpu_name`), visible in **Admin > Workers**.

**Prerequisites on the host:**

- An NVIDIA driver new enough for CUDA 12. The container carries its own CUDA
  userspace, so a driver that reports CUDA >= 12.4 (`nvidia-smi`) is enough --
  no driver upgrade needed on e.g. a 566.x driver.
- The [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  with the Docker runtime configured
  (`sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`).
- Verify GPU passthrough works: `docker run --rm --gpus all <cuda-image> nvidia-smi`.

**Build and run** (the GPU image is a separate `latest-gpu` tag / `Dockerfile.gpu`):

```bash
docker build -f Dockerfile.gpu -t splitsmith:local-gpu .

SPLITSMITH_SERVER_URL=https://my.splitsmith.app \
SPLITSMITH_REGISTRATION_TOKEN=<REGISTRATION_TOKEN> \
SPLITSMITH_IMAGE_TAG=local-gpu \
docker compose -f docker-compose.agent.gpu.yml up -d
```

`docker-compose.agent.gpu.yml` is the agent compose file with an added GPU
reservation. Set `SPLITSMITH_ONNX_DEVICE=cpu` to force the CPU path (e.g. while
parity-checking a new GPU or onnxruntime version); the default `auto` uses the
GPU when the CUDA provider initialises and falls back otherwise.

### Without Docker (native, e.g. WSL2)

On a box where Docker isn't an option -- WSL2 is the common case -- run the agent
natively with the same GPU acceleration. From a clone of the repo:

```bash
scripts/setup-agent-gpu.sh                 # creates .venv-agent-gpu, installs the
                                           # GPU onnxruntime stack, verifies CUDA + NVENC
scripts/run-agent-gpu.sh \
  --server-url https://my.splitsmith.app \
  --token <REGISTRATION_TOKEN> \
  --state-dir ~/.splitsmith               # REQUIRED natively: the default is /data
                                          # (a Docker path), which a normal user
                                          # can't write. See the note below.
```

`setup-agent-gpu.sh` installs `onnxruntime-gpu` plus the CUDA 12 / cuDNN 9
runtime wheels and checks that a real CUDA session binds. **No `LD_LIBRARY_PATH`
setup is needed:** the engine calls `onnxruntime.preload_dlls()` to load the
CUDA libraries from those wheels, and a normal WSL2/Linux install already exposes
`libcuda` from the driver via `ld.so.conf.d`. The only host requirement is an
NVIDIA driver reporting CUDA >= 12.4 (`nvidia-smi` -- on WSL2 it lives at
`/usr/lib/wsl/lib/nvidia-smi`); no driver upgrade is needed on e.g. a 566.x
driver.

Validated on WSL2 + RTX 2070 SUPER: with no environment variables set, ensemble
detect runs on CUDA (~4.5x faster than CPU, identical detections) and audit
trims use `h264_nvenc`. `--token` is only needed on first run, exactly as in the
Docker path.

**State dir (native gotcha).** The state dir defaults to `$SPLITSMITH_AGENT_STATE_DIR`
or `/data` -- a path that suits the Docker image (where a named volume is mounted
there) but that an ordinary user cannot create. Running natively without
overriding it fails on first registration with `PermissionError: /data`, *after*
the server has already consumed the one-time token -- so you then have to delete
the half-registered worker and register a fresh one. Always pass `--state-dir`
(e.g. `~/.splitsmith`) or set `SPLITSMITH_AGENT_STATE_DIR`. `agent.json` and the
source cache (`<state-dir>/projects`) live there and persist across restarts.

To run it under systemd so it starts on boot, point the unit at the same state
dir and run it as your user, e.g.:

```ini
# /etc/systemd/system/splitsmith-agent.service
[Unit]
Description=splitsmith self-hosted GPU worker agent
After=network-online.target
Wants=network-online.target

[Service]
User=<you>
Environment=HOME=/home/<you>
Environment=SPLITSMITH_AGENT_STATE_DIR=/home/<you>/.splitsmith
WorkingDirectory=/path/to/splitsmith
ExecStart=/path/to/splitsmith/scripts/run-agent-gpu.sh --server-url https://my.splitsmith.app
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Register once by hand (with `--token` and `--state-dir`) so `agent.json` exists,
then `sudo systemctl enable --now splitsmith-agent`. On WSL2 the distro's
`systemd=true` boot plus a Windows "start WSL at logon/boot" task is what brings
the unit up without an interactive login.

### From PyPI (no clone; auto-updating)

`splitsmith` is published to PyPI, so a long-lived native worker doesn't need a
git checkout at all -- install the wheel into a dedicated venv and upgrade it in
place. This is the better shape for a home worker: updates are one `uv pip
install -U`, and there's no working tree to drift or clobber (and no risk of the
agent running uncommitted WIP from a dev clone).

The one wrinkle is the GPU wheel. `splitsmith[hosted]` depends on the CPU
`onnxruntime`, and `onnxruntime` / `onnxruntime-gpu` share an import name and
cannot coexist -- so every base install or upgrade pulls the CPU wheel and you
must swap it back:

```bash
VENV=~/.venv-splitsmith-agent
uv venv "$VENV" --python 3.11
uv pip install --python "$VENV" "splitsmith[hosted]"
# swap CPU onnxruntime -> GPU (pins mirror scripts/setup-agent-gpu.sh).
# Uninstall BOTH and reinstall fresh -- see the gotcha below.
uv pip uninstall --python "$VENV" onnxruntime onnxruntime-gpu
uv pip install --python "$VENV" --reinstall-package onnxruntime-gpu \
  onnxruntime-gpu==1.22.0 \
  nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 \
  nvidia-curand-cu12 nvidia-cufft-cu12
```

**The swap must reinstall, not just re-name (upgrade gotcha).** `onnxruntime`
and `onnxruntime-gpu` share the same on-disk `onnxruntime/` directory. A base
`uv pip install -U "splitsmith[hosted]"` upgrade pulls the CPU `onnxruntime`
*over* the GPU files. Uninstalling then installing onnxruntime-gpu *by name* is a
no-op when its dist-info survives -- uv reports "already satisfied" and skips it,
leaving a gutted namespace package: `import onnxruntime` gives `__file__=None`
and no `CUDAExecutionProvider`, so ensemble detect silently falls back to CPU.
That is why the swap uninstalls **both** distributions, deletes the leftover
`onnxruntime/` dir, and forces `--reinstall-package onnxruntime-gpu`. Any
auto-updater that upgrades the base package **must** re-run this exact swap after
every upgrade, and should verify `CUDAExecutionProvider` is present afterward
(`onnxruntime.preload_dlls(); onnxruntime.get_available_providers()`) rather than
trust the install.

Point the systemd unit's `ExecStart` at the venv binary directly (no
`run-agent-gpu.sh` needed):

```ini
ExecStart=/home/<you>/.venv-splitsmith-agent/bin/splitsmith agent --server-url https://my.splitsmith.app
Environment=SPLITSMITH_ONNX_DEVICE=auto
Environment=SPLITSMITH_AGENT_STATE_DIR=/home/<you>/.splitsmith
```

Register once by hand (`... agent --token <TOKEN> --state-dir ~/.splitsmith`) so
`agent.json` exists, then `enable --now` the unit.

**Auto-update.** There is no server-push "update" command -- the worker channel
only carries wake / enabled / disabled / replaced (a deleted worker gets a 404
and the agent exits). Updates are client-pull: a `systemd` timer that upgrades
from PyPI and restarts the agent. A minimal updater compares the installed
version against the latest on PyPI, and when a newer one is out **and the agent
is idle** runs the `uv pip install -U` + GPU-swap above and `systemctl restart`s
the service. Gate the restart on the drain state -- the agent logs `wake
received; draining` when busy and `drain finished; waiting` when idle, so keying
on the most recent marker avoids killing a running job. Credentials live in the
state dir, independent of the venv, so an upgrade never re-registers. Drive it
with a `.timer` (e.g. `OnUnitActiveSec=6h`, `Persistent=true`).

## Source cache

Every job needs the raw video local. The agent mirrors each raw file from object
storage on first use and reuses that copy for later jobs on the same file
(detect, trim, shot-detect, export), so the download happens once per file
instead of once per job. The mirror lives under the agent's state volume at
`/data/projects`, so it persists across restarts - the same `-v splitsmith-agent:/data`
mount covers both `agent.json` and the cache. No extra volume is needed.

The cache is bounded. After each drain the agent evicts least-recently-used files
until the cache fits a byte budget, so a home box's disk cannot fill even across
many large matches. The default cap is 20 GB. Override it with
`SPLITSMITH_SOURCE_CACHE_MAX_GB` (a number of gigabytes; `0` disables eviction):

```bash
docker run -d \
  --restart unless-stopped \
  --name splitsmith-agent \
  -v splitsmith-agent:/data \
  -e SPLITSMITH_SOURCE_CACHE_MAX_GB=50 \
  <IMAGE> agent \
  --server-url https://my.splitsmith.app \
  --token <REGISTRATION_TOKEN>
```

Everything under `/data/projects` is reconstructable from the server and object
storage, so eviction is always safe - an evicted file is simply re-downloaded on
next use. Point the state volume at a disk with room for the cap you set.

To check logs:

```bash
docker logs -f splitsmith-agent
```

On the first successful start you will see lines like:

```
splitsmith agent: connecting to https://my.splitsmith.app (state-dir=/data, concurrency=1)
INFO [splitsmith.agent] registered as worker 01J...
INFO [splitsmith.agent] connected to wake channel; waiting for wake events
```

Later starts log `using cached registration for worker <id>` instead of
`registered`. When a job arrives you will see `wake received; draining queued
jobs`, the drain output, then `drain finished; waiting for next wake`. Between
jobs the agent is idle and quiet - that is expected.

## Agent lifecycle

| Event | Agent behaviour |
| --- | --- |
| First start with `--token` | Registers, writes `agent.json`, connects to channel |
| Later starts (no token) | Reads `agent.json`, connects directly |
| `wake` event received | Drains pending jobs, releases Neon connection |
| Worker disabled in admin UI | Agent idles (receives `disabled` event, does not drain) |
| Worker re-enabled in admin UI | Agent resumes draining on next `wake` |
| Worker deleted in admin UI | Channel returns 404; agent logs "token revoked or worker deleted" and exits |

If the agent exits after deletion, remove the container and volume before re-registering
with a fresh token:

```bash
docker rm splitsmith-agent
docker volume rm splitsmith-agent
```

**Credential-revocation note.** Deleting a worker revokes its channel and worker tokens
but not the Neon and R2 credentials the agent already holds in `agent.json`. If you need
to fully revoke access, rotate the Neon connection string and R2 access keys at the
provider level after deleting the worker row.

## Per-environment registration

Staging and production are separate servers with separate databases. Register the home
worker against each environment independently - one container per environment,
each with its own `agent.json` volume and registration token.

## Verifying the setup

1. With the agent connected, enqueue a detection job from your account (e.g., trigger
   beep detection on a stage in the UI).
2. Watch `docker logs -f splitsmith-agent` - you should see a drain log within a few
   seconds of enqueuing.
3. In the Railway dashboard, confirm the Railway worker service did not redeploy.
4. In **Admin > Workers**, the home worker row shows "online" status and an updated
   "last seen" timestamp.

To test Railway fallback:

1. Disable the home worker in **Admin > Workers** (toggle the enabled switch).
2. Enqueue another detection job.
3. The Railway worker boots and processes the job; the home agent stays idle.
4. Re-enable the home worker to restore the preferred dispatch path.
