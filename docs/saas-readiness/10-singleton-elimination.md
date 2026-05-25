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

### Tier 1 -- blocks every other migration

**`AppState._bound_root` + `_bound_kind` + `_bound_name` +
`_bound_match_id`** (`splitsmith.ui.server`).

"The currently open project". A process-wide singleton. Every
shooter-scoped property (`bound_root`, `match_root`,
`shooter_root(slug)`, `shooter_project(slug)`) reads it. Two
concurrent requests for different projects from different tenants
would race on it -- whichever bind ran last wins.

The `current_match_root` / `current_match_id` ContextVars (#353
Phase 3) were a partial step toward per-request scoping: when a URL
carries `/api/matches/{match_id}/...` the middleware sets the
ContextVar and `shooter_root(slug)` resolves against that instead
of the singleton. But every method falls back to `self._bound_root`
when the ContextVar is unset, and the picker UX + the
`splitsmith ui --project <path>` boot path both populate the
singleton. The fallback IS the violation.

**Elimination path:**

0. **(in progress)** `state.match_root` reads ContextVar only --
   the singleton fallback for match-level operations is gone.
   `/api/match/*` endpoints now 409 ``no_project`` unless the
   request comes through `/api/matches/{match_id}/`. This was the
   smallest defensible first cut: only ~10 test callsites needed
   the prefix, and `shooter_root` still has its singleton fallback
   so the bulk of shooter-scoped routes are unchanged. Next cuts
   chip away at `shooter_root` and `shooter_project`.
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

**Elimination path:**

1. Pick the backing store per doc 04 -- Postgres `compute_jobs`
   table is the spec. `arq` Redis queue is the runtime.
2. `JobRegistry` becomes a thin facade: `submit` inserts into
   `compute_jobs`, the worker picks up via `arq`, status writes
   land in Postgres, `list` / `get` / `cancel` read from Postgres.
3. The local-mode backend stays in-memory (no Postgres dep on the
   desktop) but presents the same interface, so handlers don't
   branch. A SQLite-backed `LocalJobRegistry` is the obvious
   bridge if the desktop ever needs job persistence too -- defer
   until a user actually loses work to a crash.

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

**Elimination path:** in hosted mode both move to per-user Postgres
rows (`users.recent_project_ids` JSONB or a `recent_projects`
table; `users.scoreboard_identity` JSONB). The `user_config`
module gets a `RecentProjectsStore` abstraction with two backends
(JSON file in local mode, Postgres in hosted mode); the handler
calls `state.recent_projects.list(user.id)`.

`~/.splitsmith/auth.json` (the desktop-link refresh token store
mentioned in doc 02) is per-machine on purpose -- desktop linking
binds a device to an account, and that's what device-scoped state
is for. Not a violation.

### Tier 4 -- per-machine paths

**`splitsmith ui --project <path>`** opens an arbitrary local path.
The implicit assumption is "the user's filesystem layout is the
source of truth". In hosted mode there is no local path -- the
project lives in R2 under a stable prefix.

This is not a singleton in the same sense as the others, but it
**presupposes** the singleton: the path determines `_bound_root`.
Once Tier 1 is done, the CLI either:

- Resolves the path to a `match_id` via `MatchRegistry` and opens
  the browser at `/match/<id>`, OR
- Opens the picker and lets the user select via the UI.

Either way the URL carries the project identity, not the boot
flag.

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
