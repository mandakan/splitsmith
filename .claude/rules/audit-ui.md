---
paths:
  - "src/splitsmith/ui_static/src/pages/Audit*.tsx"
  - "src/splitsmith/ui_static/src/pages/MobileBeepReview*.tsx"
  - "src/splitsmith/ui_static/src/components/audit/**"
  - "src/splitsmith/ui_static/src/lib/auditStep*.ts"
  - "src/splitsmith/ui_static/src/lib/auditBand*.ts"
  - "src/splitsmith/ui_static/src/lib/beepFilter*.ts"
  - "src/splitsmith/ui_static/src/lib/useBeepQueue*"
  - "src/splitsmith/ui_static/src/components/StageTimeSection.tsx"
  - "src/splitsmith/ui_static/src/components/BeepSection.tsx"
  - "src/splitsmith/ui_static/src/lib/overview*.ts"
  - "src/splitsmith/beep_tone.py"
  - "src/splitsmith/ui/beep_tone_check.py"
  - "scripts/measure_beep_tones.py"
---

# Audit page and beep review

Multi-camera behaviour is in `pip.md`; the shared timeline band in
`breakdown-ui.md`.

## Layout

- Beep confirmation is Audit's step 1 (`BeepStep` on `useBeepQueue`);
  `/beep-review` redirects there on desktop, the phone keeps
  `MobileBeepReview`. Its top row is `BeepPreview` left and the candidate
  radiogroup right, bounded by `lg:h-[max(300px,calc(100dvh-560px))]`.
- Step 2's top row is the video (`MultiCamColumn`, a 16:9 letterboxed tile) and
  `ShotList`; on lg the row
  is `max(300px,calc(100dvh-502px))`, `max(398px,...)` with two or more cameras
  (two cameras at 1440x900 end the band's Audio row at the sticky footer). Below
  lg the tile is capped at `max-h-[max(240px,calc(100dvh-620px))]`.
- **The band's header row is Audit's only control row**: `TransportLine` is its
  `toolbar`, `LegendKey` its `actionsStart`, the help `?` its `actionsEnd`, and
  `TransportMenuItems` its `menuExtra` in the band's one `...` menu (frame
  steps, auto-step, full-resolution video, the trim / detect action, the
  `lib/auditBand` readouts). There is no strip above the band and no transport
  row in `MultiCamColumn`; the band's ruler and zoom replaced Audit's own (don't
  re-add `ZoomControls`). A new
  control goes in that header if it fits one row at 1440 wide, else in that
  menu, or on `CurrentShotLine`.
- On the band the audio track carries `WaveformTrack` and `MarkerLayer`; pins are
  on a Flags row above it that does not seek.
- A new per-shot signal goes on `ShotList` through `lib/auditStep.shotRows`.
- `_after_beep_reviewed` in `ui/server.py` is the one place a confirm chains
  trim and detection.

## Beep timeline (desktop)

`components/audit/BeepTimeline` fetches the video's own peaks and owns the
band's zoom, with `origin` at the detector's beep (`peaks.beep_time`, else 0) so
the ruler never moves while picking. A Candidates row (one pin per candidate,
outside-the-clip ones hidden) over a seekable Audio row; `offset = videoBeepTime
- peaks.beep_time` converts the band's clip-local time to a source-seconds pick
on release. A pick within 5 ms of the detected time is "no override".
`BeepWaveformPicker` keeps its own zoom for `MobileBeepReview` and
`StageTimeSection` only, never for `BeepStep`.

## Spectrum, filter and tone offset

- Spectrum row: `SpectrogramTrack`, `GET .../videos/{id}/spectrum` (spec
  `2026-10-17-beep-spectral-view-design`). Filter (`lib/beepFilter`): Off, Timer
  tone, Custom. Timer tone is the spectrum's `tone_band` (`beep_tone.tone_band`,
  about +/-200 Hz around the timer's frequency near the beep), else
  `BeepDetectConfig`'s 2.2-3.5 kHz. Detail peaks and Snap read it through the
  peaks route's `band`.
- `beep_tone.tone_offset_ms` is a confirmed beep's distance from its tone: the
  earliest rise within `reach_s` (0.3 s) that stays up for 80 % of the next
  100 ms. It is deliberately not Snap's nearest-peak rule (which lands mid-tone
  on a wavering tone). It reads any sustained sound in reach, so **it is a
  measure for confirmed beeps, not a beep detector**.
- Stored on the video with the beep time it was measured for
  (`beep_tone_offset_ms` / `beep_tone_for`; `tone_flag_ms` ignores a value whose
  beep moved or was unreviewed). Written on confirm, on the manual override and
  by the video `derive_audio` job. Triage carries it; Overview shows "check
  beep" at `BeepToneConfig.flag_ms` (20 ms). `scripts/measure_beep_tones.py`
  measures or backfills a match folder.
