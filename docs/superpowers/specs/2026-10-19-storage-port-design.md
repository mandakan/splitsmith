# The storage port (design, 2026-10-19)

One interface for the per-match documents, with a file implementation and a
database implementation, so a request handler loads, edits and saves a
document without knowing which world it is in. Issue #1581; step 5 of the
order recorded on #680.

Decisions taken while ideating:

- Scope is the **document** store only: the match doc, the project docs, the
  audit docs, the export-run logs. Media (raw sources, trims, renditions,
  derived audio) stays on `Storage` and `StoragePresence`; jobs stay on the
  job backends; the sync merge rules stay in `sync.run`. The port does not
  grow a media API (`media.md`).
- The surface is **synchronous**. 107 of the 198 handlers in `create_app`
  were sync when counted on 2026-10-19 and the hosted path already bridges
  through `run_sync`; the port moves that bridge into the database
  implementation, one place, instead of making every handler async at the
  same time. An async surface is a later, separate change.
- The lock question is answered by one method, `update`, that hides "process
  lock around read-modify-write" (files) and "optimistic version with a
  bounded re-merge" (database). Callers stop choosing.
- The port lives in `ui/`, not in `db/` and not in the engine: it is the
  seam between request handlers and persistence, and both implementations
  are already in `ui/state.py` and `ui/server.py` today, interleaved.

## 1. What is there today

`AppState` already is the port, informally. Its accessors branch on
`project_state is not None` (database bound) and `current_match_id`:

| Accessor | Hosted | Local |
|---|---|---|
| `match()` | `store.load_match`, bound for a versioned save | `Match.load(root)` |
| `shooter_project(slug)` | `store.load_project` then `_project_from_doc` (bind state, confine paths, bind storage) | `MatchProject.load(shooter_root)` plus `bind_storage` |
| `match_bundle(match)` | one `load_docs_for_matches` query | `shooter_project` per slug, `audits=None` |
| `load_audit` / `save_audit` | `state_docs`, optimistic version | `audit/stage<N>.json`, temp file, `.bak` rotate, under `audit_lock`, version always 0 |
| `load_audit_docs(slug)` | one `list_audit_docs` query | `None` (callers read disk) |
| `load_export_runs` / `save_export_runs` | `state_docs` | `export_runs.json` |
| `delete_audit`, `materialize_audit` | store | file |

Around them, four helpers re-implement the dispatch for writers:
`_audit_rmw` (lock locally, no-op hosted), `_save_audit_with_remerge` (lock
and merge onto disk locally; optimistic retry with merge-onto-winner hosted),
`_record_export_run` (same shape for the run log), and `_classify_doc` (reads
regions off the doc before classifying, see `stage-events.md`).

Counted on 2026-10-19, after the router split: 9 branches in `AppState`, 23
in `server.py`, 15 in `match_api.py`, 8 in `me_api.py`, 3 in `stages_api.py`,
3 in `sync_api.py`. The ones in handlers are of three kinds:

1. "Is this a hosted request?" to pick a code path (`create_match_manual`,
   `bind_recent_project`, the desktop-token and picker routes). Most of these
   are **feature** questions in disguise and belong to `require_feature` or
   to a tenant store being `None`; the port does not solve them and should
   not try.
2. "Where does this document live?" (`add_match_shooter`,
   `remove_match_shooter`, `move_shooter_endpoint`, the stage comment routes,
   `export_compare_grid`). These are the port's.
3. The sync routes' `_project_state` / `_matches_store` accessors, which are
   hosted-only by contract and 404 otherwise. Out of scope.

So the target is not "zero branches in `ui/`"; it is **zero branches of kind
2**, and the metric is the count of `project_state is not None` and
`*_doc_target()` calls outside `ui/state.py` and `ui/docstore/`.

## 2. The port

```python
# ui/docstore/port.py
class DocStore(Protocol):
    """One match's documents. Every method is synchronous."""

    def load_match(self) -> tuple[dict, int]: ...
    def save_match(self, doc: dict, *, version: int) -> int: ...

    def list_projects(self) -> dict[str, tuple[dict, int]]: ...
    def load_project(self, slug: str) -> tuple[dict | None, int]: ...
    def save_project(self, slug: str, doc: dict, *, version: int) -> int: ...
    def delete_project(self, slug: str) -> None: ...

    def load_audit(self, slug: str, stage: int) -> tuple[dict | None, int]: ...
    def list_audits(self, slug: str) -> dict[int, tuple[dict, int]]: ...
    def save_audit(self, slug: str, stage: int, doc: dict, *, version: int) -> int: ...
    def delete_audit(self, slug: str, stage: int) -> bool: ...
    def update_audit(self, slug: str, stage: int, merge: Callable[[dict], dict],
                     default: Callable[[], dict]) -> int: ...

    def load_export_runs(self, slug: str) -> tuple[dict | None, int]: ...
    def update_export_runs(self, slug: str, merge: Callable[[dict], dict]) -> int: ...

    def bundle(self) -> MatchBundleDocs: ...   # every project + every audit, one round trip where possible
```

A store is **bound to one match**: the request's `current_match_id` and
`current_match_root` are resolved once, by the thing that builds the store,
and never again inside it. That is the change that removes the ContextVar
reads from the accessors.

`update_*` is the whole point:

- `FileDocStore.update_audit` takes `audit_lock`, loads from disk, applies
  `merge` (or `merge(default())` when the file is absent), writes with the
  temp-file-and-rotate dance, returns 0. This is `_save_audit_with_remerge`'s
  desktop branch and `save_audit`'s file write, in one place.
- `DbDocStore.update_audit` loads, applies `merge`, saves at the read
  version; on `StateConflictError` reloads the winner and re-applies `merge`
  to it, up to `_AUDIT_SAVE_MAX_ATTEMPTS`. This is the hosted branch.
- `merge` is therefore the contract callers already honour for
  `_save_audit_with_remerge`: a pure function from the current doc to the
  desired doc, safe to run twice. `_classify_doc` stays what it is, a step
  inside such a `merge`.

`version` keeps its meaning (0 on files, the row version in the database),
and the return value is the new version, so handlers that hand the client an
`expected_version` for the next positional patch (#844) keep working.

### What each implementation is made of

- `FileDocStore(match_root, audit_lock, export_run_lock)`: the file branches
  of today's accessors, moved. `shooter_root` resolution stays in `AppState`
  (it is also the legacy single-shooter fallback's home) and is passed in.
- `DbDocStore(project_state_store, match_id)`: thin; each method is
  `run_sync(store.<call>)` plus the retry loops. `ProjectStateStore` keeps its
  async API and its tests.
- `MatchBundleDocs` is `MatchBundle` with the versions kept: `projects:
  dict[slug, (doc, version)]`, `audits: dict[slug, dict[stage, (doc, version)]]`.
  The file implementation fills `audits` from disk instead of returning
  `None`; the status helpers then take one shape, and the "read audits from
  disk when `None`" fallback in `MatchProject.stage_statuses` and
  `audited_count` goes.

### Binding the project model

`MatchProject.bind_state(store, match_id, slug, version)` exists so
`project.save()` round-trips under optimistic locking hosted. The port keeps
that behaviour through one small change: `bind_state` takes the `DocStore`
instead of the `ProjectStateStore`, and `save()` calls
`docstore.save_project(slug, doc, version=...)`. Locally the same call
writes `project.json`, which `MatchProject.save(root)` does today. One save
path for both. `confine_paths` stays hosted-only, decided by the store
(`DocStore.confines_paths: bool`), because the reason for it is "this doc
may carry another machine's paths", which is a property of the store.

## 3. Where it plugs in

- `AppState.docstore()` builds the bound store for the current request: a
  `DbDocStore` when `project_state` is set and `current_match_id` resolves,
  else a `FileDocStore` over `current_match_root`. The accessors in `AppState`
  become one-line delegates during the migration and are deleted at the end.
- Handlers get the store the way they get the state today:
  `state = request.app.state.splitsmith_state; docs = state.docstore()`.
- `_audit_rmw`, `_save_audit_with_remerge`, `_record_export_run` become
  calls to `update_audit` / `update_export_runs` and are deleted.
- `audit_doc_target` and `export_runs_doc_target` go; the share alias and the
  alias middleware keep setting the ContextVars, which only `docstore()`
  reads from now on.

### What stays outside

- **Sync** (`sync.run._apply_pull`, `PULLABLE_DOC_KINDS`, the per-kind
  merge rules, `audit_lock` being held across a pull's audit merge). The
  pull writes files directly on the desktop and must keep its own lock
  discipline; the port's `FileDocStore` takes the same `audit_lock` object,
  so a handler's `update_audit` and a pull's merge still exclude each other.
  Nothing in the port duplicates a merge rule.
- **Tenant stores** (shares, desktop tokens, comments, device auth, the
  picker). Hosted-only features; their `is None` checks are feature gates.
- **Media**. `StoragePresence`, `Storage`, the `ensure_*` producers and the
  job queue are untouched. A request still never runs ffmpeg.
- **Jobs**. `JobBackend` is already a port of its own.

## 4. Migration

Each step is a PR gated on the route table being identical (the dump used
for #680) and the full suite, plus the contract suite below.

1. **The port and both implementations, unused.** `ui/docstore/{port,files,db}.py`
   and `tests/ui/test_docstore_contract.py`: one parametrized suite run
   against `FileDocStore(tmp_path)` and `DbDocStore` over the SQLite engine
   the hosted tests use. It pins: load-after-save, version semantics (0 on
   files, increments in the database), `update_*` under a concurrent writer
   (two threads, both edits survive), delete, bundle equals the per-item
   loads. This is the step that proves the two worlds agree before anything
   depends on it.
2. **`AppState` delegates.** The nine accessors call `self.docstore()`. No
   caller changes. The branch count in `state.py` drops to the one inside
   `docstore()`.
3. **Writers.** `_save_audit_with_remerge` and `_record_export_run` become
   `update_*`; `_audit_rmw` goes. Every audit writer that classifies still
   goes through `_classify_doc` inside its `merge` (the rule in
   `stage-events.md` is unchanged).
4. **Handlers of kind 2**, router by router, starting with `match_api` (15
   sites). Each PR lists the sites it removed and the count left.
5. **`MatchBundle` with audits on both sides**, and the `None` fallback in
   `MatchProject` removed.
6. **`bind_state` on the port**, the last place a model knows the database
   store.

The metric after step 6: `grep -c 'project_state is not None\|_doc_target()'
src/splitsmith/ui/*.py` is zero outside `state.py` and `docstore/`.

## 5. Risks and how each is held

- **Behaviour drift between the two implementations.** The contract suite is
  the answer, and it is step 1, before any caller moves.
- **Lock reach.** The file store must take the *same* `audit_lock` instance
  the sync pull takes; a store that made its own lock would silently undo
  #1075. The constructor takes the lock; `AppState` owns it.
- **`run_sync` inside the store, called from an async handler.** This is
  exactly today's situation (17 handlers do it) and the hosted rule
  (`run_sync`, never `asyncio.run`) holds. The async surface, when it comes,
  is a second protocol, `AsyncDocStore`, with the sync one implemented over
  it; not a change to this design.
- **The legacy single-shooter fallback.** `shooter_root` keeps it; the file
  store only ever receives a resolved root.
- **Request cost on hosted.** `bundle()` is the existing one-query path;
  nothing in the port adds a round trip, and `tests/test_match_bundle_queries.py`
  keeps pinning two `state_docs` SELECTs per request.

## 6. Open questions for review

1. Should `bundle()` be the only way to read audits for a list route, with
   `list_audits` kept for the per-shooter routes, or should `list_audits`
   go too? (Leaning: keep both; they have different callers.)
2. The export-run log has no `delete`; is one needed before the port freezes
   the surface? (Leaning: no; the retention decision on #629 keeps the log.)
3. Does the share surface, which reads through `_share_alias` impersonating
   the owner, need a read-only store flavour, or is "the write routes are
   allowlisted" (`share.md`) enough? (Leaning: enough; the port must not
   become a second authorization layer.)
