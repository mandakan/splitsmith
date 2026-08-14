# Account display-name follow-ups (#876, #877, #878) - design

Date: 2026-08-14
Status: approved pending review
Issues: #876, #877, #878. All three filed out of #869's whole-branch review.
Surfaces: `.github/workflows/ci.yml` + `alembic/`; the `/api/sync/*` surface
and the desktop account chip; the SPA's vitest config.

## Why these three are one document and three branches

They share no code. They share only a cause: each is something #869's review
found and recorded rather than fixed. Planning them together keeps the
context in one place; landing them together would put a CI workflow change,
a hosted API route and a vitest config change in one diff.

Three PRs off `main`, merged in order 876 -> 877 -> 878, each branch cut from
`main` after its predecessor merges. Not stacked: stacked PRs in this repo
have produced squash bodies that break release-please's parser, and a
migration gate should not wait on frontend test hygiene.

Squash bodies stay short for the same reason.

---

# #876 - CI never applies a migration

## Problem

No workflow in `.github/workflows/` runs `alembic upgrade head`. The only
mention of alembic in CI is a comment in `deploy-app.yml` noting that `serve`
runs it on boot. So the first process that ever applies a new migration is
the production deploy.

The schema tests do not close this. `tests/test_comments_schema.py` builds
its tables with `Base.metadata.create_all`, and so does every other
default-suite test -- the suite is SQLite-backed and constructs schema from
metadata. Those tests pass whether or not a migration file exists and whether
or not it agrees with the models.

The only coverage that genuinely runs the migration chain against Postgres is
the `@pytest.mark.docker` suite, which `addopts` deselects by default and CI
never opts into.

Three failure classes ship green today: a model/migration divergence, a
migration that applies on SQLite but not Postgres, and an ordering mistake in
the revision chain.

## Approach

A dedicated always-on job, not the docker suite. The docker suite would
exercise migrations only as a side effect of booting a stack, takes about
four minutes, needs `-n0`, and reports a stack failure rather than a schema
one. The autogenerate diff checks divergence directly and runs in seconds.

### The job

New `migrations` job in `ci.yml`, same triggers as `test` (every PR, every
push to main):

- `services: postgres:16-alpine` with a health check, exposed on the
  runner.
- `uv sync --frozen --extra hosted --no-dev`. Deliberately not
  `--all-groups`: this job needs sqlalchemy, alembic, asyncpg and
  procrastinate, and has no business installing torch. It does not need
  pytest -- the check is a standalone script.
- `SPLITSMITH_DATABASE_URL` pointed at the service. `alembic/env.py`
  already honours that variable, so no CI-specific ini file.
- One step: `uv run python scripts/ci/assert_migrations_match_models.py`.

### The script

`scripts/ci/assert_migrations_match_models.py`, four assertions in order,
each failing with a message that names the offending revision or object:

1. **Single head.** `ScriptDirectory.get_heads()` has exactly one entry.
   Catches the ordering mistake: two migrations claiming the same
   `down_revision` produce two heads, and `upgrade head` then fails at deploy
   time with a message about multiple heads.
2. **Applies.** `alembic upgrade head` against the live Postgres. Catches
   "runs on SQLite, not Postgres" -- which is not hypothetical here, since
   `ba72882f8c1c` is Postgres-only by construction and is a no-op on SQLite.
3. **Agrees with the models.** `alembic.autogenerate.compare_metadata()`
   against `Base.metadata` returns an empty op list. On failure, print every
   op so the log says which table and column diverged, not just that
   something did.
4. **Reverses.** `downgrade base`, then `upgrade head` again. #867's
   migration was confirmed reversible once, by hand, locally; nothing keeps
   that true. Cheap on an empty database.

Exit non-zero with a readable summary on any of the four.

### The filter this needs, and the bug it fixes

`compare_metadata` diffs the live schema against `Base.metadata`. The
procrastinate tables, sequences, types and functions are applied by
`ba72882f8c1c` as raw SQL and are not in `Base.metadata`, so an unfiltered
diff emits `drop_table` for every one of them and the gate false-fails on its
first run.

This is not only a CI problem. **Running `alembic revision --autogenerate`
today would generate a migration that drops the job queue.** Nobody has run
it since `ba72882f8c1c` landed, which is the only reason that has not
happened.

So the filter goes in one shared place -- a small module under
`src/splitsmith/db/` exposing an `include_object` callable -- and is wired
into `alembic/env.py`'s `context.configure()` (both online and offline) *and*
used by the CI script. One implementation, so the developer command and the
gate cannot disagree.

It excludes `alembic_version` and everything procrastinate owns, matched by
name prefix. It excludes nothing else: a diff on a table splitsmith owns is a
finding, not noise.

Expect the first run to surface real divergences on our own tables --
`server_default` spellings and index naming are the usual suspects. Those are
fixed by writing the migration that closes them, not by widening the filter.

## Verification

The gate must be seen to go red before it is trusted:

- Add a column to a model in `db/models.py` with no migration. The script
  must fail at assertion 3 and name the column.
- Break a `down_revision` so two revisions share a parent. The script must
  fail at assertion 1.
- Make a `downgrade()` body wrong. The script must fail at assertion 4.

Each reverted after observing the failure. A gate nobody has watched fail is
not a gate -- this repo has shipped three green-CI release bugs whose common
factor was a check that validated something other than the artifact it
claimed to.

## Out of scope

Running the docker suite in CI. It stays a local command. If it earns a place
later it belongs on push-to-main or behind a label, like `slim-smoke`.

---

# #877 - the desktop chip keeps a stale display name

## Problem

`HostedAccountRef` is a snapshot written into `config.yaml` once, when the
device flow completes (`server.py`'s `get_device_status`). Nothing refreshes
it.

The sequence a user hits: link the desktop app while `display_name` is
`NULL`, set a display name on the web at `/account`, and the desktop chip
goes on rendering the email forever -- `display_name ?? email`. The only
repair is to unlink and re-link, which nobody would guess.

The naive fix does not work. The desktop holds a sync-scoped token, and
`_auth_gate` confines that scope to `/api/sync/*` plus `/api/device/session`.
A sync token gets 403 on `/api/me` deliberately (#719): a credential that
exists to push match data should not read or name an account.

## Approach

A sync-surface identity route plus an opportunistic refresh on a call the
desktop already makes. Widening the token scope was rejected -- it would undo
the containment #719 established and #866/#869 both leaned on. Riding along
on an existing sync response was rejected because an install that has not
pushed recently would stay stale, and it would couple identity to an
unrelated payload.

### Hosted side

`GET /api/sync/whoami` in `ui/sync_api.py`, returning `{id, email,
display_name}` and nothing else. It carries identity, never a credential --
the same rule `HostedSyncSettings` already states.

No new auth logic: `_auth_gate` already admits `/api/sync/*` for scope
`sync`, and `_hosted_gate()` / `_current_user(request)` already exist in that
module. The route is the whole hosted-side change.

### Desktop side

`GET /api/settings/hosted-sync` -- which `HostedAccountChip` already calls on
mount and on `HOSTED_ACCOUNT_CHANGED_EVENT` -- refreshes the cached snapshot
through the existing `_build_device_client(base_url, token=...)` helper,
under these rules:

- **Only when linked.** `hosted_base_url`, `hosted_token` and
  `hosted_account` all set. Otherwise there is nothing to refresh.
- **Best effort.** Any exception, timeout or non-200 returns the cached value
  unchanged and surfaces no error. The chip's `loadFailed` state must stay
  false: #738 established that a transient failure making a linked operator
  look unlinked is the worse outcome, and that reasoning applies here
  unchanged.
- **Short timeout**, well under the chip's tolerance for a mount.
- **In-process TTL**, roughly five minutes, held on server state rather than
  persisted. `GlobalBar` and the mobile drawer each render a chip with
  independent state, and both refetch on route changes; without a TTL a
  desktop session would issue a steady trickle of upstream calls for a label.
- **Write on change only.** `save_global_prefs` runs when a field actually
  differs, so config.yaml is not rewritten on every mount.

`email` refreshes alongside `display_name`. That retires the "a hosted-side
email change will not propagate until the install re-links" limitation that
`HostedAccountRef`'s docstring currently accepts, at no extra cost.

### Deliberate non-goal

A 401 from whoami does **not** unlink the device. Auto-unlinking on an
upstream status code would mean a hosted outage signs every desktop install
out, and a stale chip is a far smaller harm than a lost link. Revocation
still surfaces where it already does: on the next sync.

### Comments that become false

Four places record the limitation this closes. All four are updated, none
deleted -- the reason the route exists is worth keeping next to the code that
depends on it:

- `user_config.HostedAccountRef` docstring
- `server.py`'s `HostedAccountInfo` docstring
- the snapshot comment inside `get_device_status`
- `ui/device_auth_api.py`'s `poll_device_token`

## Verification

- Hosted: a sync-scoped token gets 200 on `/api/sync/whoami` **and 403 on
  `/api/me`**, asserted in the same test. The fix and the boundary it must
  not break are pinned together, so a future widening of the scope fails a
  test that explains itself.
- Hosted: a session cookie also reaches the route (it is not sync-token-only).
- Desktop: a changed upstream `display_name` reaches the settings response
  and is persisted.
- Desktop: an upstream failure returns the cached account with no error field
  set.
- Desktop: a second call inside the TTL issues no upstream request.
- Desktop: an unchanged response performs no config write.

---

# #878 - the route-suite hook timeout, copy-pasted six times

## Problem

Six files carry the same hand-applied workaround -- `App.routes.test.tsx`,
`.pickup`, `.account`, `.hosted`, `.share`, `.modegate`. Each awaits
`import("@/App")` in `beforeAll` and each passes an explicit 30s hook
timeout, because vitest's 10s default was not enough under load.

The progression is the complaint: three files were bumped during #869, a
later review run reproduced the identical failure in two that had been left
alone, and `modegate` made six. Each new route test file is one more copy of
a workaround, discovered the same way and fixed by whoever trips it. Nothing
stops file seven from starting at 10s.

## Measurements taken

On an idle box (2026-08-14):

- One route file alone: 3.6s, of which 2.1s is transform.
- All six together: 4.8s wall, but **11.4s cumulative transform** -- roughly
  2s per file for the same route tree, paid six times.
- Full SPA suite: 88 files, 517 tests, 33s wall, 822% CPU. The route files'
  own tests run 30-600ms each. The cost is entirely the import.

`App.tsx` eagerly imports about 30 page modules with no lazy boundary
anywhere, which is where the 2s goes.

The number still missing is the hook's own elapsed time under a loaded full
suite -- 30s is an observation from a loaded box, not a measurement. Getting
it is the first implementation step, not a research task: instrument the hook
to log `performance.now()` deltas, run the full suite, take the worst
observation, and set the budget as a stated multiple of it. If the
measurement says 30s is right, it stays 30s and stops being folklore.

## Approach

`vitest.config.ts` grows `test.projects`:

- a `routes` project globbing `src/App.routes.*.test.tsx`, carrying the
  `hookTimeout`
- a default project for the other 82 files at vitest's 10s, so a genuine hang
  outside the route suite still reports in 10s rather than 30

Both inherit `environment: "jsdom"` and `setupFiles`. All six inline timeout
arguments and their six near-identical comments come out; one comment in the
config carries the measurement, the multiple, and why the route files are
different from everything else.

## Verification

The six still passing is not the point -- they pass today. What must be shown:

- `pnpm test` still collects 88 files / 517 tests, and `pnpm vitest run
  src/App.routes` still filters as before.
- **A new `src/App.routes.*.test.tsx` file inherits the budget without
  touching anything.** That is the regression this closes, and the only
  assertion that distinguishes this change from six edits.

## Out of scope, filed as a follow-up

Lazy route boundaries in `App.tsx`. Splitting the ~30 eager page imports
would cut the transform cost for all six files at once and shrink the
production bundle -- but it changes production rendering, needs Suspense
fallbacks, and turns route assertions async. That is a product change wearing
a test-hygiene label. A new issue captures it, citing the 2s/file transform
number above as the evidence.
