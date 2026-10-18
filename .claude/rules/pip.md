---
paths:
  - "src/splitsmith/ui_static/src/components/video/**"
  - "src/splitsmith/ui_static/src/lib/pip*.ts"
  - "src/splitsmith/ui_static/src/lib/usePip*.ts"
  - "src/splitsmith/ui_static/src/lib/usePrimaryAudio*.ts"
  - "src/splitsmith/ui_static/src/lib/stageCameras*.ts"
  - "src/splitsmith/ui_static/src/lib/auditPip*.ts"
  - "src/splitsmith/ui_static/src/lib/phoneCameraStack*.ts"
  - "src/splitsmith/ui_static/src/lib/keyboard.ts"
  - "src/splitsmith/ui_static/src/components/coach/StageViewer.tsx"
  - "src/splitsmith/ui_static/src/components/results/ResultsPlayer.tsx"
  - "src/splitsmith/ui_static/src/components/audit/MultiCamColumn.tsx"
  - "src/splitsmith/ui_static/src/components/audit/mobile/CameraStack.tsx"
  - "src/splitsmith/ui_static/src/pages/ResultsStage*.tsx"
  - "src/splitsmith/ui_static/src/pages/Audit*.tsx"
  - "src/splitsmith/ui_static/src/pages/MobileAudit*.tsx"
---

# Multiple cameras: picture-in-picture

A stage's other cameras show as **picture-in-picture** through
`components/video/PipView`, never stacked tiles or a camera picker. Compare
keeps its grid; the phone Audit stacks (below). The usage note is at the top of
`PipView.tsx`.

## Where the rules live

- `lib/pip.ts`: swap, cycle over the cameras that are not big, inset size (28 %
  of the frame, never under 180 px nor over 45 %), corners, the beep-offset
  clock and drift threshold, `insetStream` (the rendition whenever there is
  one, then only kinds sharing the beep anchor; `null` = unavailable).
- `lib/pipSync`: outside the inset clip's span it holds paused at the clamp and
  is never played (play on ended media restarts at 0).
- `lib/usePip`: state, reset to the primary (or a page's `start` camera) on a
  new `stageKey`. `lib/pipPrefs`: the corner, per browser.
- The page keeps its own big player (src from `pip.big`, passed as an element
  through a callback ref) and its own key handler (`pipKeyAction`: C /
  Shift+C).
- The inset spends no red; the primary's speaker glyph and PRIMARY chip travel
  with it.
- **Audio stays the primary's.** With a secondary big, `lib/usePrimaryAudio`
  plays the primary's own stream anchored on its own `beep_in_clip` (the player
  muted, a detached `<audio>` on `attachInsetSync`), with a lead correction per
  play start, never per frame. An audio error unmutes the player and falls back
  to the next stream kind.
- **A new follower element goes through `attachInsetSync`**, never a loop of its
  own.

## Coach and Breakdown

`components/coach/StageViewer`'s `StageVideo` (cameras from `lib/stageCameras`:
primary first, "Cam N"). C / Shift+C through `lib/keyboard.usePipCycleKeys`,
which skips a press another handler claimed. A swap hands the big camera's beep
to the workspace (`useStageWorkspace.setBigBeep`). `currentTime` stays in the
primary clip's seconds (where `coach.beep_time` and `shots[].time_absolute`
live): the video's clock comes in through `fromVideoTime` and every seek goes
out through `stageCameras.primaryToBig` / `bigToPrimary`. **A new seek path
calls the workspace, never `videoRef.current.currentTime`.** A moment before
the big clip's start lands on its first frame.

## Splits stage page

`ResultsPlayer` takes the PipView as its `overlay` and hands the big `<video>`
out through `onVideoElement`. **A swap changes `src` in place, never by a
remount** (no `key` on the player), carrying the position as seconds from the
beep and the play state, without re-running the window-start, moment or
autoplay seeks. The starting camera (`?v=` on a moment link, else `?cams=`,
else `compare_camera`) is `usePip`'s `start`, latched once per mount; a swap
writes `?cams=`.

## Audit (desktop)

The tile is the whole column under its header. `lib/auditPip` maps each camera
to its inset clip and beep through `planServedClip`, so a camera reads the same
instant inset or big. Each camera's `CamPill` travels with its picture; a
camera with no beep cannot enter the inset and keeps its pill in the header. The
big camera is `usePip`'s (`activeVideoIndex` derives from it; a new stage resets
to the primary). **A swap changes the picture only**: `beepOffset` maps the big
clip onto the primary's timeline, so waveform, markers, shot list and every
seek stay the primary's; the sound is `usePrimaryAudio` playing the stage audit
WAV (the waveform's file), anchored on `peaks.beep_time`.

## Audit (phone)

`pages/MobileAudit.tsx`'s Video dialog stacks instead (`lib/phoneCameraStack.
stackCameras` over `auditPipCameras`, 720p first;
`components/audit/mobile/CameraStack`): Cam 1 full width, one other camera
under it, a chooser cycling the others (two decodes at most). The active camera
has native controls and is the clock; the other follows muted through
`attachInsetSync`; a tap makes it active without remounting. Sound is never two
cameras and never `usePrimaryAudio`: Cam 1 sounds unless the active camera's own
control unmutes it (`volumechange`). No lead correction there (seeking the
playing follower overshot). One camera keeps the single player; beep review
stays one camera.
