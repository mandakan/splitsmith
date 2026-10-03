# Hosted access tiers and access requests

Date: 2026-10-03. Status: design approved in brainstorming, spec under review.

## Goal

Let friends and club mates use the hosted side to sync and share from
the desktop app without letting them run hosted-only work: no
hosted-native matches, no raw uploads, nothing on the worker fleet.
Letting someone in must be a click in an admin UI, not an env-var edit
and a redeploy.

Success: the admin can approve a stranger's request or a club mate in a
few seconds, and every account below `full` provably cannot reach the
worker fleet, including through routes and job kinds added later.

Decisions taken during brainstorming, in order:

1. **Access is its own axis, separate from billing.** The unused
   `users.entitlement` / `entitlement_until` / `stripe_customer_id`
   columns stay reserved for billing (doc
   `docs/saas-readiness/08-billing-and-quotas.md`). "A trusted club mate
   gets hosted compute free" and "a stranger pays for compute" are
   different reasons that unlock the same thing; one column for both
   would make the first awkward.
2. **Getting in: access requests first, invite links later.** This spec
   covers requests. Invite links are a follow-up built on the same tiers.
3. **Two tiers today, but code checks features, never tier names.** A
   tier is a named feature set; adding a third tier is configuration.
4. **Tier definitions live in a code registry**, overridable through
   `SPLITSMITH_CONFIG` YAML. No tiers table, no tier editor, no per-user
   grants (a later addition on top of this, not blocked by it).
5. **One list.** The marketing waitlist (Cloudflare KV today) feeds the
   same `access_requests` table; the existing KV entries are imported
   once.

## What exists today (2026-10-03)

- Magic-link login only (`db/magic_link.py`). New accounts are gated by
  `db/signup_policy.py`: `SPLITSMITH_SIGNUPS_OPEN` (default true) and
  `SPLITSMITH_SIGNUP_ALLOWLIST`. Prod and staging run closed with an
  allowlist. A blocked email gets a challenge-shaped response and no
  mail, so callers cannot tell which emails have accounts. Existing
  users always pass the gate.
- Admins are `SPLITSMITH_ADMIN_EMAILS`; `User.is_admin` is computed per
  request. The only admin surface is worker-fleet CRUD
  (`/api/admin/workers`, `pages/AdminWorkers.tsx`).
- Match capabilities (`ui/capabilities.py`: EDIT, REVIEW, SHARE_MANAGE,
  COMMENT_WRITE) derive from `matches.origin` and are enforced only by
  the `/api/matches/{id}/...` alias middleware. A desktop mirror has no
  EDIT, so every match-scoped job route 403s on a mirror, beep confirm
  on a mirror short-circuits, and a sync push starts no hosted job.
- But any signed-in user can, outside that middleware:
  - create a hosted-native match (`POST /api/match/create-manual`,
    `/api/match/create-from-scoreboard`, `/api/me/projects/import`),
    which grants EDIT on it and so every job route;
  - upload raw video (`/api/me/raw/upload*`), which enqueues
    `generate_proxy` even with zero matches;
  - retry their own failed jobs (`POST /api/me/jobs/{id}/retry`) with no
    capability re-check.
- `PostgresJobBackend.submit` has no per-user check; every registered
  job kind can reach a worker.

## Model

### Features

`splitsmith.access.Feature`, a `StrEnum`:

| Feature | Grants |
|---|---|
| `sync` | `/api/sync/*`: desktop push/pull, mirror creation, the device-flow token |
| `share` | creating and managing share links on own matches |
| `create_match` | hosted-native matches: `create-manual`, `create-from-scoreboard`, `projects/import` |
| `raw_upload` | `/api/me/raw/upload*` browser upload to R2 |
| `hosted_compute` | submitting any job to the hosted backend (including retry) |

Viewing one's own matches, comments and the REVIEW routes on a mirror
need no feature: they are reachable by any signed-in account that owns
the match, and the match capabilities continue to govern them. They
start no hosted job (verified on the current code; the backstop below
keeps it that way).

"Features" is the account-level name on purpose: match-level
"capabilities" already exist and depend on origin. A request needs both
the match capability and the account feature.

### Tiers

`AccessConfig` (Pydantic, in `config.py`, part of `Config`):

```python
class AccessConfig(BaseModel):
    tiers: dict[str, frozenset[Feature]] = {
        "full": frozenset(Feature),
        "sharing": frozenset({Feature.sync, Feature.share}),
        "disabled": frozenset(),
    }
    default_tier: str = "full"
```

- An unknown feature name in YAML fails validation at boot.
- `default_tier` must name a tier in `tiers` (validator).
- `default_tier` is `full` so dev, tests and self-hosting are unchanged;
  prod and staging set it to `sharing` through their config YAML.
- `disabled` doubles as revoke: with no `sync`, a desktop token stops
  working and nothing else is reachable but `/api/me` and logout.

### Storage

- New column `users.access_tier` (String, not null). The migration adds
  it with `server_default='full'` so every existing row is `full`, then
  keeps the server default (an insert that bypasses the ORM still gets a
  safe-for-today value; the ORM sets the real value explicitly).
- New table `access_requests`:

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `email` | String, unique | lowercased |
| `note` | Text, nullable | requester's "who are you", capped at 500 chars |
| `source` | String | `login`, `form`, `waitlist`, `import` |
| `status` | String | `pending`, `approved`, `declined` |
| `requested_at` | timestamptz | first request |
| `last_requested_at` | timestamptz | bumped on repeats |
| `decided_at` | timestamptz, nullable | |
| `decided_by` | String, nullable | admin email |
| `tier_granted` | String, nullable | set on approve |
| `email_sent_at` | timestamptz, nullable | the "you're in" mail; null after a failed send |

Not under RLS (like `users`); only admin routes and the request writer
touch it.

### Resolution

`features_for(user, access_config, admin_emails) -> frozenset[Feature]`,
a pure function in `splitsmith/access.py`:

- an env admin gets every feature regardless of tier;
- otherwise the tier's set;
- a tier name not in the registry resolves to the empty set and logs a
  warning (fail closed).

Local mode never consults it: there are no accounts, everything is
allowed. `require_feature` is a no-op outside hosted mode.

## Enforcement

Two layers, both required.

1. **Route dependency.** `require_feature(Feature.X)` as a FastAPI
   dependency on each entry point:
   - `create_match`: `POST /api/match/create-manual`,
     `/api/match/create-from-scoreboard`, `/api/me/projects/import`;
   - `raw_upload`: `POST /api/me/raw/upload` and the four
     `/api/me/raw/upload/multipart/*` routes;
   - `hosted_compute`: `POST /api/me/jobs/{id}/retry`;
   - `sync`: every `/api/sync/*` route;
   - `share`: `POST /api/matches/{id}/match/shares` and the share
     management writes.

   Refusal is `403 {"error": "feature_required", "feature": "<name>"}`.
   It is a clean, explainable error for the SPA, distinct from the
   match-level `read_only_mirror`.

2. **Backstop.** `PostgresJobBackend.submit` refuses any job when the
   tenant user lacks `hosted_compute`, raising the same
   `feature_required` error. The backend is built per tenant
   (`_build_tenant`), so it carries the resolved feature set. This is
   what keeps a route or job kind added later off the fleet; the route
   dependencies are for good errors, the backstop is for safety.

### Downgrades

Lowering a user from `full` to `sharing` touches no data. Their
existing hosted-native matches stay visible and shareable; anything
that would start a job on them stops at the backstop with
`feature_required`.

## Access requests

### Coming in

Three writers, one table, one upsert (`access_requests.record`):

- **Login with an unknown email.** When the signup policy blocks a new
  email, `begin_login` upserts a pending request (`source: login`)
  instead of silently dropping it. The response stays the
  challenge-shaped one it returns today.
- **"Request access" form on the login page.** Email plus an optional
  note. `POST /api/v1/access-requests` -> always `202`, the same body
  whatever the email's state (`source: form`).
- **The marketing waitlist.** The site's form posts to the same route
  (`source: waitlist`); the route answers CORS for `splitsmith.app`
  only. The Pages Function `functions/api/waitlist.js` is retired once
  the form points at the app. `scripts/import_waitlist.py` reads a
  `wrangler kv key list` / `get` dump and imports existing entries once
  (`source: import`, original timestamps kept).

Rules for every writer:

- **No existence leak.** The user-visible text after either login or
  the form is identical for a known account, a pending request, a
  declined one and a new one: "If you have access, a sign-in link is on
  its way. Otherwise your request has been noted." A test compares the
  response bodies byte for byte.
- **No mail to the requester.** Sending one would let anyone make
  splitsmith mail anyone.
- **Dedupe by email.** A repeat bumps `last_requested_at` and fills a
  previously empty note; it never moves a request out of `declined` or
  `approved`.
- **Email of an existing account:** nothing is recorded.
- **Rate limit** on `POST /api/v1/access-requests`: 5 per IP-hash per
  hour, the waitlist function's existing rule, in-process like
  `CommentRateLimiter`. Over the limit still answers `202` and records
  nothing.

### Admin alert

When a request first becomes `pending`, one email goes to each address
in `SPLITSMITH_ADMIN_EMAILS` naming the email, note and source, with a
link to `/admin/access`. Repeats do not re-alert. A failed alert is
logged and never fails the request.

### Deciding

- **Approve with a tier.** Creates the `users` row at once with that
  `access_tier` (email unverified until the link is redeemed), so the
  existing "known users always pass" path admits them and
  `signup_policy` needs no change. Mints a magic link and sends a
  "you're in" mail. If the email already has an account (they got in by
  the env allowlist meanwhile), approve sets the tier and closes the
  request.
- **Decline.** Silent. The row stays `declined`; repeat requests do not
  return it to pending. It can still be approved later from the
  decided list.
- Deciding an already-decided request is a `409` (two admins, two tabs);
  approving a declined one is allowed.
- **Mail failure on approve.** The approval stands; `email_sent_at`
  stays null, the row shows "email not sent", and
  `POST /api/admin/access-requests/{id}/resend` mints a fresh link.

`EmailSender` gains `send_access_granted(to, link)` and
`send_access_request_alert(to, request)`, implemented on
`ConsoleEmailSender` and `LettermintEmailSender`.

The env allowlist stays as the bootstrap path; accounts created through
it get `default_tier`.

## Admin API

Same gate as `/api/admin/workers` (404 outside hosted mode, 403 for a
non-admin):

| Route | Purpose |
|---|---|
| `GET /api/admin/access-requests?status=` | list, pending first |
| `POST /api/admin/access-requests/{id}/approve` `{tier}` | approve |
| `POST /api/admin/access-requests/{id}/decline` | decline |
| `POST /api/admin/access-requests/{id}/resend` | resend the "you're in" mail |
| `GET /api/admin/users` | email, display name, tier, created |
| `PATCH /api/admin/users/{id}` `{access_tier}` | change tier |
| `GET /api/admin/access-tiers` | the registry, for the tier picker |

A tier not in the registry is a `422`. Changing an env admin's tier is
allowed and has no effect on what they can do; the Users row says so.

## SPA

- `Me` (`GET /api/me`) gains `access_tier` and `features: string[]`.
- `lib/access.ts`: pure `can(me, feature)`, with tests. Pages read
  features through it and never compare tier names.
- Without `create_match`: Matches hides "New match" and "Import"; the
  empty state says, in one line, that matches arrive from the desktop
  app, with a link to the install page.
- Without `raw_upload`: the hosted upload entry points on Footage are
  hidden.
- A `feature_required` 403 that gets through anyway (a stale tab after a
  downgrade) maps to one muted line naming what is not included in this
  account's access; no generic error toast.
- Login page: a "Request access" link opening the form; the
  post-submit copy is the fixed text above.
- `/admin/access` (`pages/AdminAccess.tsx`), linked from the
  AccountChip beside Workers, on the primitives (`PageHeader`, `Table`,
  `Segmented`, `Menu`, `Button`, one primary per view):
  - **Requests:** pending rows with email, note, source and age; a tier
    `Segmented` and Approve / Decline per row. Decided requests in a
    folded group, with "email not sent" and Resend where it applies.
  - **Users:** email, display name, tier, created; tier changed from the
    row menu.
  - Data derivation in `lib/adminAccess.ts` with tests.
- Marketing site: the waitlist form posts to the app route; copy and
  look unchanged.

## Testing

- **`features_for`:** each default tier; env-admin override; unknown
  tier fails closed; YAML override; a bad feature name fails validation;
  `default_tier` not in `tiers` fails validation.
- **Backstop:** for a `sharing` user, submitting every registered job
  kind is refused. The test enumerates the job registry so a new kind is
  covered with no edit. Delete the backstop and confirm the test fails
  before calling it done.
- **Route gates:** a table test over every gated route, `sharing` vs
  `full` vs `disabled`. Same deletion check for one route.
- **Request flow:** unknown email at login creates a request; the login
  and form responses for known / pending / declined / new emails are
  byte-identical; dedupe; note fill-in; rate limit still answers 202;
  approve creates the user with the tier and sends mail; approve of an
  existing account sets the tier; decline; re-request after decline
  stays declined; 409 on double decide; mail failure leaves
  `email_sent_at` null and resend works; admin alert once per request.
- **Migration:** existing rows become `full`; upgrade and downgrade
  round-trip.
- **Waitlist import:** a dump fixture imports with timestamps kept and
  skips emails already present.
- **SPA:** vitest for `lib/access.ts` and `lib/adminAccess.ts`;
  Playwright on the demo harness for the login form, the Matches empty
  state without `create_match`, and `/admin/access`, with screenshots
  shown before calling the UI done.

## Rollout

1. Ship with `default_tier: full` everywhere: no behaviour change.
2. Set prod and staging config to `default_tier: sharing`.
3. Point the marketing form at the app, run the KV import, retire the
   Pages Function.

## Out of scope (follow-ups)

- Invite links (admin-created, tier-bearing, use-limited, expiring).
- Per-user feature grants or removals on top of a tier.
- Quotas, usage display and billing (`entitlement`).
- Hosted-mode hardening found while tracing this is tracked separately.
