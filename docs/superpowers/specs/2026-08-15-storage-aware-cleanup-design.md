# Storage-aware project cleanup - design

Date: 2026-08-15
Status: approved pending review
Issues: #629 (retention half), #632 (hosted feature-complete epic), #919
Surfaces: `src/splitsmith/cleanup.py`, `src/splitsmith/match_project.py`,
`src/splitsmith/ui/server.py`'s two cleanup routes, and a new SPA surface.

## Why this exists

#629 asked for a retention policy on hosted export deliverables. Answering
it against the code produced three findings, each of which moved the target.
They are recorded here because none is recoverable from the issue text, and
two of them contradict what #629's own body says.

**1. Deliverables do not accumulate per export run.** `stage_file_base`
(`export_naming.py`) is deterministic in `(stage_number, stage_name)`, so a
second export of the same stage overwrites the first in place. #629's
open-questions section says "deliverables in R2 accumulate per export run".
They do not. Storage grows with *matches x stages x artefact kinds*, and a
user who exports one stage fifty times costs exactly what a user who exports
it once costs.

The consequence: there is no measured growth problem to automate against, so
this change ships **no cap and no background sweep**. That decision is
recorded on #629 and supersedes the size-capped-LRU sketch in the earlier
comment on that issue.

**2. The prune mechanism already exists.** `cleanup.py` is a shipped tiered
plan/apply subsystem: seven independently-toggled categories, a `CleanupPlan`
that is pure and previewable, `apply_cleanup` that never raises on individual
failures, and a `.cleanup.log` JSONL audit trail. Its categories already
encode the text-vs-derived-video distinction the retention decision arrived
at independently -- `EXPORTS_LIGHT` for `.fcpxml` / `.csv` / `_report.txt`,
`EXPORTS_OVERLAYS`, `EXPORTS_TRIMS`.

So the work is not a new subsystem. It is making an existing one see the
place the bytes actually are.

**3. Nothing is reachable from the SPA.** `lib/api.ts` defines `cleanupPlan`
and `applyCleanup`; grepping every `.ts`/`.tsx` outside `api.ts` for either
symbol returns nothing. The routes are live and have no caller. The only way
to run a cleanup today is `splitsmith cleanup` on the CLI, which is exactly
what a hosted user does not have.

This is the #866 shape -- a branch that shipped with no writer and was dead
in production for that reason. Fixing only the engine would move the feature
from "inert because it globs the wrong directory" to "inert because nothing
calls it", with the same user-visible outcome.

## Problem

`plan_cleanup` enumerates by globbing `project.exports_path(root)`,
`project.trimmed_path(root)`, `project.audio_path(root)` and friends. In
hosted mode those directories are an ephemeral container cache; the durable
bytes live in object storage under `<scope>/`. A hosted plan therefore
reports zero items and reclaims nothing.

Same shape as the #565 source-cache LRU: shipped, correct, and inert in the
deployment that needs it. That parallel is the reason this is worth doing
now -- the pattern recurs, and `export_overview` already solved it once with
`_stored_exports()`.

## Approach

### One category table, two enumerators

The main structural risk is two competing definitions of "what is an
overlay": a glob in `_iter_paths` and a prefix filter in a new storage
walker, drifting apart silently. The exact failure `export_naming.py` was
written to prevent, one layer up.

So the per-category globs in `_iter_paths` lift into a single table mapping
each `CleanupCategory` to `(directory resolver, filename patterns)`. The
disk walker and the storage filter both consume that table. Adding a
category stays what the enum's docstring already promises: extend the enum,
extend the table, add the SPA checkbox and CLI flag.

### Storage enumeration is one `list()` call

Not one per category. `_stored_exports()` establishes the pattern for
`export_overview`, and the reasoning (N HEADs versus one list) applies
harder here because cleanup spans seven categories rather than one prefix.
One `storage.list(f"{scope}/")` per plan, filtered in memory.

`StorageObject` already carries `size`, so per-category byte totals come
free and are honest rather than estimated.

The local/storage distinction that `_stored_exports` documents must be
preserved: `None` means "no bound storage, ask the disk", an empty dict
means "storage answered and there is nothing there". Collapsing them makes
a storage hiccup look like a desktop project with no files.

### `CleanupItem` gains `storage_key`

```python
storage_key: str | None = None   # set => the bytes are in object storage
```

When set, `path` stays the local-equivalent display path so the CLI's Rich
table and the SPA render unchanged. `apply_cleanup` branches on it:
`storage.delete(key)`, then `path.unlink(missing_ok=True)` for any local
mirror, so the running container stops serving a copy it already pulled.

The local unlink is deliberately not counted twice in `bytes_freed` -- the
durable object is the thing being reclaimed; the mirror is a cache.

### The source-presence guard is a flag, not a block

A derived video is reconstructable only while the thing it derives from
survives. A trimmed MP4 is a lossless cut of a source at beep-derived in/out
points: re-running the export reproduces it byte-for-byte *provided the raw
source is still there*. Once the upload is gone, the trim is as
irreplaceable as the CSV.

The earlier sketch on #629 had this guard *exclude* such items from the
plan. That is wrong, and the reason is legibility: a user staring at a
dialog that will not offer their 4 GB trim has no way to learn why. Silently
omitting things from a plan that promises to show what can be reclaimed
makes the plan a liar.

Instead `CleanupItem` carries `reconstructable: bool`, and unreconstructable
items are excluded from the "select all" affordance and require an explicit
opt-in. That is not a new mechanism: it is exactly the shipped precedent for
`AUDIT_DATA`, which `SAFE_CATEGORIES` already excludes for the same reason
(deleting it destroys user work rather than costing recompute time).

Every item carries the flag; what differs per category is the input it is
computed against. "Reconstructable" means *this artefact's own input still
exists*, not "the source video exists":

| category | reconstructable when |
| --- | --- |
| `CACHES` | always -- thumbs, probes, peaks are re-derived on demand |
| `EXPORTS_LIGHT` | the stage's **audit doc** is present |
| `EXPORTS_TRIMS` | the stage primary's **source** is durably present |
| `EXPORTS_OVERLAYS` | same as trims |
| `AUDIT_TRIMS` | same as trims |
| `AUDIO` | same as trims |
| `AUDIT_DATA` | never -- and it stays gated as it is today |

`EXPORTS_LIGHT` keying on the audit doc rather than the source is the
non-obvious row, and getting it wrong regresses desktop. A CSV or FCPXML
encodes audit state at export time, so it is re-derivable from the audit doc
without touching the source -- and the audit doc is durable, deletable only
through the separately-gated `AUDIT_DATA` category. Calling `EXPORTS_LIGHT`
unreconstructable outright would drop it out of "select all", where it sits
today via `SAFE_CATEGORIES`, and would quietly make the desktop select-all
weaker than it is now for the cheapest, most re-derivable category in the
table.

In the ordinary case -- audit doc present, sources present -- every
non-`AUDIT_DATA` item is reconstructable and select-all behaves exactly as
it does today. The flag only bites once something upstream is already gone,
which is precisely when the user needs telling.

### `source_present` needs a durable variant

`MatchProject.source_present` returns `True` when `(root / video_path)`
exists on local disk. On hosted that path is the ephemeral source cache, so
a cached copy would make a trim look reconstructable when it is not -- the
cache is wiped on the next redeploy and the "reconstructable" promise
evaporates with it.

It gains `durable: bool = False`. When `True` and a storage is bound, the
local-disk check is skipped and only `storage.exists` answers. On desktop no
storage is bound and the local file *is* the durable copy, so the parameter
is a no-op there. Only the cleanup planner passes `durable=True`; every
existing caller keeps today's behaviour.

This is a real distinction and not defensive coding: the whole value of the
`reconstructable` flag is that it survives the container.

### The audit trail follows the bytes

`apply_cleanup` writes one JSONL line to `<root>/.cleanup.log`. On hosted
that is ephemeral, so the audit trail of every reclamation is lost on the
next deploy.

It moves to `<scope>/.cleanup.log` in object storage when a storage is
bound, via read-modify-write. Two concurrent cleanups can lose a line to
that race. Accepted, and documented in the code: this is an audit trail of
a manual, single-user, jobs-blocked action, and the alternative on offer
today is losing the entire file on every redeploy. A new tenant table would
be the durable answer and is not worth one JSONL line -- and would pull in
the #632 `match_id` constraint for no benefit.

### Storage-side safety guard

`_safe_under_raw` refuses any item resolving under `raw/`. Its storage
analogue refuses any key that is not under `<scope>/`, and any key under
`<scope>/raw/`. Fails closed: a key that cannot be classified is not
deleted.

Verified while planning, and worth stating because it inverts which half
of that guard is load-bearing: **raw sources are not under the scope at
all.** They are keyed `raw/<name>` at the storage root
(`server.py:7904`), and `bind_storage`'s docstring is explicit that
`scope` prefixes derived-artefact caches only, while the raw resolver
keys off the user-prefix-relative `StageVideo.path`. So a single
`storage.list(f"{scope}/")` structurally cannot reach a raw source -- the
scope confinement is the real protection and the `raw/` refusal is
defence-in-depth against a future scope-layout change.

The same fact has a sharp edge for tests: seeding a source at
`<scope>/raw/clip.mp4` puts it where `source_present` will never look.

### Audit-doc presence has to come from the caller

The `EXPORTS_LIGHT` row keys on the stage's audit doc. On hosted those
live in the `state_docs` table, not on the container's disk, so a planner
that stats `audit_path` finds an empty directory and marks every CSV and
FCPXML unrebuildable -- pushing the cheapest category out of "select all"
on precisely the deployment this change exists for.

`plan_cleanup` therefore takes `audit_stages: set[int] | None`, supplied
by the route from `state.load_audit`. This is not a new pattern:
`MatchProject.export_overview` already takes `audit_docs` for the same
reason and from the same accessor. `None` means "read the disk", which is
desktop; an empty set means the caller looked and found none.

The existing 409-while-jobs-active block on `cleanup_apply` is unchanged and
still correct -- a mid-flight ffmpeg write racing a delete is the same
hazard whether the target is a disk or a bucket.

## The SPA surface

New: category checkboxes with live byte totals from the plan endpoint, a
select-all that omits unreconstructable items and `audit-data`, an explicit
opt-in for each of those, the 409 `jobs_active` state rendered as a blocking
message naming the job, and a confirm step before apply.

The plan endpoint is already debounce-friendly by design (unknown categories
yield an empty plan rather than 400, specifically so the SPA can fetch as
checkboxes toggle). That contract was written for a caller that never
arrived; this is it.

**Placement: the Export page.** It already renders per-stage artefacts and
match-level deliverables with presence and timestamps, so it is where a user
forms the intent "I have too many of these". #629 is an Export-page issue.

This is the one call in this document most open to being wrong: cleanup also
spans `caches`, `audio` and `audit-trims`, which are not export concepts,
and Home is the defensible alternative as the match-level surface. Say so
now if Home is preferred -- it is a cheap change before implementation and
an expensive one after.

Build on the `ShellChrome` seam rather than inventing chrome; `RootLayout`
owns the global header and each shell portals its own context row.

## Testing

An in-memory fake `Storage` (the suite already has this shape for other
storage tests). What must genuinely fail against pre-change code:

- A plan over a seeded hosted scope returns the right items, categories and
  byte totals. Pre-change this returns an empty plan -- that is the bug.
- Apply deletes exactly the planned keys from storage, and no others.
- A trim whose *source object* is absent appears in the plan with
  `reconstructable=False` and outside the safe set. Seed the source present
  in the local cache but absent in storage: that is the case `durable=True`
  exists for, and it fails against a `source_present` without the parameter.
- `EXPORTS_LIGHT` stays reconstructable while the audit doc is present, and
  flips only when the audit doc is gone. This is the row that regresses
  desktop select-all if it is keyed on the source by mistake, so it is
  pinned in both directions rather than one.
- The storage safety guard refuses a key outside `<scope>/`.
- The SPA dialog renders totals, excludes unreconstructable items from
  select-all, and surfaces the 409.

Stated honestly, because the project's review practice asks for it: the
desktop regression tests (no storage bound, plan and apply byte-identical to
today) **pass against pre-change code by construction**. They guard a
surface against this change; they are not evidence the change works. Only
the five above are.

Each new test gets the mutation drill before the PR: delete the fix, watch
the test fail. A test that passes against the pre-change code is not
coverage, and this repo has shipped several that did.

## Out of scope

- Any cap, quota or background eviction. Finding 1 removed the premise.
- #629's export-run record (run grouping, duration, formats, anomaly count).
  Independent, still open, unaffected by this.
- Versioned export filenames. Overwrite-in-place is the current contract and
  changing it would break the six readers `export_naming.py` names.
