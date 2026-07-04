# Public share links for match results (MVP)

Date: 2026-07-04
Issues: #349 (product/UX), #449 (data-model design, token path), #348 (cloud sync)
Approved: approach A (token-scoped public router reusing existing handlers), raw
token storage, video included, revoke-only lifetime.

## Goal

An owner of a hosted match can create an unguessable, revocable link that lets
anyone - no account - open the read-only Results surface for that match in a
browser: multi-shooter overview plus per-stage playback with video, splits, and
stage stats.

Non-goals (explicitly out of scope for this MVP):

- Rate limiting / abuse controls on the public prefix
- Presigned-URL video egress (streaming stays proxied through the server)
- Expiry UI (schema carries `expires_at`, always NULL from the MVP UI)
- Scoped shares (`scope_json`: stage/shooter/CSV slicing per #349)
- Account-member sharing (`match_members` + RLS OR-clause per #449)
- Local mode (share endpoints and UI are hosted-only)

## Why not presigned URLs as the link

A presigned S3 URL expires (7-day max), cannot be revoked without key
rotation, addresses a single object, and most Results data lives in Postgres
`state_docs`, not object storage. The share link is an opaque capability token
checked server-side; presigned URLs may later appear inside the video egress
path as an optimization, never as the shared artifact.

## Data model

New table `share_tokens` (Alembic migration):

| column       | type                    | notes                                    |
|--------------|-------------------------|------------------------------------------|
| `id`         | String ULID PK          | `new_ulid()` default                     |
| `user_id`    | String FK users.id      | owner; CASCADE on delete; indexed        |
| `match_id`   | String, non-null        | the shared match                         |
| `token`      | String, unique          | `secrets.token_urlsafe(32)` (256 bits), stored raw |
| `created_at` | DateTime(tz)            | server default now()                     |
| `revoked_at` | DateTime(tz), nullable  | revoke = set timestamp; row kept as audit trail |
| `expires_at` | DateTime(tz), nullable  | honored by resolver; always NULL in MVP  |

- Multiple live tokens per match are allowed.
- Raw storage (not hashed) is deliberate: the share dialog re-displays links
  for copying. The token is a read-only, revocable capability; this differs
  from `sessions`/`magic_link_tokens` where a DB leak would yield full account
  access.
- No RLS, following the `sessions` precedent: anonymous resolution runs before
  any `app.user_id` GUC exists. The unique-token lookup is the boundary for
  the anonymous path; owner-management queries filter by `user_id` explicitly
  and isolation tests guard that invariant (multi-tenant checklist).

New store `src/splitsmith/db/share_tokens.py`:

- `ShareTokenStore(session_factory, user_id)` for owner management:
  `create(match_id) -> ShareToken`, `list_for_match(match_id)`,
  `revoke(share_id) -> bool` (scoped to `user_id`).
- Module-level `resolve(session_factory, token) -> ResolvedShare | None`
  (owner `user_id` + `match_id`) for the anonymous path: returns None when
  missing, revoked, or expired - the three cases are indistinguishable to the
  caller by design.

## Backend: owner management routes (session-authed, hosted-only)

Mounted only in hosted mode; absent (404) in local mode, like the login routes.

- `POST /api/matches/{match_id}/shares` - validates match ownership (the
  existing `_match_id_alias` middleware already resolves ownership through the
  RLS-scoped matches store), creates a token, returns
  `{id, url, created_at, revoked_at: null}` where
  `url = {public_url}/share/{token}`.
- `GET /api/matches/{match_id}/shares` - all tokens for the match, live and
  revoked, newest first, each with its full `url`.
- `DELETE /api/matches/{match_id}/shares/{share_id}` - sets `revoked_at`;
  404 if the id is not the caller's.

## Backend: anonymous read path

`_auth_gate` exempts the `/api/share/` path prefix (prefix match alongside the
existing `_PUBLIC_API_PATHS` exact-match set). A dedicated router mounts
exactly four routes:

- `GET /api/share/{token}/match` - match doc + shooter roster + stage
  statuses: the same payload shape MatchShell feeds the Results overview.
- `GET /api/share/{token}/shooters/{slug}/project`
- `GET /api/share/{token}/shooters/{slug}/stages/{stage_number}/coach`
- `GET /api/share/{token}/shooters/{slug}/videos/stream`

A shared resolver dependency:

1. `resolve(token)`; None -> uniform `404 {"detail": "not found"}` (no
   existence/revocation leak).
2. Pins `current_tenant = state.build_tenant(owner_user_id)` and
   `current_match_id`/`current_match_root` from the token row - never from the
   URL - for the request duration, mirroring what `_auth_gate` +
   `_match_id_alias` do for session requests.
3. Delegates to the existing handler logic for project/coach/stream (no
   duplication); `shooter_root`'s roster check still rejects slugs outside the
   match.

Containment guardrail for the owner-impersonation tradeoff accepted in
approach A: a test asserts the share router exposes exactly these four
GET routes and nothing else. Route handlers under `/api/share/` never read
`match_id` from client input.

## SPA

- New public routes outside MatchShell and outside the auth guard:
  - `/share/:token` - Results overview
  - `/share/:token/:slug/:stage` - stage playback
- `ShareShell` fetches `/api/share/{token}/match`, provides the same outlet
  context MatchShell gives Results (shooters with stage_statuses, project),
  and renders the existing `Results` / `ResultsStage` pages unchanged. This is
  the "share provider" the Results read-only contract anticipated; no
  mutations, localStorage, or auth assumptions exist in `components/results/`.
- `scopeRequestPath` in `lib/api.ts` gains a share mode: when the location is
  under `/share/{token}`, `/api/shooters/...` and `/api/match/...` paths
  rewrite to `/api/share/{token}/...`. Video stream URLs already flow through
  the same rewrite.
- A 404 from any share endpoint renders a full-page "This link is no longer
  available" state; the login redirect must not fire on `/share` routes.
- Mobile-friendly by construction (Results already is; no DesktopGate).

## Share management UX

"Share" button on the Results overview page, hosted mode only (via
`useDeploymentMode()`), signed-in owner view. Opens a dialog following the
overlay conventions (z tokens, body Portal, `useDialogFocus`):

- "Create link" button; new link appears in the list.
- Each live link: full URL, copy button, created date, "Revoke".
- Revoked links stay listed with their revocation date, visually muted.
- No expiry controls.

## Error handling summary

| case                                   | behavior                          |
|----------------------------------------|-----------------------------------|
| unknown / revoked / expired token      | uniform 404 on every share route  |
| slug not in match roster               | existing 404 from `shooter_root`  |
| share routes in local mode             | 404 (router not mounted)          |
| management routes without session      | 401 from `_auth_gate`             |
| management on someone else's match/id  | 404 (RLS + user_id-scoped store)  |

## Testing

- Store: create/resolve round-trip, revoke stops resolution, expired token
  stops resolution, cross-tenant isolation (owner B cannot list/revoke A's).
- API (hosted test app, seeded state docs): happy path per share endpoint;
  revoked/invalid 404; whitelist assertion on the share router's route table;
  management routes 401 anonymous; local mode has no share routes.
- SPA: typecheck + build + scoped eslint (no test runner in ui_static).
- Gates before PR: ruff + black + pytest locally; `pytest -m docker` because
  this adds a migration.
