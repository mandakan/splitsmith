---
name: splitsmith-match
description: Drive a splitsmith match end-to-end -- ingest a folder of competition videos, run beep + shot detection, write per-stage exports, and stitch the match-level FCPXML / MP4 / YouTube sidecar. Pauses at HITL checkpoints (ambiguous video assignments, low-confidence beeps, shots needing review) and runs straight through when everything's confident. Use when the user says things like "build the match", "run splitsmith on these videos", "process this match", "export the match recap", or drops a folder of competition footage in a splitsmith project. Triggers on requests to automate the full splitsmith pipeline rather than running individual steps.
---

# splitsmith-match

You are orchestrating splitsmith's pipeline end-to-end on a folder of
competition videos. The user wants a finished match recap with as little
ceremony as possible -- you handle the mechanical work and only ask the
user for a decision when their eye matters.

This skill is the implementation of issue #211 and assumes the
splitsmith MCP server (issue #211 layers 3a-3e) is wired into the
client. If MCP isn't installed, fall back to the HTTP API at
`http://127.0.0.1:5174/api/...` (the user must launch `splitsmith ui`
first).

## Inputs to gather

Before running anything, confirm with the user:

1. **Project root** -- the path to the splitsmith match project
   directory. If the user didn't give one, check for a `project.json`
   in the current working directory; if none, ask. The MCP tools
   take this as the first arg of nearly every call.
2. **Source folder** -- where the raw competition videos live. The
   user often points at a USB drive (`/Volumes/GO 3S/DCIM/...`).
3. **Template** -- the export preset. Default is `match-recap` if
   the user doesn't say. List options with `list_templates` and
   echo back the picks before continuing.

Do not start the pipeline until you have all three. Be terse -- this
is a recap workflow, not a wizard.

## Sequence

The MCP tools are idempotent (detect_beep skips already-detected
videos, trim_audit_clip cache-hits, etc.) so re-running this skill
on the same project picks up where the last run stopped. The run
log under `<project>/.splitsmith-match-log.md` records every step
+ HITL decision.

### 1. Discover + assign

- `discover_videos(directory=<source folder>, recursive=true)` ->
  list of video paths.
- For each video, call `probe_video` to confirm readability + read
  duration. Drop unreadable files with a one-line note.
- Call `get_project` to read existing assignments and the stage
  list. If there are no stages yet, the user has to import a
  scoreboard first via the UI -- pause and tell them.
- Call `match_videos_to_stages` (HTTP only today; not yet an MCP
  tool) to get suggested assignments. If MCP fallback isn't
  available, present the videos sorted by mtime and ask the user
  which goes where.
- For each suggested assignment, call `assign_video(project_root,
  video_path, stage_number=N, role="primary"|"secondary")`. The
  first video on a stage auto-upgrades to primary; subsequent ones
  default to secondary.
- **HITL checkpoint -- ambiguous assignments**: when two videos
  could map to the same stage by timestamp (or one video could
  span two stages), present the candidates and ask the user to
  pick. Don't guess silently.

### 2. Beep detection

- For each stage's primary video, call `detect_beep(project_root,
  stage_number, video_id)`. It honours the auto-trust gate (#219):
  high-confidence beeps flip `beep_reviewed=True` automatically.
- For each secondary, call `detect_beep` too. Secondaries that fail
  in-stream detection mark `beep_auto_detect_failed=true` -- the
  SPA's cross-align flow handles those today; for now, surface them
  in the HITL summary so the user can complete those by hand.
- After the loop, call `get_hitl_queue(project_root)`. Two kinds:
  - `beep_low_confidence` -- the detector's top pick is below the
    auto-trust threshold (default 0.6). Show the candidate list to
    the user, ask which is right; call `select_beep_candidate` with
    their pick, then `mark_beep_reviewed`.
  - `beep_missing` -- detection found nothing. Ask the user for a
    timestamp by ear (or open the SPA's waveform); call
    `set_beep_manual` with their value.
- After resolving the queue, re-call `get_hitl_queue` to confirm
  it's empty before moving on. If items remain, surface them and
  stop -- don't fudge.

### 3. Trim + shot detection

- For each stage with a confirmed beep, call `trim_audit_clip(project_root,
  stage_number)` to build the audit clip. Cache-hits return
  immediately so this is cheap on re-runs.
- Call `detect_shots(project_root, stage_number)`. First call in a
  server lifetime loads CLAP + GBDT + PANN (~5 s); subsequent
  stages reuse the runtime. Each stage takes 20-40 s on CPU.
- After all stages, read each `<project>/audit/stage<N>.json` and
  count low-confidence shots (per-shot confidence < 0.7 is the
  rough HITL line; tune to taste).
- **HITL checkpoint -- shots to review**: if any stage has > 0
  flagged shots, summarise: "stage 5 has 3 low-confidence shots
  around t=6.4s, t=8.1s, t=11.5s". Ask whether to open the audit
  UI to review (open `http://127.0.0.1:5174/audit?stage=N` if so),
  or to ignore and proceed. Record the decision in the run log.

### 4. Per-stage + match export

- Read the chosen template via `list_templates(user_dir=null)`,
  pick the row whose id matches the user's choice, take its
  `settings` dict as the default args for the export tools.
- For each stage, call `export_stage(project_root, stage_number,
  ...settings)` with the `write_*` flags from the template.
- Call `export_match(project_root, stage_numbers=[1,2,...],
  ...settings)` with the template's match-level fields
  (`output_format`, `transition_kind`, `title_kind`,
  `youtube_sidecar`, etc.).
- **HITL checkpoint -- final review**: surface the output paths
  (FCPXML, MP4, sidecar) and any anomalies the engine raised.
  Ask whether the user wants to render an MP4 (if not already)
  or build a YouTube sidecar (if not already). Default is to
  trust the template's flags.

## HITL prompt format

Keep prompts short. Pattern:

```
Stage 5 needs your eye:
- beep is low-confidence (0.42, threshold 0.6)
- top candidates: 22.94s (conf 0.42), 25.43s (conf 0.31), 19.70s (conf 0.18)
Which is right? Reply with the timestamp, "skip" to leave for later, or "manual <s>" to type a value.
```

Don't ask sequential questions when one batch question covers the
case. ("Stage 3 + stage 5 both need a candidate pick -- here are
both." beats two separate prompts.)

## Run log

After every meaningful step, append to `<project>/.splitsmith-match-log.md`:

```
- 2026-05-09T12:34:56Z  detect_beep  stage 5 video go3s  conf=0.92  reviewed=auto
- 2026-05-09T12:35:01Z  HITL         stage 7 beep_missing -> manual 18.42s
- 2026-05-09T12:35:42Z  detect_shots stage 1  candidates=15 kept=12 consensus=3
- 2026-05-09T12:38:11Z  export_match output=match.fcpxml stages=8 anomalies=0
```

The log is the source of truth for "what did the agent do?". On a
re-run, scan it briefly to set context, but trust the MCP tools'
idempotency for the actual state.

## Error handling

- Tool errors (`ValueError`, `FileNotFoundError`) usually mean a
  precondition isn't met. Surface the message verbatim and stop;
  don't paper over with a guess.
- HITL queue items left after a "resolve" pass mean the user's
  reply didn't match -- ask again with the available options
  (the queue's `suggested_action` string is the verbatim ask).
- Sandbox errors (`SandboxError`) mean the project root or video
  path falls outside `SPLITSMITH_MCP_ALLOWED_ROOT`. Tell the user
  to restart the MCP server with `--allowed-root` set wider, or
  unset the env var.

## What you do NOT do

- Don't invent timestamps. If detection failed and the user can't
  give one, leave the beep unset and surface the stage as "needs
  manual beep" in the final summary.
- Don't run `force=True` on `detect_beep` unless the user asks for
  re-detection. Manual entries especially: `set_beep_manual` is
  the only path to overwrite, and only with the user's explicit
  word.
- Don't promise an MP4 render unless the template asked for it AND
  the user confirmed. MP4 output bakes the timeline (overlay /
  PiP / titles all rendered into a single file) which takes minutes
  and isn't reversible.
- Don't dive into the SPA without the user's say-so. The SPA is the
  manual-fix surface; this skill's job is the agent path. Pointing
  at a URL is fine; clicking around for the user isn't.

## Telling the user when you're done

End with a one-screen summary:

```
Match: Blacksmith 2026
Output: <project>/exports/blacksmith-2026.fcpxml
        <project>/exports/blacksmith-2026.mp4 (if rendered)
        <project>/exports/blacksmith-2026.yt.json (if sidecar)
8 stages exported, 1 with no shots audited (stage 6 -- detection
ran but consensus was empty; review at /audit?stage=6).
```

Run log is at `<project>/.splitsmith-match-log.md` for the full
trace.
