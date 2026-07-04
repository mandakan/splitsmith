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

There is no published container image yet. Railway builds the image from the repository
Dockerfile on each deploy, but does not push it to a public registry. Until an image is
published at `ghcr.io/mandakan/splitsmith`, the home box must build locally.

**Option A - local build (works today):**

```bash
git clone https://github.com/mandakan/splitsmith.git
cd splitsmith
docker build -t splitsmith:local .
```

Use `splitsmith:local` wherever `<IMAGE>` appears below.

**Option B - pull from registry (once published):**

```bash
docker pull ghcr.io/mandakan/splitsmith:latest
```

Use `ghcr.io/mandakan/splitsmith:latest` wherever `<IMAGE>` appears below.

The register dialog on the server shows a command with `SPLITSMITH_AGENT_IMAGE` as the
image tag. If you built locally, replace that tag with `splitsmith:local` before running
the command.

## Registering the worker

1. Sign in to the splitsmith UI with an admin account.
2. Go to **Admin > Workers** in the navigation.
3. Click **Register worker**, enter a name (e.g., "home-server"), and confirm.
4. The dialog shows a one-time registration token and a `docker run` command.
   Copy the full command - the token is shown only once.

## Running the agent

Run the command from the dialog, substituting the image tag if using a local build:

```bash
docker run -d \
  --restart unless-stopped \
  --name splitsmith-agent \
  -v splitsmith-agent:/data \
  <IMAGE> agent \
  --server-url https://my.splitsmith.app \
  --token <REGISTRATION_TOKEN>
```

The `-v splitsmith-agent:/data` flag mounts a named volume at the agent's state dir.
On first start, the agent exchanges the registration token for credentials and writes
`/data/agent.json`. That file persists across container restarts.

The `--token` flag is only needed on the first run. Once `agent.json` exists, the agent
uses it directly and `--token` is ignored.

To check logs:

```bash
docker logs -f splitsmith-agent
```

On successful registration you will see lines like:

```
registered worker <id>
connecting to channel ...
connected, waiting for wake events
```

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
