# Lab redesign: fold the legacy Algorithm Lab into the dev-mode workflow

**Date:** 2026-08-14
**Status:** Approved (design discussed and accepted in-session)

## Context

The legacy Algorithm Lab (`/dev/legacy/lab`, `Lab.tsx`, ~3,500 lines) predates the
match/shooter data model and the dev-mode workflow (#331). A week of fixes
(#883-#888) made it functional again, but each fix confirmed the same diagnosis:
the page's single-column catalog-plus-drawers layout fights the current corpus
size (126 fixtures) and data model. The dev-mode workflow pages (Corpus / Review
queue / Validate / Retrain) were built as its successor and already own
browsing, queue, validation-by-eval, and calibration builds. This spec moves the
four capabilities only the legacy page still owns into the workflow pages, then
deletes the legacy page.

Chosen approach (option C of three discussed): page structure, layout, and
chrome are designed fresh on the dev-mode design language via the
frontend-design skill; the proven interaction primitives (labeling stepper,
waveform diff, snippet player, keyboard shortcuts) are ported as components and
restyled, not reinvented; the glue (routes, data loading) is rewritten clean.

## Decisions (made by the user)

1. **Labeling home**: a full-page fixture detail route reached from Corpus rows.
2. **Tuning home**: the Validate page.
3. **Promotion home**: the Corpus page.
4. **Sweeps** move to Validate; the legacy Lab page is **deleted in this series**
   (no transition period). Rebuild-calibration button drops (Retrain owns it).

## Routes

All under `DeveloperShell`; the `?match=` dev-mode match context keeps riding
every internal link (existing `withMatch` mechanism).

| Route | Change |
| --- | --- |
| `/dev/corpus` | Gains promote entry points (batch per-shooter, from-anchor) and rows navigating to the detail route. |
| `/dev/corpus/:slug` | **New** full-page fixture detail -- the labeling home. |
| `/dev/validate` | Gains the tuning panel (sliders, live rescore, save-as-YAML) and the sweeps card. |
| `/dev/review`, `/dev/retrain` | Unchanged; queue items link to `/dev/corpus/:slug` (labeling) and `/review` (marker edits). |
| `/dev/legacy/lab(/:slug)` | Deleted. Redirects: `/dev/legacy/lab` -> `/dev/corpus`, `/dev/legacy/lab/:slug` -> `/dev/corpus/:slug`. The older `/lab(/:slug)` redirects re-point the same way. Sidebar "Lab playground LEGACY" entry removed. |

## Components

Ported out of `Lab.tsx` into `src/components/lab/`, one focused file each,
interaction logic intact, styling on the dev-mode (cyan) design language:

- `StepThroughPanel` (candidate stepper, `REASON_SHORTCUTS`/`SUBCLASS_SHORTCUTS`
  keyboard maps, truth -> subclass / reject -> reason labeling)
- `CandidateTable`, `DiffList`, `LabelDropdown`, `LabelBreakdown`
- `ZoomedWaveform`, `Pin`, the `LAB_PALETTE` outcome palette + helpers
- `SnippetPlayer` plus the module-level audio-buffer cache (`loadAudioBuffer`,
  `disposeLabAudio`, `AUDIO_CACHE_MAX`)
- `VoterChips`, `VoterRecallTable`

Re-laid-out rather than ported (they are glue, not engines):

- `PromoteStagesPanel` (from `PromoteAllStagesButton`: match selector, shooter
  checkboxes all-on-by-default, per-(shooter, stage) rows -- behavior per
  #886/#885 tests)
- `PromoteFromAnchorPanel` (from `PromoteFromAnchorButton`)
- `TuningPanel` (from `TuningCard` + `SaveYamlButton`)

Everything else in `Lab.tsx` is deleted with the page. `SweepsCard` moves as-is.

## Page layouts

**Fixture detail (`/dev/corpus/:slug`)**

- Header: slug, event/shooter identity, ground-truth count; actions: Edit
  markers (-> `/review?fixture=...`), delete fixture, re-review promotion (when
  `anchor_slug`); prev/next fixture navigation to walk the corpus without
  returning to the list.
- Status strip: eval state chip -- `cached` / `auto-running (progress)` /
  `failed (retry)`.
- Full-width waveform diff (ground truth vs candidates, zoom lane).
- Working area: candidate table (left) + sticky step-through labeling panel
  (right); the active candidate and its label buttons never leave the viewport.
- Footer row: P/R/F1 metrics + per-voter recall, compact.

**Corpus list**: stays a table; promotion becomes a full-width expandable
section (not a 640 px popover). Pencil = Edit markers; row click navigates to
the detail route.

**Validate**: run-config bar (existing) -> headline metrics -> tuning panel and
sweeps side by side. Slider changes live-rescore the cached universe via
`/api/lab/rescore` exactly as today.

## Data flow

- **Detail-page eval**: on open, if the slug is not in the cached last run,
  auto-run `POST /api/lab/eval {slugs: [slug]}` with the current config, poll
  the job, then refresh from `/api/lab/last-run`. Progress shows in the status
  strip. Measured cost: seconds (vs ~10 min full-corpus).
- **The one backend change**: a slug-scoped eval whose config matches the cached
  run's config **merges** its fixtures into the cached universe instead of
  replacing it; a config mismatch replaces, as today. This stops labeling from
  clobbering a full Validate run.
- Label saves, rescore, and promote endpoints are consumed unchanged.

## Testing

- TDD throughout. Existing Lab tests migrate with their features: promote-panel
  tests (#885/#886) -> Corpus; pre-eval feedback + scroll tests (#887/#888)
  become detail-page auto-eval tests.
- New backend test: merge-vs-replace semantics of slug-scoped eval.
- Full Playwright drive (bundled headless Chromium) of the workflow: corpus ->
  detail (auto-eval) -> label -> validate -> tune, before the deletion PR lands.
- Grep sweep for dangling references to the deleted routes/components
  (including stale code comments that name `/dev/legacy/*`).

## Delivery

A PR series on `feat/lab-redesign`-prefixed branches, each merged on green CI:

1. Component extraction out of `Lab.tsx` (no behavior change; legacy page keeps
   working off the extracted components).
2. Backend merge-eval change.
3. Fixture detail page + Corpus row navigation.
4. Corpus promote section; Validate tuning + sweeps.
5. Legacy deletion + redirects + reference sweep.

## Out of scope

- Any change to detection, eval, or calibration logic.
- The Review queue's own detail panel (keeps linking out).
- Retrain-page gaps noted in its header comment.
- Mobile layouts (dev mode is desktop-gated).
