---
paths:
  - "src/splitsmith/export_presets.py"
  - "src/splitsmith/export_preview.py"
  - "src/splitsmith/logo_placeholder.py"
  - "src/splitsmith/db/export_presets.py"
  - "src/splitsmith/ui/export_presets_api.py"
  - "src/splitsmith/ui/export_preview_api.py"
  - "src/splitsmith/ui_static/src/pages/Export*.tsx"
  - "src/splitsmith/ui_static/src/pages/matchExportModel*.ts"
  - "src/splitsmith/ui_static/src/components/export/**"
  - "src/splitsmith/ui_static/src/lib/api.ts"
  - "src/splitsmith/ui_static/src/lib/api.exportBodies.test.ts"
  - "src/splitsmith/ui_static/src/lib/exportPresets*.ts"
  - "src/splitsmith/ui_static/src/lib/lookGallery*.ts"
  - "src/splitsmith/ui_static/src/lib/looks*.ts"
  - "src/splitsmith/ui_static/src/lib/useLooks*"
  - "src/splitsmith/ui_static/src/lib/renderOptions*.ts"
  - "src/splitsmith/ui_static/src/lib/logoSpots*.ts"
  - "src/splitsmith/ui_static/src/lib/logoPlan*.ts"
  - "src/splitsmith/ui_static/src/lib/exportPreview*.ts"
  - "tests/test_export_pre*.py"
  - "scripts/render_look_thumbnails.py"
---

# The Export page: presets, the Look gallery, previews

Look manifests and templates are in `looks.md`; the renderers in
`rendering.md`; the page's blocker ladder in `ui-budget.md`.

## Presets and request bodies (spec 2026-09-15)

- `export_presets.ExportPresetBody` is **the one shape** the API, the local
  `export_presets.json` and the SPA's `localStorage` last-used entry share.
  Every field defaults and `extra="ignore"` is set, so a body saved before an
  option shipped loads with that option at its default. **Adding an option is a
  defaulted field here plus a column in `lib/exportPresets.settingsToBody` /
  `applyBody`, and nothing else.**
- **The export wrappers in `lib/api.ts` (`exportMatch`, `exportStage`,
  `exportCompareGrid`) spread their payload and never list fields.** A
  hand-written list once silently dropped the title page, closing card and
  summary hold from every UI export while the page tests, which mock the
  wrappers, stayed green. `api.exportBodies.test.ts` types each payload
  `Required<...>` so a new request field fails the typecheck until it is shown
  to reach the wire.
- Presets are per user (`export_presets` table hosted, the JSON file locally)
  and never a `state_docs` kind (a per-match kind would enter the sync
  manifest).
- Match-specific fields (stage selection, the title line, the description lead,
  the bundle name, the reference shooter, a publish date) are never stored;
  `isDirty` ignores them by construction because `settingsToBody` does not emit
  them.
- The page's recurring state is one `ExportSettings` object; a new recurring
  field goes on it, not on a fresh `useState`.
- The Export page's tests open the folded groups through their `openGroups`
  helper before reaching a control; a new group needs adding there.

## The Look gallery (spec s2)

- `lib/lookGallery.ts` is **the one registry** of slots, variants, thumbnails,
  parameters and which mode and format can draw each;
  `components/export/LookGallery.tsx` renders it and owns nothing.
- A new effect is one registry entry plus one thumbnail from
  `scripts/render_look_thumbnails.py` (Chromium once, at authoring time; **the
  gallery never rasterizes**). The cut and the FCP effects are bundled stills;
  the xfade families come from the server with looping WebP clips of the real
  transition, rendered through the project ffmpeg.
- `slotsForLook(looks, settings, transitions)` folds the catalog into the static
  table: Look tiles first when more than one Look is installed; a Style
  `Segmented` (`VARIANT_FIELD`) under a card that is on when the chosen Look has
  more than one template variant; the Look's stings among the transitions with their
  catalog previews. A stored kind reads as its family; picking a family keeps
  the direction already chosen or takes its first; a family with more than one
  direction shows a Direction `Segmented`.
- The kind filter (`visibleTransitionKind`) admits on MP4 only what
  `requestLook` lists: the server's kinds and the chosen Look's stings, or the
  stored kind itself while the catalog is not there. The rail and group summary
  name a kind with `transitionLabel`.
- Installed Looks come through `lib/looks.ts` (the pure reader of
  `GET /api/looks`: `visibleLook`, `visibleVariant`, `stingsFor`,
  `resolveLookChoice`) and `useLooks` (fetched once; `BUILTIN_LOOKS` until it
  answers).
- **A Look field rides a request only when it is not the default
  (`nonDefault`)**, so an untouched form sends the body it always sent. A stored
  Look no longer installed, or a variant the Look lacks, is resolved before any
  request.
- Transitions live in the Look group (FCPXML only, sent as `none` elsewhere);
  the title line in Details.
- A slot whose seconds field is being edited reads NaN and must still count as
  on, or the input vanishes under the cursor (`summaryHold.read`).
- Pins: `lookGallery.test.ts` (the per-format visibility table; every committed
  thumbnail is referenced) and `renderOptions.test.ts` (the mappers never send a
  field the registry hides).

## The rail's preview (spec s3)

- `POST /api/shooters/{slug}/export-preview`, engine
  `export_preview.render_preview`: it **declares the card exactly as
  `ui/match_exports.py` does** and composes it through the renderers' own
  builders over a head or tail frame from the trim on this disk (the surface
  when there is none, which is every hosted container by design; a seek past the
  clip's end takes its last frame).
- Cached under `cache_dir/export-preview` by a content key including the
  project's `updated_at` and the audit version. The request carries `look` and
  `variant` (the focused slot's, only when not the defaults), and both are in
  the key.
- 503 is no browser, 409 is the overlay without shots; the SPA maps each to one
  muted line in `PreviewPane` and **never blocks the Export button**.
- **A new Look variant needs a `previewCardFor` case** or it previews as the
  frame.
- An animated template previews at its `poster()` through `render_template`, or
  with `motion` (what the rail and the editor's big preview ask for) as a
  looping animated WebP of its own frames at `MOTION_FPS` over its still's
  backdrop, last frame held. A still template answers the PNG it always did, so
  `motion` is safe on any card. The response type follows the bytes (`_image`)
  and the cache keeps `<key>.png` or `<key>.webp` accordingly.
- **Logo placeholders** (both previews): `logo_placeholders` on the request
  fills every logo spot no logo fills (the shooter's corner, your brand, the
  event's centre; title, slate, lower third, closing) with a labelled dashed square from `logo_placeholder` (a
  content-named PNG under `cache_dir/logo-placeholders`), keyed apart by
  `PLACEHOLDER_REVISION`. The switch is the viewer's (`lib/logoSpots`, on by
  default); **no export path ever asks for it**.
