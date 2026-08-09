# #775: audited stages are always fully classified; review state stays derived

Issue #775: `coach.statistic_splits` (and its TS mirror `statisticSplits`)
switch to the classified path as soon as any interval carries a class. On a
partially classified stage they average over whichever intervals happened to
get a class and silently drop the rest.

Decision: instead of changing the read rule (all-or-nothing), make the
partial state unreachable. Classification becomes complete as part of the
audit lifecycle; "reviewed in coaching mode" is carried by data that already
exists and never gates calculations.

## 1. Invariant

An audited stage's shots - those with `ms_after_beep` - all carry an
`interval_class`. Enforced at two points:

- **Audit save**: the audit save endpoint runs `classify_intervals_in_dicts`
  on the shots before persisting, in the same write that appends the `save`
  event. Manual classes are preserved (the classifier already skips
  `interval_class_source == "manual"`). No extra audit event is appended -
  classification is derived data riding on the save.
- **Lazy backfill on read**: the coach GET (and any stage-payload read that
  feeds statistics) runs the same classifier when it finds an unclassified
  shot that has `ms_after_beep`. For the owner the result is persisted; for
  share-token/anonymous readers it is classified in-memory only and never
  written (share-token requests impersonate the owner's tenant, so RLS would
  not reject the write - the `current_share_request` guard is the only
  defense). Legacy audited stages therefore heal on first touch. The overlay
  export path (`compare/overlay_data.py::_load_shots`) heals the same way,
  in memory only, so a legacy partial doc never renders a wrong average into
  an exported MP4.

Shots without `ms_after_beep` remain unclassified by design. Python drops
them in `audit_shots_to_engine_shots` before statistics see them; the TS
class filter excludes them.

## 2. `statistic_splits` / `statisticSplits`: no logic change

The `any`/`some` branch stays. With the invariant, "some classified but not
all" occurs only for `ms_after_beep`-less shots, which are excluded on both
sides anyway. All-or-nothing was considered and rejected: a permanently
`ms`-less shot would lock a fully reviewed stage onto the threshold rule
forever on the TS side. Comments and docstrings on both sides are updated to
state the invariant and cite #775.

## 3. Review state: derived, data-only

No new fields. "Reviewed in coaching mode" means
`interval_class_source == "manual"` for that interval; the field is already
in the audit docs and the coach payload. Future consumers (share cards)
derive per-stage summaries themselves. Nothing gates calculations on review
state. No UI surface for now.

## 4. Coach SPA

Remove the mount-time auto-reclassify effect in `Coach.tsx` (redundant once
the backend guarantees the invariant). Keep the manual "Reclassify" button
and the per-shot class picker unchanged.

## 5. Tests

- Save endpoint: unclassified stage, then save - every `ms`-bearing shot is
  classified with source `auto`; manual classes are untouched.
- Backfill: legacy audited doc with no classes - owner coach GET returns
  classified shots and persists them; share-token read returns classified
  shots but the stored doc is unchanged.
- `ms`-less shot: stays `None`; the following shot classes as `first_shot`
  (existing behavior); statistics unaffected.
- `test_statistic_splits_partial_classification_trusts_the_classes` and its
  TS mirror stay as documentation of the read rule.

## 6. Scope

Both sides move together per the issue: `coach.py` and `splits.ts` changes
land in the same PR as the server changes. The share-card work consumes
`statistic_splits` unchanged.
