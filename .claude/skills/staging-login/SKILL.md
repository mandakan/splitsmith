---
name: staging-login
description: Use when the user asks for a staging login link, wants to sign in to my.staging.splitsmith.app (e.g. from a phone), or asks how to log in on staging without email access.
---

# Staging login link

Staging never sends real mail -- magic links land in the Railway serve
logs. `scripts/staging_login_link.py` mints one and prints it:

```bash
uv run python scripts/staging_login_link.py [email]   # default: m@thias.se
```

Paste the printed URL to the user. It is **single-use** and expires in
**15 minutes**; mint a fresh one on any failure or reuse.

Requirements: an authenticated Railway CLI (`railway` on PATH or
`~/.railway/bin/railway`) linked to the `splitsmith` project.
(`railway status` showing "Linked service: None" is normal -- only the
project/environment link matters.) If the
CLI is missing or unauthenticated (typical in cloud sessions), say so
and ask the user to run `railway login` -- there is no other way to
read the staging logs.

If the script fails after login checks out, debug from its two moving
parts: `POST https://my.staging.splitsmith.app/api/v1/auth/begin`
(mints the link) and `railway logs --service serve --environment
staging` (must show a `MAGIC_LINK <email> <url>` line).
