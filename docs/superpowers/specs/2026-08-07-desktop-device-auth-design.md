# Browser-assisted desktop auth - design

Date: 2026-08-07
Status: approved (brainstorming session)
Tracking: #719 (filed from this spec), under #631 -- the "browser-assisted
device auth" follow-up noted when the sync MVP landed in #707
Precondition: #550 (RootLayout extraction) -- DONE, PR #724. See "Sequencing"

## Problem

The desktop-to-hosted sync MVP authenticates with a paste-once personal
desktop token: the user generates it on the hosted account page, copies it,
and pastes it into `SyncSettingsDialog` on the desktop install. Two things
are wrong with that.

1. It is a copy-paste ritual across two machines. The desktop install often
   runs on a different box from the browser, so the token travels through
   whatever channel the user has to hand.
2. The token resolves to a full-tenant credential. `DesktopTokenAuth` returns
   a normal `User`, so the bearer can reach every hosted route, not just the
   sync surface it was issued for. This was spec-sanctioned for the MVP and
   flagged for exactly this iteration.

Beyond the mechanic, the desktop install has no concept of *who* it is linked
to. `GET /api/settings/hosted-sync` reports a `token_set` boolean and nothing
else, so the UI cannot say "signed in as ..." or offer a sign-out.

## Scope

In: replacing the paste with a browser-assisted device flow, scoping the
resulting credential down to the sync surface, and surfacing the linked
account as a signed-in identity in the local UI.

Out: hosted-to-desktop pull, browsing hosted matches from the local UI, token
expiry and refresh, and rate-limiting authorization creation beyond the
per-`device_code` interval throttle.

## Decisions made in session

- **Device code with polling**, not a loopback redirect. The desktop install
  frequently runs on a remote or headless box while the browser is on a
  laptop; a device code works in that topology and a loopback callback does
  not.
- **Scoped long-lived token**, not short-lived access plus refresh. The
  credential stays a non-expiring opaque token in `desktop_tokens`, gains a
  `scope` column, and is revocable from either end. No refresh machinery, no
  re-auth in the middle of a long push.
- **Signed-in identity in the local UI**, hung off the `RootLayout` that #550
  extracts.
- **#550 ships first as its own PR.** It is a pure refactor with no behaviour
  change and its own acceptance criteria. Folding it in here would put a
  refactor and an auth flow in one diff.

## Hosted side

### `device_authorizations`

A new table, not under RLS, resolved pre-tenant for the same reason
`desktop_tokens` and `share_tokens` are: the polling request authenticates
from the device code alone, before any `app.user_id` GUC exists.

```
id                 ulid, pk
device_code_hash   unique, sha256 of the plaintext device code
user_code          unique, the short human-entered code
device_name        what the desktop install called itself
scope              requested scope, "sync"
status             pending | approved | denied | consumed
user_id            null until approved, then FK users.id
created_at
expires_at
```

`user_code` is 8 characters from an alphabet with `I`, `L`, `O`, `U`, `0` and
`1` removed, rendered `XXXX-XXXX`. Its entropy is low on purpose: it is only
usable by a caller who already holds a session and who then has to approve,
and it expires in 10 minutes. The real secret is `device_code` -- 32 bytes
from `secrets.token_urlsafe`, stored only as a hash, reusing the existing
`_mint` / `_hash` helpers in `db/workers.py`.

### Endpoints

A new `ui/device_auth_api.py`, hosted-gated the same way `sync_api.py` is.

| Route | Auth | Behaviour |
| --- | --- | --- |
| `POST /api/device/authorize` | public | `{device_name}` returns `{device_code, user_code, verification_uri, verification_uri_complete, expires_in: 600, interval: 5}` |
| `POST /api/device/token` | public | `{device_code}` returns one of `pending`, `slow_down`, `denied`, `expired`, or `approved` with the token and the user |
| `GET /api/device/pending/{user_code}` | session cookie | data for the approval screen: device name, requested scope, age |
| `POST /api/device/pending/{user_code}/approve` | session cookie | sets `status='approved'` and `user_id` |
| `POST /api/device/pending/{user_code}/deny` | session cookie | sets `status='denied'` |
| `DELETE /api/device/session` | sync bearer | revokes the calling token's own row |

The two public routes join `_PUBLIC_API_PATHS` in `server.py`, on the same
rationale already recorded there for `/api/workers/register`: the credential
in the request *is* the authorization, and the session gate must not 401 a
box with no cookie jar.

### The token is minted at poll time

Approving does not mint anything. It records `status='approved'` and the
approving `user_id`. The first successful poll performs a conditional update
from `approved` to `consumed` and mints the `desktop_tokens` row only if that
update touched a row.

Two properties follow. No plaintext credential is ever stored at rest, not
even for the seconds between approval and collection. And two concurrent
polls cannot mint two tokens, because only one of them wins the conditional
update.

Once consumed, further polls on the same device code report `expired`. An
unknown device code reports `expired` as well, so a caller cannot probe for
which codes exist.

### Surviving the login redirect

`verification_uri_complete` is `/desktop/approve?code=XXXX-XXXX`, so a user
with a live session lands on a prefilled approval screen and clicks once.

With no session, the SPA stashes the code in `sessionStorage` and sends the
user to the login surface. The magic link returns them to `/`, the stashed
code is picked up, and they are bounced to the prefilled approve screen. If
the magic link opened in a different browser, that stash is gone and
`/desktop/approve` renders an input for the 8 characters instead. That is the
conventional device-flow fallback, and taking it means `magic_link.py` needs
no `next` parameter plumbed through it.

## The credential

### `desktop_tokens.scope`

Existing rows backfill to `'full'`. Every token minted after this lands is
`'sync'` -- from the device flow and from the account page's manual button
alike. The paste path therefore survives as the escape hatch for a box with
no browser at all, but stops issuing full-tenant credentials immediately.
The only `'full'` tokens that will ever exist are the ones already issued.

### `User.token_scope`

`User` gains `token_scope: str | None = None`. `DesktopTokenAuth` populates
it from the row; `MagicLinkAuth` and `LoopbackAuth` leave it `None`, meaning
unrestricted.

`CompositeAuth`'s docstring currently states that downstream code never
distinguishes which backend answered. That stops being true here, so the
docstring is updated in the same change rather than left to go stale.

### One gate, one place

In `_auth_gate` (`ui/server.py`), immediately after `request.state.user = user`:

```python
if (
    user.token_scope == "sync"
    and not path.startswith("/api/sync/")
    and path != "/api/device/session"
):
    return JSONResponse(status_code=403, content={"detail": "token scope"})
```

A sync token reaching `/api/matches/...`, `/api/admin/workers`, or the
share-token mint surface gets a 403 rather than full tenant access.
`/api/device/session` is the single exception, and it is what lets the local
UI sign out without holding a cookie.

Two consequences, stated rather than discovered later:

- A sync-scoped token cannot read `/api/me`. The local install's knowledge of
  which account it is linked to therefore comes from the device-flow poll
  response, cached in `config.yaml`, not from a live lookup. A hosted-side
  email change will not propagate until the install re-links. Accepted: the
  alternative is widening the scope for a cosmetic field.
- Installs holding a pasted `'full'` token are untouched, because
  `token_scope='full'` fails the `== "sync"` test.

## Local side

### Config

`GlobalPrefs` keeps `hosted_base_url` and `hosted_token` unchanged and gains
a single nested field:

```python
hosted_account: HostedAccountRef | None = None
#   id, email, display_name, device_name, linked_at
```

One nested model rather than five flat fields, per that model's own
instruction to add sparingly.

### Endpoints

Under the existing `/api/settings/hosted-sync` prefix:

| Route | Behaviour |
| --- | --- |
| `POST .../device/start` | calls hosted `authorize` with `device_name = socket.gethostname()`, keeps `{device_code, interval, expires_at, last_polled_at}` in memory on `AppState`, returns the user code and verification URLs |
| `GET .../device/status` | forwards to hosted `POST /api/device/token` at most once per `interval`, returning the cached verdict in between; on approval writes the token and account into prefs and clears the pending state |
| `DELETE .../session` | calls hosted `DELETE /api/device/session`, then clears `hosted_token` and `hosted_account` |

`GET /api/settings/hosted-sync` grows the account block. It continues never
to echo `hosted_token` back to the SPA.

Polling is lazy rather than a background task. The SPA's own poll drives it,
the server-side interval throttle keeps a fast-refreshing SPA from tripping
`slow_down`, and closing the tab leaves no orphaned poller behind. The cost
is that pending state is in memory, so restarting the local server mid-flow
means starting over. Acceptable inside a 10-minute window.

If the hosted self-revoke call fails on sign-out (offline, or the token was
already revoked from the account page), the local side clears its prefs
anyway and returns a flag so the UI can say the local copy is gone and offer
the account page for certainty. Leaving a dead token in `config.yaml` because
the network was down would be the worse failure.

### HTTP client

`HostedSyncClient` gains a constructor path that builds an unauthenticated
httpx client for the two device calls. There is no bearer to send yet, by
definition.

### UI

`HostedAccountChip` is a new local-mode-only component, deliberately separate
from the hosted-only `AccountChip`. They look similar and mean different
things: `AccountChip` shows the session you are logged in *as*, this one
shows the hosted account this install is *linked to*. Collapsing them would
conflate a session with a stored credential.

Signed out it reads "Sign in to splitsmith.app". Signed in it shows the email
plus a menu carrying the device name and sign out. Both chips mount once, in
`RootLayout`, and self-gate on deployment mode.

The chip deliberately does not show a "last sync" time. Sync state is
per-match and already lives on `SyncCard`; the hosted token row's
`last_used_at` is the only account-level equivalent, and a sync-scoped token
cannot read it back.

`DeviceLoginDialog` shows the user code large and monospaced, a button that
`window.open`s the prefilled approve URL, and a waiting state driven by the
status poll. Denied and expired render distinct terminal copy -- "you
declined this on splitsmith.app" and "the code ran out, start again" are
different problems and should not share a message.

The `window.open` is what makes the remote-host topology work: the SPA runs
in the operator's browser even when the server is on another box, so the
approve page opens where the operator actually is.

`SyncSettingsDialog` loses its token field and keeps the base URL, which is
still needed to point an install at staging. The paste path moves behind an
"Advanced" disclosure.

## Sequencing

1. **#550** -- extract `RootLayout`, move global chrome into it. **Done**, PR
   #724. It turned out not to be the pure refactor the issue described: the
   owner chose a real global header bar, so the shells were slimmed to a
   context row beneath it rather than keeping their own headers.
2. **This spec** -- hosted device-flow endpoints and the scope gate, then the
   local endpoints, then the UI hung off `RootLayout`.

What #550 leaves for this spec to build on:

- `GlobalBar` (`src/components/layout/GlobalBar.tsx`) is where `AccountChip`
  now mounts, and where `HostedAccountChip` mounts alongside it. Both
  self-gate on deployment mode, so the local-only and hosted-only chips
  never render together.
- `RootLayout` mounts `GlobalBar` once, so there is no per-surface
  duplication to repeat -- `globalChrome.test.tsx` guards that and will fail
  if a third mount appears. Add `HostedAccountChip` to that test's expected
  list when it lands, rather than working around it.
- On mobile the global bar renders unless the shell claims the account menu
  via `useShellOwnsMobileAccount()`; only `MatchShell` does. A device-flow
  dialog opened from the chip must therefore work on a phone from `/pick`,
  where the bar is present, and from a match page's nav drawer, where it is
  not.

## Testing

- Hosted endpoints against the SQLite state store, as existing hosted tests
  do: happy path, denied, expired, poll-before-approve, unknown device code,
  and a double-poll asserting exactly one token row is minted.
- Scope-gate tests: a sync token gets 200 on `/api/sync/*`, 403 on
  `/api/matches/*`, and 200 on `DELETE /api/device/session`; a `'full'` token
  is unaffected. These get the mutation drill from the review practice in
  CLAUDE.md -- delete the gate, confirm they go red. A scope test that passes
  without the gate present is worth nothing.
- One docker-marked test over the Postgres path, the standing rule for DB
  changes.
- Local side with the hosted server mocked: prefs written on approval,
  cleared on sign-out, cleared even when the hosted revoke call fails, and
  `hosted_token` never echoed back to the SPA.
- Vitest on the dialog state machine: pending to approved closes the dialog;
  denied and expired render their distinct terminal copy.
- One alembic migration covering `device_authorizations` and
  `desktop_tokens.scope`, with the backfill to `'full'`.

## Relation to the sync MVP spec

`docs/superpowers/specs/2026-08-07-desktop-hosted-sync-mvp-design.md` lists
browser-assisted device auth under "Out of scope (future work)". This
document is that work. Its "Auth: desktop tokens" section stays accurate for
the paste path, which survives as the escape hatch; the scope column and the
gate are additions to it, not replacements.
