# Claude Code Guidance

This file gives Claude Code project-specific context. Read SPEC.md for the full technical specification.

## Project context

Personal tool for an IPSC competitor to extract shot splits from head-mounted camera footage. The user is an experienced developer who uses Claude Code daily. They prefer:
- Concise, direct communication
- Pushing back when something is wrong rather than agreeing reflexively
- Asking clarifying questions before diving into detailed implementations

## Code conventions

- Python 3.11+, type hints everywhere
- `uv` for dependency management -- never use `pip` directly
- Pydantic for data validation
- `pathlib.Path` for paths, never strings
- f-strings for formatting
- Black formatting (line length 110)
- Ruff for linting
- Imports: stdlib, third-party, local -- separated by blank lines
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
  test -- worker startup dominates a focused run, and tracebacks are cleaner.
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
- Fixtures live in `tests/fixtures/` -- short audio clips with hand-labeled ground truth in adjacent JSON files
- Detection tests assert within tolerance (e.g., +/-15ms for shot times)
- Mock ffmpeg in trim tests; don't actually shell out during unit tests
- Integration tests can use real ffmpeg but mark them with `@pytest.mark.integration`
- CI installs ffmpeg and runs the integration suite with
  `SPLITSMITH_REQUIRE_INTEGRATION=1`, which turns any skip of an
  `integration`-marked test into a failure. A test that needs media builds it
  with `tests/synthetic_media.py` rather than depending on the gitignored
  `stage_sample.mp4` -- if a new integration test skips in CI, supply the input,
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

## Where guidance lives

This file loads into every session and subagent, so it holds only what applies
project-wide. Subsystem rules live in `.claude/rules/*.md`, each with a `paths:`
frontmatter listing the files it covers; Claude Code loads one when a matching
file is read or edited. The sections below say which file covers what.

When you add guidance: if it is about one subsystem, put it in that rule file
(or a new one) and add any file it governs to its `paths:`. Write the rule, the
reason it is not obvious, and the test that pins it; leave out dates, issue
numbers and how it was verified, which belong in the PR. A rule that lives in
`ui/server.py` cannot be scoped (every task touches it), so it gets a one-line
mention here. `scripts/check_claude_md.py` (CI: `claude-md.yml`) fails when
this file passes 25 KB, a rule file is unscoped, a `paths:` glob matches
nothing, a named rule file does not exist, or a line is not ASCII.

## Detection pipeline

Beep detection is a learned ranker over per-run candidates, timed at the timer
tone's start; a secondary camera's beep can also be placed from the primary's.
Shot detection is a 3-voter ensemble (envelope onsets generate candidates; CLAP
and a GBDT over CLAP/PANN features vote) with a whole-stage decoder choosing the
final set. The rules live in `.claude/rules/beep-detection.md` and
`.claude/rules/shot-detection.md`, loaded with the files they cover.

The beep job lives in `ui/server.py`: `_try_align_secondary_to_primary` places a
secondary's beep from the primary's, and an in-stream beep that disagrees with
that placement by more than 60 ms is never auto-trusted; confirming the
primary's beep re-runs detection for every unconfirmed, not hand-set secondary
(`_replace_secondary_beeps`).

What each camera's audio does to detection is in `docs/cameras.md`: read it
before tuning a threshold or a feature, and add to it, dated, whenever a
measurement shows something new about a camera.

Fixture audio is **not in git**: `tests/fixtures/audio.lock.json` maps each WAV
to its sha256 and the bytes live in R2 (`scripts/fixture_audio.py fetch` /
`push <stem>`). **Never commit a fixture WAV** (the one smoke fixture already in
git is the exception). CI fetches only the WAVs in `tests/fixtures/audio.ci.txt`,
so **a new test that reads a fixture WAV adds it to that list**.

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

## Export presets, the Look gallery and the preview

Rules in `.claude/rules/export-ui.md`, loaded with the files they cover: the one
preset body and how to add an option, why the `lib/api.ts` export wrappers
spread their payload, the gallery registry, and the rail's preview route.

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

## Desktop app

An Electron shell over the Python sidecar (`splitsmith.ui.embedded`); rules in
`.claude/rules/desktop.md` (runtime, ffmpeg, signing, the update feed, Linux
packaging). Two apply everywhere: a module-level `splitsmith.db` import on the
`create_app` path crashes the slim install the app ships
(`scripts/ci/assert_slim_import_surface.py` catches it), and every shipped
desktop build bundles the wheel PyPI serves, never one built from a checkout.

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

## Share links

Preview cards, the anonymous share surface and comments: rules in
`.claude/rules/share.md`. Three live in `ui/server.py`, which is too broad to
scope, so they are here too:

- Share token resolution and owner impersonation have exactly one
  implementation, `_share_alias`; never resolve a share token or impersonate
  the share's owner outside it (the HTML shells sub-request the anonymous API).
- `_SHARE_PATH_RE` (GET) and `_SHARE_WRITE_ROUTES` (POST/DELETE) are separate
  allowlists and must never merge; only the `comment` scope writes, and every
  refused (method, shape, scope) is the same opaque 404 as an unknown token.
- A comment's `author_handle` is server-derived, never a request field.

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

**The SPA's wire types are generated.** `scripts/export_openapi.py` writes
`ui_static/openapi.json` from the routes (`tests/test_openapi_export.py`
fails when it is stale) and `pnpm gen:api` turns it into
`lib/api/types.gen.ts` (the SPA CI job fails when that is stale). A type in
`lib/api.ts` that agrees with its schema is an alias into `Schema[...]`;
a new response model gets an alias, not a hand-written twin. After a
route or model change: run the export, then `pnpm gen:api`, commit both.

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
5. Build modules in pipeline order: video_match -> beep_detect -> trim -> shot_detect -> csv_gen -> fcpxml_gen -> cli.
6. Test each module against fixtures before moving to the next.

## Useful prior context

The user has prior data sources from match scoring. Example JSON format is in `examples/` -- review it before designing the stage matching logic. Field names matter: `time_seconds`, `scorecard_updated_at`, `stage_number`, `stage_name`, `competitor_id`, `division`, `club`.

The tool should be agnostic to division but the user typically shoots Production Optics, where splits in the 0.15-0.40s range are typical for accurate-paced shooting; use that for sensible defaults.
