# 10 -- Singleton elimination map

The abstractions in docs 02 / 03 / 04 are necessary but not
sufficient for hosted SaaS. They describe what the boundary code
should look like; this doc describes the **process-level state that
must come out before the abstractions can do anything useful in a
multi-tenant deployment**.

Two questions check each piece of state:

1. **Stateless?** Can the process die and the next one pick up
   without user-visible loss?
2. **Multi-tenant?** Can two requests with different tenants run
   concurrently without interference?

Anything that fails either gate is named below. The order at the
bottom is not negotiable -- migrating the storage callsites (doc
03) before killing `AppState._bound_root` would only entrench the
singleton: handlers would still implicitly read "the bound project"
on the way to the new storage call.

## What stays singleton on purpose

These are process-wide and **correct** because they are tenant-
agnostic shared resources. Not violations.

- `_ENSEMBLE_RUNTIME` (`splitsmith.ui.server`) -- CLAP / PANN / GBDT
  weights. ~600 MB resident. Loading once per process and serving
  every tenant from the same instance is the entire point of having
  a worker pool per doc 04.
- `_LOOPBACK_HOSTS` (`splitsmith.ui.server`) -- a `frozenset`
  constant. Not state at all.
- `LocalComputeBackend` and `LoopbackAuth` instances on `AppState`
  -- the backend implementation is stateless; only the runtime
  cache (above) holds bytes. Swapping to `RemoteComputeBackend` /
  `MagicLinkAuth` in hosted mode keeps the same shape.

## What is a singleton violation (and must come out)

### Tier 1 -- DONE (PRs #409, #410, #411, #412)

**`AppState._bound_root` + `_bound_kind` + `_bound_name` +
`_bound_match_id`** -- all gone. The four cuts:

- Step 1 (#409): `state.match_root` -> ContextVar-only.
- Step 2 (#410): `state.shooter_root(slug)` -> ContextVar-only for
  Match folders.
- Step 3 (#411): retired the legacy single-shooter layout.
- Step 4 (#412): deleted `_bound_*` fields, `bind_match`, `unbind`,
  `is_bound`, `bound_root`, `bound_name`, `bound_match_id`,
  `_health_after_bind`, `state.unbind`, the
  `/api/me/recent-projects/unbind` endpoint, and renamed the
  internal bind helper to `_register_match_at` so its sole job is
  to register the match in `state.matches` + record the open. The
  picker bind endpoint still returns a HealthResponse with
  ``match_id`` + ``default_shooter_slug`` so the SPA can navigate
  to ``/match/<match_id>`` -- there's no server-side state for it
  to flip.

What was actually a process-wide singleton (every shooter-scoped
property `bound_root` / `match_root` / `shooter_root(slug)` /
`shooter_project(slug)` reading the same fields, racing under
concurrent multi-tenant requests):

The `current_match_root` / `current_match_id` ContextVars (#353
Phase 3) were a partial step toward per-request scoping: when a URL
carries `/api/matches/{match_id}/...` the middleware sets the
ContextVar and `shooter_root(slug)` resolves against that instead
of the singleton. But every method falls back to `self._bound_root`
when the ContextVar is unset, and the picker UX + the
`splitsmith ui --project <path>` boot path both populate the
singleton. The fallback IS the violation.

**Elimination path:**

0. **(in progress)** Three cuts landed so far:
   - `state.match_root` reads ContextVar only -- the singleton
     fallback for match-level operations is gone. `/api/match/*`
     endpoints 409 ``no_project`` unless the request comes through
     `/api/matches/{match_id}/`.
   - `state.shooter_root(slug)` dropped the **Match-folder**
     singleton fallback. A bound Match folder no longer answers
     `/api/shooters/{slug}/...` bare-path requests.
   - **Legacy single-shooter projects retired entirely.** The
     `_bind_legacy_root_to_state`, `state.bind_legacy`,
     `_bound_kind == "legacy"`, and `legacy_slug` paths are gone.
     `_bind_project_to_state` now 400s ``not_a_match`` for any
     path without a ``match.json``; the picker bind endpoint
     refuses non-match paths and pushes users through
     ``POST /api/match/create-manual`` instead. Per [[clean-no-fallbacks]]
     and [[stateless-multitenant]], no production shim survives.
     ContextVar propagation for the ``JobRegistry`` worker thread
     was needed so jobs submitted from a URL-scoped request can
     resolve ``state.shooter_root(slug)`` from their executor.
   Per [[delete-obsolete-tests]], two `test_bind_recent_project_*`
   tests covering legacy scaffolding were deleted; the
   `_make_match_app` and `_seed_*` helpers + a new
   `tests/conftest.py::scaffold_match` were updated to land
   shooter state under `<match>/shooters/<slug>/`.
1. Make `/api/matches/{match_id}/...` the only accepted prefix for
   project-scoped routes. Delete the bare-path route table or have
   it 404. The middleware becomes mandatory rather than a fallback.
2. Delete `AppState._bound_root` + `_bound_kind` + `_bound_name` +
   `_bound_match_id`, `bound_root`, `match_root`, `bind`, `unbind`,
   and the no-arg `shooter_root` fallback. Everything resolves from
   the URL.
3. Local mode "picker" stops being a server-side bind operation and
   becomes a client-side route to `/match/<match_id>` after the
   picker resolves the id from the chosen path.
4. `splitsmith ui --project <path>` either adds the matching
   `match_id` to the URL the browser opens, or drops in favour of
   "open a tab, use the picker".

### Tier 2 -- in-process state that vanishes on restart

**`JobRegistry`** (`splitsmith.ui.jobs`).

In-memory `dict[str, Job]`. Loses every queued / running /
recently-finished job when the process exits. Can't be horizontally
scaled -- two replicas can't see each other's jobs. Pre-existing
shutdown-drain logic (in `_JobAwareServer`) is a graceful workaround
for the local case; in hosted mode it doesn't apply (machines
suspend; replicas come and go).

**Status:** abstraction landed. `JobBackend` Protocol lives next
to `JobRegistry` in `splitsmith.ui.jobs`; `AppState.jobs` is
typed against the Protocol so handlers depend on the abstraction.
`JobRegistry` is the only current implementation; a hosted
backend swaps in without touching handlers.

**Elimination path:**

1. **(done)** Define `JobBackend` Protocol; `JobRegistry`
   satisfies it; `AppState.jobs` widened to the Protocol.
2. Pick the backing store per doc 04 -- Postgres `compute_jobs`
   table is the spec. Procrastinate (Postgres-native) is the
   queue runtime; its schema lives alongside `compute_jobs` in
   the same DB.
3. Ship a hosted-mode backend: `submit` inserts into
   `compute_jobs`, the worker picks up via Procrastinate, status
   writes land in Postgres, `list` / `get` / `cancel` read from
   Postgres.
4. The local-mode backend stays in-memory (no Postgres dep on the
   desktop). A SQLite-backed `PersistentLocalJobBackend` is the
   obvious bridge if the desktop ever needs job persistence too
   -- defer until a user actually loses work to a crash.

### Tier 3 -- per-machine state

**`user_config.recent_projects`** (`splitsmith.user_config`).

JSON file at `~/.splitsmith/projects.json` (or the XDG equivalent
on Linux). Used by the picker to surface previously-opened paths.
Per-machine, not per-user-account -- two browsers on different
machines for the same hosted user see disjoint lists.

**`user_config.scoreboard_identity`** (`splitsmith.user_config`).

JSON file at `~/.splitsmith/scoreboard.json`. The saved
`shooter_id` + `display_name` for the SSI Scoreboard. Same per-
machine problem.

**Status:** abstraction landed. `RecentProjectsStore` +
`ScoreboardIdentityStore` Protocols live in
`splitsmith.user_config`; `JsonRecentProjectsStore` and
`JsonScoreboardIdentityStore` wrap the existing module functions
that read/write `~/.splitsmith/*.json`. `AppState.recent_projects`
and `AppState.scoreboard_identity` are typed against the
Protocols and the six production handler callsites (`/api/me/
recent-projects`, `/api/me/recent-projects/forget`,
`/api/me/scoreboard-identity` GET/PUT/DELETE, plus the
`_register_match_at` helper) go through `state.*` rather than
the module functions.

**Elimination path:**

1. **(done)** `RecentProjectsStore` + `ScoreboardIdentityStore`
   Protocols; JSON impls; `AppState.*` widened to the Protocols;
   handlers route through the abstraction.
2. Hosted-mode backend: per-user Postgres rows
   (`users.recent_project_ids` JSONB or a `recent_projects`
   table; `users.scoreboard_identity` JSONB). Constructed
   per-request after the auth check, not as an `AppState`
   singleton.

`~/.splitsmith/auth.json` (the desktop-link refresh token store
mentioned in doc 02) is per-machine on purpose -- desktop linking
binds a device to an account, and that's what device-scoped state
is for. Not a violation.

### Tier 4 -- per-machine paths -- DONE

**`splitsmith ui --project <path>`** opened an arbitrary local
path AND relied on the server's now-gone bound-state concept to
land the SPA on the project. After Tier 1 step 4 retired bound
state, the SPA loaded the picker even when `--project` was given.

**Resolution:** the CLI now resolves the path to a `match_id`
(via `Match.load(path).match_id`) and opens the browser at
`http://<host>:<port>/match/<match_id>/` directly. The server's
`create_app(project_root=...)` still registers the match in
`state.matches` so the alias middleware can resolve it; the URL
just carries the identity from the first paint instead of waiting
for a picker click.

Either way the URL carries the project identity, not the boot
flag. Hosted mode has no local path concept at all -- the project
lives in R2 under a stable prefix and the user navigates by
match_id from the start.

## Order of elimination

```
1. AppState._bound_root + bare-path routes      (blocks all migration)
2. JobRegistry -> compute_jobs (Postgres)        (blocks horizontal scale)
3. user_config -> per-user Postgres rows         (blocks per-user state)
4. CLI --project flag -> URL-based opening       (cleanup)
5. Storage callsite migration (per doc 03)       (now safe to do)
```

Steps 1-3 each get their own iteration / PR. Step 5 cannot be done
profitably before step 1 -- a storage migration that still threads
through `_bound_root` is a rewrite waiting to happen.

## What this doc is not

Not a redesign of the local-mode desktop UX. The picker, the
single-window assumption, "the open project" affordances in the
SPA -- those stay. The change is that the **server** stops carrying
"the open project" as state; the **client** still has a notion of
"this tab is currently looking at match X" via its URL.

Not a deadline-driven migration. Tier 1 is a prerequisite for
hosted launch, but local mode can ship a v0.x release with the
singleton still present. The order matters; the calendar doesn't.

Not a rejection of the abstractions in 02 / 03 / 04. Those are
correct. This doc is the **prework** that lets them do their job
in hosted mode.
