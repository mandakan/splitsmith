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
``trim_pre_buffer_seconds_for``), and the audit WAV is the one the desktop
pushed (below), else cut from the rendition by a job. A hosted-native
match keeps "``kind=trim`` never substitutes", pinned by
``test_hosted_native_match_without_a_trim_still_404s_kind_trim``.

**Requests read derived media; jobs make it** (spec
``2026-10-14-derived-media-off-the-request-path-design``). On 2026-10-13
three requests from one phone each cut a stage's audio themselves, one from
a rendition another was still downloading, and the phone was served a short
WAV. A request never runs ffmpeg, decodes a source or mirrors a large file:
the audio and peaks routes, the beep snap and the raw-file peaks read
through ``ui/audio.published_audit_audio`` / ``published_video_audio`` /
``published_take_audio`` and, on a miss, ``_audio_pending`` queues one
``derive_audio`` job (deduplicated on kind, stage, shooter, video) and
answers 202 ``{pending, active_job, job_id}``; the SPA's ``lib/pending``
(``whenReady``) asks again and the page says "Preparing audio". The trim
job also cuts the audit WAV (``ensure_video_audit_wav``) and the desktop
push carries it after the rendition (``sync/plan.audit_wav_for``, the
``audio/`` key in ``_SYNC_MEDIA_KEY_RE``, never deletable); a stored WAV is
current only when it is not older than the clip it was cut from. Every
derived file is written through ``splitsmith.derived``: a unique partial,
a check (``valid_mp4``, ``wav_problem`` against the clip's ``mvhd``
length), a rename. A lock is opt-in (``produce(lock=True)``,
``derived.locked``) for writers two local processes run at once; the queue
is what coordinates. The beep preview clip stays synchronous (one clip per
scrubbed ``t``) but publishes whole. A new route that needs a derived file
reads it and queues its job; it never calls an ``ensure_*`` producer.

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
**confirmed** regions only *hint* the auto-classifier (a gap over
``transition_max_s`` overlapping a confirmed reload region auto-classes
``reload``, ``coach.gap_overlaps_reload``, which ignores anything not
``source == "manual"``) and never own a class. An auto proposal never
touches a stored class: not through a save, a note typed on Coach,
Reclassify, ``match reclassify``, the GET's heal or ``stale``. Every audit-doc
writer that classifies (audit PUT, triage accept, coach PATCH, Reclassify,
events PUT) goes through ``_classify_doc`` in ``ui/server.py``, which
passes the doc's own confirmed events; a writer that called the classifier
region-blind would flip a region-derived ``reload`` back to ``movement``
on the next unrelated save, silently, since ``stale`` is not rendered.
A new writer calls ``_classify_doc``; a corrupt events list there is the
GET's 422, raised before the save.
``is_classification_stale`` takes the same ``reload_overlap`` input as
the classifier, or a region-derived ``reload`` would report stale. Every
figure (per-shot ``moving``, ``reload_figures`` with the **exposed** time =
reload duration minus its overlap with the union of the movements, floored
at 0, so a standing reload is exposed for all of it; ``stage_event_summary``
and its ``exposed_reload_s``)
is derived, never stored, by ``splitsmith/events.py`` and its TS twin
``lib/events.ts``, which run ``tests/fixtures/events/cases.json`` case
for case -- a rule changes on both sides or not at all. A reload's
handles mean hand off the grip -> gun back on target.

Seeding (``events.seed_doc``) runs once per stage (``events_seeded``,
which holds ``events.SEED_VERSION``) on the coach GET **with ``?seed=1``**,
which only Breakdown sends (``useStageWorkspace``'s ``seed``); Coach, the
Splits stage page and Compare read every stage and must never write one.
Movement (seed v3): one proposal per gap the stored classes call
``movement``, inset ``MOVEMENT_SEED_INSET_S`` from both shots, since
``shot_is_moving`` is inclusive and a region on a shot's time would call
the shots either side fired moving; it may sit over a reload proposal (a
reload on the move). Draw (seed v4, spec ``2026-10-17-draw-region-design``):
a ``draw`` region (the hands starting the draw to the first shot; its own
lane, first; one per stage, ``validate_lanes`` on both sides; may overlap a
movement, a draw on the move) is proposed from the beep to the first shot
on every stage whose first shot follows the beep, and a movement over the
same span, inset from the shot, when that shot comes more than
``draw_movement_min_s`` (2.0 s, ``CoachAutoClassifyConfig``) after the beep.
2.0 s catches 47 % of the corpus's stages (median first shot 1.94 s) on
purpose: a wrong guess is one tap to delete or relabel. Both are opt-in
seeder arguments only the coach GET passes. ``events_from_doc`` skips a
region of a kind this version does not know (with a warning) instead of
failing the stage, so an install one version behind reads a doc holding
a draw. A region starting before the beep (what moving a beep later leaves
behind) starts at the beep instead, or is skipped when it ends there; any
other corrupt known region still fails it. The stage's **Draw figure**
is ``events.draw_seconds`` (TS ``drawSeconds``, ``cases.json`` ``draws``):
what a shot timer reports, the first shot's time from the beep, whatever the
regions say (#1528; on 2026-10-17 it was briefly the region's length). A
confirmed draw region whose start was moved splits it into reaction and
presentation (``events.draw_parts`` / ``drawParts``), which Coach shows
beside the Draw and in the budget's Draw row. A stage has a **start**
(``MatchStageDefinition.start`` copied to ``StageEntry.start`` like
``stage_rounds``, set through the stage editor's "Std start" column, kept
across a scoreboard re-import): ``standard`` (holstered and loaded) or
``non_standard``; any other value reads as non-standard
(``match_model.is_standard_start``). Draws are compared across stages only
between standard starts: the match summary's Best draw
(``TileStageData.standard_start``), Coach's Draw baseline
(``match_distributions(non_standard_starts=)``) and the budget's Draw delta
and outliers (``timeBudget``'s ``standardStart``).
Every Draw surface reads it: ``TileStageData.draw`` (the summary card, its
figures, the match summary, the compare cell; every tile builder carries
the stage's confirmed ``events`` for it), ``share_card.stage_figures(...,
regions=)`` (the project payload, the triage cells, the share card) and
Coach's ``reviewFigures``. A new Draw surface calls it. There is no "on the
move" figure: a draw and a movement are independent regions. Reloads: every hinted gap (``reload_hint_min_s``), or with a division
capacity (``DivisionCapacityConfig``, keyed on the SSI string so the power
factor rides in the name) the round count decides: the fewest reloads that
keep every run within ``capacity + 1`` shots, in the longest gaps those
runs allow, each gap at least ``reload_min_s`` (any gap when no such
placement exists); a stage one magazine holds gets none. ``capacity + 1``
is a bound, not a count: shooters start with one chambered. A stage an
older seeder proposed (``events_seeded`` below the version) whose events
are all untouched proposals is seeded again on the next seeding owner
read, and its shots classified again (confirmed regions only); a manual region or
deleted proposals keep the stage as it is. A reset re-detection (``detection_merge.merge_detection_into``, the one rule the server job and the MCP ``detect_shots`` tool share) drops
the ``auto`` regions and ``events_seeded`` and keeps ``manual`` ones, so
a stage with no surviving manual region seeds afresh over the new
shots. The GET seeds but never re-classifies stored shots against the
seed; the writers above are the explicit path. A first seed that proposes
nothing writes nothing (``seed_doc`` returns False and leaves the doc), so
it is recomputed next time; once proposals exist ``events_seeded`` is
stored, which keeps deleted ones from coming back. The seed runs on a copy
and is persisted only on a seeding owner read that is not a mirror; a seed
that raises is logged and the stage served without proposals, and
``seed_events`` never proposes a region that would not validate (a gap
starting before the beep, no length, a non-finite time). The heal is
persisted on any owner read, mirrors included, as in v0.64.1; the seed
never rides it. The payload's ``event_summary`` (Coach's capacity warning
included), per-shot ``moving`` and ``stale`` read confirmed regions only;
its ``events`` keep the proposals for Breakdown's lanes. A shot whose time
is unreadable (``events.shot_ms``) is left out of the payload and skipped by
the classifier, never a 500. The GET's ``_version`` is the revision of the doc *as stored*
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
on an older one; the request's ``StageEventIn`` forbids them. Regions sync
both ways (spec ``2026-10-18-breakdown-hosted-sync-design``):
``sync.merge._merge_regions`` merges ``events`` per region by id against the
base (a delete is the base having it; an edit beats a delete; the same id
added on both sides is two regions, remote's renamed; of two regions from
different sides that collide in a lane the older doc's goes, surfaced;
``events_seeded`` takes the larger), and ``sync.run`` classifies the merged
doc's shots again when its confirmed regions moved. The PUT needs the
``breakdown`` capability: local and hosted-native always, a desktop mirror
only once ``matches.desktop_merges_events`` is set, which ``POST
/api/sync/matches`` does when the desktop sends ``X-Splitsmith-Sync-Features:
events`` (``sync.pull.SYNC_FEATURES``). Before that the mirror answers 403
``read_only_mirror``, so an older desktop's push can never overwrite a
phone's edit. The SPA renders Breakdown read-only on
``capabilityDenied(project.capabilities, "breakdown")``, phone and desktop. Every PUT appends an
``audit_events`` entry, which is why the SPA saves on commit only
(release or keyboard nudge) through a 350 ms debounce in
``lib/useStageEvents.ts``: PUTs run one at a time with the revision the
previous one returned. A coach response, the PUT's own or a foreign
one through ``apply`` (shot PATCH, reclassify), never replaces the local
list while a local edit is outstanding (in the debounce, in flight or in
a 409's reload, owed a re-send, or under a live drag). While one is, a
foreign response whose regions differ from the ones this tab last saw
from the server (another writer's) takes only the payload and is
*withheld*: its revision and regions wait, so an edit in flight 409s on
its own revision and is discarded rather than overwrite those regions;
one with the same regions advances. A withheld response is cleared by a
409's reload or by a later foreign response that is adopted (both
arrived after it); a successful PUT's answer does not clear it, since a
PUT sent on a revision read before the withhold and still accepted was
written before the withheld write. When nothing
is outstanding any more (a PUT's answer, a 409's decision, a non-409
failure, an Esc / pointercancel via ``LaneEditor`` ``onCancel``, an
overlapping release) the list catches up, adopting a withheld response
first, so an Esc shows another writer's regions at once and the next
edit saves on the newest revision. A 409 reloads and stops anything
queued; when the reload's regions equal the ones the
failed PUT started from (the revision moved for something else, e.g. a
shot PATCH), the newest local list is re-sent once on the fresh
revision (a live drag's release carries it, a cancel sends it),
otherwise, or on a second 409, the server's list wins, a live drag's
release is dropped, and ``onDiscard`` fires once (the seam for the
inline notice). The hook's ``issue`` is that notice (``SaveIssue``,
rendered by ``components/coach/SaveNotice`` in Breakdown's inspector): a
discard is muted; any other failed save, a failed 409 reload included,
reverts the list like a non-409 failure and never replaces the page, but
shows ``retry``, which re-sends the list the failed PUT carried unless
the regions it started from have since changed (then it is a discard)
and is disabled while ``busy`` (an edit outstanding); the server's
message is the line's ``title``, never its copy; the next successful
PUT or ``dismiss`` clears it. A commit whose lanes overlap never goes out: local
state reverts to the last valid list. Never save per drag frame.

The coach payload carries ``events``, ``event_summary``, ``_version``,
per-shot ``moving`` and per-video ``trim_version`` / ``scrub_version``;
the Coach and Breakdown players go through ``useScrubSource`` like Audit,
and Breakdown's timeline band options menu "Full-resolution video" entry
is the same ``GlobalPrefs.full_res_scrub``. ``components/coach/LaneEditor`` owns the
DOM only; geometry (clamp, snap, ``MIN_EVENT_S``) is ``lib/events.ts``.
On Breakdown the lanes are a track of the shared timeline band
(``components/timeline/Timeline``, spec 2026-10-09): the band owns the
ruler, the playhead, zoom (``lib/timelineView``: ``null`` is Fit, a
multiplier up to 16x, never narrower than the viewport), the wheel rules
(a plain wheel is the page's unless ``splitsmith.timeline.wheelZooms`` is
on; a horizontal wheel over the band is always consumed) and
follow-playhead, which never scrolls while a pointer is down in the band.
The follow rule depends on ``playing``: while playing it keeps the
playhead in the middle 80 % (the edge-triggered rule); while paused it
only brings an off-screen playhead into view and never re-centres one
already on screen, since a paused seek's ``currentTime`` can land after
pointerup already cleared (Breakdown's arrives through the video's async
``timeupdate``). Either way a zoom or a resize keeps its anchor: a
resize recomputes ``scrollLeft`` to hold the left edge's time, which is
why Cmd/Ctrl+B and a window resize never move the visible window at a
zoom. A track positions by percentage of the band's content div, so the
editor's pointer maths reads its own rect and needs no zoom code; a new
track does the same. A track can also be ``seekable`` (Breakdown's Audio
track is), which scrubs on a press-and-drag through the content div's
rect like the ruler, no snap, with pointer capture and one seek per
frame; a press inside ``[data-audit-marker]`` is ignored. ``onScrubEnd``
fires once the drag ends, and a track can take ``onDoubleClick(t,
shiftKey)``, ignored the same way inside a marker. ``WaveformTrack`` draws only the visible window
into a viewport-sized canvas, never a full-content one. Audit moved onto
the band in PR 2 (its own paragraph below, "UI: the visual budget"); the
beep step moved onto it in PR 3: ``components/audit/BeepTimeline`` fetches
the video's own peaks and owns the band's zoom, with ``origin`` at the
detector's beep (``peaks.beep_time``, else 0) so the ruler reads seconds
from it and never moves while picking. A Candidates row (one pin per
candidate, outside-the-clip ones hidden) sits over a seekable Audio row;
the same ``offset = videoBeepTime - peaks.beep_time`` rule
``BeepWaveformPicker`` already used converts the band's clip-local time
to a source-seconds pick on release, and BeepStep treats a pick within
5 ms of the detected time as "no override". ``BeepWaveformPicker`` keeps
its own zoom and stays mounted on ``MobileBeepReview`` and
``StageTimeSection``; this is desktop-only.
Pointer rules: a lane click seeks to the press point
snapped to the nearest shot (a ruler click does not snap), unless the
snap would land inside a same-lane region (then the raw press time), a
click on empty lane space deselects, a region being created stops at its
same-lane neighbours, ``pointercancel`` undoes like Esc. Edges and lane
clicks snap only while "Snap to shots" is on (the band's options menu,
``timelinePrefs.useSnapToShots``, per browser, default on); Alt inverts
it for one gesture, and keyboard nudges never snap. During an edge or
create drag the video seeks to the time under the pointer, **unsnapped**
(#1440: a snapped seek pinned the picture on one shot's frame across a
shot-free reload); a body move seeks to its leading edge. The time pill
(seconds and frame number from the beep) shows the edge as it will be
saved, with "snapped to shot N" when the snap pulled it off the pointer;
a release seeks nowhere, so the video stays on the last frame shown.
Every drag or scrub seek on a player goes through ``lib/coalescedSeek``
(one seek in flight; the latest request lands on ``seeked``): the
workspace's ``seekClip``, Audit's ``handleScrub`` and ``BeepTimeline``'s
scrub. A seek on the 720p rendition takes 0.1-0.2 s, and a write that
replaces the one decoding paints nothing. A pending request is dropped on
``emptied`` / ``loadstart`` or when ``currentSrc`` moved (a PiP swap, a
re-cut), and every other write to an element that takes coalesced seeks
goes through ``seekNow`` (cancel, then write: the source-swap carry, the
loop wrap, a parked position); an element that never takes one (the PiP
inset, the primary audio, the Results player) needs nothing. Arrows nudge
(bracket keys sit behind AltGr on Nordic layouts).

Part 2 (rendering and export; the spec's "Part 2 as built" section wins
over its older "Rendering and export" text). Every rendered or exported
output -- overlay, summary card, ``events.csv``, FCPXML markers, share
figures -- reads **confirmed** regions only (``source == "manual"``)
through ``events.confirmed_from_doc``, which leaves out each unreadable
region on its own and keeps the rest (dropping the whole list for one bad
entry took Little Trouble 2's nine regions out of every export, silently);
a new consumer calls it, never re-derives the rule. Breakdown
alone shows proposals: on the lanes, the region card and the region list.
Coach's ``StageStrip`` and its stat strip (on the move, exposed reload,
``lib/coachReview.reviewFigures``) draw and count confirmed regions only
(``lib/events.confirmedEvents``), like every output. **Keep** on the region card (``default`` button,
auto proposals only) commits ``lib/events.keepEvent``: ``source`` to
``manual``, nothing else; dragging, nudging or changing kind confirm too.

**Use Breakdown** (the export's one switch, on by default): off, an export
reads every stage as one nobody broke down, confirmed regions included (no
markers, no reload row or Static / Moving split, no ``moving`` column, no
stage bar or reload chip, the Draw is the first shot; a ``write_csv`` stage
export deletes an earlier events CSV, locally and hosted, as for any stage
with no confirmed region). It is ``breakdown``
on every request body (stage, match, grid, preview), ``ExportPresetBody``,
``MatchExportRequestData``, both renderers, both CLIs and ``splitsmith
overlay`` (``--no-breakdown``) and both MCP export tools; a new region
reader takes it. The engine half is the loaders' ``breakdown=``
(``stage_summary_data._confirmed_events``), ``export_preview``'s and
``overlay_hud_render``'s reads and Classic's draw. An overlay drawn without
it records ``breakdown: false`` (``overlay_settings``; on, no key, so every
record from before matches), so ``overlay_record_matches`` redraws or, in
the CLI and MCP, leaves out an overlay drawn the other way; the CLI's note
names the flag that stitches it. The SPA sends it only when off
(``useBreakdown`` on ``ExportSettings``; a hidden switch sends on, the preview
included, ``sentBreakdown`` in ``Export.tsx``) and shows the switch only where it
changes something: a selected stage with a confirmed region
(``figures.regions``), or the grid. A match with none shows no Breakdown on
Export at all (#1371).

HUD contract (``overlay_hud.hud_stage_data``): ``data.stage.events``
(``{kind, start, end}``, clip seconds like ``shots[].t``),
``data.stage.reloads`` (``{start, end, duration, exposed}``, exposed
the whole duration standing; templates never re-derive a figure), ``shots[].moving``,
``options.reload_chip`` / ``options.stage_bar`` (both off by default, on
the existing style-toggle seam), palette ``reload`` (``#FBBF24``),
``movement`` (``#06B6D4``) and ``draw`` (``#F472B6``, the app's
``--color-draw``) in ``OverlayTheme`` / ``look.json``, optional.
All five template styles draw them; Classic (drawtext) and the compare
grid's sprite overlay draw no regions. A reload or a draw on the move
draws split on the stage bar (it in the top half, the movement under it,
``hud.js`` ``pieces``). Shot 1's split is the Draw on every overlay, the
timer draw (``hud_stage_data``'s first ``split``, Classic's
``build_frame_states``). On an
upright page the context also carries ``data.safe_area`` (see "The upright
HUD"). The
stage-event helpers the five styles share (the stage bar and its bands,
the reload chip, ``chipSettle``, easing and colours) live in
``_shared/hud.js`` (``window.splitsmithHud``); ``template_digest`` hashes
all of ``_shared/``, so an edit there invalidates every cached MOV.
The overlay's ``<base>_overlay.json`` record carries the audit revision
for every style (``ui/exports.overlay_audit_revision``), so any audit
edit redraws; a template style's record also carries ``template``
(``overlay_hud.overlay_template_identity``: the template's bytes and
``shared_digest``), so an edit to the template or a shared script redraws
the exports' MOV too; Classic records carry none. Every style's record,
Classic included, carries ``theme`` (``overlay_hud.overlay_theme_identity``:
``theme_tokens(load_theme(look))`` plus both face ids, an own face by its
content-named file name only, so a hosted materialisation agrees), so a
palette or font edit to the Look redraws. ``audit_revision``, ``template``
and ``theme`` are all read *before* the render; a null ``template`` or
``theme`` never matches. The data a template draws is code, which none of
those see: ``data.stage`` carries ``overlay_hud.STAGE_DATA_VERSION`` (in the
record as ``stage_data`` from 2 on) and each opt-in provider its
``HudDataProvider.version`` (in ``hud_data``), and
``tests/test_hud_data_pins.py`` pins their outputs on sample stages, failing
until a changed output comes with a bumped version (the 0.78.0 reload refill
fix did not bump, and exports kept the old ammo count). One rule decides reuse,
``ui/exports.overlay_record_matches``, for the match export job, the MCP
tool and ``splitsmith match export`` alike (the CLI wants the recorded style
and Look, like the MCP tool, unless an explicit ``--theme`` asks for another
Look; it cannot redraw, so a stale overlay is left out with a note, which
names the recorded Look when only the Look differs); a legacy record never
matches, and a failed
redraw drops that stage's overlay with an anomaly rather than reusing the
stale one.

Summary card: with confirmed reloads the Splits band gains Reloads /
Reload avg / Exposed, drawn whenever there is a confirmed reload,
unsigned and standing reloads included. Static / Moving split rows need a
single-shooter cell that is landscape or square and >= 480 px tall
(``_SPLIT_ROWS_MIN_CELL_HEIGHT``), or an upright card (its own stacked
layout, above); grid holds pass ``split_rows=False``
so cells stay comparable, and keep the reload row. A stage with no
confirmed regions declares exactly the groups it did before regions existed
(pixels follow fit.js; portrait cards changed with the 0.6 em gap rule).
The stage summary stills (``build_summary_still``, the grid hold's
``build_hold_still``) and the grid's match summary
(``build_match_summary_grid_still``), and only they, set ``fit_columns`` on
``single_html`` / ``grid_html``: ``fit.js``'s
``fitColumns`` shrinks the band until no grid column's text overflows its
column or comes within 0.6 em (the band's caption size, for every
column) of the next column that overlaps it vertically (a table row sits on
its last baseline, so a caption-less "Static" label sits lower than its
figures), which is what keeps a portrait card from cutting 1.42 to "1.4"
and "Reload avg" from running into "Exposed"; landscape cards and grid
holds were pixel-identical under it (the grid-hold check covered holds
without regions: ``render_grid_frames.py`` writes no events). That shrink
floors at the smallest font, the captions, so on a dense upright grid hold
(9-up at 720x1280) the figures still ran together at the floor; there
(``fitColumnsPastFloor``, #1417, only when a *figure* still collides) the
rows wrap to half their columns, then go one line without captions, then
both, the band's text shrinking by one factor but none below the floor. A
caption that merely wrapped (the dense match summary's "Best draw") does
not trigger it, so those frames are unchanged. The live race
does not opt in (its rows change text per frame). The single-shooter match
summary is its own table document (``match_summary_html``, no ``fit.js``):
its type follows the canvas height on any canvas at least
``SIZE_ASPECT`` (6:5) wide, byte-identical to before there (6:5, 5:4, 4:3,
16:9, 1920x1088 and wider, where it always fitted); a squarer or upright
canvas sizes it as a 6:5 card of its width, since by height its headline
row ran off the page (measured break-even about 1.17:1). A ``fit.js`` change
reaches the summary PNG by content and the preview through the key's
``shared_digest``; any other change to what a preview draws still needs
``PREVIEW_REVISION``. Exports: the splits CSV gains
``moving`` as its last column (``read_splits_csv`` takes both headers);
``<base>_events.csv`` is written only with shots and confirmed regions.
A ``write_csv`` export deletes, locally and in hosted storage, an earlier
events CSV when the stage has no confirmed region, and both CSVs when it
has no shots (#1331); ``StageExportResult.removed_paths`` names what it
deleted and the hosted push deletes exactly those keys. With
``write_csv`` off both earlier files stay, like every output a run was
not asked for. Region markers
(``Reload 1.42`` / ``Movement`` / ``Activation``, with duration, named by
``events.region_marker_label``) go on
the stage clip in single-stage and match FCPXML and FCP7 XML, clamped to
the visible window; compare carries none. ``stages[].figures`` on the
project payload carries ``moving_shots``, ``reloads``, ``reload_avg_s``,
``exposed_reload_s`` (``_stage_region_figures``): all ``null`` with no
confirmed region, ``exposed_reload_s`` a number whenever there is a
confirmed reload, and no capacity warning (a Coach hint, not a shared figure).

**Coach is the review page, Breakdown the editor** (epic #1370). Coach
edits metadata only: per-shot ``coaching_note`` / ``improvement_flag``
and the **stage note** (``stage_note`` on the stage audit doc,
``coach.FIELD_STAGE_NOTE``), each saved by ``lib/useNoteAutosave`` after a
pause and on blur. Regions and interval classes (and Reclassify) are
Breakdown's; Coach shows them read-only through ``StageStrip``
(``lib/stageStrip``, confirmed regions only). ``PATCH
.../stages/{n}/stage-note`` writes under ``_audit_rmw()`` with the audit
revision check (409 ``version_conflict``) and an ``audit_events`` entry; it
is a ``_REVIEW_ROUTES`` entry, because ``merge_audit_doc`` merges
``stage_note`` three-way like a shot's note (newer doc wins a conflict,
surfaced), unlike desktop-owned ``events``. A clear is an explicit
``null`` (the tombstone); an **absent key means the writer never knew the
field** (an older install): the sync audit PUT (``sync_api.put_audit_doc``)
keeps the stored note when the body lacks the key, and the merge reads a
side without it as unchanged, so an older desktop's push or recorded base
never erases a note. When that PUT keeps a note the body lacked it answers
``kept_fields`` (``SyncAuditDocVersionResponse``) and ``run_push`` leaves the
doc's version unseen, so the next sync pulls and merges it; and a sync state
below ``state.STAGE_NOTE_SCHEMA`` (3) runs ``run._repull_audit_docs_for_stage_note``
once (forget every audit version, drop ``stage_note`` from every audit base),
marked done once that first pull has merged (a failure before it runs it again; after it, the note-aware bases stand). Never drop the key to clear it. The audit event records
the note's length and a short hash, not its text (so does ``coach_patch``
for ``coaching_note``). The coach payload carries ``stage_note``, ``null``
for none, a clear and a share read alike. "Adjust in Breakdown" and
"Review in Coach" carry ``?t=`` (seconds from the beep) and ``&shot=``,
and Breakdown also reads ``&region=`` (``lib/stageLink``, applied once on
load by ``useStageWorkspace``'s ``link``); a stale or malformed one is
ignored.

**Breakdown on a phone** (spec ``2026-10-17-mobile-breakdown-design``):
below 768 px ``App.tsx``'s ``BreakdownRoute`` renders ``MobileBreakdown``
(the desktop workspace stays behind ``DesktopGate``; Coach stays desktop
only). ``/breakdown/:slug`` is the stage list (``breakdown/mobile/StageIndex``,
``lib/breakdownIndex``, the shooter chips over it, confirmed region counts
only: proposals are seeded when a stage opens), and a stage opens on its
region list with nothing open; "Review N proposals" starts the queue and any
row opens that region. Add (``lib/addRegion``) puts a new manual region over
the open one (a movement during the draw) or in the shot gap at the playhead,
cut to the free room in its lane, and opens it in edge mode: Done saves it,
Cancel removes it unsaved. With more than one camera a Camera
``Segmented`` under ``PhoneVideo`` swaps the camera in the player (``usePip``'s
``focus``, no inset) and hands the workspace its beep (``setBigBeep``, as
``StageVideo`` does); the camera plays its own sound. It reviews the seeder's proposals one at a time: the queue
(``lib/proposalQueue``, proposals by start), a focus strip of the open
region plus 2 s each side (``lib/focusWindow``,
``components/breakdown/mobile/FocusStrip``, 44 px handles that open edge
mode, never a drag on the strip), Keep / Delete / Kind, Play region. Edge
mode (``lib/edgeDrag`` over ``laneDrag.dragFrame``, which takes a
``DragInit``) moves an edge by the finger's travel on the video or the
pad (``EdgePad``: one pad width is one second), each frame judged from the
region at finger-down, the video seeking to the unsnapped edge; frames are
``regions.change(next, false)`` and only Done commits, Cancel restores the
region edge mode found and a ``pointercancel`` the one the gesture found.
Frame steps use ``clipFps`` (the coach payload's per-video ``fps``, read
from the trim's own ``mdhd`` by ``media_check``, null off this disk, else
30). The phone plays the 720p rendition whatever ``full_res_scrub`` says
(``scrubSource``'s ``phone``), and ``PhoneVideo`` shows "Tap to load"
until metadata loads (iOS loads no media before a tap). Read-only is the
edit capability alone (``deriveStageView``), so a desktop mirror on hosted
reads only. A phone reaches the desktop's server through the CLI's
``--host`` / ``--lab-token`` until phone pairing exists (a later design).

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

A stage's other cameras show as **picture-in-picture** through
``components/video/PipView`` (epic #1405), never stacked tiles or a
camera picker; Compare keeps its grid. Every rule is in ``lib/pip.ts``
(swap, cycle over the cameras that are not big, the inset at 28 % of the
frame but never under 180 px nor over 45 %, its corners, the beep-offset clock and the drift threshold, ``insetStream``:
the rendition whenever there is one, then only kinds sharing the beep
anchor, ``null`` = unavailable), the clock in ``lib/pipSync`` (outside the
inset clip it holds paused at the clamp and is never played: play on
ended media restarts at 0), the state in ``lib/usePip`` (reset
to the primary, or to a page's ``start`` camera, on a new ``stageKey``), the corner per browser in
``lib/pipPrefs``. The page keeps its own big player (src from
``pip.big``, passed as an element through a callback ref) and its own key
handler (``pipKeyAction``: C / Shift+C); the usage note is at the top of
``PipView.tsx``. The inset spends no red; the primary's speaker glyph and
PRIMARY chip travel with it.
Coach and Breakdown (#1409) host it in ``components/coach/StageViewer``'s
``StageVideo`` (cameras from ``lib/stageCameras``: primary first, "Cam N",
the coach payload's ``beep_in_clip`` and kind; C / Shift+C through
``lib/keyboard.usePipCycleKeys``, which skips a press another handler
claimed). A swap hands the big camera's beep to the workspace
(``useStageWorkspace.setBigBeep``): ``currentTime`` stays in the primary
clip's seconds (where ``coach.beep_time`` and ``shots[].time_absolute``
live), the video's clock comes in through ``fromVideoTime`` and every seek
(shot, strip, band's ``seekFromBeep``, a deep link, a remount) goes out
through the same mapping (``stageCameras.primaryToBig`` / ``bigToPrimary``),
so a new seek path calls the workspace, never
``videoRef.current.currentTime``. A moment before the big clip's start
lands on its first frame and the playhead goes where the picture is. Audio
stays the primary's through ``lib/usePrimaryAudio`` (the primary camera's
own stream and ``beep_in_clip``; an audio error falls back to its next
stream kind).

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
payload, never from triage (owner-only). The stage page's other cameras
are the PipView inset (#1408, no camera picker): ``ResultsPlayer`` takes
the PipView as its ``overlay`` and hands the big ``<video>`` out through
``onVideoElement``; a swap changes its ``src`` in place, never by a
remount (no ``key`` on the player), carrying the position as seconds from
the beep and the play state, without re-running the window-start, moment
or autoplay seeks. The starting camera (``?v=`` on a moment link, else
``?cams=``, else ``compare_camera``) is ``usePip``'s ``start``, latched
once per mount; a swap writes ``?cams=`` like the picker did. With a
secondary big the primary's sound comes from ``lib/usePrimaryAudio``
(shared with Coach and Breakdown; audio stays the primary's): the player
muted, a detached ``<audio>`` of the primary's own stream on
``attachInsetSync``, anchored on that file's own ``beep_in_clip``, plus a
lead correction per play start (the <audio> starts 40-85 ms late; it
seeks ahead by the seek cost it learns from the residual, never per frame,
and a tighter drift line measured worse). An audio error unmutes the
player and the page falls back to the next stream kind. C /
Shift+C is a window keydown through ``pipKeyAction``. Audit (``pages/Audit.tsx``,
``components/audit/*``, ``lib/auditStep.ts``): beep confirmation is its
step 1 (``BeepStep`` on ``useBeepQueue``; ``/beep-review`` redirects
there on desktop, the phone keeps ``MobileBeepReview``); its own top row
is ``BeepPreview`` left and the candidate radiogroup right, bounded by
``lg:h-[max(300px,calc(100dvh-560px))]`` (step 2's own bound is below), and
its waveform is the shared timeline band too
(``components/audit/BeepTimeline``, spec 2026-10-09 PR 3): a candidates
row of pins over a seekable Audio row, origin at the detector's beep,
one pick per release. ``BeepWaveformPicker`` and its own zoom code
remain for ``MobileBeepReview`` and ``StageTimeSection``, never for
``BeepStep``. The band also has a Spectrum row (``SpectrogramTrack``,
``GET .../videos/{id}/spectrum``, spec ``2026-10-17-beep-spectral-view-design``)
and a Filter (``lib/beepFilter``: Off, Timer tone, Custom). Timer tone is the
spectrum's ``tone_band``, the timer's own frequency found near the beep
(``beep_tone.tone_band``, about ±200 Hz; timers in the corpus run from 1.6
to 5.4 kHz), else ``BeepDetectConfig``'s 2.2 to 3.5 kHz. The detail peaks
and Snap read it through the peaks route's ``band``. A confirmed beep's
distance from its tone is ``beep_tone.tone_offset_ms``: the earliest rise
within ``reach_s`` (0.3 s) that stays up for 80 % of the next 100 ms. That is
deliberately not Snap's nearest-peak rule, which lands mid-tone on a tone
that wavers. It reads any sustained sound within reach, a steel ring or a
voice as well as a tone, so it is a measure for confirmed beeps, not a beep
detector (78 of 92 isolated shots gave a number in a review). The value
is stored on the video with the beep time it was measured for
(``beep_tone_offset_ms`` / ``beep_tone_for``; ``tone_flag_ms`` ignores a value
whose beep moved or was unreviewed). It is written on confirm, on the manual
override and by the video ``derive_audio`` job. Triage carries it, and
Overview shows "check beep" at ``BeepToneConfig.flag_ms`` (20 ms).
``scripts/measure_beep_tones.py`` measures or backfills a match folder. On the
2026-10-17 corpus pass it agreed with the audited beep within 6 ms on 57 of
66 primaries; three wrong beeps were moved onto their tones with every
audited time shifted with them. Step 2's top row is
the video (``MultiCamColumn``, a 16:9 tile capped at
``max-h-[max(240px,calc(100dvh-620px))]`` below lg, letterboxed) and
``ShotList``; on lg the row is ``max(300px,calc(100dvh-502px))``, and
``max(398px,...)`` with two or more cameras (#1359: two cameras at
1440x900 end the band's Audio row at the sticky footer, 863; a taller
screen gives the extra height to the video). The other cameras are a
PiP inset over the big player (#1407), never a strip: the tile is the
whole column under its header (two cameras at 1440x900, a 648x365
frame; 746x420 at 1000 high). ``lib/auditPip`` maps each camera to its
inset clip and beep through ``planServedClip``, so a camera reads the
same instant in the inset as big; each camera's ``CamPill`` travels with
its picture (the big one's at the tile's top right, the inset's as its
note), and a camera with no beep, which cannot enter the inset, keeps
its pill in the column header. The big camera is ``usePip``'s
(``activeVideoIndex`` is derived from it; C / Shift+C in the page's key
handler, the grid's pick is ``pip.focus``, a new stage resets to the
primary). A swap changes the picture only: ``beepOffset`` maps the big
clip onto the primary's timeline, so the waveform, markers, shot list
and every seek stay the primary's, and while a secondary is big the
sound is ``lib/usePrimaryAudio`` (as on Splits) playing the stage audit
WAV, the waveform's own file, anchored on ``peaks.beep_time`` (the
primary's stream, same anchor, if the WAV fails). The phone Audit's
Video dialog (``pages/MobileAudit.tsx``, #1410) stacks instead of an
inset: with two or more cameras that line up
(``lib/phoneCameraStack.stackCameras`` over ``auditPipCameras``, so the
same streams, 720p rendition first) ``components/audit/mobile/CameraStack``
shows Cam 1 full width with one other camera the same size under it (a
"2 / 3" chooser cycles the others: two decodes at most). The active camera
has the native controls and is the clock, the other follows muted through
``attachInsetSync``; a tap makes it active without remounting either.
Sound is never two cameras at once and never ``usePrimaryAudio``: Cam 1
is on screen and synced anyway, so with another camera active Cam 1's own
element sounds and the active one is muted, until the active camera's
control unmutes it (``volumechange``), which mutes Cam 1 for as long. No
lead correction there: the clocks read ~30 ms apart whichever camera
drives (not a start lag), and seeking the playing follower overshot. The
stack sits top-aligned under the close row. The opening seek (1.5 s before the shot, ``videoSeekRef``)
lands on the active camera, the follower synced to it. One camera keeps the
old single player untouched; beep review stays one camera. Nothing else syncs a
second element: the page's old secondary loop is gone, and a new
follower goes through ``attachInsetSync``, never a loop of its own,
and the shared timeline band (spec 2026-10-09) underneath now hosts the
waveform: the audio track carries ``WaveformTrack`` and ``MarkerLayer``
together, pins are on a Flags row that does not seek, above it, and the
band's own ruler and
zoom replaced Audit's static ruler and ``TransportLine``'s
``ZoomControls``. Since #1359 the band's header row is Audit's only
control row (no strip above the band, no transport row in
``MultiCamColumn``): ``TransportLine`` is its ``toolbar`` (play, clock,
loop, camera label, the "show" filter), ``LegendKey`` its ``actionsStart``
(swatches + Key, labels in a popover), the help ``?`` its ``actionsEnd``,
and ``TransportMenuItems`` its ``menuExtra`` in the band's one ``...``
menu (frame steps, auto-step, full-resolution video, the trim / detect
action, then ``lib/auditBand`` readouts: peaks and linked cameras). A new
control belongs in that header if it fits one row at 1440 wide, else in
that menu, or on ``CurrentShotLine``; ``components/ui/Menu`` flips upward
when the room below cannot hold it. A new per-shot signal still goes on ``ShotList``
through ``lib/auditStep.shotRows``; ``_after_beep_reviewed`` in
``ui/server.py`` is the one place a confirm chains trim and detection.
Footage (``pages/Ingest.tsx``,
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
twice its type's match median. Coach is the review page: a new per-shot
review control (metadata, never an interval) belongs on ``ReviewShotList``'s
open row, a new per-type figure on ``TimeBudgetCard``, a new mark on the
stage on ``StageStrip`` (``lib/stageStrip``); an interval or region control
belongs on Breakdown (``BreakdownInspector``, ``ShotIntervalCard``). Compare
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
``components/results/ResultsPlayer.tsx``,
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
