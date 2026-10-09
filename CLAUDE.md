# Claude Code Guidance

This file gives Claude Code project-specific context. Read SPEC.md for the full technical specification.

## Project context

Personal tool for an IPSC competitor to extract shot splits from head-mounted camera footage. The user is an experienced developer who uses Claude Code daily. They prefer:
- Concise, direct communication
- Pushing back when something is wrong rather than agreeing reflexively
- Asking clarifying questions before diving into detailed implementations

## Code conventions

- Python 3.11+, type hints everywhere
- `uv` for dependency management — never use `pip` directly
- Pydantic for data validation
- `pathlib.Path` for paths, never strings
- f-strings for formatting
- Black formatting (line length 110)
- Ruff for linting
- Imports: stdlib, third-party, local — separated by blank lines
- No relative imports beyond a single dot (`.module`, not `..module`)

## Architecture rules

1. **Detection logic stays out of the CLI.** `cli.py` orchestrates; analysis happens in dedicated modules.
2. **Pure functions where possible.** Detection functions take audio data + config and return results. No file I/O inside detection logic.
3. **Pydantic models for all data crossing module boundaries.** No dicts of unknown shape passed around.
4. **Configuration is data, not code.** Tunable parameters go in `config.py` as Pydantic models with defaults; users can override via YAML.
5. **Every detection module has fixture-based tests.** Don't merge a detection change without a test that would have caught it.

## Testing approach

- `pytest` for everything
- The suite runs in parallel by default (`addopts` carries `-n auto --dist load`).
  `-n0` restores serial execution and is the right thing when debugging a single
  test — worker startup dominates a focused run, and tracebacks are cleaner.
  New tests must not depend on execution order or share mutable state outside
  `tmp_path`: a worker's process-global caches are its own, but the filesystem,
  ports, and `~/` are not.
- `pytest -m docker` needs `-n0` when the run spans more than one docker-marked
  file: the compose fixtures use fixed container names, and concurrent xdist
  workers collide on them.
- Fixtures live in `tests/fixtures/` — short audio clips with hand-labeled ground truth in adjacent JSON files
- Detection tests assert within tolerance (e.g., ±15ms for shot times)
- Mock ffmpeg in trim tests; don't actually shell out during unit tests
- Integration tests can use real ffmpeg but mark them with `@pytest.mark.integration`
- CI installs ffmpeg and runs the integration suite with
  `SPLITSMITH_REQUIRE_INTEGRATION=1`, which turns any skip of an
  `integration`-marked test into a failure. A test that needs media builds it
  with `tests/synthetic_media.py` rather than depending on the gitignored
  `stage_sample.mp4` — if a new integration test skips in CI, supply the input,
  don't re-add the skip.

## Review practice

For changes to the detection or export pipeline, run a review pass before merging. On PR #612 every substantive defect was found this way and none by the test suite, which was green over all four.

What actually finds things:

- **Name the specific claims to verify.** "The implementer says X is provably equivalent to Y -- check it against the original and treat any diverging input as a finding" beats "review this diff", which returns generic results.
- **Tell the reviewer the implementation report is unverified.** A stated rationale never downgrades a finding's severity.
- **Ask whether each new test genuinely fails against the pre-change code.** Several tests on that branch would have passed against the bug they claimed to cover. Deleting the fix and watching the test fail takes a minute and is the only real proof.
- **Run the code when behaviour is in question.** The exit-code defect was demonstrated by invoking the verb twice and capturing both codes, not by reading.
- **Finish with one whole-branch pass over the seams.** One defect lived in a seam no single task owned; only a cross-cutting read found it.

A green suite over a change is evidence the change didn't break anything known -- not evidence it works. A fix can also be real and still invisible: on #617 the note reached the table cell and rich ellipsized it away, so the assertion passed while the user saw nothing. Read the actual output.

## When in doubt

- **Ask before guessing.** Especially about audio detection thresholds, FCPXML structure, or anything user-facing.
- **Default to the conservative choice.** Better to under-detect shots and flag uncertainty than to invent shots from echoes.
- **Optimize for the audit trail.** Every analysis should produce a report file the user can review later. Don't silently make decisions.

## What this project is NOT

- A real-time tool. All processing is offline batch.
- A library. Single-purpose application.

## Detection pipeline

Beep detection runs inside per-stage derived search windows for multi-stage single-take videos (ffmpeg extracts the window's audio via -ss/-t; results are offset back to source-absolute). Its candidates are ranked by a logistic regression over seven per-run features (``beep_features.candidate_features``, the one implementation both runtime and trainer use), and confidence is a calibrated head over (logit, margin) (#949, spec 2026-10-06). The numbers are ``BeepRankerConfig`` defaults in ``config.py``, pasted from ``ranker_report.json``'s ``models.lr.final_fit``; ``tests/test_beep_ranker_config.py`` fails on any drift. To retrain after adding fixtures: rebuild the manifest, run ``scripts/train_beep_ranker.py``, paste the new ``final_fit``, regenerate ``baseline.json`` (``scripts/eval_beep_detector.py --track clip --json ...``) and list every newly wrong fixture in the PR. ``ranker: heuristic`` is the old hand-written product, kept as the escape hatch. Out of fold it picks the beep on 106 of the 111 fixtures where the beep is a candidate at all; most remaining misses are the 16 fixtures where it never is (run merging, late onsets), and every confident mistake so far is one of those.

The shot-detection pipeline is a 3-voter ensemble, not raw signal processing:

- **Voter A** -- ``splitsmith.shot_detect`` envelope onsets, gated at the
  auto-calibrated ``min_confidence`` floor (the lowest positive-shot
  confidence across the calibration set). This is the candidate generator;
  every other voter sees only candidates A emits.
- **Voter B** -- threshold on the CLAP shot-vs-not-shot prompt similarity
  differential; calibrated against labeled fixtures.
- **Voter C** -- a ``GradientBoostingClassifier`` over hand-crafted
  features + CLAP per-prompt similarities + PANN gunshot probability;
  calibrated to a target recall on the same set. Trained with
  ``sklearn`` in the build script, but shipped as one ONNX graph per
  camera class and run through ``onnxruntime`` -- nothing under
  ``src/`` imports sklearn or unpickles an estimator. Switches
  to a per-stage adaptive top-(K+slack) mode when the audit JSON has
  ``stage_rounds.expected``. The PANN gunshot-class probability used to
  be a separate voter D; it is now a feature column on voter C so the
  GBDT learns its interaction with the other inputs instead of casting
  an independent vote.
- **Consensus** -- a candidate is kept when ``vote_total + apriori_boost
  >= consensus`` (default 2-of-3). The apriori boost biases toward
  expected-shot-count regions when prior info is known.

The pipeline lives in ``src/splitsmith/ensemble/`` and is wired into the
production UI's ``/api/stages/{n}/shot-detect`` endpoint. Calibration
artifacts ship under ``src/splitsmith/data/`` (built once by
``scripts/build_ensemble_artifacts.py``) -- ``ensemble_calibration.json``
plus the voter C / voter E ONNX graphs it names; the FastAPI server
lazy-loads the CLAP / PANN / GBDT models on the first detection. Re-run
the build script after adding new audited fixtures. Set
``SPLITSMITH_ARTIFACTS_DIR=/path/to/experimental`` to point the engine
at a different artifact set for A/B comparisons without rebuilding the
shipped one (see ``splitsmith.runtime`` for the full env-var list).

The review-time variant generator ``scripts/build_ensemble_fixture.py``
still exists for offline comparison under ``build/ensemble-review/``.

## Rendered cards and stage summaries (#973, #972)

Both MP4 renderers (``mp4_render`` for one shooter, ``compare/mp4_grid``
for the grid) put every generated card -- title page, stage slate,
closing card, and the single-shooter summary hold -- on the spine as
**its own segment**, encoded exactly like a stage so the final stitch
stays a video stream copy. The grid's card segment therefore carries the
grid's full N+1 audio layout (``build_card_segment_command``); the
single-shooter stitch re-encodes audio only when a generated segment is
present, so a zero-card render's argv is byte-identical to before. A
lower-third is the one card that is not a segment: it rides the stage's
own filter graph through ``overlay_card.lower_third_filters``, upstream
of the grid's hold ``concat``, so it can never reach a summary frame.

Backdrop grabs differ by end and the difference is deliberate: a *head*
grab takes the first frame (``-frames:v 1``); a *tail* grab reads a
0.5 s window and keeps the last decoded frame, because a seek straight
to the last timestamp can come back empty. Using the tail technique for
a head lands 0.5 s into the stage (a review caught it).

The stage card's round count comes from ``project.json`` alone
(``overlay_data.load_expected_rounds``), never from the overlay's data,
so ``--titles slate`` prints it without ``--overlay``. The summary's
declaration lives in core (``overlay_summary_cell``,
``stage_summary_data``); ``compare/overlay_summary`` rebinds the old
private names because its tests monkeypatch them. Card and summary
encodes go through their own runner hooks (``card_runner``,
``still_runner``), never the progress ``runner`` both CLIs count for
"stage N of M". The visual checks are ``scripts/render_match_frames.py``
and ``scripts/render_grid_frames.py`` with their card flags; look at the
frames, a green argv test proves nothing about pixels.

The live overlay has **template styles** (spec
``2026-10-08-template-hud-overlay-design``): a Look's ``overlay`` slot names
HUD templates (``plate`` ships first); ``default`` is always Classic, the
engine path (run-length PNGs and the ``drawtext`` clock), and a manifest
cannot name a file for it. ``render_overlay(variant=...)`` sends a template
variant through ``overlay_hud_render``: the template draws clock, counter
and split per frame through ``seek(t)`` and declares ``settle()``, the
seconds it moves after the last shot; ``overlay_hud.hud_frame_plan`` renders
only the beep to ``last shot + settle`` and holds a frame either side. The
data (splits, coach classes, speed tiers) is computed in ``overlay_hud``,
never in a template. The MOV is cached in the render segment cache by the
template digest and left untouched on a hit (the MP4 keys on its mtime). A
template failure or an unknown variant draws Classic and lands in
``degraded``; no browser is still ``OverlayRenderError``. Classic's argv
and pixels never change for any of this; check with
``scripts/render_overlay_frames.py`` against main.
``looks check`` runs a HUD template on three sample stages
(``look_tools.hud_samples``: twelve rounds with classes, thirty-two, none
classified), probed at rest mid-stage and landed, and renders frames either
side of the live span to catch motion the frame plan would freeze; the
``hud`` starter is the minimal template and ``docs/looks/authoring.md``
documents the contract.

The single-shooter render keeps every encoded segment in
``segment_cache`` (``<cache_dir>/render-segments``, LRU past
``OutputConfig.render_cache_gb``), keyed on the segment's ffmpeg argv
with the output path factored out, inputs outside ``work_dir`` by path,
size and mtime, the per-render card PNGs by content, and the ffmpeg
binary's identity. So anything that changes a segment must reach its
argv or one of its input files; state that reaches ffmpeg any other way
(an env var, a file named inside a filter string) would be served stale.
Bump ``segment_cache.KEY_VERSION`` when the recipe changes. The job and
the CLI get the cache through ``match_exports.render_segment_cache``;
tests run with ``SPLITSMITH_RENDER_CACHE=0`` (conftest) and a cache test
passes its own. ``RenderStep`` is the per-segment progress the job maps
onto its bar. The grid renderer shares the cache
(``render_grid_mp4(segment_cache=)``, its ``.mov`` segments keep their
suffix): it renders into a fresh temp dir, so the key writes the work dir
as ``<work>`` in tokens and in small work files (the sprite concat list),
and the files ffmpeg reads with no token of their own (every sprite the
list names, the clock's font in ``drawtext``) reach it as
``extra_inputs`` through ``_overlay_inputs``; a new indirect input goes
there. Its progress is ``GridRenderStep`` through ``progress=``, one per
stage, encoded or reused, then the stitch; never count ``runner`` calls,
a reused stage makes none.

Cards draw through a **Look** (``splitsmith.looks``, spec 2026-10-06):
``data/looks/<name>/look.json`` holds the palette and names one HTML
template per card slot; ``~/.splitsmith/looks/<name>/`` shadows a shipped
one. ``overlay_theme.load_theme`` reads the manifest, so a palette change
is a manifest change. The shipped ``card.html`` draws the engine's own
markup through ``_shared/cell.js`` (a port of ``overlay_html._cell_div``,
held to it by ``tests/test_look_template.py``) and ``_shared/fit.js``
(the one fit policy, also inlined by ``overlay_html``); a template gets
``window.splitsmith`` from ``look_template.TemplateContext`` before its
scripts run. The summary and the live overlay are not templates yet.
Check pixels with the frame scripts, not argv.

A template that animates (``duration() > 0``) is rendered frame by frame
(``Rasterizer.render_template_frames``, seeking ``seek(i / fps)``),
written as a lossless alpha MOV by ``look_motion.write_motion_clip`` and
overlaid on the card's backdrop with the last frame held
(``motion_overlay_filters``, ``tpad=stop_mode=clone``); an animated lower
third is the same clip through ``lower_third_clip_filters``. The segment
cache keys that clip by ``look_template.template_digest`` (template
bytes, context, fps, Chromium version) through
``SegmentCache.key(virtual_inputs=...)`` and renders the frames in the
encode's ``prepare`` step, so a cached card renders no frame. A still
template takes the PNG path unchanged, which is what keeps the default
variant pixel-identical. The manifest names variants per slot
(``"slot": {"default": ..., "rise": ...}``; a bare string is ``default``),
the IR carries ``variant`` on ``MatchTitle`` and ``TitleCard``.
``card_variant`` is the CLI's one knob (``--card-variant`` on ``match
export`` and ``compare export``) and the fallback; the request bodies
carry a variant per slot since #1246 (``title_page_variant``,
``stage_card_variant`` for the slate and the lower third together,
``closing_card_variant``; ``None`` is the knob), threaded by
``match_exports`` and ``compare/cards.CardOptions``. A variant the Look
lacks falls back to ``default`` with a warning. The shipped ``splitsmith``
Look has ``default`` and ``rise`` (``card-rise.html``, Web Animations
driven by ``seek``, ``poster()`` at the end of the rise so previews never
show its invisible first frame). ``overlay_theme`` on every request is any
installed Look name (``overlay_theme.ThemeName`` is ``str``), validated
against ``looks.look_names()`` by ``exports_api.installed_look`` with the
installed set in the 422; ``ExportPresetBody.look`` and its three
``*_variant`` fields are validated by shape only, so a preset loads on a
machine without that Look and the page falls back to the default. The
catalog is ``looks.look_catalog()`` (``GET /api/looks`` in
``ui/looks_api.py``: every installed Look with every slot's variants and
the preview each resolves to) and ``GET /api/looks/{name}/preview/{file}``
serves a bare ``<slot>-<variant>.png|webp`` from the Look's ``preview/``
directory, the shipped default's standing in for one a Look lacks
(``looks.preview_file``); the shipped previews are rendered by
``scripts/render_look_thumbnails.py --look-previews``.

Authoring a Look (epic #1267, spec 2026-10-07): ``splitsmith looks list |
new | check | preview`` (``looks_cli`` over ``look_tools``) and the guide
``docs/looks/authoring.md`` with four starters in ``data/looks/_starters/``.
``looks check`` loads every template a Look owns through
``ChromiumRasterizer.probe_template`` against three sample cards (one
shooter with a logo, two without, a 52-character stage name) and words what
it saw: script errors, an animation without ``seek``, ``poster()`` outside
``duration()``, fonts other than the two bundled faces, and text cut off by
the canvas or a clipping ancestor (an ellipsis is fine); it reads a broken
user Look strictly (``looks.read_look``), where ``load_look`` would fall back
to the shipped one. ``_shared/fit.js`` fits width as well as height (#1268): a line whose
text runs past the cell's real edge is shrunk on its own (to no less than 60%
of its size), then capped with an ellipsis, a margin in from each edge it
crossed; text inside the cell is never touched, so every frame that fit before
is pixel-identical (checked against main with both frame scripts). Measure
text with a Range, not an element's box: a row's elements are as wide as the
row a long sibling stretches. ``tests/test_look_template.py`` forbids the
digits of the legibility floor anywhere in that file, issue numbers included.

A Look chooses its faces (#1272) from the bundled catalog ``splitsmith.fonts``
(open-licensed files in ``data/fonts/`` beside their licences): one per role,
``display`` and ``mono``. Templates never name a file; they draw ``"Splitsmith
Display"`` / ``"Splitsmith Mono"``, and ``overlay_html._style_rules`` points
those family names at the theme's ``display_font`` / ``mono_font`` ids
(``theme_for`` resolves ``look.json``'s ``fonts`` by id or family label, an
unknown one falling back to the role's default, so the shipped manifest's
``"Antonio"`` and ``"sans": "Geist"`` still load). The ffmpeg clock reads the
mono id too, both renderers (``theme_font_face``, ``overlay_render``), so the
counter and the clock never disagree. The defaults are today's files and
weights byte for byte, which is what keeps every default frame identical. A
stored Look's ``fonts`` is validated strictly (``fonts.check``) and copies
normalize to ids (``body_from_manifest``); ``theme_tokens`` skips the font ids,
which are not colours. ``GET /api/looks/fonts/{id}`` serves a face by catalog
id for the editor's samples (``FontPicker``). A desktop Look may also name its
own file (step 2): ``own:font-<12hex>.ttf|otf`` in the Look's ``fonts/``,
written by ``own_fonts.save_font`` (2 MB, bytes sniffed, WOFF and collections
refused, opened with Pillow's FreeType, which is what ``drawtext`` reads with),
uploaded through ``/api/looks/{name}/fonts`` (local only, in
``LOCAL_ONLY_ROUTES``; ``db.looks._check_hosted`` refuses an ``own:`` value).
``fonts.resolve(root=)`` turns it into the file's absolute path, so a theme's
face is a catalog id or a path and every consumer takes both
(``overlay_html._face_source``, ``overlay_text.resolve_overlay_face``). The
content name is what keeps the caches honest: the card PNG and the
``@font-face`` URL inside ``template_digest`` both move with the bytes. A copy
of a Look (``looks.look_files``: ``looks new --from``, the editor's
``draft_look``) carries every file but its manifest and previews, except a
copy of a *shipped* Look, which names no template (``slots`` empty, ``base``
set) so it draws the current shipped ones; ``--templates`` copies them. A
copied ``card.html`` froze the cards before the brand, the event logo and
the credit. ``data/looks/_history.json`` (every version of every shipped
template by sha256, from git: ``scripts/record_template_history.py``, and
``tests/test_look_tools.py`` fails until a changed template is recorded)
lets ``look_tools.outdated_copies`` recognise an unedited copy of an older
version; ``looks check`` warns, the Export rail's ``LookHealth`` offers "Use
the current cards", and ``looks refresh`` / ``POST /api/looks/{name}/refresh``
(local only) drop every unedited copy and its slot entries. On hosted, Looks carry colours,
fonts from the catalog and card styles, never a template: **custom templates
are desktop only, by decision** (2026-10-07). A template is code; hosted would
run it beside the database and R2 credentials, and Chromium's own OS sandbox
cannot start on Railway (probed on staging: the platform's seccomp filter
refuses user namespaces, ``unshare -Ur`` is denied and ``chromium_sandbox=True``
fails to launch). Opening them on hosted would need a separate render service
holding no secrets; do not build it, or relax any of the local-only guards
below, without the user asking.

Every template page loads in **the sandbox** (``look_sandbox``, #1266), local
and hosted alike: navigated from ``https://look.invalid/look/<file>``, never
``file://``, with every request answered by ``Sandbox.handle`` through
``context.route``: ``/look/`` (the template's own folder, symlinks out
refused), ``/shared/``, ``/fonts/``, and ``/file/<digest>/<name>`` for each
``logo`` value naming a real PNG, JPEG or WebP that is not a symlink; the
stylesheet and ``assets`` may name files inside the mounted folders only. Never
widen that: ``data`` carries user text (a stage named
``file:///proc/self/environ`` would be mounted, caught by
``test_user_text_naming_a_file_is_never_mounted``), and an own font that is a
symlink was a way to read any file (the security review's C1; ``fonts.resolve``
and ``own_fonts`` refuse a symlinked font). Everything else aborts and
lands in ``TemplateProbe.blocked``, which ``looks check`` words; websockets
are routed to nothing, service workers blocked, files capped at
``MAX_ASSET_BYTES``, and the browser launches with ``_SANDBOX_SWITCHES``
(WebRTC's UDP and DNS prefetch off, which the route cannot see: a local STUN
listener received packets before; a V8 heap cap). A crashed renderer is a
``TemplateScriptError``, a skipped card. Answers come back through the binding
by a random call id, only for a call in flight, so a template cannot answer
for the probe. Calls into template code go through ``_TemplatePage.call``
(``wait_for_function`` over ``_GUARD_JS``, answers back through the
``__splitsmithDeliver`` binding), never a bare ``page.evaluate``, which waits
forever on a stuck page. Playwright's own timeouts do not hold once a page
sticks mid-call either (measured), so ``_TemplatePage.watched`` arms a
watchdog that SIGKILLs the rasterizer's browser, found by the
``--splitsmith-rasterizer=<uuid>`` switch it launched with (``_pids_with``:
``/proc``, else ``ps``), and ``_live_browser`` relaunches it; the caller gets
``TemplateTimeoutError``. Budgets live in ``look_sandbox``. The 27 shipped
template renders were pixel-identical to the old ``file://`` path at the
switch; ``overlay_raster.png`` (our own overlay HTML) still navigates by
``file://`` and is not sandboxed.

A Look carries **your brand** (the branding work): ``look.json``'s ``brand``
(``looks.LookBrand``: a content-named ``brand-<12hex>.<ext>`` in the Look's
``brand/`` folder, written by ``look_brand.save_brand_logo`` with the shooter
logo's checks, and a line). ``look_brand.brand_json`` hands it to the title
page and the closing card only, as ``data.brand``; a Look without one sends no
key, so its contexts, digests and pixels are what they were (checked against
main for every shipped card). ``_shared/brand.js`` draws it as a top-left corner
mark, the line beside the logo, and never moves the card's text. The upload and serve routes are local only, and
``db.looks._check_hosted`` refuses a brand *logo* (the line is fine) until Looks
have a file store. It is the video maker's brand, never a shooter's: a shooter's
logo stays theirs and nothing falls back between the two.

The single-shooter MP4 can close with a **match summary** (spec
``2026-10-07-match-summary-design``): ``match_summary.build_match_summary``
reads the same ``TileStageData`` the stage summary hold does and the same
rules (``statistic_splits``, the draw is the first split), never sums stage
times and never shows a match %. It is ``Composition.match_summary``, a
``_MatchSummaryItem`` on the spine after the last stage's summary and before
the closing card, drawn by ``build_match_summary_still`` (engine HTML over
the last stage's blurred tail frame, like the stage summary; not a Look
template). It adds no chapter: YouTube drops every chapter when one is under
ten seconds. The preview's ``match_summary`` card is built from every
stage's audit by ``export_preview.match_summary_for`` and keyed by
``summary_digest``, since the preview's own key reads one stage's audit.
The grid's (spec ``2026-10-08-grid-match-summary-design``) is a
``GridCardItem`` of kind ``match_summary`` (``render_grid_mp4(
match_summary_seconds=)``, 0 is off): a title strip over the grid, each
shooter's tile declared by ``match_summary.match_summary_groups`` in the
stage hold's bands and drawn by ``grid_html``, over their own tail frame on
the last stage that has footage of them
(``overlay_summary.extract_match_summary_freezes``). It reads
``load_overlay_data`` itself, so it never needs the overlay; there is no
ranking between shooters, as on the stage hold. The rail preview cannot draw
it (the route previews one shooter), so the pane shows its gallery thumbnail.

**Logo spots** (``logo_spots``, spec 2026-10-09): beyond the cards, an export
may put a logo in more places, chosen per export by the Export page's Logos row
(``lib/logoPlan``: Cards only, Polished, Choose), ``--logos`` on both CLIs and
``logo_spots`` on every request body, the preset and the preview request.
``polished`` (``wipe`` + ``summaries``) is the default of all of those; the
renderers' own default (``Composition.logo_spots``, ``render_grid_mp4(logo_spots=)``)
is empty, so a caller that says nothing draws what it always drew. ``wipe``
hands the sting ``data.brand`` (``look_brand.brand_mark_json``: the Look's
brand, else ``Composition.brand`` / the grid's ``brand``, the account's when
``account_brand`` is on) and the shipped wipe carries it over the shooter's
logo; ``summaries`` pastes the shooter's logo top right on the stage summary
and the match summary, and on the grid in each shooter's own tile, with Pillow
(``logo_spots.paste_logo``), so it needs no browser and reaches the segment
cache through the still's bytes. Each logo keeps one corner everywhere (brand
top left, shooter top right, event centre). A stored preset drops a spot it
does not know; the request bodies refuse one. ``thumbnail`` makes the YouTube
thumbnail a card (``thumbnail_card``): the Look's ``thumbnail`` slot
(``looks.thumbnail_template_for``, the shipped ``thumbnail.html``) drawn over a
sharp action frame, the first stage's first shot taken from the stage's own
clip (``youtube_sidecar.action_frame_source``, so no HUD is burnt in; the grid
takes its render's frame), with ``data.thumbnail`` (title, lines),
``data.shooters``, ``data.brand`` and ``data.event``. No browser or a failed
template is the plain frame it always was plus an anomaly; a thumbnail never
fails an export.

The closing card ends with **"Made with splitsmith"** unless turned off
(``MatchTitle.credit``, drawn by ``_shared/credit.js`` from ``data.credit``,
which ``card_context`` sets on the ``closing`` slot only). The switch is
``made_with``, on by default on every request body, ``ExportPresetBody``,
``CardOptions``, ``MatchExportRequestData``, the preview request and both
CLIs (``--no-made-with``); it joins the preview cache key only on a closing
card that draws it. A closing card with it off is the card it always was.

An account's Looks (#1263) go through ``look_store.LookStore`` (``state.looks``;
``GET / PUT / DELETE /api/looks/{name}``): ``FolderLookStore`` over the Looks
folder locally (``put`` on a hand-made Look rewrites only the stored fields of
its ``look.json`` and keeps its templates; a local name may shadow a shipped
one), ``db.looks.PostgresLookStore`` over the ``user_looks`` table hosted (a
``StoredLookBody``: label, ``base``, colours, accent series and ``styles``,
never a template; a shipped name or a non-shipped base is refused). ``styles``
on a manifest picks the variant a slot's ``default`` draws, which is how a
template-less Look has card styles. Renderers never see a store: they resolve
Looks by name through ``looks.user_looks_dir()``, and hosted sets
``looks.set_user_looks_provider(tenant_looks_provider(tenant))`` everywhere it
pins ``current_tenant`` (the auth gate, the share alias, the queue task), which
materializes the account's rows as manifest-only folders under
``user_looks_cache_root()/<user_id>/<content hash>/``. A new place that pins a
tenant must set the provider too, or a render there sees no user Looks.

The Look editor (#1264) is the Export page Look group's Advanced row
(``components/export/LookAdvanced`` -> ``LookEditor``, rules in
``lib/lookEditor``): Duplicate copies server-side first
(``POST /api/looks/{name}/duplicate``: ``looks new --from`` locally, so a
hand-made Look keeps its templates; a manifest on the source's shipped base
hosted), then the sheet edits the copy. Its draft previews through the
export-preview route's ``draft`` (``look_store.draft_look``: the saved
Look's manifest with the draft's fields, beside copies of its own
templates) and ``at`` (``render_template(at=)``; the preview's
``_AtTime`` wrapper), plus a ``sting`` card; both fields join the cache key
only when set. The strip renders one card at a time
(``lookEditor.serialQueue``): the server's render bound answers 429 to a
second preview in flight. Contrast is a warning; only invalid fields block
Save. ``refreshLooks()`` re-fetches the catalog for every mounted surface
after a write.

The template editor (#1265) is the editor's Templates tab on the desktop
(``components/export/TemplateEditor``, CodeMirror 6 lazy-loaded through
``CodeEditor``, rules in ``lib/templateEditor``). Its routes
(``/api/looks/{name}/templates`` GET/PUT, ``/samples``, ``/check``,
``/reveal``) are ``LOCAL_ONLY_ROUTES``: a template is code, hosted runs none
of an account's (desktop only, see above). Unsaved text rides the preview and the check as
``templates`` (``look_store.TemplateEdit``, applied by ``draft_look`` /
``apply_template_edits``; the preview answers 403 hosted when it is set) and
is keyed per own *file* (``editKey``): the shipped ``card.html`` draws four
cards, and the tab says so (``sharedWith``). A borrowed slot is written to
``<slot>-<variant>.html`` and named in ``look.json`` on first save. Page
errors carry the template's line (``describe_page_error``).
The Export rail's ``LookHealth`` (#1276) checks the chosen Look when it is
your own and the app is local, through the same check route without a draft;
that case is cached under ``cache_dir/look-check`` by every file of the Look's
folder (``_folder_digest``), so choosing a Look again launches no browser and
any edit checks again. A draft or template text is never cached. Failures are
grouped by message (``lib/lookHealth``): one broken ``card.html`` is one line
naming the four cards it draws. It never blocks Export.

Palette suggestions (#1273) sit at the top of the Palette tab
(``components/export/PaletteSuggestions``); every rule is in the pure
``lib/palette`` (schemes from one colour, the footage ranking, the weak-accent
warning, the grid accent series, the ready-made set). The server only
measures: ``palette_sources`` (a deterministic k-means over two frames per
stage from the trim on this disk, merging near-identical clusters, plus the
logo's colours) behind ``POST /api/shooters/{slug}/palette-sources``; a
hosted container has no trims, so its footage is empty and the source is
disabled. "Stands out" is OKLCh hue separation from the footage's tinted
swatches, not raw colour distance: a saturated green is far from dull grass
in OKLab and still reads as the grass. Choosing a suggestion replaces the
draft's colours and accent series; the neutrals stay.

A shooter has an **identity** (``splitsmith.identity``, spec section 2,
#1243): ``MatchProject.identity`` holds an optional ``#rrggbb`` accent, a
club line and the name of a logo under ``<shooter>/identity/``
(content-named ``logo-<12hex>.<ext>``, PNG / JPEG / WEBP, 2 MB). The
renderers never read it raw: the request layer (the export jobs in
``server.py``, ``match_cli``, ``compare/cli``, the preview API) resolves
it through ``ui/identity_media.resolved_identity_for`` /
``grid_identities`` into a ``ResolvedIdentity`` whose accent is the
shooter's own or ``None`` and whose ``logo_path`` is a file on this disk
or ``None`` (hosted mirrors the logo down like a trim; a missing file is
a card without a logo, never a failed render). A shooter who set nothing
renders exactly as before identities existed: the spec's slot default
(the Look's ``accent_series`` by slot, alphabetical by label, filler
tiles keep their slot) is opt-in through ``series_default`` and only the
frame scripts' ``--identity-demo`` asks for it (a ruling from the slice
3 review; the series is otherwise the sheet's swatches). The pixel gate
against main runs the frame scripts' default path, which goes through
the same resolver an export uses. ``Composition.shooters`` and
``render_grid_mp4(identities=)`` carry it in; templates read
``data.shooters`` and ``_shared/identity.js`` draws the logos top-right
(a lower third only when exactly one shooter has one); the summary
tile's accent bar and the name's colour are ``--accent`` on the cell
wrapper, unset today's pixels; the club line prints under the shooter's
name on the title page (``title_info_lines``). The upload sniffs the
bytes (PNG / JPEG incl. MPO / WEBP), caps the side at ``LOGO_MAX_SIDE``
and never reads the client's filename. The logo syncs over the media
channel (``identity/`` in the push plan, the hosted key rule and the
delete route). The roster shows it too (#1249): ``GET
/api/shooters/{slug}/identity/logo`` serves the logo (``ensure_local_logo``,
``nosniff``) and is on the share GET allowlist (the logo is in every video a
share shows; the alias binds it to that match's shooters), the compare payload
carries ``identity``, and ``Avatar`` takes ``accent`` (a ring) and ``logo`` (in
place of the initials) through ``lib/identityMark``, whose URL carries the
content-named file so a new logo is a new URL; with neither it renders as before.

Three logos, and none stands in for another: the shooter's (top-right,
above), **your brand** (``LookBrand`` on a Look, the top-left mark on the
title page and the closing card) and the **event logo**, the match's own
(``Match.branding.event_logo``, ``event-<12hex>.<ext>`` in
``<match>/identity/``, routes ``/api/match/branding/event-logo``; the
Export page's Branding row under Details). The event logo is the
centrepiece of those two cards only, above the match name, which moves below it (``MatchTitle.logo`` ->
``data.event`` -> ``_shared/event.js``); the old match-logo fallback for a
shooter without one is gone. It syncs at match level
(``matches/{id}/identity/event-*``: the push plan, the gc's
``_EVENT_KEY_LOCAL_RE`` and the hosted key rule move together), and every
renderer resolves it through ``identity_media.ensure_local_event_logo``,
which mirrors it down on hosted and answers ``None`` when it is missing.

**Shooters and the account menu** (spec
``2026-10-09-shooters-page-and-account-menu-design``): ``/shooters`` lists
everyone you have filmed (``GET /api/me/shooters``, ``ui/shooter_roster``: one
row per SSI id over the recently opened matches locally and the account's
matches hosted, the book's look else the newest match's, you first; listing
writes nothing), and ``components/shooters/ShooterSheet`` edits the book only.
The same sheet opens from a shooter chip's menu in a match
(``ShooterChipStrip`` ``onEditLook``) and from Footage's "Edit look". The
account pill (``AccountMenu``, top right, both modes) holds You, Shooters,
Branding (``/you#brand``) and, hosted, Account; the splitsmith.app chips stay
beside it. The SPA no longer sends ``scope="match"``; the server still takes it.

**You, your brand and the shooter book** (spec
``2026-10-08-account-identity-and-shooter-book-design``). "You" is the existing
``ScoreboardIdentity.shooter_id``. The **shooter book** (``shooter_book``) keeps
a shooter's look per account keyed by SSI shooter id, never by name:
``identity_media.identity_source`` takes the book's entry for
``selected_shooter_id`` when it sets anything, else the match's own record (as
a whole), else nothing (**the book wins**, spec
``2026-10-09-shooters-page-and-account-menu-design``: no per-match overrides,
and an old match record must not draw over the Shooters page), so an empty book
renders exactly as before. Renderers never read a
store: the request layer loads ``load_snapshot(state.shooter_book)`` once per
export and passes ``book=`` to ``resolved_identity_for`` / ``grid_identities``
(every export job, the preview, the palette route, both CLIs). Identity edits
write the book (``scope="book"``, the default; ``"match"`` writes only this
match's record, which the book now draws over; an empty look removes the
entry); an edit starts from the look the videos draw (the book's, logo copied
in, else the match's own); ``use-book`` is refused when the
book has nothing to fall back to. The account's **brand** (``AccountProfile``,
the shape of ``LookBrand``) is ``MatchTitle.brand``, resolved by the request
layer like the event logo; ``look_brand.brand_json`` draws the Look's brand when
it has one, else the account's, as a whole. ``account_brand`` (default on) on
every request body, the preset and both CLIs turns it off. The preview key gains
``book_identity`` / ``account_brand`` only when the card draws them. Stores:
``JsonShooterBookStore`` / ``JsonAccountProfileStore`` under ``<user
config>/account/`` locally; hosted ``db.account_identity`` (tables
``shooter_book`` and ``account_profiles`` under RLS, files in the tenant's own
storage prefix at ``account/files/`` and ``account/brand/``, mirrored into the
cache by content name). ``AppState.shooter_book`` / ``account_profile`` never
fall back to the local files on a hosted server. The book fills once from the
account's existing matches (``account_backfill``; most recent wins, entries the
book already holds are kept; ``account_profiles.backfilled_at`` hosted, a
``.backfilled`` marker locally). Never a ``state_docs`` kind, not synced
between desktop and hosted, and no share route reads either: the roster (the
shooters list, Compare's payload, the logo route) and the title page's club line
show the look the video draws (``identity_media.effective_identity``) through
``_roster_book``, which is the empty book on a share request.

The single-shooter MP4 draws **transitions** (#1244, spec section 3) on a
boundary segment. ``plan_timeline`` turns the stage-indexed
``Composition.transitions`` into ``TimelinePlan.boundaries`` between
consecutive spine items (the last item of a stage's run, its summary
when it has one, and the first of the next, its slate when it has one);
a transition of d seconds is a crossfade of length d centred on the cut,
so the timeline, the chapters and the duration estimate keep their
length. Each neighbour gives up d/2 (``head_cut_seconds`` /
``tail_cut_seconds`` on the item; ``_narrow_plan`` shifts a stage's plan
and recomputes its cams) and the boundary is ``xfade`` + ``acrossfade``
over two *edge* renders made with the item's own builder
(``_edge_plan``: the last d/2 of effective footage plus whatever handle
the trim holds past the tail pad, up to d/2, and the mirror at the head;
the boundary holds the edge's last or first frame for the rest
(``tail_pad_seconds`` / ``head_pad_seconds`` on the boundary command), so
the default 5 s pads over 5 s trim buffers, which leave no handle at all,
still get a transition; a card's handle is its own frame, an animated
card's head edge delays its clip and its tail edge offsets it, cloning
the last frame before the skip so an offset past the animation still
shows the card). The fit check reports and never clamps: d/2 must fit
the pad (the beep and the last shot stay out of the fade), and a card
must be at least d long; a miss or a failed edge is a cut with a
degradation. A lower third the head edge showed in full is dropped from
the trimmed stage (``_trimmed_lower_third``; a looped PNG with ``-t 0``
runs forever). The driver prepares each item once, decides a boundary
(edges, then the xfade) *before* encoding the item that opens it, and
keys the boundary by its edges' cache keys, not their files (the
cache's LRU touch re-dates them). ``KEY_VERSION`` is 3. Kinds are
the curated ``xfade`` names plus the two FCP effects, which the MP4 maps
through ``xfade_name`` and the FCPXML path substitutes with ``zoom`` and
an anomaly. ``composition.XFADE_FAMILIES`` (#1259) is the one list: a
family is a gallery tile with one or more directions (Wind: ``hlwind``,
``hrwind``, ``vuwind``, ``vdwind``), ``XFADE_KINDS``, the request
validation and both CLIs' help derive from it, ``GET /api/looks`` serves
it as ``transitions`` with a looping preview per family
(``data/looks/_transitions/preview/<family>.webp``, the ``_transitions``
owner of the preview route), and ``tests/test_xfade_kinds_integration.py``
pins every kind against the ffmpeg on PATH (CI's FFmpeg 6.1 is the
oldest the project meets). A new transition is one family entry plus
``render_look_thumbnails.py --look-previews``; the SPA has no list of its
own. The grid draws them too (``compare/mp4_grid``):
``plan_grid_spine`` places the same boundaries between its items (a
stage's segment is action plus hold; a slate when present),
``narrow_grid_plan`` rebuilds every tile's seek and lead pad from the cut
head pad (the beep stays on it; the overlay plan is built from the
narrowed plan so clocks and sprites move with it) and takes a tail cut
from the hold first, ``grid_edge_plan`` reads the handle every real
tile's trim holds (a tile with no footage in the window becomes filler,
a tail edge inside the hold is a still of it), and
``build_boundary_segment_command`` crossfades the video and each of the
N+1 tracks by stream index. Edges and boundaries go through
``boundary_runner``, never ``runner``, so the CLIs' "stage N of M" stays
honest. ``scripts/render_grid_frames.py --transition fade`` shows it.
``scripts/render_match_frames.py --transition fade --transition-seconds 1``
shows the boundary (``boundary-1-in`` / ``-mid`` / ``-out``); a lower
third on the stage after a boundary starts in the head edge
(``lower_third_filters(delay_seconds=)``) and continues in the trimmed
stage (``skip_seconds=``), never restarting.

A **sting** (#1245) is a transition kind ``sting:<name>`` where ``<name>``
is a variant of the Look's ``transition`` slot (``look.json``:
``"transition": {"wipe": "sting-wipe.html"}``; ``looks.sting_template_for``
resolves it against the Look and the shipped default, with no fallback to
another variant). ``TransitionKind`` is therefore an open ``str``;
``composition.validate_transition_kind`` is the one grammar check and the
request bodies, ``ExportPresetBody`` and both CLIs run it, so ``sting:``
alone or an unknown closed kind is still a 422 / usage error, and
``Transition.__post_init__`` runs it again so a caller that skipped them
(the MCP tool annotates the open string) never puts text into
``xfade=transition=``. Both
renderers decide a sting while deciding the boundary (``sting_for_boundary``
in each driver): ``look_sting.sting_motion`` loads the template with
``data.transition`` (kind, name, duration, the labels either side of the
cut) and ``data.shooters`` (the identities the cards see), the clip is written with ``write_motion_clip`` and
laid over the boundary's ``fade`` (``xfade_name`` of a sting) through
``sting_overlay_filters`` for the whole segment from its first frame,
whatever handle the edges had; the single-shooter cache keys it by
``template_digest`` as a virtual input, so a cached boundary renders no
frame. A sting the Look lacks, or one with no browser, is a fade plus a
degradation naming it; frames that fail are a cut like any failed
boundary. Without a sting every argv is unchanged. The shipped
``sting-wipe.html`` sweeps an accent band across the seam carrying a logo
when the shooters have exactly one distinct logo between them (each
shooter's own; no other logo stands in for one) or the next item's
name, clipped to the band in the bundled display face;
``window.duration()`` returns the transition's length, so the renderer
samples exactly the boundary. The FCPXML path substitutes zoom with an
anomaly; the gallery tile is ``sting:wipe`` (MP4, both modes), its
thumbnail the template at its poster over the mid-fade
(``scripts/render_look_thumbnails.py``, Chromium at authoring time).
``scripts/render_match_frames.py --transition sting:wipe --identity-demo``
and the grid script show it.

## What's new (in-app release notes)

Every user-facing change adds its entry to ``src/splitsmith/data/whats_new.json``
**in the same PR**: the app's What's new sheet is the only place most users
learn a feature exists, and release-please's changelog is commit subjects,
not user copy. Use the ``whats-new`` skill (``.claude/skills/whats-new``):
it says when an entry is needed and the house style; ``tests/test_whats_new.py``
fails an entry that breaks the mechanical rules (length, ASCII, no dash
punctuation, no issue numbers, no hype). Seen-ness is a set of entry ids
(``GlobalPrefs.whats_new_seen`` locally, ``users.whats_new_seen`` hosted), so
an id is never renamed; a user with no matches starts with everything seen
(``whats_new.first_seen``). The sheet (``components/whatsNew``) opens itself
once per session when something is unseen and lives in ``RootLayout``, so
share pages never show it; a feature's ``<NewChip feature=...>`` shows for 60
days or until ``dismissNewChip`` is called where the feature is used.

## Hosted playback streams the web rendition (#1031)

The audit trim (``trimmed/stage<N>_cam_<id>_trimmed.mp4``) is a
full-resolution scrub cache: tens of Mbit/s, ``moov`` at the tail. It is
what the audit screen wants and the wrong thing to stream from R2, which
is why every trim now has a ``_web.mp4`` beside it (720p, faststart,
``WebTrimConfig`` in ``config.py``, cut *from the trim* by
``trim.transcode_web_trim`` so the two share a window and a beep anchor).
``ui/audio._ensure_web_trim`` cuts it whenever the trim is cut or found
cached, best-effort: a failed transcode never fails the trim job.
``sync.run.backfill_web_trims`` cuts missing ones before every push, so a
match trimmed before this existed is fixed by its next sync, not a
re-trim. ``_video_clip_anchor`` reports ``kind: "web"`` only when the
byte path is a presigned redirect and the object exists; the clip anchor
(Results, Coach) stays ``trim`` locally, pinned by
``test_get_coach_entry_kind_stays_trim_locally_with_web_file``. On
``stream_video`` the kinds mean one thing in both modes: ``web`` is the
streaming rendition, else the trim, else the source, never a 404 (what
Results, Coach and Compare rely on); ``trim`` never substitutes the
rendition; ``scrub`` is the Audit players' pin (#1209): the fresh
rendition, else the trim, else 404, never the source -- a re-cut deletes
both while it encodes and a pinned player must error and remount, not
play the source under trim offsets. Fresh is one rule,
``audio.fresh_rendition`` (non-empty, not older than the trim, equal
timestamps fresh; on a mirror the rendition alone), fed by local files,
``storage.stat`` in the route, and the request's ``StoragePresence``
listing (one ``trimmed/`` listing per request, which now keeps each
object's size and ``last_modified``) for the payload's ``scrub_version``.
``lib/useScrubSource`` asks for ``scrub`` when the video dict carries
``scrub_version``, unless ``GlobalPrefs.full_res_scrub`` is on (local
only; hosted has no switch) or that rendition (path + version) already
errored on the page (#1192: a 4K trim at ~150 Mbit/s stalls software
decode and ends after ~2 s in Chromium's low-end mode, #1191). The GOP stays 30: GOP 15
measured +35-47 % bytes for ~10 ms of seek. Hosted Compare prefers ``trimmed/<...>_web.mp4``
over the lossless export.

Web-only mirrors (spec 2026-09-27 v1.1): a desktop mirror has no
``_trimmed.mp4`` on R2 by default, only ``_web.mp4`` and ``.params.json``.
On a mirror only (``_is_mirror()``, origin ``desktop``), ``kind=trim`` and
``kind=auto`` redirect to the rendition when the full trim is absent, the
clip anchor comes from the pushed params (``try_pull_web_trim`` +
``trim_pre_buffer_seconds_for``), and the audit WAV is extracted from the
rendition (``ensure_audit_audio(web_fallback=True)``). A hosted-native
match keeps "``kind=trim`` never substitutes", pinned by
``test_hosted_native_match_without_a_trim_still_404s_kind_trim``.

## Export presets (spec 2026-09-15)

``export_presets.ExportPresetBody`` is the one shape the API, the local
``export_presets.json`` and the SPA's ``localStorage`` last-used entry
share. Every field defaults and ``extra="ignore"`` is set, so a body
saved before a new option shipped loads with that option at its default:
adding an option means adding a defaulted field here, a column in
``lib/exportPresets.settingsToBody`` / ``applyBody``, and nothing else.
The export wrappers in ``lib/api.ts`` (``exportMatch``, ``exportStage``,
``exportCompareGrid``) spread their payload and never list fields: a
hand-written list silently dropped the title page, closing card and
summary hold from every UI export in 0.42.0 while the page tests, which
mock the wrappers, stayed green. ``api.exportBodies.test.ts`` types each
payload ``Required<...>`` so a new request field fails the typecheck
until it is shown to reach the wire.
Presets are per user (``export_presets`` table hosted, the JSON file
locally) and never a ``state_docs`` kind: a per-match kind would enter
the sync manifest. Match-specific fields (stage selection, the title
line, the description lead, the bundle name, the reference shooter, a
publish date) are never stored; ``isDirty`` ignores them by
construction because ``settingsToBody`` does not emit them. The page's
recurring state is one ``ExportSettings`` object; a new recurring
field goes on it, not on a fresh ``useState``. The Export page's tests
open the folded groups through their ``openGroups`` helper before
reaching a control; a new group needs adding there.

The Look group is a gallery (spec s2): ``lib/lookGallery.ts`` is the one
registry of slots, variants, thumbnails, parameters and which mode and
format can draw each; ``components/export/LookGallery.tsx`` renders it
and owns nothing. A new effect is one registry entry plus one thumbnail
from ``scripts/render_look_thumbnails.py`` (Chromium once, at authoring
time; the gallery never rasterizes; the cut and the FCP effects are
bundled stills, the xfade families come from the server with looping
WebP clips of the real transition through the project ffmpeg).
``slotsForLook(looks, settings, transitions)`` turns the families into
the transition tiles: a stored kind reads as its family, picking a family
keeps the direction already chosen or takes its first, and a family with
more than one direction shows a Direction ``Segmented`` under it. The
kind filter (``visibleTransitionKind``) admits on MP4 only what
``requestLook`` lists: the server's kinds and the chosen Look's stings,
or the stored kind itself while the catalog is not there. The rail and
the group summary name a kind with ``transitionLabel`` ("Wind up", a
sting as "Wipe sting"). ``lookGallery.test.ts`` pins the per-format
visibility table and that every committed thumbnail is referenced;
``renderOptions.test.ts`` pins that the mappers never send a field the
registry hides. The installed Looks reach it through ``lib/looks.ts``
(the pure reader of ``GET /api/looks``: ``visibleLook``,
``visibleVariant``, ``stingsFor``, ``resolveLookChoice``) and
``useLooks`` (fetched once; ``BUILTIN_LOOKS`` until it answers):
``slotsForLook(looks, settings)`` folds the catalog into the static
table, the Look tiles first when more than one Look is installed, a
Style (``Segmented``, ``VARIANT_FIELD``) under a card that is on when the
chosen Look has more than one template variant, and the Look's stings
among the transitions with their catalog previews. A Look field rides a
request only when it is not the default (``nonDefault``), so an
untouched form sends the body it always sent; a stored Look no longer
installed, or a variant the Look lacks, is resolved before any request.
Transitions live in Look (FCPXML only, sent as ``none`` elsewhere) and
the title line in Details. A slot whose seconds field is being edited
reads NaN and must still count as on, or the input vanishes under the
cursor (``summaryHold.read``).

Both previews (the rail and the Look editor) can draw **logo placeholders**: ``logo_placeholders`` on the request fills every logo spot no logo fills (the shooter's corner, your brand, the event's centre; title, slate, lower third, closing) with a labelled dashed square from ``logo_placeholder`` (a content-named PNG under ``cache_dir/logo-placeholders``), keyed apart by ``PLACEHOLDER_REVISION``. The switch is the viewer's (``lib/logoSpots``, on by default); no export path ever asks for it.

The rail's preview (spec s3) is ``POST /api/shooters/{slug}/export-preview``
-> PNG, engine ``export_preview.render_preview``: it declares the card
exactly as ``ui/match_exports.py`` does and composes it through the
renderers' own builders over a head or tail frame from the trim on this
container's disk (the surface when there is none, which is every hosted
container by design; a seek past the clip's end takes its last frame).
Cached under ``cache_dir/export-preview`` by a content key that includes
the project's ``updated_at`` and the audit version. 503 is no browser,
409 is the overlay without shots; the SPA maps each to one muted line in
``PreviewPane`` and never blocks the Export button. A new Look variant
needs a ``previewCardFor`` case or it previews as the frame. The request carries ``look`` and ``variant``
(#1246, the focused slot's, only when not the defaults); both are in the
cache key, and an animated template previews at its ``poster()`` through
``render_template``, or, with ``motion`` (#1249, what the rail and the
editor's big preview ask for), as a looping animated WebP of its own frames at
``MOTION_FPS`` over the backdrop its still uses, the last frame held; a still
template answers the PNG it always did, so ``motion`` is safe to ask for on any
card a template draws. The response's type follows the bytes (``_image``), and
the cache keeps ``<key>.png`` or ``<key>.webp`` accordingly.

## YouTube upload (#1000)

``splitsmith.youtube`` uploads a rendered MP4 with its ``-youtube.json``
sidecar's metadata; the result is written back into the sidecar as
``upload`` and that is the only record (a re-render rewrites the sidecar,
which is when a new upload is allowed). The OAuth client is baked into
the wheel at publish (``scripts/bake_youtube_client.py`` in
``publish-pypi.yml``, from repo secrets; the constants in
``youtube/oauth.py`` are empty in git and a checkout uses the env
overrides); a user-supplied client would not have escaped YouTube's
private-only lock on unaudited projects, so there is none. One scope,
``youtube.force-ssl``. Google's OAuth verification and the YouTube API
audit both passed on 2026-09-17: the consent screen has no "unverified
app" step, refresh tokens no longer expire after 7 days, and an upload
requested ``unlisted`` or ``public`` stays so (checked with a test
upload on 2026-09-18, read back through ``videos.list``, deleted). The
spec's two corrections to the issue text are in
``docs/superpowers/specs/2026-09-14-youtube-upload-design.md``.

The local server side is ``ui/youtube_api.py`` (local mode only; the
routes 404 hosted): ``/api/settings/youtube`` plus the connect state
machine (``ConnectAttempt`` runs ``oauth.connect`` on a thread, the SPA
opens the consent URL itself and polls), the ``youtube_upload`` job and
``POST /api/shooters/{slug}/exports/youtube-upload``. ``_run_match_export``
chains that job when the request carries ``youtube_upload``; the history
route reads each run's ``youtube`` record from the sidecar per request.
In the SPA, a new upload option belongs on
``components/export/YouTubeConnect``'s options block and on
``youtube.upload.UploadOptions`` (playlist by title or, from the picker,
by ``playlist_id`` which wins; ``publish_at`` which implies private;
``notify_subscribers``); a new per-run action on
``ExportHistory`` through ``lib/youtubeRows``. One options block per page
(the form's "Upload after render"), the history rows reuse it through
``rowUploadOptions``. ``playlistItems.insert`` answers 409 for a few
seconds after a playlist is created; ``_add_to_playlist_with_retry``
backs off rather than noting a failure.

## Desktop app (``desktop/``, spec 2026-09-20)

An Electron shell, not a second frontend: ``desktop/src/main.ts`` spawns
``Contents/Resources/python/bin/python3.12 -m splitsmith.ui.embedded``,
parses the ``SPLITSMITH_READY`` banner and loads the sidecar's URL. The
runtime is python-build-standalone plus the wheel (``build-runtime.sh``),
never PyInstaller; what ships is ``uv.lock``'s default set, and
``scripts/ci/assert_slim_import_surface.py`` builds the local-mode app
on a slim install because a module-level ``splitsmith.db`` import
anywhere on the ``create_app`` path crashes exactly that install (#1057
did it through ``ui/youtube_api.py``). The pure parts (``sidecar.ts``,
``cliLink.ts``) have vitest tests; ``main.ts`` and ``menu.ts`` are wiring.
ffmpeg is our own static build (``build-ffmpeg.sh``; ``--lgpl`` is the
#986 variant, and the pipeline only ever names ``fontfile=`` so there is
no fontconfig), published as a GitHub release the app fetches by sha256.
Bumping it follows the ffmpeg change rule: the single-shooter frames
were pixel-identical to Homebrew's build, the grid's action frames
differ at edges only because two compiles of the same x264 commit make
different rate-control choices; look at the frames, not the deltas. The
bundle is sealed: ``PYTHONDONTWRITEBYTECODE``, precompiled bytecode,
``NUMBA_CACHE_DIR`` under ``~/Library/Caches``. Data stays in
``~/.splitsmith``, shared with the CLI on purpose. ``POST /api/shutdown``
ends the sidecar process (``exit_event`` in ``embedded.py``), which is
what makes Cmd-Q take half a second instead of the SIGTERM grace period.
``CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh`` is the unsigned
build; ``desktop/smoke.sh`` runs the built sidecar and a detection;
``desktop/verify-signed.sh`` is what a signed build must pass;
``desktop/release.sh [vX.Y.Z]`` is the release: it builds the tag (default
the newest release) signed in a throwaway worktree, smokes it and uploads
the DMG to the GitHub release, reading the ``APPLE_API_*`` notarization
vars from ``~/.appstoreconnect/splitsmith-desktop.env``; signing stays
local on purpose;
``desktop/scripts/cdp-shot.mjs`` screenshots the window when Electron
runs with ``--remote-debugging-port``. The one engine surface added for
it is ``ui/system_api.py`` (Chromium probe and install, local only) and
the button under ``PreviewPane``'s browser line. The update check is
``desktop/src/updateCheck.ts`` (pure, tested) plus ``updates.ts``
(fetch, dismissed file under userData, the sheet); the feed is the Pages
Function ``functions/desktop/latest.json.js`` on splitsmith.app, which
picks the newest ``v*.*.*`` release that has a ``.dmg`` attached (GitHub's
``releases/latest`` can be an ffmpeg source release, and release-please
publishes before the DMG exists). Moving the download behind a purchase
changes that function only. No auto-update.

Every shipped desktop build, DMG and Linux alike, bundles the wheel PyPI
serves, never one built from a checkout: only ``publish-pypi.yml`` bakes
the YouTube OAuth client in, and the 0.53.0 DMG shipped with empty
constants. ``release.sh`` fetches it with ``scripts/ci/fetch_pypi_wheel.py``
and passes ``build.sh --wheel``; release builds set
``SPLITSMITH_REQUIRE_YOUTUBE_CLIENT=1``, which makes ``build-runtime.sh``
run ``desktop/scripts/check_youtube_client.py`` and refuse a bundle
without the client.

Linux ships as an x86_64 AppImage and .deb, built by
``.github/workflows/desktop-linux.yml`` in ``ubuntu:22.04`` (glibc 2.35
floor) from the wheel PyPI serves (``scripts/ci/fetch_pypi_wheel.py``),
chained after ``publish-pypi`` in ``release-please.yml``; PRs build and
smoke it through ``desktop.yml`` without uploading, and a failed release
is re-run with ``workflow_dispatch`` and the tag. ``desktop/build.sh
--linux`` builds locally; ``desktop/lib/target.sh`` is the one place a
target is resolved (``host_target``); the Linux ffmpeg is our own
static build (``build-ffmpeg-linux.sh``, release
``ffmpeg-linux-x86_64-9.0.2-r1``), its tag landing in
``desktop/build/FFMPEG_RELEASE``, never in ``build/bin`` (which ships
verbatim into the bundle). The deb's ``linux/postinst.sh`` /
``postrm.sh`` are electron-builder's stock scripts plus
``linux/cli-link.sh`` (``/usr/bin/splitsmith``, a ``#!/bin/sh``
wrapper rather than a link so terminal runs get the bundled ffmpeg: only
the Electron sidecar sets ``SPLITSMITH_FFMPEG``, and the CLI looks next to
its interpreter, not in ``resources/bin``; left alone when it is not ours) and must stay that way (``scripts/check-deb.sh``
in CI); inside them ``${letters}`` is an electron-builder macro. The
AppImage refuses with a dialog where the sandbox cannot start
(``src/sandbox.ts``; ``appImage.executableArgs: []`` stops the stock
desktop entry's unconditional ``--no-sandbox``); ``StartupWMClass`` is
``splitsmith-desktop`` because the window class comes from
package.json ``name``, and adding ``productName`` would move the
macOS userData dir. The feed's ``?platform=linux`` waits for both the
AppImage and the .deb; no parameter stays the macOS answer.

## Releasing

release-please keeps a ``chore(main): release X.Y.Z`` PR open. Its CI and
desktop runs end ``action_required`` or ``failure`` with **zero jobs**
(GitHub will not run workflows the actions bot triggers); that is not a
failure. Merge it when main's CI passed on the commit it releases and the
PR touches only the manifest, ``CHANGELOG.md``, ``pyproject.toml``,
``src/splitsmith/__init__.py`` and ``uv.lock``. The ``Release`` workflow
then tags, publishes to PyPI, pushes both GHCR images, deploys
production on Railway, and builds and attaches the Linux AppImage and .deb
(``desktop-linux``, after ``publish-pypi``). The DMG is a separate local
step, ``desktop/release.sh`` (signing never leaves the Mac; notarization
can take close to an hour). The update feed announces per platform: macOS
once the DMG is attached, Linux once both the AppImage and the .deb are.

There are no required status checks on ``main``, so ``gh pr merge
--auto`` merges at once. "Merge on green" means ``gh pr checks <n>
--watch`` first, then merge.

The marketing site and the feed function deploy through
``.github/workflows/deploy-marketing.yml`` on any push touching ``site/``,
``functions/`` or ``wrangler.toml``; the feed reads GitHub releases per
request, so a release needs no site deploy. The ``CLOUDFLARE_API_TOKEN``
secret must be an **account**-scoped token with Pages Write (a zone-scoped
one cannot deploy Pages). It went invalid in August 2026 and the workflow
failed silently for six weeks: a red run of that workflow means the site
is stale.

## Multi-shooter comparison (`compare/` package)

``splitsmith compare export <manifest>`` reads N existing single-shooter
``MatchProject`` directories (all from the same match) and emits one
FCPXML where each stage is a beep-aligned grid compound clip. It does
not run detection -- it only reads finished projects' per-stage trims.

Slot order is alphabetical by manifest label and stable across stages
(missing trims become black filler, never reshuffle the grid). The
audio-source shooter from the manifest drives the sequence frame rate
and is the only unmuted tile. Per-module breakdown lives in SPEC.md
under "Module responsibilities"; the example manifest is at
``examples/compare-bromma-classifier-2026.yaml``.

## Share-link previews

A share link previews as a 1200x630 card. The match card is a roster --
deliberately no summed stage time, since IPSC ranks by hit factor and
accumulated raw time is not a figure the sport produces. The stage card
leads with draw and average non-anomaly split, the numbers splitsmith
itself computes. The split rule lives in ``coach.statistic_splits``
(main, issue #772) -- ``share_card.stage_figures`` does not own it, only
shapes its output into a card's two headline figures. An uncoached stage
falls back to the auto-classifier's own cutoff,
``coach.split_stat_split_max()`` (``CoachAutoClassifyConfig.split_max_s``,
1.0 s since 2026-09-14; 0.5 s before) -- *not* an independent constant,
which #773/#776 retired for this purpose because the figures would
otherwise jump the moment a stage got classified. (#772 also brings the
video summary and results page onto that same definition.) The
thresholds (``split_max_s`` 1.0, ``transition_max_s`` 2.0) are read
through ``coach.auto_classify_config()``, which honours a
``SPLITSMITH_CONFIG`` YAML; ``lib/splits.ts`` mirrors the split cutoff
as a literal and moves with it. ``splitsmith match reclassify`` re-runs
the auto-classifier over stored audits (manual overrides survive) after
a threshold change. ``ui/share_og.py``'s ``build_stage_card`` heals legacy
unclassified audit docs in memory and never persists -- a share-only
route must not mutate a doc an anonymous caller reached through
impersonation. The heal's guard is ``coach.heal_unclassified`` (#780),
shared with ``get_stage_coach``, the compare payload and the overlay
renderer; adding a fifth consumer of ``statistic_splits`` that reads raw
audit shots means calling it, not re-deriving the condition.

``ui/share_og.py`` serves four route families on the anonymous share
surface: the card PNGs, a JSON ``og-meta`` route, and the HTML shells
at ``/share/{token}``, ``/share/{token}/results``, and
``/share/{token}/results/{slug}/{stage}``. The shells sit outside the
middleware that pins a share's owner -- ``_share_alias`` only rewrites
``/api/...`` paths -- so a shell fetches its data through an in-process
ASGI sub-request back into the anonymous API rather than resolving the
token or impersonating the owner itself. This is deliberate and is the
single least obvious thing on this surface: token resolution and owner
impersonation must have exactly one implementation, in ``_share_alias``.
Do not "simplify" a shell handler to read the tenant directly -- that
would be a second implementation of impersonation, not a shortcut.

``og:image`` URLs are content-addressed: they carry ``?v=<card_hash>``,
and that query *is* the freshness mechanism, not a cache-buster afterthought.
A re-audit moves the figures, which moves the hash, which moves the URL,
so crawlers refetch. Without it, a re-audit writes a new cached object
that nothing's ``og:image`` tag points at, and a crawler goes on serving
a stale card behind a long-lived ``Cache-Control``. Nothing invalidates
a cache; there is nothing to invalidate. Meta tags are injected
server-side in ``ui/share_og.py`` for every client -- crawlers do not run
JavaScript, so a client-side helmet would reach none of them.

The anonymous ``/api/share/{token}/...`` surface is no longer
categorically read-only. ``_SHARE_PATH_RE`` (GET) and
``_SHARE_WRITE_ROUTES`` (POST/DELETE, method-paired) in ``ui/server.py``
are two separate allowlists on purpose, and must never merge: merging
them would make ``_SHARE_PATH_RE``'s own GET-only docstring false, and
would let a write shape be reachable through the read table's fullmatch.
``comment`` is the only write-capable scope
(``db.share_guard._WRITE_CAPABLE_SCOPES``) -- a plain ``read`` link can
list the comment thread (the thread is deliberately in both allowlists)
but can never post or delete through it; ``scope_may_write`` in
``_share_alias`` is what enforces that, and every non-admitted
(method, shape, scope) combination collapses to the same opaque 404 as
an unknown token. ``author_handle`` on a posted comment is
server-derived (``comment_identity.derive_handle``, or the signed-in
viewer's own ``display_name``) and never client-supplied -- the request
model has no field for it. That is the invariant a future contributor is
most likely to break by "simplifying" the compose box: adding a
display-name input to the POST body would let anyone sign a comment with
someone else's name.

A signed-in visitor comments under ``users.display_name``, which the
``/account`` page writes through ``PATCH /api/me`` (#867). Nothing else in
the codebase writes that column -- ``splitsmith.db.profile`` is the single
owner, which is what makes "can this branch be reached?" answerable by
grep. #866 shipped the branch with no writer and it was dead in
production for exactly that reason. An account with a blank name still
falls back to a generated handle; that invariant is pinned in
``tests/test_comments_signed_in.py`` and does not move.

## Stage events (spec 2026-10-08)

Movement, reload and activation are **regions** (``events`` on the stage
audit doc, ``config.StageEvent``, seconds from beep), independent of
shots: a movement may span several shots, which the per-gap
``interval_class`` cannot say. The two views coexist: the gap partition
is still what the time budget sums and ``statistic_splits`` filters on;
the regions only *hint* the auto-classifier (a gap over
``transition_max_s`` overlapping a reload region auto-classes ``reload``,
``coach.gap_overlaps_reload``) and never own a class. Every audit-doc
writer that classifies (audit PUT, triage accept, coach PATCH, Reclassify,
events PUT) goes through ``_classify_doc`` in ``ui/server.py``, which
passes the doc's own events; a writer that called the classifier
region-blind would flip a region-derived ``reload`` back to ``movement``
on the next unrelated save, silently, since ``stale`` is not rendered.
A new writer calls ``_classify_doc``; a corrupt events list there is the
GET's 422, raised before the save.
``is_classification_stale`` takes the same ``reload_overlap`` input as
the classifier, or a region-derived ``reload`` would report stale. Every
figure (per-shot ``moving``, ``reload_figures`` with the **overhang** =
reload end minus the enclosing movement's end, ``stage_event_summary``)
is derived, never stored, by ``splitsmith/events.py`` and its TS twin
``lib/events.ts``, which run ``tests/fixtures/events/cases.json`` case
for case -- a rule changes on both sides or not at all. A reload's
handles mean hand off the grip -> gun back on target.

Seeding (``events.seed_doc``) runs once per stage (``events_seeded``) on
the coach GET, reload only, never movement: every hinted gap
(``reload_hint_min_s``), or with a division capacity
(``DivisionCapacityConfig``, keyed on the SSI string so the power factor
rides in the name) the first hinted gap in a ``capacity + 1``-shot
window, else the longest, and only when a shot beyond that window
exists. ``capacity + 1`` is a bound, not a count: shooters start with
one chambered. A reset re-detection (``_merge_detection_into``) drops
the ``auto`` regions and ``events_seeded`` and keeps ``manual`` ones, so
a stage with no surviving manual region seeds afresh over the new
shots. The GET seeds but never re-classifies stored shots against the
seed; the writers above are the explicit path. The seed and the heal are persisted
under one condition in ``get_stage_coach``: an owner read that is not a
mirror. The GET's ``_version`` is the revision of the doc *as stored*
(taken before the in-memory seed and heal), so a mirror or share read
still hands out a ``_version`` the PUT accepts. The share surface does
reach the coach GET (``_SHARE_PATH_RE``): ``_build_coach_response``
strips ``events[].note`` there, as it strips ``coaching_note``.

``PUT /api/shooters/{slug}/stages/{n}/events`` replaces the list under
``_audit_rmw()`` with the audit revision check (409 ``version_conflict``;
422 ``lane_overlap`` names both ids) and returns the coach payload with
the saved doc's revision. A ``NaN`` / ``Infinity`` in the body is a 422
before the lock (``_reject_non_finite``, #843). The stored
``StageEvent`` ignores unknown keys, so a doc a newer version wrote loads
on an older one; the request's ``StageEventIn`` forbids them. It is **not** in ``_REVIEW_ROUTES``: ``events``
is desktop-owned, ``sync.merge.merge_audit_doc`` keeps local's copy, so
a hosted write on a mirror would be silently overwritten by the next
sync. A desktop-origin mirror answers 403 ``read_only_mirror`` and the
SPA renders the editor read-only on
``capabilityDenied(project.capabilities, "edit")``; a hosted-native
match keeps the PUT. The read-only rendering serves a hosted mirror (a
desktop browser) and ``isMobile``, but the Coach route sits behind
``DesktopGate`` in ``App.tsx``, so a phone never reaches it until a
phone Coach surface exists. Every PUT appends an
``audit_events`` entry, which is why the SPA saves on commit only
(release or keyboard nudge) through a 350 ms debounce in
``lib/useStageEvents.ts``: PUTs run one at a time with the revision the
previous one returned, a 409 reloads the coach payload and drops
anything pending, and a response that lands while a newer edit is
pending or a drag is live (frames sent, no commit yet) takes only the
revision. A commit whose lanes overlap never goes out: local state
reverts to the last valid list. Never save per drag frame.

The coach payload carries ``events``, ``event_summary``, ``_version``,
per-shot ``moving`` and per-video ``trim_version`` / ``scrub_version``;
the Coach player goes through ``useScrubSource`` like Audit, and the
lane editor's "Full-resolution video" entry is the same
``GlobalPrefs.full_res_scrub``. ``components/coach/LaneEditor`` owns the
DOM only; geometry (clamp, snap, ``MIN_EVENT_S``) is ``lib/events.ts``.
Pointer rules: a lane click seeks to the press point
snapped to the nearest shot (a ruler click does not snap), unless the
snap would land inside a same-lane region (then the raw press time), a
click on empty lane space deselects, a region being created stops at its
same-lane neighbours, ``pointercancel`` undoes like Esc. Auto proposals
are dashed with an ``AUTO ?`` label; a time pill (seconds and frame
number from the beep) follows the drag's seek target. Arrows nudge
(bracket keys sit behind AltGr on Nordic layouts).

Part 2 (rendering and export; the spec's "Part 2 as built" section wins
over its older "Rendering and export" text). Every rendered or exported
output -- overlay, summary card, ``events.csv``, FCPXML markers, share
figures -- reads **confirmed** regions only (``source == "manual"``)
through ``events.confirmed_from_doc``, which degrades a corrupt list to
none; a new consumer calls it, never re-derives the rule. The Coach page
alone shows proposals. **Keep** on the region card (``default`` button,
auto proposals only) commits ``lib/events.keepEvent``: ``source`` to
``manual``, nothing else; dragging, nudging or changing kind confirm too.

HUD contract (``overlay_hud.hud_stage_data``): ``data.stage.events``
(``{kind, start, end}``, clip seconds like ``shots[].t``),
``data.stage.reloads`` (``{start, end, duration, overhang}``, overhang
``null`` standing; templates never re-derive a figure), ``shots[].moving``,
``options.reload_chip`` / ``options.stage_bar`` (both off by default, on
the existing style-toggle seam), palette ``reload`` (``#FBBF24``) and
``movement`` (``#06B6D4``) in ``OverlayTheme`` / ``look.json``, optional.
All five template styles draw both; Classic (drawtext) and the compare
grid's sprite overlay draw neither. A reload on the move draws split on
the stage bar (reload in the top half, the movement under it). HUD
helpers stay inline in each template: ``_shared/`` scripts are not in
``template_digest``, so an edit there would not invalidate a cached MOV.
The overlay's ``<base>_overlay.json`` record carries the audit revision
for every style (``ui/exports.overlay_audit_revision``), so any audit
edit redraws; a legacy record never matches, and a failed redraw drops
that stage's overlay with an anomaly rather than reusing the stale one.

Summary card: with confirmed reloads the Splits band gains Reloads /
Reload avg / Overhang; Overhang is omitted when every reload is standing
(never a drawn ``+0.00``). Static / Moving split rows need a
single-shooter cell that is landscape or square and >= 480 px tall
(``_SPLIT_ROWS_MIN_CELL_HEIGHT``); grid holds pass ``split_rows=False``
so cells stay comparable, and keep the reload row. No confirmed regions
renders byte-identically to before. Both summary stills (and only they)
set ``fit_columns`` on ``single_html`` / ``grid_html``: ``fit.js``'s
``fitColumns`` shrinks the band until no grid column's text overflows its
column, which is what keeps a portrait card from cutting 1.42 to "1.4";
landscape and grid holds were pixel-identical under it. The live race
does not opt in (its rows change text per frame). A ``fit.js`` change
reaches the summary PNG by content, the preview only through
``PREVIEW_REVISION``. Exports: the splits CSV gains
``moving`` as its last column (``read_splits_csv`` takes both headers);
``<base>_events.csv`` is written only with confirmed regions and deleted,
locally and in hosted storage, when a re-export that writes the splits
CSV (``write_csv`` and shots, the CSV gate) has none; outside that gate
a prior file stays, as the splits CSV does. Region markers
(``Reload 1.42`` / ``Movement`` / ``Activation``, with duration, named by
``events.region_marker_label``) go on
the stage clip in single-stage and match FCPXML and FCP7 XML, clamped to
the visible window; compare carries none. ``stages[].figures`` on the
project payload carries ``moving_shots``, ``reloads``, ``reload_avg_s``,
``overhang_s`` (``_stage_region_figures``): all ``null`` with no
confirmed region, ``overhang_s`` ``null`` unless a reload overlaps a
movement, and no capacity warning (a Coach hint, not a shared figure).

## Hosted access tiers (spec 2026-10-03)

An account has **features** (``splitsmith.access.Feature``: ``sync``,
``share``, ``create_match``, ``raw_upload``, ``hosted_compute``); a match
has **capabilities** (by origin). A request needs both. A tier is a named
feature set in ``AccessConfig`` (``config.py``, overridable through
``SPLITSMITH_CONFIG``; ``SPLITSMITH_ACCESS_DEFAULT_TIER`` on ``serve``
sets the tier new accounts get, validated against that registry at
boot); ``features_for`` resolves a user (env admins get
everything, an unknown tier gets nothing). Code checks features, never
tier names, in Python and in the SPA. Local mode never consults access.
The backstop is ``submit_allowed`` on ``PostgresJobBackend``, wired per
tenant in ``_build_tenant``: no ``hosted_compute``, no job, whatever the
kind, so a new job kind is covered with no edit. A new hosted entry point
that creates matches, uploads or submits still declares
``Depends(require_feature(...))`` (``ui/access_gate.py``) so the refusal
is a clean ``feature_required`` 403, and a request handler that chains a
job after a committed write catches ``FeatureRequiredError`` and skips
the chain rather than reporting the write as refused. The intake
(``POST /api/v1/access-requests`` and sign-in's blocked branch) answers
the same bytes whatever the email's state, rate-limited or not, and
never mails the requester: only admins are alerted. Admin routes live in
``ui/admin_access_api.py`` with the db imports inside the functions (the
slim local install imports it). The SPA reads ``lib/access.can`` /
``useCan`` and maps a stray refusal with ``featureRefusal``. Deploy
facts (worker env, ``FORWARDED_ALLOW_IPS``, the waitlist cut-over) are in
``docs/saas-readiness/11-environment-strategy.md``.

## Hosted database connections (#1178)

asyncpg binds a connection to the event loop that created it; using it
from another loop crashes with "attached to a different loop" (#423).
Hosted mode therefore runs database work on exactly two long-lived
loops per process -- the main loop (uvicorn's, or the Procrastinate
worker's) and the ``DbRunner`` thread's (``splitsmith.async_bridge``) --
and ``db.engine.LoopEngines`` gives each *adopted* loop its own pooled
engine (``pool_size=5, max_overflow=10, pool_pre_ping=True``). Any other
loop gets the shared NullPool fallback, which never reuses a connection.
Every sync caller (the state accessors, the job backend's thread-side
bridges, ``youtube_api``) goes through ``run_sync``, never
``asyncio.run``: a new ``asyncio.run(<store coroutine>)`` anywhere on
the hosted path is a per-call connection again. Local mode builds no
engine and installs no runner, so ``run_sync`` there is ``asyncio.run``
as before. The pool adds no traffic of its own (no warm-up, no ping, no
``pool_recycle``): Neon closes idle connections after five minutes and
scales to zero, and ``pre_ping`` reconnects on the next request. The
regression gate is ``tests/test_pooling_docker.py`` (the compose stack
is what #423 crashed on) and proves pooling by a ``pg_stat_database.sessions``
delta over 40 requests, not by connection count, which NullPool also keeps flat;
SQLite in tests keeps one NullPool engine on purpose, a per-loop ``:memory:``
engine would be a database per loop.

Existence questions on the request path (is this source in storage? is
this trim cached?) go through ``ui/presence.StoragePresence`` (#1180),
one per request: it lists the tenant's ``raw/`` prefix once and each
shooter's ``trimmed/`` prefix once and answers by set membership, with
the same local-disk-first semantics as ``MatchProject.source_present``
and ``audio.trim_available``. ``_audit_trim_targets`` takes it as
``presence=``; its two callers (the shooters list, the bulk rebuild) each
build one. A new per-angle existence check on a list route belongs on
the index, not on ``storage.exists``: that was two R2 HEADs per angle
and 11 s to open a match. A share request skips the
``stages_missing_trim`` count entirely (it gates a button the anonymous
shell never renders), pinned by ``tests/test_share_shooters_presence.py``.

The hosted picker (``GET /api/me/recent-projects?detail=true``) loads every
match's detail in two queries (#1179): ``ProjectStateStore.load_docs_for_matches``
returns the match, project and audit docs of the whole list grouped per
match, ``PostgresMatchStore.list`` gives the origins, and
``_hosted_detail_from_docs`` derives each card with no I/O. A new per-match
fact the picker needs goes into that batch, never into a per-row ``await``:
``tests/test_recent_projects_batch.py`` pins that the route's SELECT count
does not grow with the number of matches.

The triage grid, the triage summary and the beep queue load the bound
match once per request through ``AppState.match_bundle`` (#1181): one
``load_docs_for_matches`` query for every shooter's project and audit
docs, each project bound by ``_project_from_doc`` exactly as
``shooter_project`` binds one. A route that walks every shooter takes the
bundle, not ``shooter_project`` in a loop; ``tests/test_match_bundle_queries.py``
pins two ``state_docs`` SELECTs per request whatever the shooter count. On
the SPA side the shell fetches the beep queue once per load and hands it
down as ``beepQueue`` on the outlet context; the Overview reads it there
and fetches triage once on mount (not again when the project arrives).

The jobs poll (``GET /api/me/jobs``, every 5 s per open tab) is bounded
(#1182): hosted ``PostgresJobBackend.list`` returns every active job and
every unacknowledged failure plus the ``RECENT_FINISHED_RETAINED`` most
recent others (never the account's whole history -- the SyncCard's job
echo is the only reader of terminal rows; the strip's "N of M" counts its
own batch, ``lib/jobBatch``, never the list's terminal rows, #1190),
and the route nulls ``result`` and ``timings`` on each entry. A job's
result is read through ``GET /api/me/jobs/{id}`` (``pollJob``), so a new
SPA surface that needs a result fetches the job, never the list;
``tests/test_jobs_poll_payload.py`` pins both.

## State doc kinds and the sync allowlist

Adding a ``doc_kind`` to ``state_docs`` is not a local change. The sync
pull manifest comes from ``ProjectStateStore.list_doc_meta``, which is
deliberately kind-agnostic and therefore returns the new kind
immediately; ``SyncClient._doc_path`` then maps anything it does not
recognise onto the audit URL shape, so an unhandled kind fails the
**entire** sync for that match, not the one document.
``sync.pull.PULLABLE_DOC_KINDS`` is an allowlist for exactly that reason
-- it keeps the next kind inert by default rather than catastrophic.
Widen it only together with a merge rule in ``sync.run._apply_pull``.

The allowlist is applied in **two** places and they move together:
``sync.pull.plan_pull`` (the client) and ``sync_api.get_doc_manifest``
(the server). Neither is redundant. There is no client-version
negotiation on the sync surface, so the client-side filter protects only
clients that have already been upgraded -- a desktop install in the
field still aborts its whole pull the first time a hosted export writes
an ``export_runs`` row, unless the server declines to offer it. The
server-side filter is the one that protects installs nobody controls;
the client-side one is what protects a new client talking to an older
server. Both are pinned:
``tests/test_sync_api.py::test_doc_manifest_omits_kinds_a_desktop_client_cannot_pull``
and the ``plan_pull`` cases in ``tests/test_sync_pull.py``.
The cascade queries are the other half: ``delete_match`` filters on
``match_id`` alone and ``delete_shooter`` on ``(match_id, slug)``, so
both sweep a new per-shooter kind without edits -- ``list_project_docs``
and ``list_audit_docs`` are the kind-*specific* ones and must not be
"generalised" into the cascade's job.

Two ``duration_seconds`` fields mean different things and are one
mis-wire apart: ``export_runs.ExportRun.duration_seconds`` is wall clock
for the job body, ``match_exports.MatchExportResult.duration_seconds`` is
the length of the stitched timeline. Never assign one from the other.

## Desktop auto-sync (spec 2026-09-27)

A running desktop (app or ``splitsmith ui``) syncs every match it has
synced before, both ways, with no clicks. ``ui/auto_sync.AutoSyncService``
polls ``GET /api/sync/fingerprints`` (``(doc_count, version_sum)`` over
the pullable kinds per match, compared with
``sync.state.local_fingerprint``) and marks a match dirty from two hooks:
an HTTP middleware on successful writes under ``/api/matches/{id}/`` and
a job terminal listener. The pure core (``sync/auto.py``) decides when:
pulls immediately, pushes after 45 s of quiet, never while the match has
a job running, never while a render (``RENDER_KINDS``) runs on *any*
match (a sync opens with the web-clip backfill, one encode per trim on
the render's CPU), one sync at a time, backoff to 15 min, a 401 pauses until
the token changes. Automatic runs are the ``auto_sync`` job kind (the
``sync_match`` body), hidden from the jobs strip unless they fail.

Every sync, manual or automatic, ends with the reconciler
(``sync/reconcile.py``, pure): from the ``processed`` flags and audit docs
it queues missing ``trim`` and ``shot_detect`` steps, which is how a beep
confirmed on the phone gets trimmed and detected on the desktop.
``_after_beep_reviewed`` uses the same ``video_step`` with
``explicit=True``; a new pipeline step belongs there, not in a second
rule. A reconcile step that fails is remembered with its inputs in
``auto_sync.json`` and skipped until they change.

Per-match state (``enabled``, the failure memo, the last run) lives in
``<match>/auto_sync.json``, never ``sync_state.json``: ``run_sync`` saves
that file from memory during a run and would undo a toggle. The global
switch is ``GlobalPrefs.auto_sync_enabled``. ``SPLITSMITH_AUTO_SYNC=0``
disables the service; ``tests/conftest.py`` sets it, and a test that
wants the service opts back in.

Audit GET/PUT carry ``_version`` (``audit_revision``, a content hash, the
same in both modes); a PUT with a stale one is a 409 ``version_conflict``
and both audit pages reload. It is never stored. The pull's audit
read-merge-write holds ``AppState.audit_lock``, the lock the PUT's
compare-and-save holds. ``find_active`` is scoped to ``current_match_id``
when set: shooter slugs repeat across matches.

Pushes are web-only unless the match's ``full_media`` flag
(``auto_sync.json``, the SyncCard's Web / Full switch) is on:
``build_push_plan(full_media=...)`` skips a ``_trimmed.mp4`` whose
``_web.mp4`` exists (a clip without a rendition keeps its trim), and the
push's gc deletes a remote full trim only when its rendition key is in
``sync_state.items``. The hosted delete route accepts beep_review keys,
``*_trimmed.mp4``, and a ``*_web.mp4`` only while its ``_trimmed.mp4`` is
in storage (#1077: the gc removes a rendition whose local file a re-trim
swept once hosted has the current trim, so a stale window is never
preferred); the params sidecar is never deletable. The end-to-end check is
``tests/test_sync_integration.py::test_phone_beep_confirm_reaches_the_desktop_reconciler``.

Rules added by the #1067-#1078 follow-ups (Sep 2026), each pinned by tests:

- **Failure parks, it does not retry on a timer** (#1070). A failed sync
  parks the match in ``AutoSyncCore`` until a local write, a hosted
  fingerprint that moved since the failure, a poll recovering from an
  outage, a successful manual sync or a restart. The backoff floors the
  retry after a release. ``status_for`` reports ``waiting_for_change``.
- **A match deleted on hosted stays deleted** (#1088). ``run_sync`` checks
  the manifest *before* ``ensure_match`` (which re-adopts a mirror): pushed
  docs recorded and none on hosted means deleted, so it records
  ``hosted_deleted_at`` in ``auto_sync.json``, raises ``SyncMirrorGone``
  and auto-sync stops watching. ``POST /api/match/sync/republish`` is the
  only way back (resets ``sync_state.json`` and ``sync_base/``). A 409 on
  one doc missing from the manifest fails naming the doc, never three
  "could not converge" retries.
- **One auto-sync per machine, one sync per match** (#1076). ``flock`` on
  ``~/.splitsmith/auto_sync.lock`` (the service owner; the other process
  shows "another splitsmith window ... is syncing" and takes over when the
  owner exits) and on ``<match>/.sync.lock`` around every sync job body.
- **Every local audit read-modify-write runs under ``_audit_rmw()``**
  (#1075): the lock locally, a no-op hosted. A new audit writer in
  ``server.py`` wraps its load..save in it, or the pull can lose its edit;
  ``tests/test_audit_lock_wiring.py`` has the probe lock to pin it (#1073).
- **Fingerprints carry a digest** (#1072): ``versions_digest`` over
  identity -> version, computed the same way from ``sync_state`` and from a
  manifest. A hosted without the digest is compared on the pair, and one
  without ``/fingerprints`` at all falls back to per-match manifests (#1071).
- **The reconcile failure memo keys on every input** (#1069): beep, stage
  time, and for a trim the buffers, source path and source presence.
- **The desktop command queue** (#1100, spec 2026-09-28): the phone asks,
  the desktop runs. Hosted: the ``desktop_commands`` table (never a
  ``state_docs`` kind), phone routes under ``match/desktop-commands`` (a
  REVIEW capability, mirrors only), desktop routes under
  ``/api/sync/commands`` on a 10 min lease, ``pending_commands`` on each
  fingerprint row. Desktop: ``ui/command_runner.CommandRunner`` inside
  ``AutoSyncService`` (so only the owner-lock process claims); it claims
  after that match's sync succeeds, refuses a stage whose audit revision
  moved since the request (``sync/commands.refuse_reason``), and completes
  only after a sync that started after the job ended. The result push uses
  ``AutoSyncCore.request_push_now``, never a backdated ``mark_dirty``: the
  job's own dirty mark would restart the quiet timer. A new command kind
  is ``COMMAND_KINDS`` hosted plus ``RUNNABLE_KINDS`` and the server's
  ``_start_desktop_command`` on the desktop. In the SPA, a desktop-synced
  match (``origin === "desktop"`` with the review capability) asks instead
  of detecting: ``lib/useDesktopCommands`` (polls only while a request is
  active), ``lib/desktopCommands`` (all wording), ``DesktopCommandLine``,
  the phone Audit's header menu and empty state, the desktop Audit
  overflow's ``DetectShotsBadge`` ``desktop`` prop, and the Overview's
  "Desktop requests" sheet. A new kind's phone entry goes through the same
  hook.

## UI: the visual budget (Sep 2026)

The SPA was restructured in eight PRs (spec
``docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md``,
all merged 2026-09-14). Every page is on the primitives now; any new
screen or surface, whatever session builds it, follows the budget below.
The ESLint rule ``no-restricted-syntax`` in ``ui_static/eslint.config.js``
enforces the mechanical half.

**Build with the primitives in ``components/ui``, nothing else:**
``PageHeader`` (the only way to get a page or stage title), ``Label``
(the only tracked-caps style, 1-3 words, never a sentence), ``Stat`` /
``StatStrip``, ``Table`` / ``Th`` / ``Td`` / ``Tr``, ``Chip`` (neutral
pill with a coloured tick; never a coloured fill), ``Button``
(``primary`` is Antonio led-fill and there is **one per view**;
``default`` neutral; ``destructive`` is an outline, never a red fill),
``PipelineDots``, ``ProgressStrip``, and the ``numeral`` utility for
every count, time and split. Sizes come from the theme scale
(``text-sm`` 12 px, ``text-md`` 14 px, ``text-base`` 15 px); arbitrary
``text-[...]`` and ``tracking-[...]`` are lint errors outside
``components/ui``. ``Kicker``, ``DisplayHeading``, ``Readout`` and
``TickStrip`` are deprecated exports: do not add consumers.

**Rules the lint cannot check:** red marks the brand, the one primary
action, the current position and focus, nothing else (stage state is
amber / green / hollow, errors are ``--color-destructive`` as outline +
text); glow only on live state; leading zeros only on stage and shot
ordinals (``Stage 03``), never on counts (``4 / 12``); one card level
per view, lists are hairline rows not stacked cards; no flavour copy,
no issue numbers in the UI; splits rank at least equal with scorecard
figures in any summary. Every page's data derivation lives in a pure
``lib/*.ts`` module with tests; the page maps results to primitives.

**Rebuilt and open for parallel work (build on their seams, not around
them):** Overview (``pages/Home.tsx``, ``components/overview/*``,
``lib/overview.ts`` -- a new per-stage state or action is a ``rowAction``
case); Splits and the stage page (``pages/Results.tsx``,
``pages/ResultsStage.tsx``, ``lib/splitsTable.ts``,
``components/results/SplitsTable.tsx`` / ``SplitsCards.tsx`` /
``SplitsList.tsx`` / ``Scorecard.tsx`` / ``StageStats.tsx``,
``components/share/ShareShell.tsx``); per-stage split figures for any
share-surface consumer come from ``stages[].figures`` on the project
payload, never from triage (owner-only). Audit (``pages/Audit.tsx``,
``components/audit/*``, ``lib/auditStep.ts``): beep confirmation is its
step 1 (``BeepStep`` on ``useBeepQueue``; ``/beep-review`` redirects
there on desktop, the phone keeps ``MobileBeepReview``); a new control
belongs on ``TransportLine``'s overflow menu or ``CurrentShotLine``, a
new per-shot signal on ``ShotList`` through ``lib/auditStep.shotRows``;
``_after_beep_reviewed`` in ``ui/server.py`` is the one place a confirm
chains trim and detection. Footage (``pages/Ingest.tsx``,
``components/footage/*``, ``lib/footage.ts``): in local mode Add footage
is the footage sort (``pages/FootageSort.tsx``, ``ui/footage_sort_api.py``,
spec 2026-10-01) whenever the match has scorecards; the per-shooter import
is the fallback without them; the coverage matrix
derives from ``buildFootageRows`` over every shooter's project; a new
per-video control belongs on ``ClipSheet``, a new per-stage action on
``CoverageMatrix``'s row menu, shooter management on ``ShootersPanel`` /
``AddShooterSheet`` (the Shooters page is gone; ``/shooters`` redirects).
``components/ui/Sheet`` and ``components/ui/Menu`` are the side-panel and
popover primitives. Coach (``pages/Coach.tsx``, ``components/coach/*``,
``lib/timeBudget.ts``): the time budget sums each shot's split into its
stored interval class (``timeBudget`` / ``matchBudget``; the segments
equal the stage time to 1 ms, pinned by fixture); the budget hues are
the chip ticks (``BUDGET_TICK``), and an outlier is an interval over
twice its type's match median. A new per-shot control belongs on
``ShotEditor``, a new per-type figure on ``TimeBudgetCard``. Compare
(``pages/Compare.tsx``, ``pages/compare/*``): header on ``PageHeader``,
the nav row appears only on multi-shooter matches
(``matchNavItems({ multiShooter })``). Matches (``pages/Pick.tsx``,
``lib/matches.ts``): the Continue card names ``RecentProjectDetail.
next_step``, which both recent-project enrichers in ``ui/server.py``
derive from the same per-stage status walk as ``stages_audited``
(``_next_step_from_statuses``: first ready / in-progress stage, then the
first stage still needing footage or scores, then export). Export
(``pages/Export.tsx``, ``components/export/*``, ``lib/exportPlan.ts``):
the blocker ladder in ``stageBlock`` is the one place a stage's "why
not" and its fix are worded, and it is what keeps hosted copy free of
drives; the bundle gate is ``ready_to_export_bare`` (a reviewed beep and
a stage time), not ``ready_to_export`` (audited shots): a bare stage
renders with its chapter and cards and loses only the shot-dependent
extras, which the row's "No splits" chip, the rail's "Splits" line and
``bareHint`` under the Overlay, Stage summary and YouTube fields say;
its summary hold is the scoring-only card (``summary_groups`` draws the
Scoring band from the stage time and scorecard and no Splits band); the compare grid is the page's third mode (``pages/
matchExportModel.ts`` still owns the payload and the partial-result
summary). Deleting a match lives on the Matches row menu (every row,
including "Folder not found" ones, which cannot be opened); hosted and
desktop deletes are separate actions and ``lib/matchDelete.ts`` words
which copy goes and which stays. ``components/ui/Menu`` portals its
popover to ``document.body``, so a row menu is never clipped by a
``Table``. Account
(``pages/Account.tsx``, ``components/account/*``) and every settings
surface use ``components/ui/Field`` rows; ``components/ui/Segmented``
is the closed-choice control. Triage and Jobs have no pages: the
Overview rows carry accept / audit and the flag count, the progress
strip and its drawer are Jobs, and ``/triage`` + ``/jobs`` redirect to
Overview (drop the redirects after one release). Still grandfathered --
restyle onto the primitives whenever you touch them:
``components/results/ResultsPlayer.tsx``, ``CamPicker.tsx``,
``ReclassifySheet.tsx``, ``ShareDialog.tsx``,
``components/comments/CommentPanel.tsx``, ``components/BeepSection.tsx``,
``components/Waveform.tsx``, ``components/MarkerLayer.tsx``,
``components/VideoPanel.tsx``, ``components/audit/MultiCamColumn.tsx``,
``CamGridModal.tsx``, ``CamSyncPill.tsx``, ``AnomalyPins.tsx``,
``components/FolderPicker.tsx``, ``HostedUploadModal.tsx``,
``RelinkDialog.tsx``, ``UploadDock.tsx``, ``StageTimeSection.tsx``,
``components/scoreboard/ConnectMatchDialog.tsx``.

**Nav rows** live in ``components/match/navItems.tsx`` (grouped by
phase: Prepare / Review / Analyse / Deliver); a new row needs a group,
and a queue about state belongs on the Overview rows or the progress
strip, never as a row of its own.

**Verifying a screen locally without real footage:**
``uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match`` (two
shooters and the match stage table, so Compare renders too)
then ``SPLITSMITH_AUTO_SYNC=0 uv run splitsmith ui --project ~/.claude-tmp/demo-match
--skip-system-check --no-browser --port 5174`` (wait for
``/api/health``; the match id is ``match_id`` in ``match.json``), then
screenshot with Playwright. Add ``--media`` to the seeder (ffmpeg on
PATH) for a real clip with a beep and shots, so Audit's waveform, the
beep picker and the trim -> detect chain run locally. Never start a
verification server on the real ``~/.splitsmith`` with auto-sync on: it
reads the real hosted token and syncs every previously synced match to
production, concurrently with the user's desktop app (it happened on
2026-09-27). To exercise sync UI, point ``SPLITSMITH_HOME`` at a scratch
dir whose ``GlobalPrefs`` name a dead ``hosted_base_url``. If the server
comes up unbound (``/api/health`` shows ``"bound": false``), open the
match from Matches -> Open by path. Show the rendered page before calling a visual change
done.

## Things Claude Code should not do

- Add new dependencies without asking. The dep list is small on purpose.
- Refactor the architecture without discussion. The pipeline structure in SPEC.md is intentional.
- Add features not in SPEC.md without confirming they belong.
- Generate fake test fixtures. Real audio samples or skip the test.

## First-session checklist

When starting fresh on this project:
1. Read SPEC.md fully before writing code.
2. Check that `uv`, `ffmpeg`, and Python 3.11+ are available.
3. Set up the project skeleton (pyproject.toml, src layout, tests dir).
4. Get a sample video from the user before tuning detection thresholds.
5. Build modules in pipeline order: video_match → beep_detect → trim → shot_detect → csv_gen → fcpxml_gen → cli.
6. Test each module against fixtures before moving to the next.

## Useful prior context

The user has prior data sources from match scoring. Example JSON format is in `examples/` — review it before designing the stage matching logic. Field names matter: `time_seconds`, `scorecard_updated_at`, `stage_number`, `stage_name`, `competitor_id`, `division`, `club`.

The tool should be agnostic to division but the user typically shoots Production Optics, where splits in the 0.15-0.40s range are typical for accurate-paced shooting; use that for sensible defaults.
