# Real-footage corpus for overlay design (issue #686)

Short excerpts of real match video, local to this machine, for the design
judgement calls the synthetic fixture cannot answer: whether `dim=0.45`
still reads as "stopped" over a bright stage, whether the summary blur
holds up against real detail, whether white ink with a stroke survives
gravel and orange mesh, whether an empty tile reads as broken over real
picture.

Use it through the frame tool:

    uv run python scripts/render_grid_frames.py --corpus tests/fixtures/corpus \
        --shooters 4 --overlay --summary-hold 3

The tool normalizes each clip to the fixture's exact geometry (frame
rate, frame count, size) and assigns them to roster slots in sorted
filename order, cycling when there are more shooters than clips. Scoring,
audits and shot times stay the synthetic roster's -- only the pixels are
real.

## What must never happen

- **Nothing in this directory may enter the repository.** The repo is
  public and the footage carries other competitors, ROs and bystanders --
  faces and voices. This is a consent constraint, not a size one. The
  `.gitignore` block tracks only this README.
- **CI must never depend on it.** Any test that reads this directory must
  be excluded from the CI selection by construction, never skipped at
  runtime -- a skipped test reads as success (see #670/#671).

## The slots

Each clip should be 10-15s, with the timer beep landing ~3s in so it
lines up with the fixture's declared beep offset (3.009s). `run-end` is
the exception: it starts ~2.5s before the run's last shot, so the summary
freeze (which seeks ~7.0-7.6s in) lands in the pause after the run.

| clip | answers | current source |
|---|---|---|
| `bright-movement.mp4` | dim + legibility over the hardest bright case | hfo-masters-2026, s_f88d8aa0 stage 1 (B50), trim 2.0s+12s |
| `busy-background.mp4` | stroke + shadow over high-contrast props and banner text | hfo-masters-2026, s_f88d8aa0 stage 9 (B4), trim 2.0s+12s |
| `run-end.mp4` | whether the blurred, dimmed freeze reads as a conclusion | hfo-masters-2026, s_f88d8aa0 stage 3 (A200), trim 11.7s+12s |
| `shaded.mp4` | white ink + dark stroke in shade and against greenery | hfo-masters-2026, s_750a732e stage 2 (A100), trim 2.0s+12s |

Rebuild recipe (sources live under `/Volumes/X9/matches/`, beep is at
5.0s in every trim -- check `audit/stageN.json`):

    ffmpeg -y -ss <start> -i <trim.mp4> -t 12 -vf scale=1920:1080 \
        -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
        -c:a aac -b:a 128k -movflags +faststart tests/fixtures/corpus/<slot>.mp4
