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

Beep detection runs inside per-stage derived search windows for multi-stage single-take videos (ffmpeg extracts the window's audio via -ss/-t; results are offset back to source-absolute). The shot-detection pipeline is a 3-voter ensemble, not raw signal processing:

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
onto its bar; the grid renderer has no cache yet.

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
byte path is a presigned redirect and the object exists; local mode keeps
serving the trim from disk and the anchor stays ``trim``, pinned by
``test_get_coach_entry_kind_stays_trim_locally_with_web_file``. On the
stream routes ``kind=web`` falls back web -> trim -> source and never
404s; ``kind=trim`` never substitutes the rendition (audit scrubbing
needs the real GOP). Hosted Compare prefers ``trimmed/<...>_web.mp4``
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
time; the gallery never rasterizes). ``lookGallery.test.ts`` pins the
per-format visibility table and that every committed thumbnail is
referenced; ``renderOptions.test.ts`` pins that the mappers never send a
field the registry hides. Transitions live in Look (FCPXML only, sent
as ``none`` elsewhere) and the title line in Details. A slot whose
seconds field is being edited reads NaN and must still count as on, or
the input vanishes under the cursor (``summaryHold.read``).

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
needs a ``previewCardFor`` case or it previews as the frame.

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
