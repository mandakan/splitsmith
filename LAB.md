# Lab guide

The Lab is splitsmith's algorithm-development surface. It lives at `/lab`
in the production UI and is mirrored as a `splitsmith lab` CLI. End-user
paths (Ingest, Audit, Export) are untouched -- the Lab is opt-in and
loads its heavy ML models lazily on the first eval call.

This guide walks through:

1. [What the Lab actually does](#what-the-lab-actually-does)
2. [Running the Lab end-to-end](#running-the-lab-end-to-end)
3. [Adding new videos as fixtures](#adding-new-videos-as-fixtures)
4. [Tuning parameters interactively](#tuning-parameters-interactively)
5. [Working with Claude Code in parallel](#working-with-claude-code-in-parallel)
6. [Reproducing and committing a tuning result](#reproducing-and-committing-a-tuning-result)
7. [Reference: file layout and JSON shapes](#reference)

---

## What the Lab actually does

The shot-detection pipeline is a 4-voter ensemble (see `CLAUDE.md` for
the architecture). Every Lab call runs against the audited fixtures
under `tests/fixtures/` -- short hand-labeled audio clips with ground
truth in adjacent JSON.

There are three operations the Lab supports:

| Operation | Endpoint | CLI | Cost | When to use |
| --- | --- | --- | --- | --- |
| **List** fixtures | `GET /api/lab/fixtures` | `splitsmith lab fixtures` | instant | always (catalog) |
| **Eval** the ensemble | `POST /api/lab/eval` | `splitsmith lab eval` | seconds (loads CLAP+PANN; runs detector+models against every fixture) | first run, or after fixtures / models change |
| **Rescore** a cached universe | `POST /api/lab/rescore` | `splitsmith lab rescore` | <100 ms | tweaking thresholds / consensus / boost |

Eval emits an `EvalRun` JSON containing:

- `summary`: aggregate precision / recall / F1 / TP-FP-FN across all fixtures.
- `universe.fixtures[]`: one entry per fixture with the full per-candidate
  feature universe (CLAP / PANN / GBDT signals), the per-candidate vote
  vector, ground-truth labels, and per-fixture metrics.
- `config_hash`: 12-char SHA prefix of the active `EvalConfig` -- two
  runs with identical config produce identical hashes, so it doubles as
  a "did anything change?" check.

Rescore takes that universe + a new `EvalConfig` and recomputes only
votes / consensus / metrics. No model calls. That's what makes the UI
sliders feel live.

---

## Running the Lab end-to-end

### 1. Start the production UI

The Lab is part of the production server, so the same command that runs
the audit/ingest UI also serves the Lab:

```bash
cd /path/to/splitsmith
uv run splitsmith ui --project /path/to/your/match
```

Open the URL it prints (usually `http://127.0.0.1:5174/`). The sidebar
has a new **Lab** entry; click it.

> **First-time only**: the first `Run eval` click downloads / loads the
> CLAP and PANN model weights into `~/.cache/`. Expect 30-90 seconds.
> Subsequent eval calls reuse the cached `EnsembleRuntime` on the
> server, so they only pay the per-fixture forward pass (a few seconds
> for the full set of 12 fixtures).

### 2. Run the first eval

Click **Run eval**. The Summary card fills in once the run completes:

- **Precision / Recall / F1 / TP-FP-FN** across the whole fixture set.
- The fixture table at the bottom now shows per-fixture P/R/F1, plus
  FP/FN counts highlighted (orange / red) when non-zero.
- The `cfg <hash>` badge in the header is the run identity.

The run is also persisted to disk:

```
build/lab/runs/<timestamp>-<config_hash>.json
build/lab/runs/latest.json   # always points at the most recent run
```

These files are deterministic JSON (sorted keys, ASCII), so they diff
cleanly across runs.

### 3. Drill into a fixture

Click any row in the fixture table. The pencil icon at the right edge
of each row deep-links into the review editor (`/review?fixture=...`)
so you can re-label shots / edit the beep without leaving the SPA --
the same shortcut is also available as a **Re-label** button on the
fixture detail header. A detail card appears below with:

- **Waveform** of the fixture's WAV. Pins overlay every kept candidate
  (green = TP, orange = FP, top-edge red = FN ground truth that no
  kept candidate matched within the configured tolerance).
- **Per-voter recall** on this fixture (which voter actually carried the
  ground-truth shots).
- **Diff list**: timestamps of FPs and FNs.
- **Candidate table** (collapsed by default): every candidate the
  detector emitted, with its per-voter votes, ensemble score, kept flag,
  and truth label, color-coded by outcome.

Use this view to ask "*why* did fixture X regress?". The candidate
table makes it obvious whether voter A produced the candidate, which
voters voted for it, and what the ensemble score landed at.

### 4. Run the same thing from the terminal

Every UI action has a CLI counterpart. The CLI emits JSON to stdout, so
it composes with `jq` and Claude Code's tool calls:

```bash
# List fixtures (mirrors the catalog table).
uv run splitsmith lab fixtures --no-pretty | jq '.[] | {slug, n_shots, expected_rounds}'

# Run an eval at the default config; print summary only.
uv run splitsmith lab eval --summary-only

# Run on a subset (matches what selecting rows would do in the UI).
uv run splitsmith lab eval -s stage-shots-blacksmith-2026-stage1 -s stage-shots-tallmilan-2026-stage5 --summary-only

# Tweak knobs from the CLI; --save persists under build/lab/runs/.
uv run splitsmith lab eval --consensus 2 --apriori-boost 1.5 --summary-only
```

The CLI calls into the same `splitsmith.lab` Python module the FastAPI
server uses, so the JSON shapes are byte-identical between channels.

---

## Adding new videos as fixtures

A fixture is a hand-labeled audio clip of a single stage. There are two
paths to create one: from inside the production UI (the typical flow)
or from the CLI (for batch / scripted runs).

### Path A -- promote from the production UI (recommended)

This is the one-button flow once a stage has been audited end-to-end.

1. **Open the project that contains the stage you want to capture.**

   ```bash
   uv run splitsmith ui --project /path/to/match
   ```

2. **Run the full pipeline for that stage.** Ingest the video, detect
   the beep, audit-trim, run shot-detect, and walk the audit screen
   accepting / nudging / rejecting candidates as usual. The fixture
   inherits whatever ground truth you finalize here.

3. **Click the flask icon** (next to Save) on the audit screen. The
   "Promote to fixture" popover appears with:

   - **Slug**: pre-filled with `stage-shots-<match-slug>-stage<N>`.
     Edit if you want a different stem.
   - **Overwrite if exists**: leave unchecked unless you mean to.

   Click **Promote**. The popover shows the destination path on success.

   What this does, atomically:

   - Copies `<project>/audit/stage<N>.json` to
     `tests/fixtures/<slug>.json`, adding `promoted_at`,
     `promoted_from`, and a `provenance` block (project root, stage
     number, stage name).
   - Copies the stage's audit-mode WAV (the same clip the audit screen
     was scrubbing) to `tests/fixtures/<slug>.wav`.

4. **Re-run eval in the Lab.** The new fixture shows up in the catalog
   table immediately. The Run eval button picks it up on the next click.

### Path B -- promote from the CLI

Useful when you're scripting from Claude Code, working on a remote
machine, or batching a backlog of audited stages:

```bash
uv run splitsmith lab promote \
  --audit-json /path/to/match/audit/stage4.json \
  --audit-wav /path/to/match/audio/stage4_audit.wav \
  --slug stage-shots-myclub-2026-stage4
```

The command refuses to overwrite an existing fixture without
`--overwrite`. On success it prints the same `FixtureRecord` JSON the
UI catalog uses.

### Path C -- audit-prep from a fresh video

When you don't have a project yet and just want to curate a fixture
directly from a raw video, the existing `audit-prep` / `audit-apply`
flow still works:

```bash
uv run splitsmith audit-prep \
  --video /path/to/clip.mp4 \
  --time 42.76 \
  --stage-number 1 \
  --stage-name "K-vallen" \
  --stem stage-shots-myclub-2026-stage1 \
  --output-dir tests/fixtures
```

Then audit it (`splitsmith review tests/fixtures/<stem>.json` opens the
review UI), and either:

- run `splitsmith audit-apply` to finalize, or
- promote-then-audit by opening it in the production UI's `/review`
  route and editing in place.

### Categorical labeling (issue #86)

Each candidate in the fixture catalog can carry a category label so eval can
break down precision *by failure mode*:

- **Rejected (FP) candidates**: optional ``reason`` --
  ``cross_bay``, ``echo``, ``wind``, ``movement``, ``steel_ring``,
  ``speech``, ``handling``, ``agc_artifact``, ``other``, ``unknown``.
- **Kept (TP) candidates**: optional ``subclass`` -- ``paper``, ``steel``,
  ``unknown``.

How to label from the UI:

1. Open a fixture's detail card in the Lab (click a row in the
   catalog table).
2. Scroll to the **Candidates** table at the bottom of the detail
   card. Each row has a dropdown in the ``label`` column -- positives
   get the subclass options, rejected candidates get the FP reasons.
3. Picking a value auto-saves through ``POST /api/lab/labels``. The
   detail card refreshes (eval re-runs in the background) so the
   "Label breakdown" panel + the corpus-wide counts on the Summary card
   update right away.
4. Clear a label by selecting ``--``.

**Keyboard shortcuts** (faster than the dropdown for the half-day
labeling pass): click any row to select it (or ``J`` / ``K`` /
arrow keys to navigate), then press a single key:

| Key | Reason (rejected) | Subclass (TP) |
| --- | --- | --- |
| ``X`` | cross_bay | -- |
| ``E`` | echo | -- |
| ``W`` | wind | -- |
| ``M`` | movement | -- |
| ``S`` | steel_ring | steel |
| ``H`` | handling | -- |
| ``A`` | agc_artifact | -- |
| ``Y`` | speech | -- |
| ``O`` | other | -- |
| ``U`` | unknown | unknown |
| ``P`` | -- | paper |
| ``0`` / ``Bksp`` | clear | clear |
| ``J`` / ``↓`` | next row | -- |
| ``K`` / ``↑`` | prev row | -- |
| ``Esc`` | deselect | -- |

The legend at the bottom of the fixture detail shows the active
selection and the available shortcuts. Shortcuts are scoped to the
selected row only and are silently ignored when an input / textarea
has focus.

How to label from the CLI:

```bash
uv run splitsmith lab label \
  --audit-json tests/fixtures/stage-shots-myclub-2026-stage4.json \
  --candidate 12 \
  --reason cross_bay
```

Use ``--clear-reason`` / ``--clear-subclass`` to remove an existing
label. Atomic write with a ``.bak`` backup of the prior JSON.

What you get back, in eval output:

- ``EvalRun.summary.fp_by_reason`` -- corpus-wide FP composition.
- ``EvalRun.summary.positives_by_subclass`` -- corpus-wide TP composition.
- ``EvalFixture.metrics.fp_by_reason`` / ``positives_by_subclass`` -- per fixture.

Unlabeled FPs / positives are counted under the ``unlabeled`` key, so
you can track labeling progress over time. The "Label breakdown" panel
in the fixture detail and the summary card render these directly.

> **Why bother labeling?** With 12 fixtures and ~1500-2000 negatives, you
> don't have enough data for a clean multi-class classifier. But labels
> still pay off in three ways: (1) measurement -- "of 35 surviving FPs,
> 22 are cross_bay" beats "the user thinks cross-bay is the worst";
> (2) hard negatives for binary specialist voters (cross-bay first); (3)
> ready-to-go training set for #10 (vision) and #18 (community corpus).

### Re-labeling an existing fixture

You don't have to promote a stage from scratch to fix labels. Two ways:

- **From the Lab UI**: in the fixture table, click the pencil icon at
  the right edge of any row, or open the detail card and click
  **Re-label**. Either deep-links to `/review?fixture=<audit_path>` --
  the same audit screen used for stages, but pointed at the fixture's
  JSON. Drag markers, accept/reject candidates, retune the beep, save
  with Cmd+S. Writes back atomically with a `.bak` of the previous
  version next to the JSON. The Lab catalog and metrics refresh on the
  next Run eval.
- **From the CLI**: `uv run splitsmith review tests/fixtures/<slug>.json`
  starts the production UI and opens the review tab on that file.

Re-labeling never re-triggers calibration on its own. If you want the
new labels to influence the shipped voter thresholds, hit **Rebuild
calibration** after.

### After adding a fixture

The ensemble's calibration artifacts (`src/splitsmith/data/`) were
built against the original calibration set. New fixtures **don't**
alter calibration until you rebuild.

**From the UI**: click **Rebuild calibration** in the Lab header. The
popover lets you pick `target_recall` and `tolerance_ms` and requires
an explicit confirmation checkbox before submitting -- the operation
overwrites `src/splitsmith/data/ensemble_calibration.json` and
`voter_c_gbdt.joblib`. Progress streams into the popover as the script
logs; the cached `EnsembleRuntime` is invalidated on success so the
next Run eval picks up the new thresholds.

**From the CLI**:

```bash
uv run python scripts/build_ensemble_artifacts.py
```

The Lab's eval step re-computes per-candidate features on every call,
so it can score against any fixture immediately. But the *thresholds*
voter A/B/C/D use are still the shipped ones until you rebuild. If you
want the new fixture to influence those thresholds, hit Rebuild
calibration (or run the build script) -- this regenerates the JSON +
joblib artifacts.

For pure regression-tracking ("is the current pipeline still good on
this new stage?"), no rebuild is needed -- just add the fixture and
hit Run eval.

> **Heads up**: rebuild calibration requires the CLAP / PANN feature
> caches under `tests/fixtures/.cache/`. If you've added a new fixture,
> build its caches first via `scripts/extract_clap_features.py` and
> `scripts/extract_audio_embeddings.py`. The build script will refuse
> to proceed without them.

---

## Tuning parameters interactively

### The cached-universe trick

Tuning is the loop:

1. **Run eval once.** This is the slow path: detector + CLAP + PANN +
   GBDT for every fixture. The result is cached server-side as the
   "current universe".
2. **Move sliders.** The SPA debounces (~120ms) and posts the new
   `EvalConfig` to `/api/lab/rescore`. The server reuses the cached
   universe -- no model calls -- and recomputes votes + consensus +
   metrics. Result: sub-100ms updates across all 12 fixtures.
3. **Run eval again** only if you've changed something the cache
   doesn't cover (added a fixture, edited audit JSON / WAV, swapped
   models).

In other words: the universe is the model output. The config is the
post-processing. Sliders only re-run post-processing.

### Knobs in the Tuning panel

- **Consensus K** (1-4 of 4): how many voters must agree to keep a
  candidate. Default 3. Lower it to recover recall on hard fixtures;
  raise it to cull FP-noisy stages.
- **Apriori boost** (0-2, step 0.05): when the audit JSON has
  `stage_rounds.expected`, the top-K candidates by detector confidence
  get this boost added to their consensus score. 0 disables; the
  default 1.0 is "equivalent to one extra vote".
- **Use expected_rounds** (checkbox): when off, the ensemble ignores
  `stage_rounds.expected` -- voter C reverts to its global threshold,
  apriori boost goes to 0. Useful for comparing "with vs without prior".
- **Per-voter threshold overrides** (advanced details panel):
  - **Voter A floor** (0 - 0.5): detector confidence floor.
  - **Voter B threshold** (-0.05 - 0.2): CLAP shot-vs-not-shot
    differential.
  - **Voter C threshold** (0 - 1): GBDT probability cutoff (only used
    when expected_rounds is unavailable / disabled).
  - **Voter D threshold** (0 - 0.5): PANN gunshot-class probability.

  Each row shows the calibrated value next to the live override; click
  **clear** to revert the override and re-use the calibrated default.

### A typical tuning session

1. Run eval at the default config; note the headline P/R/F1.
2. Find the worst fixture(s) in the catalog table -- click into them.
3. Look at the candidate table. Are the FPs being kept by 3-of-4 or
   barely scraping in at consensus 3 with the boost? That tells you
   which knob has leverage.
4. Slide consensus to 4. Watch the table: did FPs drop more than TPs?
   If recall holds, you've improved precision for free.
5. Open the per-voter override details. If voter D thinks every
   movement clip is a gunshot, raise its threshold by a hair until
   the worst FP loses its D vote.
6. Switch fixtures to verify the change generalizes -- this is what
   the live rescore is for; you don't have to re-run eval.
7. When you're happy: click `cfg <hash>` mentally vs the original.
   Save the config (next section) so the result is reproducible.

### Tuning from the CLI

Same operations as the UI, but you point at a saved run JSON and
override knobs by flag:

```bash
# Latest UI eval lives here.
LATEST=build/lab/runs/latest.json

# Try consensus 2 with no apriori boost.
uv run splitsmith lab rescore --universe $LATEST --consensus 2 --apriori-boost 0 --summary-only

# Per-voter threshold sweep, scripted.
for d in 0.20 0.25 0.30 0.35 0.40; do
  uv run splitsmith lab rescore --universe $LATEST --voter-d-threshold $d --summary-only \
    | jq --arg d "$d" '{d: $d, summary}'
done
```

`rescore --save` writes a new entry under `build/lab/runs/`, which is
what you want for "I ran a sweep, here are the results, let me diff
them".

---

## Working with Claude Code in parallel

The whole point of the Lab's CLI symmetry is to let you have a browser
window open *and* a Claude Code session running, both touching the same
artifacts.

### Recommended workflow

1. **You drive exploration in the UI.**

   Click around. Eyeball the waveform diffs. Find the regression. Move
   sliders until you have a hypothesis ("voter D is too eager for
   movement noise on Tallmilan stages").

2. **Hand the hypothesis to Claude Code.**

   Open a Claude session in the repo and ask it to investigate. Because
   the Lab persists every run as JSON under `build/lab/runs/`, Claude
   can read those without you copy-pasting numbers:

   > "Read `build/lab/runs/latest.json`. Compare the per-fixture
   > metrics for the four tallmilan-* fixtures vs the rest. Where does
   > voter D over-fire? Suggest a threshold that keeps recall on
   > blacksmith but cuts the tallmilan FPs."

   Claude can also drive the CLI directly:

   ```bash
   uv run splitsmith lab rescore --universe build/lab/runs/latest.json --voter-d-threshold 0.32 --summary-only
   ```

   The summary-only output is small enough to inspect inline; the full
   rescore (with `--save`) lands a new run JSON Claude can compare.

3. **Iterate in two channels.**

   - You: continue exploring in the UI. Slider changes don't write
     anywhere -- they're scoped to the current cached universe.
   - Claude: edits Python (e.g., `src/splitsmith/ensemble/voters.py`,
     `scripts/build_ensemble_artifacts.py`, or feature engineering in
     `src/splitsmith/ensemble/features.py`).

   When Claude finishes a code change, it should:

   - Run `uv run pytest -x -q` (the suite has 468 tests; CI gates this).
   - Run `uv run splitsmith lab eval --summary-only` -- this rebuilds
     the universe from scratch using the new code path, so the run
     reflects Claude's actual changes.
   - Compare the new `latest.json` with the previous run via `jq` /
     `diff`.

4. **Hot-reload the server when Claude changes Python.**

   The FastAPI server doesn't reload on its own. After Claude edits
   ensemble or lab code, restart `splitsmith ui` so the cached
   `EnsembleRuntime` picks up the new module. The browser tab can
   stay open; just hit Run eval again.

   The SPA *does* hot-reload for TS / TSX edits -- the Vite dev
   workflow under `src/splitsmith/ui_static/` works as normal.

### Comparing two runs

Two rescores diff cleanly:

```bash
diff <(jq '{config, summary, per_fixture: [.universe.fixtures[] | {slug, metrics}]}' \
        build/lab/runs/<old>.json) \
     <(jq '{config, summary, per_fixture: [.universe.fixtures[] | {slug, metrics}]}' \
        build/lab/runs/<new>.json)
```

Or ask Claude to do it -- the JSON layout is stable, so a one-shot
question like "compare the two most recent run JSONs and report which
fixtures regressed" works well.

### Things to avoid handing to Claude blind

- *"Tune the thresholds for me"* -- this is exactly the loop the Lab
  is designed for; you'll get better results sweeping in the UI than
  by asking the model to guess.
- *"Run eval against my new fixture and tell me if it's good"* --
  Claude can run the CLI, but it can't *listen* to the WAV. The audit
  step is yours; the eval step is Claude's.

---

## Reproducing and committing a tuning result

When you find a config you like, capture it as YAML so it lives in git
and can be replayed.

**From the UI**: click **Save as YAML** (next to Run eval). Pick a name,
optionally write a note, and confirm. The popover shows the path on
success. The button is disabled until at least one eval / rescore has
populated the cache.

**From the CLI**:

```bash
uv run splitsmith lab save-config \
  --name tighter-d \
  --note "Cut tallmilan FPs without losing blacksmith recall" \
  --universe build/lab/runs/latest.json
```

That writes `configs/ensemble.tighter-d.yaml`:

```yaml
config:
  consensus: 3
  apriori_boost: 1.0
  tolerance_ms: 75.0
  use_expected_rounds: true
  voter_a_floor_override: null
  voter_b_threshold_override: null
  voter_c_threshold_override: null
  voter_d_threshold_override: 0.32
name: tighter-d
provenance:
  built_at: 2026-05-03T...
  config_hash: abc123abc123
  fixtures: [stage-shots-blacksmith-2026-stage1, ...]
  note: Cut tallmilan FPs without losing blacksmith recall
  universe_path: build/lab/runs/latest.json
summary:
  precision: 0.92
  recall: 0.99
  f1: 0.954
  ...
```

Commit it:

```bash
git add configs/ensemble.tighter-d.yaml
git commit -m "tuning: tighter voter-D threshold for tallmilan stages"
```

In a PR description you can now say "this changes the voter-D threshold
from `0.27` to `0.32`; metrics are at `configs/ensemble.tighter-d.yaml`"
and reviewers can replay the same eval (after pulling the branch):

```bash
# Print the saved config back out as JSON.
uv run splitsmith lab load-config configs/ensemble.tighter-d.yaml | jq .config
```

If you decide to *ship* a tuning (i.e. make it the default), the
mechanism is `scripts/build_ensemble_artifacts.py`, not the YAML --
the artifacts under `src/splitsmith/data/` are the production
calibration. The YAML is for "I ran an experiment, here's the receipt".

---

## Reference

### File layout

```
src/splitsmith/
  lab/
    __init__.py            # public exports
    core.py                # pure functions (catalog, eval, rescore, promote, persist)
  lab_cli.py               # `splitsmith lab` Typer subcommands
  ui/
    server.py              # /api/lab/* endpoints (last block before static-asset serving)
  ui_static/src/
    pages/Lab.tsx          # /lab route SPA page
    pages/Audit.tsx        # PromoteFixtureButton at the bottom
    components/AppShell.tsx# Lab nav entry
    lib/api.ts             # LabFixtureRecord / LabEvalRun / api.runLabEval / etc.

build/lab/runs/             # persisted EvalRun JSONs
  <timestamp>-<hash>.json   # one per run (eval or rescore --save)
  latest.json               # always the most recent

configs/                    # committable YAML tuning snapshots
  ensemble.<name>.yaml

tests/fixtures/             # the audited fixtures the Lab evaluates against
  *.json                    # audit ground truth (per-stage)
  *.wav                     # sibling audio clip
```

### EvalConfig fields

```python
class EvalConfig(BaseModel):
    consensus: int = 3                         # 1-5; keep when vote_total + boost >= K
    apriori_boost: float = 1.0                 # >=0; added to top-K confidences when expected_rounds set
    tolerance_ms: float = 75.0                 # truth-to-candidate match tolerance
    use_expected_rounds: bool = True           # honor stage_rounds.expected from audit JSON
    voter_a_floor_override: float | None = None  # else: calibration floor
    voter_b_threshold_override: float | None = None  # else: calibration threshold
    voter_c_threshold_override: float | None = None  # else: calibration threshold (or adaptive)
    voter_d_threshold_override: float | None = None  # else: calibration threshold
```

### EvalRun JSON shape

Top-level keys: `config`, `summary`, `universe`, `config_hash`, `built_at`.

- `summary`: `{n_fixtures, n_truth, n_kept, true_positives, false_positives, false_negatives, precision, recall, f1}`.
- `universe.fixtures[]`: per-fixture; each carries `candidates[]`,
  `truth_times[]`, and `metrics`.
- `universe.{voter_a_floor, voter_b_threshold, voter_c_threshold, voter_d_threshold}`:
  the calibration thresholds the universe was scored against. Rescore
  uses these unless an override is set in the new config.

### Endpoints

| Method | Path | Body | Notes |
| --- | --- | --- | --- |
| GET | `/api/lab/fixtures` | -- | Catalog walk; cheap, hits no models. |
| POST | `/api/lab/eval` | `{slugs?, config?, persist?}` | Slow path. Caches universe server-side and (by default) writes `build/lab/runs/<...>.json`. |
| POST | `/api/lab/rescore` | `{config}` | Uses last cached universe. 409 if no eval has run. |
| POST | `/api/lab/promote` | `{stage_number, slug, overwrite?}` | Copies stage audit JSON + WAV into `tests/fixtures/`. |
| POST | `/api/lab/labels` | `{audit_path, labels: [{candidate_number, reason?, subclass?}]}` | Patches categorical labels on a fixture's audit JSON. Drops the cached universe so the next eval reloads. |
| POST | `/api/lab/save-config` | `{name, note?, overwrite?}` | Persists the active run's config + summary as `configs/ensemble.<name>.yaml`. 409 when no eval has run. |
| POST | `/api/lab/rebuild-calibration` | `{target_recall?, tolerance_ms?, fixtures?}` | Submits a `rebuild_calibration` job that re-runs the calibration build script. Poll `/api/jobs/{id}` for progress. |

### CLI

```
splitsmith lab fixtures              # list catalog
splitsmith lab eval                  # run full eval
splitsmith lab rescore               # rescore from a saved run
splitsmith lab promote               # promote a stage to a fixture
splitsmith lab save-config           # write configs/ensemble.<name>.yaml
splitsmith lab load-config <path>    # print a saved YAML as JSON
```

Run `splitsmith lab <cmd> --help` for per-command flags.
