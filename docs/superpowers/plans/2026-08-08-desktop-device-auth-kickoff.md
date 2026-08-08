# Issue #719 kickoff -- browser-assisted desktop auth

Entry point for a session starting fresh on #719. Assumes no memory of the
sessions that built the sync MVP (#631/#707), wrote this spec (#726), or
landed its precondition (#550/#724).

**The issue:** https://github.com/mandakan/splitsmith/issues/719
**The design doc:** `docs/superpowers/specs/2026-08-07-desktop-device-auth-design.md`
-- approved, on `main`, and still accurate except where corrected below.

Read the spec first. It is the requirements. This file only carries what
has changed underneath it and what will bite you.

## Why this exists

The desktop-to-hosted sync MVP authenticates with a paste-once token: you
generate it on the hosted account page, copy it, and paste it into
`SyncSettingsDialog`. Two problems. It is a copy-paste ritual across two
machines -- the desktop install often runs on a different box from the
browser. And `DesktopTokenAuth` resolves the bearer to a normal `User`, so
it reaches every hosted route, not just the sync surface it was issued for.

This replaces the paste with a device-code flow, scopes the credential
down, and gives the local UI a signed-in identity.

## Correct the premise before you start

### Changed -- `hosted_base_url` is now the bare origin (#712)

The spec was written before #712 landed. `HostedSyncClient` now owns the
`/api/sync` path prefix itself and `base_url` is the bare hosted origin
(`https://my.splitsmith.app`, no path). See `src/splitsmith/sync/client.py`
-- every call site is now `self._http.post("/api/sync/matches", ...)` and
so on.

Consequence for this work: the two device calls must use full paths
(`/api/device/authorize`, `/api/device/token`) on the same bare-origin
client. Do not assume a `/api/device` base or reuse a stale mental model
where the client is pre-prefixed.

`sync/client.py` also gained phase timings (#710) and concurrent part PUTs
(#713/#715) since the spec. Neither blocks this work, but the file has
moved -- read it rather than trusting a remembered shape.

### Landed -- #550, and what it left you

The spec names #550 as step 1 of its Sequencing. It shipped in v0.19.0
(PR #724). Three things it leaves that this work builds on:

- **`src/splitsmith/ui_static/src/components/layout/GlobalBar.tsx`** is
  where `AccountChip` mounts (line 30). `HostedAccountChip` mounts here
  too, alongside it. Both self-gate on deployment mode -- `AccountChip`
  renders `null` outside hosted, so the local-only chip and the hosted-only
  chip never appear together.
- **`globalChrome.test.tsx` guards the mount count.** It asserts
  `<AccountChip` appears in exactly two files: `GlobalBar.tsx` and
  `MatchShell.tsx` (the mobile nav drawer). When `HostedAccountChip` lands,
  **extend that expectation** -- do not work around the test. It exists
  because the chip had accreted three copies before #550.
- **The mobile rule.** `RootLayout` renders the global bar on mobile
  *unless* the mounted shell calls `useShellOwnsMobileAccount()`. Only
  `MatchShell` does, because only it has a nav drawer. So a device-login
  dialog opened from the chip must work in two places: from `/pick` on a
  phone, where the global bar is present, and from a match page's nav
  drawer, where it is not.

### Not yet built -- confirm these are still absent

The spec describes them as additions; verify before writing:

- `User` (`src/splitsmith/auth.py`) has `id`, `email`, `display_name`,
  `is_admin`. **No `token_scope`.** The spec adds it.
- `DesktopTokenRow` (`src/splitsmith/db/models.py`) has no `scope` column.
- No `device_authorizations` table, no `ui/device_auth_api.py`.

## Current line numbers (they drift -- re-grep, do not trust these)

| what | where |
|---|---|
| `_PUBLIC_API_PATHS` | `src/splitsmith/ui/server.py:898` |
| `_auth_gate` | `src/splitsmith/ui/server.py:6241` |
| `request.state.user = user` (the scope gate goes right after) | `src/splitsmith/ui/server.py:6265` |
| `DesktopTokenStore` / `DesktopTokenAuth` | `src/splitsmith/db/desktop_tokens.py` |
| local sync settings | `GlobalPrefs` in `src/splitsmith/user_config.py:127` |
| the paste dialog this replaces | `components/match/SyncSettingsDialog.tsx` |
| hosted token management UI | `components/account/DesktopTokensDialog.tsx` |

The spec cites `server.py:6240` for the gate; it is 6241 now.

## What must not move

- **Existing pasted tokens keep working.** They backfill to `scope='full'`
  and fail the `== "sync"` test, so the gate ignores them. An install in
  the field must not break.
- **The paste path survives** as the escape hatch for a box with no
  browser at all, demoted behind an "Advanced" disclosure.
- **`/pick` keeps its mobile account menu.** This was nearly regressed
  during #550 and is the reason the mobile-ownership flag exists.

## Traps that already cost time in this repo

- **`pnpm` is not on PATH.** Use `corepack pnpm`. A silent
  "command not found" reads as success if you only grep for failures --
  five measurement runs were lost to exactly that.
- **`src/lib/features.ts` caches `getServerFeatures()` in a module-level
  promise with no invalidation.** The first deployment mode resolved in a
  test file wins for that whole file. If you need both local and hosted in
  one suite, use a second file -- see `GlobalBar.hosted.test.tsx` and
  `MatchShell.mobileAccount.test.tsx` for the established pattern.
- **Tests that pass for the wrong reason.** Three were caught during #550,
  all in plan-authored test blocks: a component asserted only under a mock
  where it renders `null`; a mobile test that could not fail because it
  mocked the breakpoint false; a route test that silently rendered a
  different page and asserted something both pages satisfy. For anything
  security-shaped here -- especially the scope gate -- **delete the check
  and watch the test go red** before believing it.
- **Squash bodies break release-please.** Merging a many-commit PR makes
  GitHub concatenate every commit message into a bullet list of
  `* type: subject` lines; the conventional-commit parser fails and the
  change is dropped from the changelog while CI stays green. Pass an
  explicit `--body` to `gh pr merge`.

## Baselines on `main` at `0d95088`

- SPA suite: 24 files / 126 tests, stable across repeated runs.
- `corepack pnpm exec tsc -b --noEmit` clean; `eslint .` 0 errors,
  41 warnings (pre-existing plus 3 `react-refresh/only-export-components`
  in `shellChromeContext.tsx`, which match a convention firing 19x).
- Released as v0.19.0, deployed to my.splitsmith.app.

## Sequencing

The spec's own order still holds:

1. Hosted device-flow endpoints (`device_authorizations`, the four routes,
   mint-at-poll-time) and the scope gate.
2. Local endpoints (`device/start`, `device/status`, `session` DELETE) and
   the lazy poll.
3. The UI: `HostedAccountChip` in `GlobalBar`, `DeviceLoginDialog`, and
   `SyncSettingsDialog` losing its token field.

Start with a plan (superpowers:writing-plans) against the spec rather than
brainstorming -- the design decisions are already made and recorded.

## Related, not in scope

- **#725** -- `/api/health` returns `bound: false` unconditionally, so
  `/_design` and `/promote-review` are unreachable on a real server. Found
  during #550. Unrelated, but it will confuse you if you try to reach
  those surfaces.
- **#684** -- the single-shooter overlay port, the other open UI thread.
