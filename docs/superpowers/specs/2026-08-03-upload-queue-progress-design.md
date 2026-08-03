# Hosted upload queue progress (#556)

Date: 2026-08-03

## Problem

Hosted raw-footage uploads run one file at a time by design -- a slow
uplink should not be starved by concurrent multi-hundred-MB transfers.
The complaint in #556 is that this is indistinguishable from a hang.

Part of the issue has already been addressed. `UploadDock` (#573, merged
2026-07-06, one day after #556 was filed) shows `Uploading X of N . P%`
with an aggregate bar, and per-file rows in both the dock and the modal
already distinguish `queued` from an active percentage. What remains:

1. **The modal has no aggregate.** `HostedUploadBody` renders
   `This session (N)` and per-file rows only. The dock is a fixed
   bottom-right portal and sits behind the modal's overlay, so while the
   upload surface is open there is no queue-level signal at all.
2. **No ETA.** Nothing tracks throughput over time.
3. **Two defects in the dock's aggregate** (`UploadDock.tsx:11-19, 32`):
   - `Uploading ${done + 1} of ${total}` counts only `done`, so one
     cancelled or errored file mislabels the active index for the rest
     of the run.
   - `pct` divides summed `bytesSent` by the size of *every* file,
     including cancelled and errored ones, so a queue containing a
     failure can never reach 100%.

## Decisions

**The pump stays strictly sequential.** Bounded concurrency is listed as
a possibility in the issue; it is declined. The dead-queue feeling is a
display problem, concurrency on a slow uplink makes every file slower,
and `pumpingRef` (`uploads.tsx:211-249`) is load-bearing for the cancel
semantics. One stream also makes the ETA honest.

**ETA is computed from rolling throughput**, not from bytes remaining
alone.

**`lib/uploadStats.ts` gets vitest coverage.** `ui_static` has no test
runner today and CI never runs SPA tests (#647), which is how both
aggregate defects survived. Scope is one devDependency plus a `test`
script, covering the pure module only -- no jsdom, no component tests.

## Design

### One stats function, two surfaces

`lib/uploadStats.ts` exports a pure function:

```ts
queueStats(uploads: PendingUpload[], samples: ThroughputSample[], now: number): QueueStats
```

returning `{ activeIndex, countable, doneCount, failedCount, bytesSent,
bytesTotal, pct, etaSeconds }`. No React, no clock of its own: `now` and
the samples are arguments, which is what makes it testable.

The provider computes it once and exposes it on the upload context.
`UploadDock` and `HostedUploadBody` read the same object rather than each
deriving its own -- the same "two surfaces that must agree get one rule"
constraint applied to `_audit_trim_targets` in #351, where the count that
gated a button and the endpoint behind it had drifted apart.

### Defect fixes, inside that function

- `countable` is every upload that is not `cancelled`. `activeIndex` is
  the active file's position within `countable`, so cancelling mid-run
  stops shifting the label.
- `pct` divides by the bytes of files that can still finish -- `done`,
  `uploading`, `queued` -- excluding `error` and `cancelled` from both
  numerator and denominator. The bar therefore reaches 100% when
  everything that can succeed has.
- `failedCount` is surfaced separately so failures are visible as a count
  instead of silently dragging the bar down.

### ETA from a trailing window

The provider keeps a `{ t, bytes }` sample ref appended on each XHR
progress tick, where `bytes` is the queue-wide sent total. Samples older
than 10s are trimmed; the ref is cleared when the queue goes idle so a
new run does not inherit the previous one's rate.

Rate is `(newest.bytes - oldest.bytes) / (newest.t - oldest.t)`.
`etaSeconds` is `bytesRemaining / rate`, and is `null` until the window
spans at least 2s and the rate is positive -- otherwise the first tick of
a file flashes a wild number.

### Surfaces

A shared `UploadQueueSummary` component renders
`Uploading 3 of 12 . 41% . 4.2 GB left . ~6 min` plus the aggregate bar.
The dock replaces its hand-rolled header with it; the modal gains one
above the `This session` list. Per-file rows are unchanged in both.

The line is composed by `summaryParts`, not inside the component, so
every clause is reachable from a test -- a label reading
`Uploading 3 of 12` over a drained queue is exactly the confusion this
issue is about, and that is component logic no pure test would catch.

The modal's list is filtered to one shooter but the pump is global, so
the summary above it reports the whole queue: a file queued for another
shooter genuinely delays these. Where the queue actually spans shooters
the line says `all shooters` rather than showing numbers that outrun the
list below.

### Two layouts, measured rather than guessed

Rendered against the built CSS at the dock's real width, the full line
wraps to two lines at 360px. Wrapping mid-clause moves the wrap point
every time the ETA appears or the byte figure crosses a unit boundary,
so the fixed bottom-right dock would change height while it worked.

`layout="stacked"` therefore splits at a chosen point: headline
(`Uploading 3 of 12 . 41%`) on one line, volatile detail
(`5.77 GB left . ~2 h 25 min . 1 failed`) muted on a second. Re-measured
after the change, every line renders unwrapped and unclipped at 360px
and the header height is stable. The modal is wide enough for the
single-line `inline` layout.

### ETA staleness

A stalled upload emits no progress events, so nothing re-trims its
window and the last known rate would keep projecting a confident
estimate. `queueStats` therefore takes `now` and disowns a window whose
newest reading is more than 10s old. A one-second interval in the
provider re-renders while uploads are in flight, so the ETA counts down
between progress events and goes quiet on a stall.

`formatBytes` is duplicated in `AddFootageModal.tsx` and
`FolderPicker.tsx`. The shared summary needs it plus a duration
formatter, so both move to `lib/format.ts` and `AddFootageModal` switches
to the shared copy -- it is being edited anyway. `FolderPicker` is left
alone; converting it is unrelated to this change.

## Testing

`lib/uploadStats.test.ts` under vitest, covering:

- a mid-run cancellation does not shift `activeIndex`
- an errored file leaves `pct` able to reach 100
- `failedCount` counts errors, not cancellations
- zero-byte files do not divide by zero
- `etaSeconds` is `null` on a cold or single-sample window
- `etaSeconds` matches a hand-computed rate on a steady window
- an idle queue reports no active file

Each test is written and watched to fail before the function exists.

The two defect fixes are the cases that would have failed against the
current `UploadDock` arithmetic; the rest pin new behaviour.

Visual verification: rendered against the built CSS at the dock's real
360px, measuring wrap and clipping rather than eyeballing a screenshot.
That is what surfaced the wrapping headline and produced the stacked
layout above.

`pnpm test`, `pnpm typecheck`, `pnpm lint` and `pnpm build` are run
locally, because the main CI job installs no Node at all. `pnpm test` is
added to the `slim-smoke` job, which already has pnpm -- a partial guard,
since that job is opt-in on PRs via the `run-slim-smoke` label. Wiring
SPA tests and typecheck into the main job is #647.

## Out of scope

- Bounded / configurable concurrency (declined above).
- Resumable multipart uploads -- that is #557.
- Component or DOM tests, and wiring SPA tests into CI -- #647 owns that.
