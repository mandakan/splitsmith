# Derived media off the request path

Date: 2026-10-14. Status: draft for review.

## Why

On 2026-10-13 the phone was given short audio on first opening a stage on a
desktop-synced match: stage 8 came back as 10.475 s of 28.223 s, stage 9 as
19.903 s of 20.180 s (the bin counts the phone requested, in the hosted logs).
Opening Audit fires three requests at once (peaks, the 1 ms peaks, the audio).
Each one that found no WAV did the work itself, inside the request: one
streamed the 720p rendition from storage straight into its final path while
another ran ffmpeg on the half-written file. ffmpeg exits 0 on a truncated
input and writes a short WAV (reproduced locally), and that WAV was served.
The shooter then nudged eight shots on HFO stage 9 by about 300 ms to match
the waveform the phone drew; they were restored on 2026-10-14.

The root cause is not the missing lock. It is that request handlers produce
derived media at all. The hosted and local designs are job queues: work runs
in jobs, requests read what jobs published. These paths predate that and grew
lazy fallbacks on top of it.

## Principles

1. **A request never derives media.** No ffmpeg, no decode of a source video,
   no mirror of a large source from storage into a shared cache. It reads
   published artifacts.
2. **A missing artifact is a job, not a wait.** The request enqueues one
   idempotent job for it (deduplicated through `jobs.find_active`, keyed by
   the artifact) and answers `202 {"pending": true, "active_job": ...}`, the
   shape `raw_video_peaks` already uses on hosted. The SPA shows a preparing
   state and retries.
3. **Artifacts are made ahead of time by whoever owns the change.** A trim job
   makes the stage audio WAV beside the trim. A desktop push carries it.
4. **Every writer publishes whole.** A unique partial, a check (an MP4's
   boxes tile the file, a WAV is readable and as long as the clip it was cut
   from), then a rename. A reader can never see a half file, and a bad result
   is never published. File locks are not the coordination mechanism; the
   queue is. A lock stays only where two local processes can genuinely
   produce the same file (the desktop app and the CLI share `~/.splitsmith`).
5. **Cheap pure computation over a published file stays in the request.**
   Peaks are computed from the WAV in milliseconds and cached with an atomic
   write; a plain download of a small published artifact from storage into
   the local read cache, published whole, is a read, not derivation.

## Design

### Producers

| Artifact | Made by | Today |
| --- | --- | --- |
| Stage audit WAV (`stage<N>_cam_<id>_audit.wav`) | the trim job, right after the trim (both modes), pushed to storage on hosted | made lazily by shot detection or by the first `/audio` or `/peaks` request |
| Same, desktop-synced match | the desktop's trim job; the push plan carries it beside `_web.mp4` and `.params.json` | hosted extracts it from the rendition inside a request |
| Per-video source WAV (beep review picker) | the beep detection job that already reads the source | `video_audio` / `video_peaks` requests extract it from the raw file |
| Take WAV (raw file overview) | the job that registers the raw file (hosted already does this) | local `raw_video_peaks` extracts in the request |
| Beep preview clip | a `beep_preview` job | built in the request |
| 720p rendition | trim job (unchanged) | `stream_video` pulls it to local disk in the request on a mirror |

### Requests

`stage_audio`, `stage_peaks`, `video_audio`, `video_peaks`, `raw_video_peaks`,
`snap_beep_for_video` and the beep preview route read the artifact (local
file, else a whole download from storage). Missing: submit the producing job
when none is active and answer 202 pending. `stream_video` serves the
rendition by presigned redirect on hosted and never copies it to local disk.

On a desktop-synced mirror, the WAV in storage is current when it is not
older than the rendition in storage and its length matches the rendition's
`mvhd` duration within 0.25 s; otherwise the request answers 202 and the
desktop's next push replaces it (the mirror has no job that can cut it). The
desktop pushes the WAV after the rendition in the same push.

### SPA

`lib/api` turns a 202 pending into a typed `Pending` result. Audit (desktop
and phone), beep review and the raw file waveform show one muted
"Preparing audio" line in place of the waveform and retry with backoff
(1, 2, 4, then every 5 s) while the page is open. Nothing is drawn from a
partial result. The phone's 1 ms peaks request is sized from the current
stage only (today it is sized from the stage just left for one render).

### Local mode

Same model: the local server already runs trim and detection as jobs. A
first open of a stage with no WAV submits the job and shows the preparing
line, which on a desktop is a second or two.

## What changes, by file

- `derived.py`: `produce` (unique partial, check, rename) and `download`
  (whole download with a check); the lock becomes opt-in (`locked=True`) for
  the two local multi-process writers (the rendition and the trim cut).
- `ui/audio.py`: the `ensure_*` functions become producers called from jobs;
  new read-only `published_*` lookups for requests; storage pulls go through
  `derived.download`.
- `ui/server.py`: the trim job also makes and pushes the audit WAV; the
  routes above become read-or-enqueue; a `beep_preview` job kind.
- `sync/plan.py`, `sync/push.py`, the hosted key rule and the gc: the audit
  WAV joins the media channel beside the rendition.
- SPA: `lib/api` pending type, the preparing line on the four surfaces.
- `CLAUDE.md`: the principle, under Hosted playback.

## Testing

- Unit: `derived` (published whole, refused check, torn download), already
  written on the branch.
- Each converted route: missing artifact gives 202 with the job submitted
  once across two concurrent requests; present artifact is served; no route
  test may observe ffmpeg being invoked (a guard fixture fails the test if
  `subprocess.run` is called with ffmpeg inside a request).
- Each producer job: the artifact appears whole and is pushed.
- The incident: a mirror whose rendition download is slow; two concurrent
  `/peaks` requests both answer 202, never a short WAV. Must fail on main.
- Push plan: the audit WAV is planned beside the rendition and deleted with it.
- SPA: the pending type and the preparing line, per surface.

## Out of scope

- Moving export rendering, which already runs as jobs.
- The rise-foot rule and the phone layout.
- Healing data: HFO stage 9 is restored. Every other shot edited since
  2026-10-12 that sits more than 100 ms from its detection sits on a sound in
  the stage audio (checked 2026-10-14), so no other stage shows the bad
  waveform's mark.

## Rollout

One branch, one PR, one review pass over the seams (the push channel, the
202 contract between server and SPA). Hosted starts serving the desktop's WAV
as soon as the user's desktop pushes it; until then mirrors answer 202 for
stages whose WAV is not in storage, so the release notes ask desktop users to
let one sync run (the desktop's auto-sync does it on its own).
