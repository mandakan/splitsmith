---
paths:
  - "src/splitsmith/looks*.py"
  - "src/splitsmith/look/**"
  - "src/splitsmith/fonts.py"
  - "src/splitsmith/own_fonts.py"
  - "src/splitsmith/overlay/theme.py"
  - "src/splitsmith/overlay/html.py"
  - "src/splitsmith/overlay/hud*.py"
  - "src/splitsmith/summary_template.py"
  - "src/splitsmith/palette_sources.py"
  - "src/splitsmith/data/looks/**"
  - "src/splitsmith/ui/looks_api.py"
  - "src/splitsmith/ui/palette_api.py"
  - "src/splitsmith/db/looks.py"
  - "src/splitsmith/ui_static/src/components/export/**"
  - "src/splitsmith/ui_static/src/lib/look*.ts"
  - "src/splitsmith/ui_static/src/lib/templateEditor*.ts"
  - "src/splitsmith/ui_static/src/lib/palette*.ts"
  - "docs/looks/**"
  - "tests/test_look*.py"
---

# Looks: manifests, templates, sandbox, fonts, editor

A **Look** (`splitsmith.looks`) is `data/looks/<name>/look.json` (palette,
fonts, brand, one HTML template per card slot) plus its templates.
`~/.splitsmith/looks/<name>/` shadows a shipped one. Rendering is in
`rendering.md`, upright rules in `upright.md`.

## Hard rules

- **Custom templates are desktop only, by decision.** A template is code; hosted
  would run it beside the database and R2 credentials, and Chromium's OS sandbox
  cannot start on Railway (seccomp refuses user namespaces). On hosted a Look
  carries colours, catalog fonts and card styles, never a template. Do not build
  a hosted render service or relax any local-only guard (`LOCAL_ONLY_ROUTES`,
  `db.looks._check_hosted`) without the user asking.
- **Never widen the sandbox** (`look_sandbox`). Every template page, local and
  hosted, is navigated from `https://look.invalid/look/<file>`, never
  `file://`, and every request is answered by `Sandbox.handle`: `/look/` (the
  template's folder, symlinks out refused), `/shared/`, `/fonts/`, and
  `/file/<digest>/<name>` for each `logo` value naming a real PNG/JPEG/WebP that
  is not a symlink. The stylesheet and `assets` may name files inside the
  mounted folders only. `data` carries user text (a stage named
  `file:///proc/self/environ` must never be mounted:
  `test_user_text_naming_a_file_is_never_mounted`); a symlinked own font was a
  way to read any file, so `fonts.resolve` and `own_fonts` refuse them.
  Everything else aborts into `TemplateProbe.blocked`, which `looks check`
  reports. Websockets are routed to nothing, service workers blocked, files
  capped at `MAX_ASSET_BYTES`. The browser launches with `_SANDBOX_SWITCHES`
  (WebRTC UDP and DNS prefetch off, which the route cannot see; a V8 heap cap),
  so the route alone is not the whole sandbox. A crashed renderer is a
  `TemplateScriptError`, a skipped card.
- **Calls into template code go through `_TemplatePage.call`**, never a bare
  `page.evaluate`, which waits forever on a stuck page. Playwright's timeouts do
  not hold mid-call either, so `_TemplatePage.watched` SIGKILLs the browser
  (found by its `--splitsmith-rasterizer=<uuid>` switch) and the caller gets
  `TemplateTimeoutError`. Answers come back by a random call id, only for a call
  in flight, so a template cannot answer for the probe. Budgets live in
  `look_sandbox`. `overlay_raster.png` (our own overlay HTML) still uses
  `file://` and is not sandboxed.
- **Default frames stay pixel-identical.** A Look without a brand sends no
  `brand` key, and the default fonts are today's files and weights byte for
  byte, so a Look that sets nothing new renders what it always did. Check with
  the frame scripts against main.

## Manifest and variants

- `overlay_theme.load_theme` reads the manifest, so a palette change is a
  manifest change.
- A slot names variants (`"slot": {"default": ..., "rise": ...}`; a bare string
  is `default`). The IR carries `variant` on `MatchTitle` and `TitleCard`.
  Request bodies carry one per slot (`title_page_variant`, `stage_card_variant`
  for slate and lower third together, `closing_card_variant`); `None` falls back
  to `card_variant`, the CLIs' one knob. A variant the Look lacks falls back to
  `default` with a warning.
- `styles` on a manifest picks the variant a slot's `default` draws (how a
  template-less Look has card styles).
- `overlay_theme` on a request is any installed Look name, validated against
  `looks.look_names()` by `exports_api.installed_look` (422 lists the installed
  set). `ExportPresetBody.look` and its `*_variant` fields are validated by shape
  only, so a preset loads on a machine without that Look.
- Catalog: `looks.look_catalog()` (`GET /api/looks`); previews from the Look's
  `preview/` with the shipped default standing in (`looks.preview_file`),
  rendered by `scripts/render_look_thumbnails.py --look-previews`.

## Templates

- The shipped `card.html` draws the engine's markup through `_shared/cell.js`
  (a port of `overlay_html._cell_div`, held to it by
  `tests/test_look_template.py`) and `_shared/fit.js` (the one fit policy, also
  inlined by `overlay_html`). A template gets `window.splitsmith` from
  `look_template.TemplateContext` before its scripts run.
- `template_digest` covers template bytes, every file under `_shared/`
  (`shared_digest`), context, fps and Chromium version. An edit under
  `_shared/` invalidates every cached clip and overlay MOV. The export
  preview's key and the `look-check` cache carry `shared_digest` too; a new
  cache of template output must as well.
- Animated templates (`duration() > 0`) render frame by frame via `seek(i/fps)`
  into a lossless alpha MOV (`look_motion.write_motion_clip`), last frame held.
  `poster()` is what stills and previews show; put it where the card is visible
  (the `rise` card's poster is at the end of the rise). A still template takes
  the PNG path unchanged.
- `fit.js`: a line past the cell's real edge shrinks on its own (to no less than
  60 %), then gets an ellipsis; text inside the cell is never touched. Measure
  text with a Range, not an element's box. `tests/test_look_template.py` forbids
  the digits of the legibility floor anywhere in that file, issue numbers
  included.
- HUD templates: data comes from `overlay_hud`, never derived in a template;
  shared HUD helpers live in `_shared/hud.js` (`window.splitsmithHud`).
  `looks check` runs them on `look_tools.hud_samples`.

## Authoring tools

- `splitsmith looks list | new | check | preview | refresh` (`looks_cli` over
  `look_tools`), guide `docs/looks/authoring.md`, starters in
  `data/looks/_starters/`.
- `looks check` probes every template (`ChromiumRasterizer.probe_template`)
  against sample cards and reports script errors, animation without `seek`,
  `poster()` outside `duration()`, non-bundled fonts and cut-off text (an
  ellipsis is fine). HUD samples are also probed on a 608x1080 page for
  safe-area intrusions (`TemplateProbe.unsafe`, see `upright.md`). It reads a
  user Look strictly (`looks.read_look`), where `load_look` would fall back.
- A copy of a Look (`looks.look_files`: `looks new --from`, the editor's
  `draft_look`) carries every file but manifest and previews. A copy of a
  *shipped* Look names no template (`slots` empty, `base` set) so it draws the
  current shipped ones; `--templates` copies them. Don't "simplify" this into
  copying: a copied `card.html` froze the cards before the brand, the event logo
  and the credit existed.
- **Every copy carries the source's sound and grade**, the template-following
  one too (`look_tools._asset_files`): the raw `sounds` block (`LookManifest`
  ignores it, so `model_dump` drops it), the `sounds/` folder's files and the
  LUT the `grade` names. A starter takes a shipped Look's from its shipped
  folder (`_STARTER_ASSETS`). Without them a copy names files it lacks: no
  Look sounds, `looks dress` refuses the map, exports go out ungraded with a
  note. `test_a_copy_of_broadcast_carries_its_sounds`,
  `test_a_copy_of_broadcast_grades_with_its_lut`.
- A shipped Look's own LUT is generated by `scripts/build_luts.py`
  (`LOOK_LUTS`), never hand-edited; `tests/test_broadcast_grade.py` holds the
  file to its recipe and pins Broadcast's indoor-safe properties (black and
  white held, a warm grey's cast not stretched).
- A shipped Look's sound is CC0 with its source in `sounds/LICENSES.md`, and
  every file is played by some cue (`tests/test_broadcast_sounds.py`): a file
  there ships in the wheel to everyone, and a CC-BY one would need a credits
  file on every export.
- `data/looks/_history.json` records every shipped template version by sha256;
  **a changed shipped template must be recorded**
  (`scripts/record_template_history.py`; `tests/test_look_tools.py` fails
  otherwise). `look_tools.outdated_copies` uses it to recognise unedited old
  copies: `looks check` warns, `LookHealth` offers "Use the current cards", and
  `looks refresh` / `POST /api/looks/{name}/refresh` (local only) drop each such
  copy and its slot entries.

## Fonts

- Faces come from the bundled catalog `splitsmith.fonts` (`data/fonts/`), one
  per role, `display` and `mono`. Templates never name a file: they draw
  `"Splitsmith Display"` / `"Splitsmith Mono"`, which `overlay_html._style_rules`
  points at the theme's `display_font` / `mono_font` ids.
- `theme_for` resolves `look.json`'s `fonts` **by id or family label**, which is
  what keeps the shipped manifest's `"Antonio"` and `"sans": "Geist"` loading.
  An unknown one falls back to the role default.
- The ffmpeg clock reads the mono id too (`theme_font_face`, both renderers), so
  clock and counter never disagree.
- Desktop only: an own font `own:font-<12hex>.ttf|otf` in the Look's `fonts/`
  (`own_fonts.save_font`: 2 MB, bytes sniffed, WOFF and collections refused,
  opened with Pillow's FreeType because that is what `drawtext` reads with),
  uploaded through `/api/looks/{name}/fonts` (`LOCAL_ONLY_ROUTES`);
  `db.looks._check_hosted` refuses an `own:` value. `fonts.resolve(root=)`
  returns a path, so every consumer takes an id or a path
  (`overlay_html._face_source`, `overlay_text.resolve_overlay_face`). The
  content name keeps caches honest: the card PNG and the `@font-face` URL in
  `template_digest` both move with the bytes.
- Stored `fonts` are validated strictly (`fonts.check`) and copies normalise to
  ids (`body_from_manifest`); `theme_tokens` skips font ids.

## Brand on a Look

`look.json`'s `brand` (`looks.LookBrand`: a content-named `brand-<12hex>.<ext>`
in `brand/`, written by `look_brand.save_brand_logo`, plus a line).
`look_brand.brand_json` sends it to the title page and closing card only, as
`data.brand`; `_shared/brand.js` draws it top left and never moves the card's
text. Upload and serve are local only; hosted refuses a brand *logo* until Looks
have a file store. It is the video maker's brand, never a shooter's (see
`identity.md`).

## Stores

`look_store.LookStore` (`state.looks`, `GET/PUT/DELETE /api/looks/{name}`):
`FolderLookStore` locally (`put` rewrites only stored fields, keeps templates),
`db.looks.PostgresLookStore` hosted (`StoredLookBody`, never a template; a
shipped name or non-shipped base is refused). **Renderers never see a store**:
they resolve by name through `looks.user_looks_dir()`. Hosted sets
`looks.set_user_looks_provider(tenant_looks_provider(tenant))` everywhere it
pins `current_tenant` (auth gate, share alias, queue task). **A new place that
pins a tenant must set the provider too**, or its renders see no user Looks.

## Editor (SPA)

- Look editor: `components/export/LookAdvanced` -> `LookEditor`, rules in
  `lib/lookEditor`. Duplicate copies server-side first
  (`POST /api/looks/{name}/duplicate`: `looks new --from` locally, so a
  hand-made Look keeps its templates; hosted, a manifest on the source's shipped
  base). Draft previews go through the
  export-preview route's `draft` (`look_store.draft_look`) and `at`; both join
  the cache key only when set. Previews run one at a time
  (`lookEditor.serialQueue`): the server answers 429 to a second in flight.
  Contrast is a warning; only invalid fields block Save. `refreshLooks()` after
  every write.
- Template editor (desktop only): `components/export/TemplateEditor`, rules in
  `lib/templateEditor`; routes under `/api/looks/{name}/templates`, `/samples`,
  `/check`, `/reveal` are `LOCAL_ONLY_ROUTES`. Unsaved text rides preview and
  check as `templates` (`look_store.TemplateEdit`, applied by `draft_look` /
  `apply_template_edits`; the preview answers 403 hosted when set), keyed per
  own *file* (`editKey`; the shipped `card.html` draws four cards). A borrowed
  slot is written to `<slot>-<variant>.html` and named in `look.json` on first
  save.
- `LookHealth` checks the chosen own Look locally, cached under
  `cache_dir/look-check` by every file of the Look's folder. A draft or template
  text is never cached. It never blocks Export.
- Palette suggestions: every rule in pure `lib/palette`; the server only
  measures (`palette_sources`, deterministic k-means, empty on hosted).
  "Stands out" is OKLCh hue separation, not raw colour distance. Choosing one
  replaces the draft's colours and accent series; the neutrals stay.
- The gallery registry is `lib/lookGallery.ts`; see `export-ui.md`.
