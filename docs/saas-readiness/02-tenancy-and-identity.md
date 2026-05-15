# 02 -- Tenancy and identity

This doc defines **how Splitsmith identifies users and authorises
access to projects**. It covers the auth abstraction, the v1 magic-
link implementation, the session model, and the data model for users
+ project membership.

The data model is **forward-compatible with v2 squad sharing and v3
public repository** even though v1 only exposes the owner ACL. Adding
ACL tables after-the-fact is painful; we pay the small cost up front.

## The `Auth` abstraction

A single Python interface, two implementations in v1.

```python
class AuthBackend(Protocol):
    async def authenticate_request(
        self, request: Request
    ) -> User | None:
        """
        Inspect the request (cookies, headers) and return the
        authenticated User or None. None means anonymous --
        downstream middleware decides whether that's allowed.
        """

    async def begin_login(self, email: str) -> LoginChallenge:
        """
        Hosted mode: send a magic link; return a challenge handle.
        Local mode: not called -- LoopbackAuth raises NotImplementedError.
        """

    async def complete_login(
        self, challenge_id: str, token: str
    ) -> Session:
        """
        Verify the magic-link token; return a session.
        Local mode: not called.
        """

    async def end_session(self, session_id: str) -> None: ...
```

The interface intentionally does NOT include password operations,
MFA setup, or password reset. Magic link is the only flow.

### Implementations

- **`LoopbackAuth`** -- local mode. `authenticate_request` always
  returns the singleton `User(id="local", email="local@splitsmith")`.
  All login-flow methods raise `NotImplementedError` (they should
  never be called -- the local UI doesn't render login affordances).

- **`MagicLinkAuth`** -- hosted mode. Wraps Clerk or WorkOS or Auth.js
  depending on the final pick. Routes magic-link emails through
  Resend / Postmark.

The wrapper is thin: we don't reimplement session storage, token
rotation, etc. Whatever the provider does is fine.

## Magic-link flow (hosted mode)

The flow is standard:

1. User submits email at `/login`.
2. Server calls `auth.begin_login(email)`. The auth provider sends
   an email with a unique link to `/auth/callback?token=<...>`.
3. User clicks the link.
4. `/auth/callback` handler calls `auth.complete_login(token)`,
   which returns a `Session`. Server sets an httpOnly Secure
   cookie with the session ID.
5. Subsequent requests carry the cookie; `auth.authenticate_request`
   resolves it back to a `User`.

**Token lifetime:** magic-link tokens expire 15 minutes after
issuance and are single-use. (Default for all the picked providers;
we don't need to override.)

**Session lifetime:** 30 days, sliding (extends on activity). The
user can sign out from any device; signing out invalidates only
that session.

**Email throttling:** the picked provider handles per-email rate
limits (typically "max 5 magic links per email per hour"). We don't
add our own rate limiter on top.

### Why no passwords

Three reasons:

1. **Surface area.** Passwords mean a password reset flow, password
   strength rules, breach detection, MFA pressure, and a password
   storage column we're now liable for. Magic link has none of that.
2. **UX.** "Type your email, click the link" is fewer steps than
   "remember your password, find it in your manager, get told it's
   expired".
3. **Threat model.** The magic link IS the second factor (you need
   email access to receive it). For a hobbyist tool with personal
   match data, this is sufficient.

If a user demands passwords (e.g. because their email is unreliable),
we point them at a password manager that supports magic-link auto-
fill, or we ship federated SSO (Q4 in doc 00, deferred to v2).

## Session model

```python
class Session(BaseModel):
    id: str                   # opaque; never the user ID
    user_id: str
    created_at: datetime
    last_used_at: datetime
    user_agent: str | None    # for the "your devices" UI later
    ip_inet: IPvAnyAddress | None
```

Sessions live in Postgres, not in the auth provider, so we can:

- List a user's active sessions.
- Revoke a single session without nuking all of them.
- Hold session metadata (UA, last-used) without depending on the
  provider exposing it.

Cookie attributes: `HttpOnly`, `Secure`, `SameSite=Lax`, host-only
(no `Domain=` set). 30-day expiry refreshed on activity.

CSRF: the FastAPI `CsrfProtect` middleware (or whatever Starlette
helper we settle on) on all state-changing routes. Magic-link
callback is a GET, so it's CSRF-safe by definition.

## The `User` table

```sql
CREATE TABLE users (
  id            TEXT PRIMARY KEY,         -- ULID, not auto-increment
  email         TEXT UNIQUE NOT NULL,
  email_verified_at TIMESTAMPTZ,
  display_name  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- billing
  stripe_customer_id  TEXT UNIQUE,        -- nullable; set on first checkout
  entitlement         TEXT NOT NULL DEFAULT 'free',
                                          -- 'free' | 'premium'
  entitlement_until   TIMESTAMPTZ,        -- null = perpetual; otherwise expiry
  -- soft delete
  deleted_at    TIMESTAMPTZ
);

CREATE INDEX users_email_idx ON users (lower(email));
```

Email is the natural key from the user's perspective (magic link
lookup) but we use a synthetic ULID as the PK so we can change email
without rewriting foreign keys.

## The project ACL data model

This is the v1-vs-v2 forward-compat hot zone. We ship v2's ACL tables
in v1; we just only ever insert one row per project (the owner).

```sql
CREATE TABLE projects (
  id              TEXT PRIMARY KEY,        -- ULID
  storage_path    TEXT NOT NULL,           -- e.g. 's3://bucket/projects/<id>/'
  display_name    TEXT NOT NULL,
  match_date      DATE,
  owner_user_id   TEXT NOT NULL REFERENCES users(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- v2 fields, present but always default in v1
  visibility      TEXT NOT NULL DEFAULT 'private',
                                            -- 'private' | 'shared' | 'public'
  deleted_at      TIMESTAMPTZ
);

CREATE TABLE project_members (
  project_id      TEXT NOT NULL REFERENCES projects(id),
  user_id         TEXT NOT NULL REFERENCES users(id),
  role            TEXT NOT NULL,           -- 'owner' | 'editor' | 'viewer'
  added_by        TEXT NOT NULL REFERENCES users(id),
  added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, user_id)
);
```

In v1:

- Every project has exactly one row in `project_members` with role
  `'owner'`.
- The hosted ACL check is a single query: "does this user have a
  `project_members` row for this project?"
- `visibility` is always `'private'` for new projects.

In v2:

- Squad sharing inserts additional rows with role `'editor'` /
  `'viewer'`. The check above already covers the new cases.
- `visibility = 'shared'` means the squad-share invite link is
  enabled.
- `visibility = 'public'` is v3 (public repo). The check changes to
  "ACL row OR (visibility='public' AND read-only operation)".

Every API handler that touches a project takes the project_id and
asserts membership before doing anything else. There is **no
"current project" implicit context** in the request -- always
explicit, always checked.

## Project ID -- what it represents

A `project_id` corresponds to one **single-shooter MatchProject** --
the same unit as today's local on-disk projects. A multi-shooter
match where N people use Splitsmith means N project_id rows, each
owned by the respective user.

The cross-shooter compare flow (`splitsmith compare export`) becomes
"the system enumerates project_ids the requesting user has access
to, filters to the same `match_date` + venue, and offers them as
manifest entries". That's a v2 deliverable -- v1 hosted does not have
multi-shooter compare in the UI.

## The `linked accounts` model (desktop -> cloud)

When a desktop user signs into hosted from the local app:

- The desktop opens a browser to `/link/desktop?challenge=<...>`.
- The user signs in (magic link as usual).
- The hosted app redirects to a deep link `splitsmith://link?
  token=<...>`.
- The desktop captures the token, exchanges it server-side for a
  long-lived refresh token (NOT a session cookie -- desktop is not
  a browser).
- Refresh token + access token are stored at `~/.splitsmith/auth.json`
  with `0600` perms.

```sql
CREATE TABLE desktop_links (
  id                TEXT PRIMARY KEY,
  user_id           TEXT NOT NULL REFERENCES users(id),
  refresh_token_hash TEXT NOT NULL,        -- argon2 of the actual token
  device_name       TEXT,                  -- 'Mathias's MacBook Pro'
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at      TIMESTAMPTZ,
  revoked_at        TIMESTAMPTZ
);
```

Desktop link tokens have separate revocation from browser sessions
so the user can "revoke this Mac from my account" without losing
their browser session.

API requests from the desktop carry `Authorization: Bearer <access>`;
the access token is short-lived (1h) and refreshed on demand.

## Anonymous access

Hosted mode has no anonymous endpoints in v1 except:

- `GET /` (marketing page)
- `POST /api/v1/auth/begin` (start magic link)
- `GET /auth/callback` (complete magic link)
- `GET /healthz`

Every other API endpoint requires authentication. The default
middleware response for unauth'd access is 401 with no body --
no implicit redirect to login (that's a frontend concern).

## What about the local-mode "user"?

Local mode's `LoopbackAuth.user.id == 'local'`. Any code path that
embeds the user ID into a JSON file or a path uses this stable
sentinel. Importing a local-mode tarball into hosted mode rewrites
this sentinel to the importing user's real ID; see 07-sync-and-
migration.md.

## Open questions

- **Final pick: Clerk vs WorkOS vs Auth.js.** Pricing, EU residency
  story, and self-host blast radius all matter. Prototype each.
- **Display name source.** Magic link gives us only the email.
  Probably ask the user for a display name on first login (one-
  field form). Can't be required (we'd block them from doing
  anything) but should be prompted.
- **Username for sharing URLs.** v2's squad-share invite probably
  wants a `splitsmith.app/u/<handle>` URL. Defer the handle
  reservation question until v2 design.
- **Revoking sessions on entitlement downgrade.** If a user cancels
  their premium subscription, do we keep their sessions? Yes -- they
  can still read their data; they just can't run new Tier 2/3 jobs.
  Detail in 08.
