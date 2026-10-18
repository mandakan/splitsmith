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
- CI runs the suite as four parallel shards (`SPLITSMITH_TEST_SHARD=i/4`, a
  CRC of each test id in `tests/conftest.py`; every shard keeps its own
  integration gate) and, on a pull request, skips the Python suite when only
  the SPA changed and the SPA job when nothing it reads changed (the
  `changes` job in `ci.yml`). A new test that reads files across that line
  (a Python test reading the SPA, an SPA test reading `tests/`) belongs in
  those rules. Every push to main runs everything.
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

Beep detection runs inside per-stage derived search windows for multi-stage single-take videos (ffmpeg extracts the window's audio via -ss/-t; results are offset back to source-absolute). Its candidates are ranked by a logistic regression over seven per-run features (``beep_features.candidate_features``, the one implementation both runtime and trainer use), and confidence is a calibrated head over (logit, margin) (#949, spec 2026-10-06). The numbers are ``BeepRankerConfig`` defaults in ``config.py``, pasted from ``ranker_report.json``'s ``models.lr.final_fit``; ``tests/test_beep_ranker_config.py`` fails on any drift. To retrain after adding fixtures: rebuild the manifest, run ``scripts/train_beep_ranker.py``, paste the new ``final_fit``, regenerate ``baseline.json`` (``scripts/eval_beep_detector.py --track clip --json ...``) and list every newly wrong fixture in the PR. ``ranker: heuristic`` is the old hand-written product, kept as the escape hatch. Out of fold it picks the beep on 106 of the 111 fixtures where the beep is a candidate at all; most remaining misses are the 16 fixtures where it never is (run merging, late onsets), and every confident mistake so far is one of those. Those were edge errors, not ranking errors: the ranker chose the tone's run and the rise foot landed off the tone. Every candidate's time is now the timer tone's start (``beep_tone.tone_onset``, ``BeepDetectConfig.tone_onset``; docs/METHODOLOGY.md step 4), and ``ranker: heuristic`` with ``tone_onset: false`` is the old product (``baseline_heuristic.json``). Many corpus beep labels had been confirmed from the old edge's picks, so a detector scored against them looked right where it was wrong: on 2026-10-17, 33 labels were moved onto their tones (read on spectrograms; the match projects on X9 too, each move in its audit doc's ``audit_events`` as ``beep_moved_to_tone``). Score a beep change against labels checked on a spectrogram, and re-extract a fixture's CLAP / PANN caches when its beep moves: its candidates move with it, which the caches' WAV stamp cannot see.

A secondary camera's beep is also found from the primary's
(``beep_cross.place_beep``, #1442): the primary clip's onset pattern from its
beep through the stage slid over the secondary's (``stage_match``; contrast
>= ``MIN_CONTRAST`` 1.2 is confident), then where the primary timer's own
frequency starts near it (``line_onset_near``), kept only within 60 ms of the
shots' projection. ``scripts/eval_beep_from_angle.py`` measures it over every
corpus stage with reviewed beeps on two or more angles (2026-10-18: 171 of
198 pairs confident, none more than 50 ms off, 133 within 5 ms; the old
``cross_align`` envelope landmark was seconds off on 32 of 194). The beep job
uses it for a secondary (``_try_align_secondary_to_primary``): the proposed
beep when in-stream detection finds none, ``StageVideo.beep_aligned_time``
(a "from primary" candidate in beep review) when it is confident, and an
in-stream beep that disagrees with it by more than 60 ms is never
auto-trusted. Confirming the primary's beep re-runs detection for every
unconfirmed, not hand-set secondary of the stage (``_replace_secondary_beeps``).

What each camera's audio does to detection (mono or stereo, compression, firmware changes, how to
recognise its files) is in ``docs/cameras.md``. Read it before tuning a threshold or a voter C
feature, and add to it, dated, whenever a measurement shows something new about a camera.

A detected shot carries **two times**: ``Shot.time_absolute`` is the shot time
(the rise foot, ``splitsmith.rise_foot``, docs/METHODOLOGY.md) that every
output reports, and ``Shot.feature_time`` is the detector's own leading-edge
walk, where candidates are found and scored. Every model input reads
``Shot.scoring_time`` (the ensemble, TTA, the build, the CLAP/PANN caches,
the sweeps): computing features from ``time_absolute`` shifted them between
the two Vanguard firmwares and made the September footage undetectable held
out (#1386). ``feature_time`` is never serialized.

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
- **Whole-stage decoder** (``ensemble/decoder.py``, on by default since
  2026-10-16) -- replaces the consensus and the headcam amplitude veto when
  the calibration has a ``decoder`` block: one logistic recalibration over
  the voters' signals gives each candidate P(shot), and a dynamic program
  keeps the best-scoring *set* for the stage under a split prior (the
  corpus's splits) and an end prior (the last shot at beep + stage time;
  off when the clip ends early). It needs only the beep and the stage time;
  the round count is not used (its weight tunes to 0). Fitted by the build
  on leave-one-match-out voter C scores (``scripts/fit_stage_decoder.py``);
  ``--decoder-only`` fits it for an existing calibration and writes only
  that block, which is how the shipped one got it.
  ``SPLITSMITH_STAGE_DECODER=0`` keeps the consensus (=1 forces the
  decoder). Held out it took F1 from 94.2 to 96.0 with round counts and
  93.3 to 95.9 without (headcams 86.5 to 92.0 blind); its worst stages are
  a camera whose shots score low and another bay's shots that score high.

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

Fixtures carry a **review status** (#1363, ``fixture_schema.review_status``):
one snapped from another angle (``anchor`` block, ``promote-from-anchor``
history) is ``needs_review`` until its ``review`` block says ``reviewed``;
everything else counts as hand-audited. ``scripts/promote_secondary_angles.py``
snaps every secondary angle that has no fixture onto its stage's reviewed
fixture (dry run by default, ``--write`` to promote; a snap of a snap is
refused), so the corpus holds unreviewed footage on purpose: the build trains
on it by default and ``--reviewed-only`` (``fixture_stems(reviewed_only=True)``)
leaves it out. Compare both before trusting a number.
``scripts/fixture_review_inventory.py`` scores every fixture for review
(``lab.inventory``: needing review, snapped, the promotion report's share of
shots at the snap window's edge, and how much the stored leading edges
disagree with a stricter onset shot to shot) into
``build/fixture_review_inventory.json``; the lab's review queue
(``/dev/review``) puts shot times to check first in that order, and "Approve
to corpus" or the fixture editor's "Mark reviewed" (``lab.confirm_review``)
writes ``review.status = reviewed``. A label pass never clears a fixture whose
times need checking.

Fixture audio is **not in git** (#1363): ``tests/fixtures/audio.lock.json``
maps every fixture WAV to its sha256, the bytes live content-addressed in a
public-read R2 bucket (``wav/<sha256>.wav``) and ``tests/fixtures/.gitignore``
keeps the local copies out. One WAV is also still in git on purpose, the
smoke fixture ``stage-shots-tallmilan-2026-stage3-s97dcec94-pin.wav`` (the slim
wheel and both desktop smokes read it, one in a bare container). CI's python
shards fetch the WAVs the tests read (``fetch --only tests/fixtures/audio.ci.txt``,
cached on the lock and the list); a new test that reads a fixture WAV adds
it to that list. ``uv run python scripts/fixture_audio.py fetch``
before a sweep (there a missing WAV is a skipped fixture, silently);
``push <stem>`` uploads a new or changed WAV to ``splitsmith-fixtures``
(served at ``fixtures.splitsmith.app``) through ``wrangler r2 object put``,
so it needs a ``wrangler login`` on the account that owns splitsmith.app, and
records it. Never commit a fixture WAV again.

The calibration build checks its inputs before it fits anything
(``ensure_audio`` and ``scripts/feature_caches.py``): every fixture's WAV
against the lock (missing ones fetched from R2; one the lock lacks, one R2
cannot serve, or a local one that differs from the lock, which is a re-cut
not pushed yet, stops the build), and its CLAP / PANN caches against that
WAV by the ``wav_sha256`` stamped into them (an unstamped cache is checked
once against fresh candidate times, then stamped). A stale or missing cache
stops the build unless ``--extract``. A row-count check let a re-cut clip's
old features through. ``--matches`` picks the corpus by match; ``--out
build/models/<name>`` writes a **model bundle** (``ensemble.model_bundle``:
calibration, ONNX graphs, ``heldout/`` with per-match leave-one-match-out
figures and labelled OOF rows, and ``model.json`` naming every file and
fixture WAV by sha256) and leaves the shipped model and the parity
references alone. The lab's Models page lists the shipped model and every
bundle and compares any two (``/dev/models?model=&vs=``). Choosing a
bundle at runtime is not built.

The review-time variant generator ``scripts/build_ensemble_fixture.py``
still exists for offline comparison under ``build/ensemble-review/``.

## Rendered cards, Looks, upright layouts, identity

These rules live in `.claude/rules/` and load when you read or edit a matching
file (each file's `paths:` frontmatter lists them):

- `rendering.md` -- the MP4 renderers: card segments, the segment cache,
  the live overlay and its reuse record, summaries, logo spots, transitions
  and stings.
- `looks.md` -- Look manifests and variants, templates, the template sandbox,
  fonts, the Look stores and editors. Custom templates are desktop only, by
  decision.
- `upright.md` -- portrait cards, grids and HUD and the platform safe area.
- `identity.md` -- shooter identity, the shooter book, your brand and the
  event logo.

Working in this area without having opened one of those files yet? Read the
matching rule file first.

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

## Stage events, Breakdown and Coach

Rules in `.claude/rules/`, loaded with the files they cover:

- `stage-events.md` -- regions, seeding, classification, the Draw figure, the
  events PUT, the stage note, the sync merge, and what outputs read.
- `breakdown-ui.md` -- Breakdown and Coach in the SPA: the save protocol in
  `useStageEvents`, the timeline band, pointer rules, the phone editor.

Two rules reach `ui/server.py`, which is too broad to scope: every audit-doc
writer that classifies goes through `_classify_doc` (it passes the doc's
confirmed regions; a region-blind call silently flips a region-derived
`reload`), and every output reads regions through
`events.confirmed_from_doc`.

## Hosted mode, sync and derived media

Rules in `.claude/rules/`, loaded with the files they cover:

- `hosted.md` -- access tiers and features, database connections and the
  per-loop pools, batching on the request path (presence, the picker, the
  match bundle, the jobs poll).
- `sync.md` -- state doc kinds and the pull allowlist, desktop auto-sync, the
  reconciler, audit locking, the push's media, hosted deletes, the desktop
  command queue.
- `media.md` -- trims and web renditions, stream kinds, web-only mirrors, and
  derived media made by jobs, never by requests.

Rules that reach `ui/server.py`, which is too broad to scope:

- A new hosted entry point that creates matches, uploads or submits declares
  `Depends(require_feature(...))`; code checks features, never tier names.
- On the hosted path a sync caller uses `run_sync`, never `asyncio.run`.
- A route that walks every shooter takes `AppState.match_bundle`; a per-angle
  existence check goes through the request's `StoragePresence`; a new per-match
  picker fact joins the batch, never a per-row `await`.
- Every local audit read-modify-write runs under `_audit_rmw()`.
- A request never runs ffmpeg or calls an `ensure_*` producer: it reads the
  derived file and queues its job (`_audio_pending`).
- A new `state_docs` kind needs `PULLABLE_DOC_KINDS` (applied in both
  `plan_pull` and `get_doc_manifest`) and a merge rule in
  `sync.run._apply_pull`.

## UI (the SPA)

Rules in `.claude/rules/`, loaded with the files they cover:

- `ui-budget.md` -- every SPA file: the primitives, the visual budget, nav,
  and which page owns which kind of control.
- `pip.md` -- multiple cameras as picture-in-picture, the audio rule, phone
  Audit's camera stack.
- `audit-ui.md` -- Audit's layout and control row, the beep timeline, the
  spectrum filter and the tone offset.
- `breakdown-ui.md` -- Breakdown and Coach, the timeline band.

Three UI rules reach `ui/server.py`, which is too broad to scope:
`_after_beep_reviewed` is the one place a beep confirm chains trim and
detection; both recent-project enrichers derive `next_step` through
`_next_step_from_statuses`, the same walk as `stages_audited`; and a beep's
`beep_tone_offset_ms` is stored with the beep time it was measured for
(`beep_tone_for`) on confirm, on the manual override and by the video
`derive_audio` job.

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
