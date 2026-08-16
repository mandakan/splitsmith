# Export-run record (#629, second half) -- design

Date: 2026-08-16. Implements the remaining half of #629, scheduled by #919
section 2. The retention decision this argues from is the comment on #629
dated 2026-08-15 ("Decision: retention"); read it before changing anything
here.

## What is missing today, precisely

#858 shipped retrieval. Every *file* an export produced is already
discoverable and downloadable from persistent state:

- `MatchProject.export_overview` reports per-stage artefacts, and
  `MatchProject.match_export_files` reports match-level ones. On hosted
  both derive from one `storage.list()` of the exports prefix; on desktop
  from the `exports/` dir.
- `download_export_file` serves any basename inside the shooter's
  `exports/` scope, pulling from object storage when it is not local.
- `lib/exportDownloads.hostedDownloads` derives the SPA's download list
  from those two persistent inputs only.

Four facts are *not* derivable from a directory listing, and are what this
design adds:

1. **Run grouping** -- which deliverables came out of one invocation.
2. **Duration** -- how long the run took.
3. **Selected formats** -- what the user asked for, which is not the same
   as what got written.
4. **Anomaly count** -- what the run reported.

`Job.result` already carries (2) and (4) for a match export, but a job row
is in-session state; the whole point is durability past it.

## Non-goals

- **Eviction.** The retention decision's size-capped LRU over derived
  video is deliberately out of scope; it is a separate mechanism with its
  own storage-accounting surface, and it ships with eviction off by
  default anyway, so nothing user-visible waits on it. Filed as a
  follow-up (Task 11).
- **Re-export from a history row.** The decision reserves "Re-export" for
  the state where bytes were evicted. Nothing is evicted yet, so every
  artefact a run recorded is either still downloadable or was deleted by
  the user's own cleanup; the history renders a plain download link and,
  when the file is gone, nothing clickable.
- **Cross-match history.** The record is per shooter within a match, which
  is what keeps the #632 `match_id`-alongside-`user_id` constraint free.

## The record

Lives in a new pure module `src/splitsmith/export_runs.py`. No I/O, no
storage seam, no FastAPI -- it is data plus two pure functions, per
CLAUDE.md architecture rules 2 and 3.

```
ExportRunLog          schema_version: int, runs: list[ExportRun]  (newest first)
ExportRun             run_id, kind, finished_at, duration_seconds,
                      stage_numbers, formats, anomaly_count, artifacts
ExportArtifact        filename (basename under exports/), kind
```

- `run_id` is `uuid.uuid4().hex`, not a ULID: ordering comes from
  `finished_at`, and the `ulid` package is a hosted-only extra while runs
  are recorded on slim local installs too. Same reasoning already written
  down at `server.py`'s `_new_event_id`.
- `filename` is a basename, never a path. That is the same key the
  download endpoint takes, and it is what makes a record written by a
  hosted worker meaningful to the API container that serves the link.
- `formats` is what was *requested*. `artifacts` is what was *written*.
  Keeping both is the audit trail: "asked for an overlay, got none" is
  exactly the thing a user comes back to the history to find out.

**`duration_seconds` is wall-clock time for the run**, taken from
`handle.timer.build()["total_ms"] / 1000.0`. There is a trap here:
`MatchExportResult.duration_seconds` already exists and means *timeline
length of the stitched output*. Reusing it would put "42.0" in a field a
user reads as "the export took 42 seconds". Do not.

**Reads never raise.** `load_log` skips a run entry that fails validation
and keeps the rest; a doc that is not a dict, or whose `runs` is not a
list, yields an empty log. A history that cannot be parsed must not be
able to fail an export or 500 a page. The cost is that a malformed entry
is dropped on the next write, which is acceptable: the log is
single-writer per mode and a malformed entry means a bug, not user data.

**No cap on the number of runs.** The retention decision says run records
are kept indefinitely and are the KB-scale half. A run is a few hundred
bytes; a thousand of them is a third of a megabyte.

## Where it lives

`state_docs`, as settled in #629 and reaffirmed by #919 -- a new
`doc_kind = "export_runs"` with `slug` set and `stage_number` NULL, so
one log per shooter per match. This needs no migration: `StateDocRow` is
polymorphic on `doc_kind` and its uniqueness index already covers the
identity.

Desktop stays file-based, at `<shooter_root>/export_runs.json`. It is
deliberately *not* inside `exports/`: everything in that directory is
listed by `MatchProject._stored_exports` and offered as a deliverable, and
the history is not a deliverable.

The mode split lives in `AppState.load_export_runs` / `save_export_runs`,
mirroring `load_audit` / `save_audit` exactly -- hosted goes through
`ProjectStateStore` under optimistic locking, local writes the file and
returns version 0. Job bodies then stay mode-agnostic, which is the
reason for the seam.

## Concurrency

Two export jobs for different stages of the same shooter run concurrently
in the normal batch case, and they contend on one document. The append
therefore re-loads and re-appends on a version conflict, exactly like
`_save_audit_with_remerge`: a conflict means someone else's run landed
first, and re-appending onto the winner's doc preserves both. Blind
overwrite would silently lose a run.

**A failed record write must not fail the export.** The deliverables are
the product; the history is bookkeeping. Retries exhausted, store
unavailable, disk full -- log at WARNING and let the job succeed. A red
job row saying "export failed" over files that were written correctly is
a worse lie than a missing history line.

## The sync seam (the non-obvious one)

`ProjectStateStore.list_doc_meta` returns *every* doc in a match, and it
is the sync pull manifest. `sync.pull.plan_pull` turns each manifest entry
into a `RemoteDoc` and `sync.run` dispatches on kind with `match` /
`project` / `else: # audit`. `SyncClient._doc_path` has the same shape --
anything that is not `match` or `project` builds `docs/audit/{slug}/{stage_number}`.

So a hosted-written `export_runs` doc would be pulled as
`GET /api/sync/matches/{id}/docs/audit/me/None`, which fails path
validation, raises `SyncClientError`, and **breaks desktop sync for that
match entirely**. This is not hypothetical -- it happens on the first sync
after the first hosted export.

The fix is a `PULLABLE_DOC_KINDS` allowlist in `sync/pull.py`, filtered in
`plan_pull`. Allowlist, not denylist: the next doc kind added should be
inert to an old desktop client by default, not a sync-breaking one. Export
history is desktop-local by design; nothing pushes it either (the push
side builds its doc list explicitly and has no `export_runs` branch).

## HTTP surface

`GET /api/shooters/{slug}/exports/runs` -> `{"runs": [...]}`, newest
first. Deliberately separate from `exports/overview`: the overview answers
"what can I download now" and the history answers "what happened", the
same split that justified `match_exports` being its own reader rather than
a field on the status rows.

Per #919's standing rule -- every feature that touches `server.py` lifts
its own routes on the way past -- the four existing export routes move to
a new `src/splitsmith/ui/exports_api.py` alongside the new one:

| route | method |
| --- | --- |
| `/api/shooters/{slug}/exports/overview` | GET |
| `/api/shooters/{slug}/exports/file/{filename:path}` | GET |
| `/api/shooters/{slug}/exports/runs` | GET (new) |
| `/api/shooters/{slug}/stages/{stage_number}/export` | POST |
| `/api/shooters/{slug}/export/match` | POST |

Paths are unchanged, so the alias middleware, `_SCOPED_PREFIXES` in the
test harness, and every SPA call site are untouched. The router follows
the `sync_api` / `device_auth_api` idiom: module-level `router`, state via
`request.app.state.splitsmith_state`, `include_router` after the
middleware is registered.

Two dependencies have to move with it, because `exports_api` must not
import `server` (server imports it):

- `ExportStageRequest` / `MatchExportRequest` move to `exports_api`.
  `ui/job_journal.rehydrate_args` imports them by name and must be
  repointed; it is the *only* other importer.
- `_ensure_source_reachable` moves to a new `src/splitsmith/ui/http_errors.py`,
  imported by both. It has 10 call sites in `server.py`, all of which keep
  the same name.

The job bodies stay in `server.py`. Lifting `register_job_bodies` is a
separate, larger job and is not what this feature needs.

## SPA

- `api.getExportRuns(slug)` plus `ExportRun` / `ExportArtifact` types in
  `lib/api.ts`.
- A presentational `components/export/ExportHistory.tsx` with its own
  test, wired into `Export.tsx`. The rendering does not go inline in
  `Export.tsx`: that file is 1,381 lines and an `import("@/pages/Export")`
  in a test would drag the whole page in, which is the cost #894 is about.
- The list refetches when an export job reaches a terminal state, next to
  the existing overview refetch. Both modes render it -- a desktop user
  has just as much use for "what did I export and when".
