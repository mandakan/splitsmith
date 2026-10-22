---
name: staging-login
description: Use when the user asks for a staging login link, wants to sign in to my.staging.splitsmith.app (e.g. from a phone), or asks how to log in on staging without email access.
---

# Staging login link

Staging never sends real mail -- magic links land in the staging serve
service's logs (Cloud Run, `splitsmith-serve-staging`, project
`splitsmith-hosting`). `scripts/staging_login_link.py` mints one and
prints it:

```bash
uv run python scripts/staging_login_link.py [email]   # default: m@thias.se
```

Paste the printed URL to the user. It is **single-use** and expires in
**15 minutes**; mint a fresh one on any failure or reuse.

Requirements: an authenticated gcloud CLI (`gcloud auth login`) that can
read the project's logs. If gcloud is missing or unauthenticated
(typical in cloud sessions), say so and ask the user to run
`gcloud auth login` -- there is no other way to read the staging logs.

If the script fails after login checks out, debug from its two moving
parts: `POST https://my.staging.splitsmith.app/api/v1/auth/begin` (mints
the link; the first request after idle cold-starts serve, which can take
most of a minute) and

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="splitsmith-serve-staging" AND jsonPayload.logger="splitsmith.db.email"' --project splitsmith-hosting --freshness 15m --limit 5
```

which must show a `MAGIC_LINK <email> <url>` line in `jsonPayload.message`.
