# Subcommand reference

All commands are run via `uv run splitsmith <subcommand>`. Run `--help` on any of them for the full option list. The main [README](../README.md) covers the common path; this file documents every subcommand.

## `ui` -- production UI

The localhost SPA that orchestrates the full ingest -> audit -> export workflow. State lives on disk under `--project`; closing the browser and re-running resumes where you left off.

```bash
uv run splitsmith ui --project ~/matches/tallmilan-2026
```

Opens `http://127.0.0.1:5174/` in your default browser.

For active SPA development with hot reload (the install / build is already covered by the [Install](../README.md#install) section):

```bash
cd src/splitsmith/ui_static
pnpm dev             # Vite dev server on :5173, proxies /api to backend on :5174
```

## `detect` -- preview only, writes nothing

Use this first when tuning thresholds or sanity-checking a video. It runs beep + shot detection and prints a table; no files are written.

```bash
uv run splitsmith detect \
    --video tests/fixtures/stage_sample.mp4 \
    --time 14.74
```

## `single` -- full pipeline for one video

Process a single video with an explicit stage time. Writes the trimmed mp4, splits CSV, FCPXML, and report into `--output`.

```bash
uv run splitsmith single \
    --video tests/fixtures/stage_sample.mp4 \
    --time 14.74 \
    --output ./analysis \
    --stage-name "Per told me to do it" \
    --stage-number 3
```

Skip individual outputs with `--no-trim`, `--no-csv`, `--no-fcpxml`.

## `process` -- batch over a stage JSON

Match a directory of videos to the stages in an SSI Scoreboard export by file timestamp, then run the `single` pipeline for each matched (stage, video) pair.

```bash
uv run splitsmith process \
    --videos ~/match_raw/ \
    --stages examples/tallmilan-2026.json \
    --output ./analysis
```

If videos can't be matched cleanly (multiple candidates for one stage, no candidate, ambiguity across stages), the offending stages and videos are listed and the run aborts. Re-run with the videos renamed/separated, or use `single` for each stage explicitly.

## `compare` -- multi-shooter side-by-side FCPXML

Render one FCPXML where each stage is a beep-aligned grid of N shooters' trims. Tile slots are alphabetical by label and stay fixed across every stage so a shooter who's missing a stage gets a black filler tile rather than reshuffling the grid. Audio comes from a single nominated shooter; everyone else is muted.

Each shooter must already have per-stage lossless trims on disk (i.e., you've run the per-stage exporter for each project). The comparison emitter reads `<project>/exports/stage<N>_<slug>_trimmed.mp4` for every stage of every shooter.

Manifest YAML:

```yaml
output: bromma-classifier-2026.fcpxml
audio_from: Mathias        # must match a label below
layout_2up: horizontal     # horizontal | vertical, only used when N == 2
shooters:
  - project: ~/splitsmith/projects/mathias-bromma-classifier-2026
    label: Mathias
  - project: ~/splitsmith/projects/anders-bromma-classifier-2026
    label: Anders
```

Render:

```bash
uv run splitsmith compare export examples/compare-bromma-classifier-2026.yaml
```

Smallest-fits grid: 1 shooter -> 1up; 2 -> 2up (horizontal or vertical); 3-4 -> 2x2; 5-9 -> 3x3; 10-16 -> 4x4. Empty slots in the chosen grid become black filler tiles for the duration of that stage. Sequence frame rate / size come from the audio-source shooter's first stage; mismatched cam rates / sizes get their own `<format>` resource and ride on FCP's edit-time conform.

## `lab` -- algorithm Lab (fixtures, eval, tuning)

The Lab is the algorithm-development surface. Lives at `/lab` in the UI and is mirrored as `splitsmith lab <cmd>` on the CLI. Fixture catalog, batch ensemble eval with per-fixture P/R/F1, live tuning sliders (cached-universe rescore in <100 ms), and a per-fixture diff overlay on the waveform. Tuning configs export to committable YAML; runs persist as deterministic JSON under `build/lab/runs/`.

See [`../LAB.md`](../LAB.md) for the full guide -- end-to-end walk-through, fixture promotion, parameter tuning, and how to drive the loop in parallel with Claude Code.

```bash
uv run splitsmith lab fixtures           # list audited fixtures
uv run splitsmith lab eval --summary-only
uv run splitsmith lab rescore --universe build/lab/runs/latest.json --consensus 4 --summary-only
uv run splitsmith lab promote --audit-json <project>/audit/stage4.json --audit-wav <project>/audio/stage4_audit.wav --slug stage-shots-myclub-2026-stage4
```

## `review` -- audit a fixture in a local web UI

Open a single-page browser UI for reviewing detected shots against the fixture's audio (and optional video). Marker pins on the waveform: click to toggle keep/reject, drag to fine-tune time, double-click empty waveform space to add a manual marker. Save (`Cmd+S`) writes back to the fixture JSON's `shots[]`.

```bash
uv run splitsmith review \
    --fixture tests/fixtures/stage-shots-tallmilan-2026-stage2-s97dcec94.json \
    --video   VID_20260426_162417_00_048.mp4
```

`--video` is optional. The video offset defaults to the fixture window's lower bound (so passing the original source video Just Works); override with `--video-offset-seconds` if you've pre-trimmed.

Keyboard:

| key | action |
|---|---|
| `Space` | play / pause |
| `M` / `Shift+M` | next / previous marker |
| `Cmd+1` / `Cmd+2` / `Cmd+3` | zoom in / fit / zoom out |
| `Cmd+Z` | undo (toggle / drag / add / delete) |
| `Cmd+S` | save |
| right-click marker | delete (manual) or reject (detected) |

Each save writes the previous fixture to `<fixture>.json.bak` first.

## `audit-apply` -- merge a marked-up candidates CSV into a fixture JSON

When you create a new test fixture from a video, the helper script writes a candidates CSV with an `audit_keep` column at the front. Mark the rows you want to keep (`Y`, `1`, `x`, `yes`, `keep`, or `true` -- case-insensitive), save, then merge into the fixture JSON:

```bash
uv run splitsmith audit-apply \
    --candidates tests/fixtures/stage-shots-blacksmith-2026-stage7-s97dcec94-candidates.csv \
    --fixture    tests/fixtures/stage-shots-blacksmith-2026-stage7-s97dcec94.json
```

The fixture's `shots[]` array is rewritten in place; the `_candidates_pending_audit` block is preserved so you can re-audit if you change your mind.

## `fcpxml` -- regenerate timeline from a (possibly hand-edited) CSV

After `single` or `process`, the splits CSV typically contains false positives (echoes, neighbouring bays). The recommended workflow is:

1. Open the splits CSV in any editor (Numbers, Excel, vim).
2. **Sort by `confidence` ascending or `peak_amplitude` ascending** -- false positives cluster at the low end.
3. Delete rows that aren't real shots.
4. Regenerate the FCPXML from the cleaned CSV:

```bash
uv run splitsmith fcpxml \
    --csv analysis/stage3_per-told-me-to-do-it_splits.csv \
    --video analysis/stage3_per-told-me-to-do-it_trimmed.mp4 \
    --output analysis/stage3_per-told-me-to-do-it.fcpxml
```

The `--beep-offset` flag (default 5.0s) tells the regenerator where the beep is in the trimmed video. It must equal `output.trim_buffer_seconds` from the config used during the original trim (5s by default).

## `mcp` -- Model Context Protocol server

Exposes splitsmith's pipeline as agent-callable tools so MCP-aware clients (Claude Desktop, Claude Code, IDE plugins) can drive a match end-to-end. Implementation of issue #211.

```bash
splitsmith mcp                            # stdio transport
splitsmith mcp --allowed-root /path/to    # sandbox: every path the
                                          # agent passes must resolve
                                          # under this directory
```

Wire into your client by pointing it at the binary. For Claude Code, add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "splitsmith": {
      "command": "splitsmith",
      "args": ["mcp"]
    }
  }
}
```

(For Claude Desktop, the equivalent file is `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, with the same shape.)

Tools registered on the server (16 total):

| category | tools |
|---|---|
| read-only | `probe_video`, `discover_videos`, `get_project`, `list_stages`, `get_hitl_queue` |
| write | `assign_video`, `set_beep_manual`, `select_beep_candidate`, `mark_beep_reviewed` |
| detect | `detect_beep`, `detect_shots`, `trim_audit_clip` |
| export | `list_templates`, `export_stage`, `export_match` |

All tools take `project_root` as a path string -- stateless, so multiple agents can collaborate on the same project. `set_beep_manual` + `detect_beep` honour the auto-trust gate (#219): confidence >= threshold flips `beep_reviewed=True`, below it the beep lands in the HITL queue (`get_hitl_queue`).

### `/splitsmith-match` Claude Code skill

The full agent flow is encoded as a Claude Code skill at `skills/splitsmith-match/`. Install once:

```bash
ln -s "$(pwd)/skills/splitsmith-match" ~/.claude/skills/splitsmith-match
```

Then `/splitsmith-match` (or just describing the task -- "run splitsmith on this folder", "build the match recap") triggers the runbook: discover videos -> match to stages -> beep + shot detection -> resolve HITL queue -> per-stage exports -> match-level FCPXML / MP4 / YouTube sidecar. HITL checkpoints fire only when the agent's confidence would otherwise force a guess.

Skill details + the runbook's HITL prompt patterns live in `skills/splitsmith-match/README.md` + `SKILL.md`.
